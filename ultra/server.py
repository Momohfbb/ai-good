"""HTTP server: dashboard + JSON API used by the dashboard, the Windows app and the Android app."""
from __future__ import annotations

import asyncio
import contextlib
import shutil
import time
import zipfile
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__, config
from .agents import ActionError, Workspace, build_agents, system_info
from .brain import Brain
from .db import Database
from .llm import Router
from .plugins import discover, file_hash, load_plugins
from .security import Access, Vault, safe_upload_name
from .tasks import EmergencyActive, TaskEngine
from .telegram import TelegramBot

COOKIE = "ultra_session"

SETTING_KEYS = {k for k in config.DEFAULT_SETTINGS} - {"host", "port"}


class Services:
    def __init__(self, data_dir: Path, telegram_api: str = "https://api.telegram.org", http=None):
        self.data_dir = data_dir
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "backups").mkdir(exist_ok=True)
        restore = data_dir / "restore_pending.db"
        if restore.exists():
            shutil.move(str(restore), str(data_dir / "ultra.db"))
            for ext in ("-wal", "-shm"):
                with contextlib.suppress(FileNotFoundError):
                    (data_dir / f"ultra.db{ext}").unlink()
        self.unclean_start = (data_dir / "ultra.db").exists() and not (data_dir / ".clean_shutdown").exists()
        with contextlib.suppress(FileNotFoundError):
            (data_dir / ".clean_shutdown").unlink()
        self.db = Database(data_dir / "ultra.db")
        for k, v in config.DEFAULT_SETTINGS.items():
            if self.db.get_setting(k) is None:
                if k == "workspace_dir":
                    v = str(data_dir / "workspace")
                self.db.set_setting(k, v)
        self.vault = Vault(self.db, data_dir / "secret.key")
        self.access = Access(self.db, data_dir / "ADMIN_KEY.txt")
        self.http = http or httpx.AsyncClient(timeout=httpx.Timeout(180, connect=10))
        local_http = http or httpx.AsyncClient(timeout=httpx.Timeout(180, connect=10), trust_env=False)
        self.router = Router(self.db, self.vault, self.http, local_http)
        self.agents = build_agents()
        self.plugin_dirs = [config.BASE_DIR / "plugins", data_dir / "plugins"]
        (data_dir / "plugins").mkdir(exist_ok=True)
        self.plugins = load_plugins(self.db, self.agents, self.plugin_dirs)
        self.ws = Workspace(Path(self.db.get_setting("workspace_dir")))
        for sub in ("inbox", "notes", "documents", "code"):
            (self.ws.root / sub).mkdir(exist_ok=True)
        self.brain = Brain(self.router, self.agents)
        self.engine = TaskEngine(self.db, self.router, self.brain, self.agents, self.ws)
        self.bot = TelegramBot(self.db, self.vault, self.engine, telegram_api, self.http)
        self._bg: list[asyncio.Task] = []

    # ------------------------------------------------------------ background jobs
    async def start(self) -> None:
        self.db.audit("system", "started", f"v{__version__}" + (" after unclean shutdown" if self.unclean_start else ""),
                      "warning" if self.unclean_start else "info")
        await self.router.probe_ollama()
        n = await self.engine.recover()
        self.bot.start()
        self._bg.append(asyncio.create_task(self._monitor()))
        if self.unclean_start:
            await asyncio.sleep(0)
            asyncio.create_task(self._late_alert(n))

    async def _late_alert(self, recovered: int) -> None:
        await asyncio.sleep(8)  # give the bot time to connect
        await self.bot.broadcast(f"⚠️ أعيد تشغيل ULTRA AI بعد توقف غير طبيعي. استعدت {recovered} مهمة غير مكتملة.")

    async def stop(self) -> None:
        for t in self._bg:
            t.cancel()
        await self.bot.stop()
        for t in list(self.engine._running.values()):
            t.cancel()
        await asyncio.sleep(0.05)
        self.db.audit("system", "stopped")
        self.db.close()
        (self.data_dir / ".clean_shutdown").write_text(str(time.time()))

    async def _monitor(self) -> None:
        last_backup = self.db.get_setting("last_backup_at", 0) or 0
        last_beat = 0.0
        tick = 0
        while True:
            try:
                info = system_info(self.engine.started_at, self.router)
                self.db.execute("INSERT INTO health(ts,cpu,ram,disk) VALUES(?,?,?,?)",
                                (time.time(), info["cpu_percent"], info["ram_percent"], info["disk_percent"]))
                self.db.execute("DELETE FROM health WHERE ts<?", (time.time() - 7 * 86400,))
                if (info["ram_percent"] or 0) > 92 or (info["disk_percent"] or 0) > 95:
                    self.db.audit("monitor", "resources_low", str(info), "warning")
                if tick % 5 == 0:
                    await self.router.probe_ollama()
                hb = self.db.get_setting("heartbeat_url")
                if hb and time.time() - last_beat > 300:
                    with contextlib.suppress(Exception):
                        await self.http.get(hb, timeout=10)
                    last_beat = time.time()
                hours = float(self.db.get_setting("auto_backup_hours", 24) or 0)
                if hours and time.time() - last_backup > hours * 3600:
                    self.backup("auto")
                    last_backup = time.time()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.db.audit("monitor", "error", repr(e)[:300], "warning")
            tick += 1
            await asyncio.sleep(180 if self.db.get_setting("low_resource_mode") else 60)

    def reload_plugins(self) -> None:
        fresh = build_agents()
        self.plugins = load_plugins(self.db, fresh, self.plugin_dirs)
        self.agents.clear()
        self.agents.update(fresh)   # same dict object: brain and engine see the change

    # ------------------------------------------------------------ backups
    def backup(self, label: str = "manual") -> str:
        name = time.strftime("%Y%m%d-%H%M%S") + f"-{label}.zip"
        bdir = self.data_dir / "backups"
        tmp = bdir / "tmp.db"
        self.db.backup_to(tmp)
        with zipfile.ZipFile(bdir / name, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(tmp, "ultra.db")
            for f in self.ws.root.rglob("*"):
                if f.is_file() and ".recycle" not in f.parts and f.stat().st_size < 50 * 1024 * 1024:
                    z.write(f, "workspace/" + self.ws.rel(f))
        tmp.unlink()
        keep = int(self.db.get_setting("backups_keep", 10) or 10)
        for old in sorted(bdir.glob("*.zip"))[:-keep]:
            old.unlink()
        self.db.set_setting("last_backup_at", time.time())
        self.db.audit("system", "backup_created", name)
        return name


# ====================================================================== app
class SetupIn(BaseModel):
    setup_key: str
    username: str
    password: str


class LoginIn(BaseModel):
    username: str
    password: str
    code: str = ""
    device_name: str = "browser"
    kind: str = "browser"


class TextIn(BaseModel):
    text: str


class PwIn(BaseModel):
    old: str
    new: str


class MemIn(BaseModel):
    content: str
    scope: str = "personal"
    project: str = ""
    tags: str = ""


class ProjIn(BaseModel):
    name: str
    description: str = ""
    deadline: str = ""


class ItemIn(BaseModel):
    title: str = ""
    priority: str = "medium"
    due: str = ""
    status: str = ""


class SecretIn(BaseModel):
    name: str
    value: str


def create_app(data_dir: Path | None = None, telegram_api: str = "https://api.telegram.org",
               http=None, start_background: bool = True) -> FastAPI:
    data_dir = Path(data_dir or config.DATA_DIR)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        svc = Services(data_dir, telegram_api, http)
        app.state.svc = svc
        if start_background:
            await svc.start()
        else:
            await svc.engine.recover()
        yield
        await svc.stop()

    app = FastAPI(title="ULTRA AI GOD MODE", version=__version__, lifespan=lifespan)

    def S(request: Request) -> Services:
        return request.app.state.svc

    def auth(request: Request) -> dict:
        svc = S(request)
        token = request.cookies.get(COOKIE)
        h = request.headers.get("authorization", "")
        if h.lower().startswith("bearer "):
            token = h[7:]
        dev = svc.access.check(token)
        if not dev:
            raise HTTPException(401, "login_required")
        return dev

    A = Depends(auth)

    # ---------------------------------------------------------------- public
    @app.get("/api/health")
    async def health(request: Request):
        svc = S(request)
        return {"ok": True, "version": __version__, "setup_needed": not svc.access.has_account(),
                "emergency": svc.engine.emergency}

    @app.get("/api/auth/local-setup-key")
    async def local_setup_key(request: Request):
        """First run only: hand the setup key to a browser on this same computer, so the owner does not
        have to find ADMIN_KEY.txt. Anyone on this PC can read that file anyway; other devices get 404."""
        svc = S(request)
        host = request.client.host if request.client else ""
        if svc.access.has_account() or host not in ("127.0.0.1", "::1"):
            raise HTTPException(404, "not_available")
        return {"setup_key": svc.access.setup_key}

    @app.post("/api/auth/setup")
    async def setup(body: SetupIn, request: Request):
        ok, code = S(request).access.create_account(body.setup_key, body.username, body.password)
        if not ok:
            raise HTTPException(400, code)
        return {"ok": True}

    @app.post("/api/auth/reset")
    async def reset(body: dict, request: Request):
        if not S(request).access.reset_with_setup_key(str(body.get("setup_key", "")), str(body.get("password", ""))):
            raise HTTPException(400, "bad_setup_key")
        return {"ok": True}

    @app.post("/api/auth/login")
    async def login(body: LoginIn, request: Request, response: Response):
        token, code = S(request).access.login(body.username, body.password, body.device_name, body.kind, body.code)
        if not token:
            raise HTTPException(401 if code != "2fa_required" else 403, code)
        response.set_cookie(COOKIE, token, httponly=True, samesite="strict", max_age=3600 * 24 * 365)
        return {"ok": True, "token": token}

    # ---------------------------------------------------------------- account
    @app.post("/api/auth/logout")
    async def logout(request: Request, response: Response, dev=A):
        S(request).access.revoke(dev["id"])
        response.delete_cookie(COOKIE)
        return {"ok": True}

    @app.get("/api/me")
    async def me(request: Request, dev=A):
        u = S(request).db.one("SELECT id, username, totp_secret IS NOT NULL AS twofa FROM users WHERE id=?",
                              (dev["user_id"],))
        return {"user": u, "device": {k: dev[k] for k in ("id", "name", "kind")}}

    @app.post("/api/auth/password")
    async def password(body: PwIn, request: Request, dev=A):
        if not S(request).access.change_password(dev["user_id"], body.old, body.new):
            raise HTTPException(400, "bad_password")
        return {"ok": True}

    @app.post("/api/auth/2fa/start")
    async def twofa_start(request: Request, dev=A):
        secret = S(request).access.start_2fa(dev["user_id"])
        u = S(request).db.one("SELECT username FROM users WHERE id=?", (dev["user_id"],))
        return {"secret": secret, "uri": f"otpauth://totp/ULTRA-AI:{u['username']}?secret={secret}&issuer=ULTRA-AI"}

    @app.post("/api/auth/2fa/confirm")
    async def twofa_confirm(body: TextIn, request: Request, dev=A):
        if not S(request).access.confirm_2fa(dev["user_id"], body.text):
            raise HTTPException(400, "bad_code")
        return {"ok": True}

    @app.post("/api/auth/2fa/disable")
    async def twofa_disable(body: TextIn, request: Request, dev=A):
        if not S(request).access.disable_2fa(dev["user_id"], body.text):
            raise HTTPException(400, "bad_password")
        return {"ok": True}

    # ---------------------------------------------------------------- overview
    @app.get("/api/state")
    async def state(request: Request, dev=A):
        svc = S(request)
        e = svc.engine
        counts = {r["status"]: r["n"] for r in svc.db.all("SELECT status, COUNT(*) n FROM tasks GROUP BY status")}
        return {
            "version": __version__,
            "emergency": e.emergency,
            "system": system_info(e.started_at, svc.router),
            "providers": svc.router.provider_status(),
            "telegram": {"enabled": bool(svc.db.get_setting("telegram_enabled")), "token": bool(svc.bot.token),
                         "connected": svc.bot.connected, "error": svc.bot.last_error,
                         "users": len(svc.bot.owner_chats()), "username": svc.bot.bot_username},
            "counts": counts,
            "active": [_brief(t) for t in e.list("active", 30)],
            "recent": [_brief(t) for t in e.list(None, 15)],
            "month_spend": svc.router.month_spend(),
            "budget": svc.db.get_setting("monthly_budget_usd"),
            "agents": {k: a.status for k, a in svc.agents.items()},
            "server_time": time.time(),
        }

    def _brief(t: dict) -> dict:
        return {k: t[k] for k in ("id", "title", "status", "progress", "source", "created_at", "updated_at",
                                  "approval_reason", "error", "verified")}

    # ---------------------------------------------------------------- tasks
    @app.get("/api/tasks")
    async def tasks(request: Request, status: str | None = None, limit: int = 100, dev=A):
        return [_brief(t) for t in S(request).engine.list(status, min(limit, 500))]

    @app.get("/api/tasks/{tid}")
    async def task(tid: str, request: Request, dev=A):
        e = S(request).engine
        t = e.get(tid)
        if not t:
            raise HTTPException(404, "not_found")
        t["logs"] = e.logs(tid)
        return t

    @app.post("/api/tasks")
    async def new_task(body: TextIn, request: Request, dev=A):
        try:
            tid = await S(request).engine.create(body.text, source=f"{dev['kind']}:{dev['name']}")
        except EmergencyActive:
            raise HTTPException(409, "emergency_active")
        except ValueError:
            raise HTTPException(400, "empty")
        return {"id": tid}

    @app.post("/api/tasks/{tid}/answer")
    async def answer(tid: str, body: TextIn, request: Request, dev=A):
        if not await S(request).engine.answer(tid, body.text):
            raise HTTPException(409, "not_waiting_for_answer")
        return {"ok": True}

    @app.post("/api/tasks/{tid}/{action}")
    async def task_action(tid: str, action: str, request: Request, dev=A):
        e = S(request).engine
        fns = {"approve": lambda: e.approve(tid, f"{dev['kind']}:{dev['name']}"), "deny": lambda: e.deny(tid),
               "pause": lambda: e.pause(tid), "resume": lambda: e.resume(tid), "stop": lambda: e.stop(tid),
               "retry": lambda: e.retry(tid)}
        if action not in fns:
            raise HTTPException(404, "unknown_action")
        if not await fns[action]():
            raise HTTPException(409, "not_allowed_in_current_state")
        return {"ok": True}

    @app.post("/api/upload")
    async def upload(request: Request, file: UploadFile = File(...), text: str = Form(""), dev=A):
        svc = S(request)
        name = safe_upload_name(file.filename or "file")
        if not name:
            raise HTTPException(400, "blocked_file_type")
        data = await file.read()
        if len(data) > 50 * 1024 * 1024:
            raise HTTPException(400, "file_too_large")
        dest = svc.ws.path(f"inbox/uploads/{int(time.time())}_{name}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        rel = svc.ws.rel(dest)
        if not text.strip():
            return {"path": rel}
        try:
            tid = await svc.engine.create(text, source=f"{dev['kind']}:{dev['name']}", attachments=[rel])
        except EmergencyActive:
            raise HTTPException(409, "emergency_active")
        return {"path": rel, "id": tid}

    # ---------------------------------------------------------------- emergency
    @app.post("/api/emergency/stop")
    async def emergency(request: Request, dev=A):
        n = await S(request).engine.emergency_stop(f"{dev['kind']}:{dev['name']}")
        await S(request).bot.broadcast(f"🛑 تم تفعيل إيقاف الطوارئ من {dev['name']} — أوقفت {n} مهمة.")
        return {"stopped": n}

    @app.post("/api/emergency/release")
    async def emergency_release(request: Request, dev=A):
        S(request).engine.emergency_release(f"{dev['kind']}:{dev['name']}")
        return {"ok": True}

    # ---------------------------------------------------------------- files
    @app.get("/api/files")
    async def files(request: Request, folder: str = "", dev=A):
        ws = S(request).ws
        p = ws.path(folder)
        if not p.is_dir():
            raise HTTPException(404, "not_found")
        return [{"path": ws.rel(x), "name": x.name, "dir": x.is_dir(),
                 "size": x.stat().st_size if x.is_file() else None, "mtime": x.stat().st_mtime}
                for x in sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name.lower())) if not x.name.startswith(".")]

    @app.get("/api/files/download")
    async def download(request: Request, path: str, dev=A):
        p = S(request).ws.path(path)
        if not p.is_file():
            raise HTTPException(404, "not_found")
        return FileResponse(p, filename=p.name)

    # ---------------------------------------------------------------- memory
    @app.get("/api/memory")
    async def memory(request: Request, q: str = "", scope: str = "", dev=A):
        sql, params = "SELECT * FROM memory WHERE content LIKE ?", [f"%{q}%"]
        if scope:
            sql += " AND scope=?"
            params.append(scope)
        return S(request).db.all(sql + " ORDER BY updated_at DESC LIMIT 500", params)

    @app.post("/api/memory")
    async def memory_add(body: MemIn, request: Request, dev=A):
        from .security import contains_secret
        if contains_secret(body.content):
            raise HTTPException(400, "refused_secret")
        now = time.time()
        cur = S(request).db.execute("INSERT INTO memory(scope,project,content,tags,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                                    (body.scope if body.scope in ("personal", "project") else "personal",
                                     body.project or None, body.content.strip(), body.tags, now, now))
        return {"id": cur.lastrowid}

    @app.put("/api/memory/{mid}")
    async def memory_edit(mid: int, body: MemIn, request: Request, dev=A):
        from .security import contains_secret
        if contains_secret(body.content):
            raise HTTPException(400, "refused_secret")
        S(request).db.execute("UPDATE memory SET content=?, scope=?, project=?, tags=?, updated_at=? WHERE id=?",
                              (body.content.strip(), body.scope, body.project or None, body.tags, time.time(), mid))
        return {"ok": True}

    @app.delete("/api/memory/{mid}")
    async def memory_delete(mid: int, request: Request, dev=A):
        S(request).db.execute("DELETE FROM memory WHERE id=?", (mid,))
        S(request).db.audit(dev["name"], "memory_deleted", str(mid))
        return {"ok": True}

    # ---------------------------------------------------------------- projects
    @app.get("/api/projects")
    async def projects(request: Request, dev=A):
        db = S(request).db
        ps = db.all("SELECT * FROM projects ORDER BY created_at DESC")
        for p in ps:
            p["items"] = db.all("SELECT * FROM project_items WHERE project_id=? ORDER BY status='done', "
                                "CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, due", (p["id"],))
        return ps

    @app.post("/api/projects")
    async def project_add(body: ProjIn, request: Request, dev=A):
        db = S(request).db
        if not body.name.strip() or db.one("SELECT id FROM projects WHERE name=?", (body.name.strip(),)):
            raise HTTPException(400, "project_exists")
        cur = db.execute("INSERT INTO projects(name,description,deadline,created_at) VALUES(?,?,?,?)",
                         (body.name.strip(), body.description, body.deadline or None, time.time()))
        return {"id": cur.lastrowid}

    @app.delete("/api/projects/{pid}")
    async def project_delete(pid: int, request: Request, dev=A):
        S(request).db.execute("DELETE FROM projects WHERE id=?", (pid,))
        S(request).db.audit(dev["name"], "project_deleted", str(pid), "warning")
        return {"ok": True}

    @app.post("/api/projects/{pid}/items")
    async def item_add(pid: int, body: ItemIn, request: Request, dev=A):
        cur = S(request).db.execute("INSERT INTO project_items(project_id,title,priority,due,created_at) VALUES(?,?,?,?,?)",
                                    (pid, body.title.strip(), body.priority, body.due or None, time.time()))
        return {"id": cur.lastrowid}

    @app.patch("/api/items/{iid}")
    async def item_edit(iid: int, body: ItemIn, request: Request, dev=A):
        db = S(request).db
        for k in ("title", "priority", "due", "status"):
            v = getattr(body, k)
            if v:
                db.execute(f"UPDATE project_items SET {k}=? WHERE id=?", (v, iid))
        return {"ok": True}

    @app.delete("/api/items/{iid}")
    async def item_delete(iid: int, request: Request, dev=A):
        S(request).db.execute("DELETE FROM project_items WHERE id=?", (iid,))
        return {"ok": True}

    # ---------------------------------------------------------------- agents / settings / secrets
    @app.get("/api/agents")
    async def agents(request: Request, dev=A):
        out = []
        for a in S(request).agents.values():
            out.append({"key": a.key, "name_ar": a.name_ar, "name_en": a.name_en, "status": a.status,
                        "note_ar": a.note_ar, "note_en": a.note_en, "phase": a.phase,
                        "actions": [{"name": x.name, "description": x.description, "risk": x.risk}
                                    for x in a.actions.values()]})
        return out

    @app.get("/api/settings")
    async def settings(request: Request, dev=A):
        db = S(request).db
        out = {k: db.get_setting(k) for k in SETTING_KEYS}
        out["ollama_models"] = db.get_setting("ollama_models", [])
        return out

    @app.put("/api/settings")
    async def settings_put(body: dict, request: Request, dev=A):
        svc = S(request)
        changed = []
        for k, v in body.items():
            if k in SETTING_KEYS:
                svc.db.set_setting(k, v)
                changed.append(k)
        svc.db.audit(f"{dev['kind']}:{dev['name']}", "settings_changed", ", ".join(changed))
        if "workspace_dir" in changed:
            svc.ws.__init__(Path(body["workspace_dir"]))
        return {"changed": changed}

    @app.get("/api/secrets")
    async def secrets_status(request: Request, dev=A):
        return S(request).vault.status()

    @app.put("/api/secrets")
    async def secrets_put(body: SecretIn, request: Request, dev=A):
        svc = S(request)
        if body.name not in config.SECRET_NAMES:
            raise HTTPException(400, "unknown_secret")
        svc.vault.set(body.name, body.value.strip())
        svc.db.audit(f"{dev['kind']}:{dev['name']}", "secret_changed", body.name, "warning")
        if body.name == "telegram_bot_token":
            svc.bot.bot_username = ""
        return {"ok": True}

    @app.get("/api/models/ollama")
    async def ollama_state(request: Request, dev=A):
        svc = S(request)
        ok = await svc.router.probe_ollama()
        import psutil
        ram = psutil.virtual_memory().total
        return {"available": ok, "models": svc.db.get_setting("ollama_models", []),
                "url": svc.db.get_setting("ollama_url"), "error": svc.db.get_setting("ollama_error", ""),
                "pulls": list(svc.router.pulls.values()), "ram_gb": round(ram / 1024**3),
                "recommended": svc.router.recommended_ollama(ram)}

    @app.post("/api/models/ollama/pull")
    async def ollama_pull(body: TextIn, request: Request, dev=A):
        svc = S(request)
        model = body.text.strip()
        if not svc.router.OLLAMA_NAME.match(model):
            raise HTTPException(400, "bad_model_name")
        cur = svc.router.pulls.get(model)
        if cur and not cur["done"]:
            return {"ok": True, "already": True}
        svc.db.audit(f"{dev['kind']}:{dev['name']}", "ollama_pull", model)
        svc._bg.append(asyncio.create_task(svc.router.pull_ollama(model)))
        return {"ok": True}

    @app.post("/api/models/test")
    async def model_test(body: TextIn, request: Request, dev=A):
        route = body.text or "chat"
        try:
            res = await S(request).router.complete(route, "Reply with the single word: OK", max_tokens=200)
            return {"ok": True, "provider": res.provider, "model": res.model, "reply": res.text[:200],
                    "cost_usd": res.cost_usd}
        except Exception as e:
            return {"ok": False, "error": str(e)[:500]}

    @app.get("/api/usage")
    async def usage(request: Request, dev=A):
        svc = S(request)
        since = time.time() - 30 * 86400
        return {"month_spend": svc.router.month_spend(), "budget": svc.db.get_setting("monthly_budget_usd"),
                "by_model": svc.db.all("SELECT provider, model, COUNT(*) calls, SUM(input_tokens) input_tokens, "
                                       "SUM(output_tokens) output_tokens, ROUND(SUM(cost_usd),4) cost_usd, "
                                       "SUM(1-ok) errors FROM usage WHERE ts>? GROUP BY provider, model", (since,))}

    # ---------------------------------------------------------------- telegram
    @app.get("/api/telegram")
    async def telegram(request: Request, dev=A):
        svc = S(request)
        return {"users": svc.db.all("SELECT * FROM telegram_users"), "connected": svc.bot.connected,
                "error": svc.bot.last_error, "username": svc.bot.bot_username,
                "enabled": bool(svc.db.get_setting("telegram_enabled")), "token": bool(svc.bot.token)}

    @app.post("/api/telegram/code")
    async def telegram_code(request: Request, dev=A):
        bot = S(request).bot
        code = bot.new_pairing_code()
        name = bot.bot_username
        if not name:
            with contextlib.suppress(Exception):
                name = (await bot.check_token()).get("username", "")
        # One tap on this link opens the bot and sends "/start <code>", which pairs the account.
        return {"code": code, "expires_in": 600, "link": f"https://t.me/{name}?start={code}" if name else ""}

    @app.post("/api/telegram/test")
    async def telegram_test(request: Request, dev=A):
        try:
            me = await S(request).bot.check_token()
            return {"ok": True, "username": me.get("username")}
        except Exception as e:
            return {"ok": False, "error": str(e)[:300]}

    @app.delete("/api/telegram/users/{uid}")
    async def telegram_remove(uid: int, request: Request, dev=A):
        S(request).db.execute("DELETE FROM telegram_users WHERE user_id=?", (uid,))
        S(request).db.audit(dev["name"], "telegram_user_removed", str(uid), "warning")
        return {"ok": True}

    # ---------------------------------------------------------------- security
    @app.get("/api/security/audit")
    async def audit(request: Request, level: str = "", dev=A):
        if level:
            return S(request).db.all("SELECT * FROM audit WHERE level!='info' ORDER BY id DESC LIMIT 300")
        return S(request).db.all("SELECT * FROM audit ORDER BY id DESC LIMIT 300")

    @app.get("/api/devices")
    async def devices(request: Request, dev=A):
        rows = S(request).db.all("SELECT id,name,kind,created_at,last_seen,revoked FROM devices ORDER BY id DESC")
        for r in rows:
            r["current"] = r["id"] == dev["id"]
        return rows

    @app.delete("/api/devices/{did}")
    async def device_revoke(did: int, request: Request, dev=A):
        S(request).access.revoke(did)
        return {"ok": True}

    # ---------------------------------------------------------------- plugins
    @app.get("/api/plugins")
    async def plugins(request: Request, dev=A):
        return S(request).plugins

    @app.post("/api/plugins/{name}/{action}")
    async def plugin_toggle(name: str, action: str, request: Request, dev=A):
        svc = S(request)
        path = next((p for p in discover(svc.plugin_dirs) if p.stem == name), None)
        if not path or action not in ("enable", "disable"):
            raise HTTPException(404, "not_found")
        enabled = svc.db.get_setting("plugins_enabled", {}) or {}
        if action == "enable":
            enabled[name] = file_hash(path)
        else:
            enabled.pop(name, None)
        svc.db.set_setting("plugins_enabled", enabled)
        svc.db.audit(f"{dev['kind']}:{dev['name']}", f"plugin_{action}d", name, "warning")
        svc.reload_plugins()
        return next(p for p in svc.plugins if p["name"] == name)

    # ---------------------------------------------------------------- backups
    @app.get("/api/backups")
    async def backups(request: Request, dev=A):
        bdir = S(request).data_dir / "backups"
        return [{"name": f.name, "size": f.stat().st_size, "mtime": f.stat().st_mtime}
                for f in sorted(bdir.glob("*.zip"), reverse=True)]

    @app.post("/api/backups")
    async def backup_now(request: Request, dev=A):
        return {"name": S(request).backup("manual")}

    @app.post("/api/backups/{name}/restore")
    async def backup_restore(name: str, request: Request, dev=A):
        svc = S(request)
        f = svc.data_dir / "backups" / Path(name).name
        if not f.is_file():
            raise HTTPException(404, "not_found")
        svc.backup("before-restore")
        with zipfile.ZipFile(f) as z:
            with z.open("ultra.db") as src, open(svc.data_dir / "restore_pending.db", "wb") as dst:
                shutil.copyfileobj(src, dst)
        svc.db.audit(dev["name"], "restore_scheduled", name, "warning")
        return {"ok": True, "restart_required": True}

    # ---------------------------------------------------------------- reports & sync
    @app.get("/api/reports")
    async def reports(request: Request, dev=A):
        svc = S(request)
        db = svc.db
        since = time.time() - 30 * 86400
        by_status = {r["status"]: r["n"] for r in db.all("SELECT status, COUNT(*) n FROM tasks WHERE created_at>? "
                                                          "GROUP BY status", (since,))}
        finished = by_status.get("completed", 0) + by_status.get("failed", 0)
        avg = db.one("SELECT AVG(finished_at-started_at) a FROM tasks WHERE status='completed' AND created_at>?", (since,))["a"]
        failing: dict[str, int] = {}
        for t in svc.engine.list("failed", 200):
            for s in t["steps"]:
                if s.get("status") == "failed":
                    key = f"{s['agent']}.{s['action']}"
                    failing[key] = failing.get(key, 0) + 1
        health = db.all("SELECT ts, cpu, ram FROM health WHERE ts>? ORDER BY ts", (time.time() - 86400,))
        suggestions = []
        rate = (by_status.get("completed", 0) / finished) if finished else None
        if rate is not None and rate < 0.8:
            suggestions.append("success_rate_low")
        if not any(svc.router.provider_status().values()):
            suggestions.append("no_model")
        if any(v >= 3 for v in failing.values()):
            suggestions.append("repeated_failures")
        if svc.router.month_spend() > 0.8 * float(db.get_setting("monthly_budget_usd") or 1e9):
            suggestions.append("budget_near")
        return {"by_status": by_status, "success_rate": rate, "avg_seconds": avg, "failing_actions": failing,
                "health": health[-288:], "suggestions": suggestions, "month_spend": svc.router.month_spend()}

    @app.get("/api/sync")
    async def sync(request: Request, since: float = 0, dev=A):
        """Everything that changed since a timestamp, for apps that keep an offline copy."""
        db = S(request).db
        return {"server_time": time.time(),
                "tasks": [_brief(t) for t in S(request).engine.list(None, 500) if t["updated_at"] > since],
                "memory": db.all("SELECT * FROM memory WHERE updated_at>?", (since,)),
                "projects": db.all("SELECT * FROM projects"),
                "settings": {"ui_language": db.get_setting("ui_language"), "theme": db.get_setting("theme")}}

    # ---------------------------------------------------------------- dashboard
    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

    @app.exception_handler(ActionError)
    async def action_error(request: Request, exc: ActionError):
        return JSONResponse({"error": str(exc)}, status_code=400)

    app.mount("/", StaticFiles(directory=str(config.WEB_DIR), html=True), name="web")
    return app
