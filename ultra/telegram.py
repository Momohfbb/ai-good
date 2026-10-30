"""Private Telegram control bot (Telegram Bot API, long polling).

Security model
- Only private chats are accepted; groups and channels are ignored.
- A Telegram account must be paired once with a one-time code created in the
  dashboard (valid 10 minutes). Unpaired users can only see how to pair.
- 5 wrong codes per hour per Telegram account, then it is blocked for the hour.
- Every command is written to the audit log.
The bot only sees messages sent to it; it cannot read your other chats.
"""
from __future__ import annotations

import asyncio
import secrets
import time

import httpx

from .agents import system_info
from .db import Database
from .language import detect
from .messages import t
from .security import Vault, detect_injection, safe_upload_name
from .tasks import EmergencyActive, TaskEngine

STATUS_AR = {"pending": "قيد الانتظار", "running": "قيد التنفيذ", "paused": "متوقفة مؤقتاً",
             "needs_approval": "تحتاج موافقة", "completed": "مكتملة", "failed": "فاشلة", "stopped": "أوقفت"}
ICON = {"pending": "⏳", "running": "⚙️", "paused": "⏸️", "needs_approval": "⚠️", "completed": "✅",
        "failed": "❌", "stopped": "⏹️"}

HELP_AR = """🤖 ULTRA AI GOD MODE — مركز التحكم

اكتب أي طلب بشكل عادي (عربية، دارجة، فرنسية، إنجليزية) وسأنفذه كمهمة.
يمكنك أيضاً إرسال صورة أو ملف (مع وصف) أو رسالة صوتية.

الأوامر:
/status — حالة النظام
/tasks — المهام الجارية
/history — آخر المهام
/task رقم — تفاصيل مهمة
/approve رقم — موافقة   /deny رقم — رفض
/pause رقم — إيقاف مؤقت   /resume رقم — استئناف
/stop رقم — إيقاف   /retry رقم — إعادة المحاولة
/emergency — 🛑 إيقاف الطوارئ لكل المهام
/resume_system — إلغاء وضع الطوارئ
/unpair — فك ربط هذا الحساب"""

STEP_ICON = {"done": "✔️", "failed": "❌", "running": "⚙️", "pending": "•"}

MAX_FILE = 20 * 1024 * 1024  # Bot API download limit


class TelegramBot:
    def __init__(self, db: Database, vault: Vault, engine: TaskEngine, api_base: str = "https://api.telegram.org",
                 http: httpx.AsyncClient | None = None):
        self.db = db
        self.vault = vault
        self.engine = engine
        self.api_base = api_base.rstrip("/")
        self.http = http or httpx.AsyncClient(timeout=httpx.Timeout(60, connect=10))
        self.offset = int(db.get_setting("telegram_offset", 0) or 0)
        self._task: asyncio.Task | None = None
        self.connected = False
        self.last_error = ""
        self.bot_username = ""
        engine.listeners.append(self.on_task_event)
        engine.owner_notifier = self.broadcast

    # ------------------------------------------------------------ api
    @property
    def token(self) -> str:
        return self.vault.get("telegram_bot_token")

    def _url(self, method: str, file: bool = False) -> str:
        return f"{self.api_base}/{'file/' if file else ''}bot{self.token}/{method}"

    async def call(self, method: str, **params) -> dict:
        files = params.pop("_files", None)
        if files:
            r = await self.http.post(self._url(method), data=params, files=files)
        else:
            r = await self.http.post(self._url(method), json=params)
        j = r.json()
        if not j.get("ok"):
            raise RuntimeError(f"telegram {method}: {j.get('description')}")
        return j["result"]

    async def send(self, chat_id: int, text: str, buttons: list[list[tuple[str, str]]] | None = None) -> None:
        chunks = [text[i:i + 3900] for i in range(0, max(len(text), 1), 3900)] or [""]
        for n, chunk in enumerate(chunks):
            params = {"chat_id": chat_id, "text": chunk or "…", "disable_web_page_preview": True}
            if buttons and n == len(chunks) - 1:
                params["reply_markup"] = {"inline_keyboard": [[{"text": a, "callback_data": b} for a, b in row]
                                                              for row in buttons]}
            await self.call("sendMessage", **params)

    async def send_file(self, chat_id: int, path, caption: str = "") -> None:
        with open(path, "rb") as f:
            await self.call("sendDocument", chat_id=str(chat_id), caption=caption[:1000],
                            _files={"document": (path.name, f.read())})

    # ------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self._task = None
        self.connected = False

    async def check_token(self) -> dict:
        me = await self.call("getMe")
        self.bot_username = me.get("username", "")
        return me

    async def _loop(self) -> None:
        backoff = 2
        while True:
            if not self.token or not self.db.get_setting("telegram_enabled"):
                self.connected = False
                await asyncio.sleep(5)
                continue
            try:
                if not self.bot_username:
                    await self.check_token()
                updates = await self.call("getUpdates", offset=self.offset, timeout=25,
                                          allowed_updates=["message", "callback_query"])
                self.connected, self.last_error, backoff = True, "", 2
                for u in updates:
                    self.offset = u["update_id"] + 1
                    self.db.set_setting("telegram_offset", self.offset)
                    try:
                        await self.handle_update(u)
                    except Exception as e:
                        self.db.audit("telegram", "handler_error", repr(e)[:500], "warning")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.connected, self.last_error = False, str(e)[:300]
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    # ------------------------------------------------------------ auth
    def is_paired(self, user_id: int) -> bool:
        return bool(self.db.one("SELECT user_id FROM telegram_users WHERE user_id=?", (user_id,)))

    def new_pairing_code(self) -> str:
        code = f"{secrets.randbelow(10**8):08d}"
        self.db.execute("INSERT INTO pairing_codes(code,expires_at) VALUES(?,?)", (code, time.time() + 600))
        self.db.audit("dashboard", "telegram_pairing_code_created")
        return code

    def try_pair(self, user: dict, chat_id: int, code: str) -> str:
        uid = user["id"]
        now = time.time()
        attempts = self.db.one("SELECT COUNT(*) n FROM pair_attempts WHERE user_id=? AND ts>?", (uid, now - 3600))["n"]
        if attempts >= 5:
            self.db.audit("telegram", "pair_blocked", str(uid), "warning")
            return "blocked"
        row = self.db.one("SELECT * FROM pairing_codes WHERE code=? AND used=0 AND expires_at>?", (code.strip(), now))
        if not row:
            self.db.execute("INSERT INTO pair_attempts(user_id,ts) VALUES(?,?)", (uid, now))
            self.db.audit("telegram", "pair_failed", f"{uid} @{user.get('username', '')}", "warning")
            return "bad"
        self.db.execute("UPDATE pairing_codes SET used=1 WHERE code=?", (row["code"],))
        self.db.execute("INSERT OR REPLACE INTO telegram_users(user_id,chat_id,username,name,paired_at) VALUES(?,?,?,?,?)",
                        (uid, chat_id, user.get("username"), user.get("first_name"), now))
        self.db.audit("telegram", "paired", f"{uid} @{user.get('username', '')}", "warning")
        return "ok"

    # ------------------------------------------------------------ handling
    async def handle_update(self, u: dict) -> None:
        if "callback_query" in u:
            return await self.handle_callback(u["callback_query"])
        msg = u.get("message")
        if not msg or msg.get("chat", {}).get("type") != "private":
            return
        user, chat_id = msg.get("from", {}), msg["chat"]["id"]
        text = (msg.get("text") or msg.get("caption") or "").strip()
        cmd, _, arg = text.partition(" ")
        cmd = cmd.split("@")[0].lower()

        if not self.is_paired(user.get("id")):
            if cmd == "/pair" and arg:
                res = self.try_pair(user, chat_id, arg)
                reply = {"ok": "✅ تم ربط حسابك بنجاح. أرسل /help لرؤية الأوامر.",
                         "bad": "❌ الرمز غير صحيح أو منتهي الصلاحية.",
                         "blocked": "⛔ محاولات كثيرة. حاول بعد ساعة."}[res]
                return await self.send(chat_id, reply)
            self.db.audit("telegram", "unauthorized", f"{user.get('id')} @{user.get('username', '')}: {text[:100]}", "warning")
            return await self.send(chat_id, "🔒 هذا بوت خاص. لربط حسابك: افتح لوحة التحكم ← Telegram ← «إنشاء رمز ربط»، "
                                            "ثم أرسل هنا:\n/pair الرمز")

        self.db.audit(f"telegram:{user.get('id')}", "command", text[:200] or ("file" if not text else ""))
        if cmd.startswith("/"):
            return await self.handle_command(chat_id, user, cmd, arg.strip())

        # pending clarification from this chat? then this message is the answer
        pending = self.db.one("SELECT id, plan FROM tasks WHERE chat_id=? AND status='needs_approval' "
                              "AND plan LIKE '%\"clarification\": \"_%' ORDER BY updated_at DESC LIMIT 1", (chat_id,))
        attachments = []
        if msg.get("photo") or msg.get("document"):
            saved = await self.download_attachment(msg)
            if isinstance(saved, str) and saved.startswith("!"):
                return await self.send(chat_id, saved[1:])
            attachments.append(saved)
            if not text:
                text = "حلل هذه الصورة" if msg.get("photo") else "لخص هذا الملف"
        elif msg.get("voice") or msg.get("audio"):
            text = await self.transcribe(msg, chat_id)
            if not text:
                return
        if not text:
            return
        if pending and not attachments:
            await self.engine.answer(pending["id"], text)
            return await self.send(chat_id, f"👍 تم استلام التوضيح للمهمة {pending['id']}")
        try:
            tid = await self.engine.create(text, source="telegram", chat_id=chat_id, attachments=attachments)
        except EmergencyActive:
            return await self.send(chat_id, t("emergency_on", "ar"))
        await self.send(chat_id, f"⏳ بدأت المهمة {tid}", [[("⏹️ إيقاف", f"stop:{tid}")]])

    async def download_attachment(self, msg: dict) -> str:
        if msg.get("photo"):
            info = msg["photo"][-1]
            name = f"photo_{info['file_unique_id']}.jpg"
        else:
            info = msg["document"]
            name = safe_upload_name(info.get("file_name") or "file")
            if not name:
                self.db.audit("telegram", "blocked_file", info.get("file_name", ""), "warning")
                return "!⛔ هذا النوع من الملفات (برامج وسكربتات) مرفوض لأسباب أمان."
        if info.get("file_size", 0) > MAX_FILE:
            return "!الملف أكبر من 20 ميغابايت (حد Telegram للبوتات)."
        f = await self.call("getFile", file_id=info["file_id"])
        r = await self.http.get(self._url(f["file_path"], file=True))
        dest = self.engine.ws.path(f"inbox/telegram/{int(time.time())}_{name}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(r.content)
        if dest.suffix.lower() in (".txt", ".md", ".csv", ".html", ".json"):
            flags = detect_injection(r.content.decode("utf-8", "replace"))
            if flags:
                self.db.audit("telegram", "injection_in_file", f"{name}: {flags}", "warning")
        return self.engine.ws.rel(dest)

    async def transcribe(self, msg: dict, chat_id: int) -> str:
        info = msg.get("voice") or msg.get("audio")
        try:
            f = await self.call("getFile", file_id=info["file_id"])
            r = await self.http.get(self._url(f["file_path"], file=True))
            text = await self.engine.router.transcribe(r.content, "voice.ogg")
        except Exception as e:
            await self.send(chat_id, "🎤 لم أستطع تحويل الرسالة الصوتية إلى نص. "
                                     "هذه الميزة تحتاج مفتاح OpenAI في الإعدادات." if "no_stt" in str(e)
                            else f"🎤 خطأ في تحويل الصوت: {str(e)[:200]}")
            return ""
        await self.send(chat_id, f"🎤 فهمت: {text}")
        return text

    def _task_line(self, task: dict) -> str:
        return f"{ICON[task['status']]} {task['id']} — {task['title'][:60]} ({STATUS_AR[task['status']]}, {task['progress']}%)"

    async def handle_command(self, chat_id: int, user: dict, cmd: str, arg: str) -> None:
        e = self.engine
        if cmd in ("/start", "/help"):
            return await self.send(chat_id, HELP_AR)
        if cmd == "/status":
            info = system_info(e.started_at, e.router)
            active = len(e.list("active"))
            lines = ["🟢 النظام يعمل" if not e.emergency else "🛑 وضع الطوارئ مفعل",
                     f"المهام النشطة: {active}", f"المعالج: {info['cpu_percent']}%  الذاكرة: {info['ram_percent']}%",
                     f"مدة التشغيل: {info['uptime']}", f"النماذج المتاحة: {info['models']}",
                     f"تكلفة هذا الشهر: ${e.router.month_spend():.2f}"]
            return await self.send(chat_id, "\n".join(lines))
        if cmd == "/tasks":
            tasks = e.list("active", 20)
            return await self.send(chat_id, "\n".join(map(self._task_line, tasks)) or "لا توجد مهام جارية.")
        if cmd == "/history":
            return await self.send(chat_id, "\n".join(map(self._task_line, e.list(None, 15))) or "لا توجد مهام.")
        if cmd == "/emergency":
            return await self.send(chat_id, "🛑 هل تريد إيقاف جميع المهام فوراً؟", [[("نعم، أوقف كل شيء", "emerg:yes"),
                                                                                  ("إلغاء", "emerg:no")]])
        if cmd == "/resume_system":
            e.emergency_release(f"telegram:{user['id']}")
            return await self.send(chat_id, "✅ تم إلغاء وضع الطوارئ. يمكن بدء مهام جديدة.")
        if cmd == "/unpair":
            self.db.execute("DELETE FROM telegram_users WHERE user_id=?", (user["id"],))
            self.db.audit("telegram", "unpaired", str(user["id"]), "warning")
            return await self.send(chat_id, "تم فك الربط.")
        actions = {"/task": None, "/approve": e.approve, "/deny": e.deny, "/pause": e.pause,
                   "/resume": e.resume, "/stop": e.stop, "/retry": e.retry}
        if cmd in actions:
            if not arg:
                return await self.send(chat_id, f"اكتب رقم المهمة بعد الأمر، مثال: {cmd} a1b2c3d4e5")
            if cmd == "/task":
                task = e.get(arg)
                return await self.send(chat_id, self.describe(task) if task else "مهمة غير موجودة.")
            ok = await actions[cmd](arg) if cmd != "/approve" else await e.approve(arg, f"telegram:{user['id']}")
            return await self.send(chat_id, "👍 تم." if ok else "لا يمكن تنفيذ هذا على المهمة في حالتها الحالية.")
        await self.send(chat_id, "أمر غير معروف. أرسل /help")

    def describe(self, task: dict) -> str:
        lines = [self._task_line(task), "", task["description"][:500], ""]
        for n, s in enumerate(task["steps"], 1):
            lines.append(f"{n}. {STEP_ICON.get(s['status'], '•')} {s['description']}")
        if task["approval_reason"]:
            lines.append("\n⚠️ " + task["approval_reason"])
        if task["error"]:
            lines.append("\n❌ " + task["error"])
        if task["result"]:
            lines.append("\n" + task["result"][:2500])
        return "\n".join(lines)

    async def handle_callback(self, cq: dict) -> None:
        user = cq.get("from", {})
        chat_id = cq.get("message", {}).get("chat", {}).get("id")
        try:
            await self.call("answerCallbackQuery", callback_query_id=cq["id"])
        except Exception:
            pass
        if not self.is_paired(user.get("id")) or not chat_id:
            return
        kind, _, arg = (cq.get("data") or "").partition(":")
        actor = f"telegram:{user['id']}"
        self.db.audit(actor, "button", cq.get("data", ""))
        if kind == "emerg":
            if arg == "yes":
                n = await self.engine.emergency_stop(actor)
                return await self.send(chat_id, t("emergency_on", "ar") + f"\n(أوقفت {n} مهمة) — للإلغاء: /resume_system")
            return await self.send(chat_id, "تم الإلغاء.")
        fn = {"approve": lambda i: self.engine.approve(i, actor), "deny": self.engine.deny,
              "stop": self.engine.stop, "retry": self.engine.retry}.get(kind)
        if fn:
            ok = await fn(arg)
            await self.send(chat_id, "👍 تم." if ok else "لم يعد هذا الإجراء ممكناً لهذه المهمة.")

    # ------------------------------------------------------------ notifications
    def owner_chats(self) -> list[int]:
        return [r["chat_id"] for r in self.db.all("SELECT chat_id FROM telegram_users")]

    async def broadcast(self, text: str) -> int:
        if not self.token:
            return 0
        n = 0
        for chat in self.owner_chats():
            try:
                await self.send(chat, text)
                n += 1
            except Exception:
                pass
        return n

    async def on_task_event(self, task: dict, event: str) -> None:
        if event not in ("completed", "failed", "needs_approval") or not self.token:
            return
        if task["source"] == "telegram" and task["chat_id"]:
            chats = [task["chat_id"]]
        elif self.db.get_setting("telegram_notify", True):
            chats = self.owner_chats()
        else:
            return
        lang = "ar"
        if event == "completed":
            text, buttons = t("task_done", lang, title=task["title"]) + "\n\n" + (task["result"] or ""), None
        elif event == "failed":
            text = t("task_failed", lang, title=task["title"], error=task["error"] or "")
            buttons = [[("🔁 إعادة المحاولة", f"retry:{task['id']}")]]
        else:
            plan = task["plan"] or {}
            text = t("needs_approval", lang, title=task["title"], reason=task["approval_reason"] or "")
            buttons = None if plan.get("clarification") else [[("✅ موافقة", f"approve:{task['id']}"),
                                                              ("❌ رفض", f"deny:{task['id']}")]]
            if plan.get("clarification"):
                text += "\n\n✍️ أرسل جوابك كرسالة عادية."
        for chat in chats:
            try:
                await self.send(chat, f"[{task['id']}] {text}", buttons)
                if event == "completed":
                    for rel in task["files"][:5]:
                        p = self.engine.ws.path(rel)
                        if p.is_file() and p.stat().st_size < 45 * 1024 * 1024:
                            await self.send_file(chat, p, rel)
            except Exception as e:
                self.db.audit("telegram", "notify_failed", str(e)[:200], "warning")
