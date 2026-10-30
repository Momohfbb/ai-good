"""Task engine: queue, parallel execution, approvals, pause/stop, emergency stop,
recovery after restart, retries, verification and the execution log.

Statuses: pending, running, paused, needs_approval, completed, failed, stopped
"""
from __future__ import annotations

import asyncio
import json
import shutil
import time
import uuid
from pathlib import Path
from typing import Awaitable, Callable

from .agents import ActionError, Agent, StepResult, Workspace, system_info
from .brain import Brain
from .db import Database
from .llm import BudgetExceeded, LLMError, LLMResult, Router
from .messages import err, t
from .pc import Desktop

STATUSES = ("pending", "running", "paused", "needs_approval", "completed", "failed", "stopped")
ACTIVE = ("pending", "running", "paused", "needs_approval")


class EmergencyActive(Exception):
    pass


class StepContext:
    def __init__(self, engine: "TaskEngine", task: dict, language: str):
        self.engine = engine
        self.db = engine.db
        self.router = engine.router
        self.ws = engine.ws
        self.task = task
        self.language = language or "ar"
        self.history: list[dict] = []
        self.models_used: list[str] = []

    def t(self, key: str, **kw) -> str:
        return t(key, self.language, **kw)

    def log(self, level: str, message: str) -> None:
        self.engine.log(self.task["id"], level, message)

    def note_model(self, res: LLMResult) -> None:
        self.models_used.append(f"{res.provider}:{res.model}")
        self.log("info", f"model {res.provider}:{res.model} in={res.input_tokens} out={res.output_tokens} "
                         f"cost=${res.cost_usd:.4f}")

    def memory_context(self) -> str:
        rows = self.db.all("SELECT content FROM memory WHERE scope='personal' ORDER BY updated_at DESC LIMIT 40")
        return "\n".join(f"- {r['content']}" for r in rows)

    def backup_file(self, p: Path) -> None:
        bdir = self.ws.path(".versions")
        bdir.mkdir(exist_ok=True)
        shutil.copy2(p, bdir / f"{int(time.time())}_{p.name}")

    def system_info(self) -> dict:
        return system_info(self.engine.started_at, self.router)

    async def notify(self, text: str) -> int:
        return await self.engine.notify_owner(text)


class TaskEngine:
    def __init__(self, db: Database, router: Router, brain: Brain, agents: dict[str, Agent], ws: Workspace):
        self.db = db
        self.router = router
        self.brain = brain
        self.agents = agents
        self.ws = ws
        self.started_at = time.time()
        self.desktop = Desktop()
        self._running: dict[str, asyncio.Task] = {}
        self._sem: asyncio.Semaphore | None = None
        self._sem_size = 0
        self.listeners: list[Callable[[dict, str], Awaitable[None]]] = []   # (task, event)
        self.owner_notifier: Callable[[str], Awaitable[int]] | None = None

    # ------------------------------------------------------------------ helpers
    @property
    def emergency(self) -> bool:
        return bool(self.db.get_setting("emergency_stop", False))

    def get(self, task_id: str) -> dict | None:
        row = self.db.one("SELECT * FROM tasks WHERE id=?", (task_id,))
        if row:
            for k in ("plan", "steps", "files", "attachments"):
                row[k] = json.loads(row[k]) if row[k] else ([] if k != "plan" else None)
        return row

    def list(self, status: str | None = None, limit: int = 100) -> list[dict]:
        if status == "active":
            rows = self.db.all(f"SELECT id FROM tasks WHERE status IN ({','.join('?' * len(ACTIVE))}) "
                               "ORDER BY created_at DESC LIMIT ?", (*ACTIVE, limit))
        elif status:
            rows = self.db.all("SELECT id FROM tasks WHERE status=? ORDER BY created_at DESC LIMIT ?", (status, limit))
        else:
            rows = self.db.all("SELECT id FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,))
        return [self.get(r["id"]) for r in rows]

    def logs(self, task_id: str) -> list[dict]:
        return self.db.all("SELECT ts, level, message FROM task_logs WHERE task_id=? ORDER BY id", (task_id,))

    def log(self, task_id: str, level: str, message: str) -> None:
        self.db.execute("INSERT INTO task_logs(task_id,ts,level,message) VALUES(?,?,?,?)",
                        (task_id, time.time(), level, message[:4000]))

    def _save(self, task_id: str, **fields) -> None:
        for k in ("plan", "steps", "files", "attachments"):
            if k in fields:
                fields[k] = json.dumps(fields[k], ensure_ascii=False)
        fields["updated_at"] = time.time()
        cols = ", ".join(f"{k}=?" for k in fields)
        self.db.execute(f"UPDATE tasks SET {cols} WHERE id=?", (*fields.values(), task_id))

    async def _emit(self, task_id: str, event: str) -> None:
        task = self.get(task_id)
        for fn in list(self.listeners):
            try:
                await fn(task, event)
            except Exception as e:  # a broken listener must not break the engine
                self.log(task_id, "warning", f"listener error: {e}")

    async def notify_owner(self, text: str) -> int:
        if self.owner_notifier:
            return await self.owner_notifier(text)
        return 0

    def _lang(self, task: dict) -> str:
        plan = task.get("plan") or {}
        return plan.get("language") or task.get("language") or self.db.get_setting("ui_language", "ar")

    # ------------------------------------------------------------------ lifecycle
    def _semaphore(self) -> asyncio.Semaphore:
        size = 1 if self.db.get_setting("low_resource_mode") else max(1, int(self.db.get_setting("max_parallel_tasks", 3)))
        if self._sem is None or size != self._sem_size:
            self._sem, self._sem_size = asyncio.Semaphore(size), size
        return self._sem

    async def recover(self) -> int:
        """After a restart: tasks that were running continue from their next unfinished step."""
        rows = self.db.all("SELECT id FROM tasks WHERE status IN ('running','pending')")
        for r in rows:
            self._save(r["id"], status="pending")
            self.log(r["id"], "warning", "recovered after restart")
            self.schedule(r["id"])
        return len(rows)

    def schedule(self, task_id: str) -> None:
        if self.emergency or task_id in self._running:
            return
        self._running[task_id] = asyncio.create_task(self._run_guarded(task_id))

    async def wait_idle(self, timeout: float = 30) -> None:
        end = time.time() + timeout
        while self._running and time.time() < end:
            await asyncio.sleep(0.05)

    # ------------------------------------------------------------------ public commands
    async def create(self, text: str, source: str = "dashboard", chat_id: int | None = None,
                     attachments: list[str] | None = None, title: str | None = None) -> str:
        if self.emergency:
            raise EmergencyActive()
        text = (text or "").strip()
        if not text and not attachments:
            raise ValueError("empty")
        tid = uuid.uuid4().hex[:10]
        now = time.time()
        self.db.execute("INSERT INTO tasks(id,title,description,source,chat_id,status,created_at,updated_at,attachments)"
                        " VALUES(?,?,?,?,?,?,?,?,?)",
                        (tid, (title or text or "file")[:80], text, source, chat_id, "pending", now, now,
                         json.dumps(attachments or [], ensure_ascii=False)))
        self.log(tid, "info", f"created from {source}")
        self.db.audit(source, "task_created", f"{tid}: {text[:200]}")
        self.schedule(tid)
        await self._emit(tid, "created")
        return tid

    async def approve(self, task_id: str, actor: str = "owner") -> bool:
        task = self.get(task_id)
        if not task or task["status"] != "needs_approval" or self.emergency:
            return False
        plan = task["plan"] or {}
        if plan.get("clarification"):
            return False  # needs an answer, not an approval
        steps = task["steps"]
        suspicious = any(w.startswith("suspicious:") for w in plan.get("warnings", []))
        if suspicious and not plan.get("approved_plan"):
            plan["approved_plan"] = True          # this approval was for the whole request
        elif task["current_step"] < len(steps):
            # One risky step, or every PC step of the task when they were listed together.
            for j in plan.pop("approve_steps", None) or [task["current_step"]]:
                if j < len(steps):
                    steps[j]["approved"] = True
        self._save(task_id, status="pending", plan=plan, steps=steps, approval_reason=None)
        self.log(task_id, "info", f"approved by {actor}")
        self.db.audit(actor, "task_approved", task_id)
        self.schedule(task_id)
        return True

    async def deny(self, task_id: str, actor: str = "owner") -> bool:
        task = self.get(task_id)
        if not task or task["status"] != "needs_approval":
            return False
        self._save(task_id, status="stopped", finished_at=time.time(), error="denied")
        self.log(task_id, "warning", f"denied by {actor}")
        self.db.audit(actor, "task_denied", task_id)
        await self._emit(task_id, "stopped")
        return True

    async def answer(self, task_id: str, text: str) -> bool:
        """Answer a clarification question: the request is re-planned with the answer."""
        task = self.get(task_id)
        if not task or task["status"] != "needs_approval" or not (task["plan"] or {}).get("clarification"):
            return False
        desc = f"{task['description']}\n\n[توضيح/clarification]: {text.strip()}"
        self._save(task_id, description=desc, plan=None, steps=[], current_step=0, status="pending",
                   approval_reason=None)
        self.log(task_id, "info", "clarification received")
        self.schedule(task_id)
        return True

    async def pause(self, task_id: str) -> bool:
        task = self.get(task_id)
        if not task or task["status"] not in ("pending", "running"):
            return False
        self._save(task_id, status="paused")
        self.log(task_id, "info", "paused (stops after the current step)")
        return True

    async def resume(self, task_id: str) -> bool:
        task = self.get(task_id)
        if not task or task["status"] != "paused" or self.emergency:
            return False
        self._save(task_id, status="pending")
        self.log(task_id, "info", "resumed")
        self.schedule(task_id)
        return True

    async def stop(self, task_id: str, reason: str = "stopped by owner") -> bool:
        task = self.get(task_id)
        if not task or task["status"] not in ACTIVE:
            return False
        running = self._running.get(task_id)
        if running:
            running.cancel()
        self._save(task_id, status="stopped", finished_at=time.time(), error=reason)
        self.log(task_id, "warning", reason)
        await self._emit(task_id, "stopped")
        return True

    async def retry(self, task_id: str) -> bool:
        task = self.get(task_id)
        if not task or task["status"] not in ("failed", "stopped") or self.emergency:
            return False
        steps = task["steps"]
        for s in steps:
            if s.get("status") != "done":
                s.update(status="pending", error=None)
        self._save(task_id, status="pending", error=None, steps=steps, finished_at=None)
        self.log(task_id, "info", "retry requested")
        self.schedule(task_id)
        return True

    async def emergency_stop(self, actor: str = "owner") -> int:
        self.db.set_setting("emergency_stop", True)
        self.db.audit(actor, "EMERGENCY_STOP", "", "critical")
        rows = self.db.all(f"SELECT id FROM tasks WHERE status IN ({','.join('?' * len(ACTIVE))})", ACTIVE)
        for task in list(self._running.values()):
            task.cancel()
        for r in rows:
            self._save(r["id"], status="stopped", finished_at=time.time(), error="emergency stop")
            self.log(r["id"], "critical", "EMERGENCY STOP")
        await asyncio.sleep(0)
        return len(rows)

    def emergency_release(self, actor: str = "owner") -> None:
        self.db.set_setting("emergency_stop", False)
        self.db.audit(actor, "emergency_released", "", "warning")

    # ------------------------------------------------------------------ execution
    async def _run_guarded(self, task_id: str) -> None:
        try:
            async with self._semaphore():
                await self._run(task_id)
        except asyncio.CancelledError:
            self.log(task_id, "warning", "cancelled")
        except Exception as e:
            self._save(task_id, status="failed", error=f"internal: {e}", finished_at=time.time())
            self.log(task_id, "error", f"internal error: {e!r}")
            await self._emit(task_id, "failed")
        finally:
            self._running.pop(task_id, None)

    async def _needs_approval(self, task_id: str, reason: str) -> None:
        self._save(task_id, status="needs_approval", approval_reason=reason)
        self.log(task_id, "warning", f"waiting for approval: {reason}")
        await self._emit(task_id, "needs_approval")

    async def _run(self, task_id: str) -> None:
        task = self.get(task_id)
        if not task or task["status"] != "pending" or self.emergency:
            return
        self._save(task_id, status="running", started_at=task["started_at"] or time.time())
        await self._emit(task_id, "running")

        # 1. analyse + plan
        if not task["plan"]:
            self.log(task_id, "info", "analysing request")
            plan = await self.brain.plan(task["description"], task["attachments"])
            steps = [dict(s, status="pending") for s in plan.steps]
            pdict = {"summary": plan.summary, "language": plan.language, "planner": plan.planner,
                     "warnings": plan.warnings, "clarification": plan.clarification}
            self._save(task_id, plan=pdict, steps=steps, language=plan.language, current_step=0)
            self.log(task_id, "info", f"plan by {plan.planner}: " +
                     " → ".join(f"{s['agent']}.{s['action']}" for s in steps) if steps else "no steps")
            for w in plan.warnings:
                self.log(task_id, "warning", w)
            task = self.get(task_id)
        plan, steps, lang = task["plan"], task["steps"], self._lang(task)

        if plan.get("clarification"):
            if plan["clarification"] == "no_model":
                self._finish(task_id, "failed", error=t("no_model", lang))
                await self._emit(task_id, "failed")
            else:
                await self._needs_approval(task_id, t("clarify", lang, q=plan["clarification"]))
            return

        sus = [w for w in plan.get("warnings", []) if w.startswith("suspicious:")]
        if sus and not plan.get("approved_plan"):
            await self._needs_approval(task_id, t("suspicious", lang, what=sus[0].split(":", 1)[1]))
            return

        # 2. execute steps
        ctx = StepContext(self, task, lang)
        prev = next((s.get("result") or "" for s in reversed(steps[:task["current_step"]]) if s.get("result")), "")
        files = list(task["files"])
        retries = int(self.db.get_setting("step_retries", 2))
        for i in range(task["current_step"], len(steps)):
            current = self.get(task_id)
            if current["status"] != "running" or self.emergency:
                self.log(task_id, "info", f"halted before step {i + 1} ({current['status']})")
                return
            step = steps[i]
            agent = self.agents[step["agent"]]
            action = agent.actions[step["action"]]
            if action.risk == "approval" and not step.get("approved"):
                if agent.key == "pc":
                    # Ask once for all PC steps, showing each one exactly as it will run.
                    group = [j for j in range(i, len(steps)) if steps[j]["agent"] == "pc"
                             and agent.actions[steps[j]["action"]].risk == "approval" and not steps[j].get("approved")]
                    plan["approve_steps"] = group
                    self._save(task_id, steps=steps, current_step=i, plan=plan)
                    await self._needs_approval(task_id, t("pc_steps", lang) + "\n" + "\n".join(
                        f"{n + 1}. {steps[j]['description']} ({steps[j]['action']} "
                        f"{json.dumps(steps[j]['params'], ensure_ascii=False)[:200]})" for n, j in enumerate(group)))
                    return
                self._save(task_id, steps=steps, current_step=i)
                await self._needs_approval(task_id, t("risky_step", lang, step=step["description"]) +
                                           f" ({step['agent']}.{step['action']} {json.dumps(step['params'], ensure_ascii=False)[:200]})")
                return
            params = {k: (prev if v == "$prev" else v) for k, v in step["params"].items()}
            step.update(status="running", started=time.time())
            self._save(task_id, steps=steps, current_step=i)
            self.log(task_id, "info", f"step {i + 1}/{len(steps)}: {agent.key}.{action.name}")
            result: StepResult | None = None
            for attempt in range(retries + 1):
                try:
                    result = await action.fn(ctx, **params)
                    break
                except (ActionError, BudgetExceeded, TypeError) as e:
                    code = str(e) if not isinstance(e, BudgetExceeded) else "budget"
                    step.update(status="failed", error=err(code, lang) if code != "budget" else t("budget", lang))
                    break
                except LLMError as e:
                    if "no_model_configured" in str(e):
                        step.update(status="failed", error=t("no_model", lang))
                        break
                    step["error"] = str(e)[:500]
                    self.log(task_id, "warning", f"attempt {attempt + 1} failed: {str(e)[:300]}")
                    if attempt < retries:
                        await asyncio.sleep(min(2 ** attempt, 8))
                except Exception as e:
                    step["error"] = f"{type(e).__name__}: {str(e)[:400]}"
                    self.log(task_id, "warning", f"attempt {attempt + 1} failed: {step['error']}")
                    if attempt < retries:
                        await asyncio.sleep(min(2 ** attempt, 8))
            if result is None:
                step.update(status="failed", finished=time.time())
                self._save(task_id, steps=steps)
                self._finish(task_id, "failed", error=step.get("error") or "step failed")
                self.log(task_id, "error", f"step {i + 1} failed: {step.get('error')}")
                await self._emit(task_id, "failed")
                return
            step.update(status="done", result=result.text[:20000], verified=result.verified,
                        llm=result.llm_output, finished=time.time(), error=None)
            files += [f for f in result.files if f not in files]
            prev = result.text
            done = sum(1 for s in steps if s["status"] == "done")
            self._save(task_id, steps=steps, current_step=i + 1, files=files,
                       progress=int(90 * done / max(1, len(steps))))

        # 3. quality check
        await self._verify_and_finish(task_id, steps, files, prev, lang)

    async def _verify_and_finish(self, task_id, steps, files, final_text, lang) -> None:
        problems = [f"step {n + 1} not verified" for n, s in enumerate(steps) if not s.get("verified")]
        problems += [f"missing file {f}" for f in files if not self.ws.path(f).exists()]
        if not (final_text or "").strip():
            problems.append("empty result")
        if problems:
            self.log(task_id, "error", "quality check failed: " + "; ".join(problems))
            self._finish(task_id, "failed", error=t("verify_failed", lang) + ": " + "; ".join(problems),
                         result=final_text)
            await self._emit(task_id, "failed")
            return
        note = ""
        if self.db.get_setting("cross_check_enabled") and any(s.get("llm") for s in steps):
            try:
                rev = await self.router.complete(
                    "verify", "Review this assistant answer for factual errors or missing parts. "
                              "Reply 'OK' if it is fine, otherwise 'PROBLEM:' and a one-line reason.\n\n"
                              f"<request>{self.get(task_id)['description']}</request>\n<answer>{final_text[:15000]}</answer>",
                    max_tokens=2000)
                verdict = rev.text.strip()
                self.log(task_id, "info", f"cross-check by {rev.provider}:{rev.model}: {verdict[:300]}")
                if not verdict.upper().startswith("OK"):
                    note = "\n\n⚠️ " + verdict[:500]
            except LLMError as e:
                self.log(task_id, "warning", f"cross-check unavailable: {e}")
        self.log(task_id, "info", "quality check passed")
        self._finish(task_id, "completed", result=(final_text or "") + note, verified=1)
        await self._emit(task_id, "completed")

    def _finish(self, task_id: str, status: str, error: str | None = None, result: str | None = None,
                verified: int = 0) -> None:
        fields = dict(status=status, finished_at=time.time(), verified=verified,
                      progress=100 if status == "completed" else None)
        if error is not None:
            fields["error"] = error[:2000]
        if result is not None:
            fields["result"] = result
        if fields["progress"] is None:
            fields.pop("progress")
        self._save(task_id, **fields)
