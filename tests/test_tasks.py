import asyncio
import json

from conftest import run

from ultra.agents import ActionError


async def wait(svc, tid, statuses=("completed", "failed", "needs_approval", "stopped", "paused"), timeout=10):
    for _ in range(int(timeout / 0.02)):
        t = svc.engine.get(tid)
        if t["status"] in statuses:
            return t
        await asyncio.sleep(0.02)
    raise AssertionError(f"task stuck: {svc.engine.get(tid)['status']}")


def test_offline_task_completes_and_is_verified(make_services):
    async def go():
        svc = make_services()
        tid = await svc.engine.create("أنشئ مشروع متجر الملابس")
        t = await wait(svc, tid)
        assert t["status"] == "completed" and t["verified"] == 1 and t["progress"] == 100
        assert svc.db.one("SELECT * FROM projects WHERE name='متجر الملابس'")
        logs = [l["message"] for l in svc.engine.logs(tid)]
        assert "quality check passed" in logs
    run(go())


def test_memory_refuses_secrets(make_services):
    async def go():
        svc = make_services()
        tid = await svc.engine.create("تذكر أن كلمة السر هي 1234abcd")
        t = await wait(svc, tid)
        assert t["status"] == "needs_approval"          # "password" is a sensitive phrase: asked first
        assert await svc.engine.approve(tid)
        t = await wait(svc, tid, ("completed", "failed"))
        assert t["status"] == "failed" and "كلمات السر" in t["error"]
        assert not svc.db.one("SELECT * FROM memory")
    run(go())


def test_risky_step_waits_for_approval_then_runs(make_services):
    async def go():
        svc = make_services()
        (svc.ws.root / "inbox" / "a.jpg").write_bytes(b"x")
        (svc.ws.root / "inbox" / "b.pdf").write_bytes(b"x")
        tid = await svc.engine.create("رتب الملفات")
        t = await wait(svc, tid)
        assert t["status"] == "needs_approval"
        assert (svc.ws.root / "inbox" / "a.jpg").exists()     # nothing moved yet
        assert await svc.engine.approve(tid)
        t = await wait(svc, tid, ("completed", "failed"))
        assert t["status"] == "completed", t["error"]
        assert (svc.ws.root / "inbox" / "images" / "a.jpg").exists()
        assert (svc.ws.root / "inbox" / "documents" / "b.pdf").exists()
    run(go())


def test_deny_and_suspicious(make_services, fake_llm):
    async def go():
        svc = make_services()
        svc.vault.set("openai_api_key", "sk-test-1234567890abcdef")
        fake_llm.replies = ['{"summary": "", "clarification": "", "steps": [{"agent": "files", "action": "delete", "params": {"path": "notes"}}]}']
        tid = await svc.engine.create("احذف كل الملفات")
        t = await wait(svc, tid)
        assert t["status"] == "needs_approval"
        assert "حساسة" in t["approval_reason"]          # whole request flagged before planning runs
        assert await svc.engine.deny(tid)
        assert svc.engine.get(tid)["status"] == "stopped"
        assert (svc.ws.root / "notes").exists()
    run(go())


def test_emergency_stop_blocks_everything(make_services):
    async def go():
        svc = make_services()
        gate = asyncio.Event()

        async def slow(ctx, **kw):
            await gate.wait()
            from ultra.agents import StepResult
            return StepResult("late")
        svc.agents["manager"].actions["time"].fn = slow
        tid = await svc.engine.create("شحال الساعة")
        await wait(svc, tid, ("running",))
        n = await svc.engine.emergency_stop()
        assert n == 1 and svc.engine.get(tid)["status"] == "stopped"
        try:
            await svc.engine.create("المشاريع")
            raise AssertionError("should be blocked")
        except Exception as e:
            assert type(e).__name__ == "EmergencyActive"
        svc.engine.emergency_release()
        tid2 = await svc.engine.create("المشاريع")
        assert (await wait(svc, tid2))["status"] == "completed"
    run(go())


def test_retry_transient_errors_then_success(make_services):
    async def go():
        svc = make_services()
        calls = {"n": 0}
        from ultra.agents import StepResult

        async def flaky(ctx, **kw):
            calls["n"] += 1
            if calls["n"] < 2:
                raise ConnectionError("network blip")
            return StepResult("ok now")
        svc.agents["manager"].actions["time"].fn = flaky
        tid = await svc.engine.create("الوقت")
        t = await wait(svc, tid)
        assert t["status"] == "completed" and calls["n"] == 2
        assert any("attempt 1 failed" in l["message"] for l in svc.engine.logs(tid))
    run(go())


def test_unverified_step_fails_quality_check(make_services):
    async def go():
        svc = make_services()
        from ultra.agents import StepResult

        async def liar(ctx, **kw):
            return StepResult("done!", verified=False)
        svc.agents["manager"].actions["time"].fn = liar
        tid = await svc.engine.create("الوقت")
        t = await wait(svc, tid)
        assert t["status"] == "failed" and "verification" in t["error"].lower() or "التحقق" in t["error"]
    run(go())


def test_pause_resume_and_recovery_after_restart(make_services, tmp_path):
    async def go():
        svc = make_services()
        tid = await svc.engine.create("المشاريع")
        await wait(svc, tid, ("completed",))
        # simulate a crash in the middle of a task
        svc.db.execute("UPDATE tasks SET status='running', progress=40 WHERE id=?", (tid,))
        svc.db.execute("UPDATE tasks SET steps=?, current_step=0, plan=? WHERE id=?",
                       (json.dumps([{"agent": "business", "action": "list_projects", "params": {},
                                     "description": "x", "status": "pending"}]),
                        json.dumps({"language": "ar", "warnings": [], "clarification": ""}), tid))
        svc.db.close()
        svc2 = make_services()
        assert svc2.unclean_start
        await svc2.engine.recover()
        t = await wait(svc2, tid, ("completed",))
        assert t["status"] == "completed"
        assert any("recovered after restart" in l["message"] for l in svc2.engine.logs(tid))
    run(go())


def test_parallel_tasks_respect_limit(make_services):
    async def go():
        svc = make_services()
        svc.db.set_setting("max_parallel_tasks", 2)
        running, peak = {"n": 0}, {"n": 0}
        from ultra.agents import StepResult

        async def busy(ctx, **kw):
            running["n"] += 1
            peak["n"] = max(peak["n"], running["n"])
            await asyncio.sleep(0.1)
            running["n"] -= 1
            return StepResult("ok")
        svc.agents["manager"].actions["time"].fn = busy
        ids = [await svc.engine.create("الوقت") for _ in range(5)]
        for i in ids:
            assert (await wait(svc, i, ("completed",)))["status"] == "completed"
        assert peak["n"] == 2
    run(go())


def test_llm_task_with_chat_and_file_output(make_services, fake_llm):
    async def go():
        svc = make_services()
        svc.vault.set("openai_api_key", "sk-test-1234567890abcdef")
        fake_llm.replies = [
            '{"summary": "خطة", "clarification": "", "steps": ['
            '{"agent": "business", "action": "write_document", "params": {"instructions": "اكتب رسالة", "filename": "رسالة"}, "description": "كتابة"}]}',
            "السلام عليكم، هذه رسالة.",
        ]
        tid = await svc.engine.create("اكتب رسالة شكر للمورد واحفظها")
        t = await wait(svc, tid)
        assert t["status"] == "completed", t["error"]
        assert t["files"] == ["documents/رسالة.md"]
        assert (svc.ws.root / "documents" / "رسالة.md").read_text(encoding="utf-8") == "السلام عليكم، هذه رسالة."
    run(go())


def test_clarification_question_and_answer(make_services, fake_llm):
    async def go():
        svc = make_services()
        svc.vault.set("openai_api_key", "sk-test-1234567890abcdef")
        fake_llm.replies = ['{"summary": "", "clarification": "أي ملف تقصد؟", "steps": []}',
                            '{"summary": "", "clarification": "", "steps": [{"agent": "manager", "action": "time", "params": {}}]}']
        tid = await svc.engine.create("افتح الملف")
        t = await wait(svc, tid)
        assert t["status"] == "needs_approval" and "أي ملف تقصد" in t["approval_reason"]
        assert not await svc.engine.approve(tid)       # an answer is needed, not a click
        assert await svc.engine.answer(tid, "ملف المبيعات")
        t = await wait(svc, tid, ("completed", "failed"))
        assert t["status"] == "completed"
    run(go())


def test_workspace_jail(make_services):
    svc = make_services()
    try:
        svc.ws.path("../../etc/passwd")
        raise AssertionError("escaped")
    except ActionError as e:
        assert str(e) == "path_outside_workspace"
