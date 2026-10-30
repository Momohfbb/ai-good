import asyncio
import json

import httpx
from conftest import run


class FakeTelegram:
    def __init__(self):
        self.sent = []
        self.docs = []

    def handler(self, request: httpx.Request):
        path = request.url.path
        if "/file/bot" in path:
            return httpx.Response(200, content=b"hello file content")
        method = path.rsplit("/", 1)[-1]
        body = json.loads(request.content) if request.headers.get("content-type", "").startswith("application/json") else {}
        if method == "sendMessage":
            self.sent.append(body)
            return httpx.Response(200, json={"ok": True, "result": {"message_id": len(self.sent)}})
        if method == "sendDocument":
            self.docs.append(path)
            return httpx.Response(200, json={"ok": True, "result": {}})
        if method == "getMe":
            return httpx.Response(200, json={"ok": True, "result": {"username": "ultra_test_bot"}})
        if method == "getFile":
            return httpx.Response(200, json={"ok": True, "result": {"file_path": "documents/f.txt"}})
        if method == "answerCallbackQuery":
            return httpx.Response(200, json={"ok": True, "result": True})
        return httpx.Response(200, json={"ok": True, "result": []})

    def texts(self):
        return [m["text"] for m in self.sent]


def msg(text, uid=42, chat_type="private", **extra):
    return {"update_id": 1, "message": {"message_id": 1, "from": {"id": uid, "username": "momo", "first_name": "Momo"},
                                        "chat": {"id": uid, "type": chat_type}, "text": text, **extra}}


def setup(make_services):
    tg = FakeTelegram()
    svc = make_services(handler=tg.handler, telegram_api="https://tg.test")
    svc.vault.set("telegram_bot_token", "123456789:AAtesttokentesttokentesttokentest00")
    return svc, tg


async def settle(svc):
    for _ in range(200):
        await asyncio.sleep(0.02)
        if not svc.engine._running:
            await asyncio.sleep(0.05)
            return


def test_unpaired_user_is_refused_and_logged(make_services):
    async def go():
        svc, tg = setup(make_services)
        await svc.bot.handle_update(msg("احذف كل شيء"))
        assert "بوت خاص" in tg.texts()[-1]
        assert not svc.db.all("SELECT * FROM tasks")
        assert svc.db.one("SELECT * FROM audit WHERE action='unauthorized'")
    run(go())


def test_group_messages_are_ignored(make_services):
    async def go():
        svc, tg = setup(make_services)
        await svc.bot.handle_update(msg("/help", chat_type="group"))
        assert tg.sent == []
    run(go())


def test_pairing_flow_and_bruteforce_block(make_services):
    async def go():
        svc, tg = setup(make_services)
        for _ in range(5):
            await svc.bot.handle_update(msg("/pair 00000000", uid=7))
        code = svc.bot.new_pairing_code()
        await svc.bot.handle_update(msg(f"/pair {code}", uid=7))
        assert "محاولات كثيرة" in tg.texts()[-1] and not svc.bot.is_paired(7)
        await svc.bot.handle_update(msg(f"/pair {code}", uid=42))
        assert svc.bot.is_paired(42) and "تم ربط" in tg.texts()[-1]
        await svc.bot.handle_update(msg(f"/pair {code}", uid=43))      # code is single-use
        assert not svc.bot.is_paired(43)
    run(go())


def test_pairing_code_accepted_in_rtl_order_or_alone(make_services):
    async def go():
        svc, tg = setup(make_services)
        await svc.bot.handle_update(msg(f"{svc.bot.new_pairing_code()}/pair", uid=50))   # typed right-to-left
        assert svc.bot.is_paired(50)
        await svc.bot.handle_update(msg(svc.bot.new_pairing_code(), uid=51))             # just the number
        assert svc.bot.is_paired(51)
        await svc.bot.handle_update(msg(f"/start {svc.bot.new_pairing_code()}", uid=53))  # dashboard t.me link
        assert svc.bot.is_paired(53)
        await svc.bot.handle_update(msg("my number is 12345678 call me", uid=52))      # not a pairing attempt
        assert not svc.bot.is_paired(52) and "هذا بوت خاص" in tg.texts()[-1]
    run(go())


def test_command_creates_task_and_reports_result(make_services):
    async def go():
        svc, tg = setup(make_services)
        await svc.bot.handle_update(msg(f"/pair {svc.bot.new_pairing_code()}"))
        await svc.bot.handle_update(msg("أنشئ مشروع تطبيق التوصيل"))
        await settle(svc)
        texts = tg.texts()
        assert any("بدأت المهمة" in t for t in texts)
        assert any("اكتملت المهمة" in t and "تطبيق التوصيل" in t for t in texts)
        await svc.bot.handle_update(msg("/history"))
        assert "مكتملة" in tg.texts()[-1]
        await svc.bot.handle_update(msg("/status"))
        assert "النظام يعمل" in tg.texts()[-1]
    run(go())


def test_approval_buttons_and_emergency(make_services):
    async def go():
        svc, tg = setup(make_services)
        await svc.bot.handle_update(msg(f"/pair {svc.bot.new_pairing_code()}"))
        await svc.bot.handle_update(msg("رتب الملفات"))
        await settle(svc)
        approval = next(m for m in tg.sent if "تحتاج موافقتك" in m["text"])
        buttons = approval["reply_markup"]["inline_keyboard"][0]
        approve_data = buttons[0]["callback_data"]
        await svc.bot.handle_update({"update_id": 2, "callback_query": {
            "id": "cb1", "from": {"id": 42}, "data": approve_data, "message": {"chat": {"id": 42}}}})
        await settle(svc)
        tid = approve_data.split(":")[1]
        assert svc.engine.get(tid)["status"] == "completed"
        # emergency via button
        await svc.bot.handle_update({"update_id": 3, "callback_query": {
            "id": "cb2", "from": {"id": 42}, "data": "emerg:yes", "message": {"chat": {"id": 42}}}})
        assert svc.engine.emergency and "الطوارئ" in tg.texts()[-1]
        await svc.bot.handle_update(msg("المشاريع"))
        assert "الطوارئ" in tg.texts()[-1]
        await svc.bot.handle_update(msg("/resume_system"))
        assert not svc.engine.emergency
        # a callback from an unpaired user does nothing
        await svc.bot.handle_update({"update_id": 4, "callback_query": {
            "id": "cb3", "from": {"id": 999}, "data": "emerg:yes", "message": {"chat": {"id": 999}}}})
        assert not svc.engine.emergency
    run(go())


def test_received_file_is_saved_and_executables_refused(make_services):
    async def go():
        svc, tg = setup(make_services)
        await svc.bot.handle_update(msg(f"/pair {svc.bot.new_pairing_code()}"))
        await svc.bot.handle_update(msg("", document={"file_id": "F1", "file_name": "setup.exe", "file_size": 10}))
        assert "مرفوض" in tg.texts()[-1]
        await svc.bot.handle_update(msg("", caption="", document={"file_id": "F2", "file_name": "notes.txt", "file_size": 10}))
        await settle(svc)
        t = svc.engine.list(None, 1)[0]
        assert t["attachments"][0].startswith("inbox/telegram/") and t["attachments"][0].endswith("notes.txt")
        assert (svc.ws.root / t["attachments"][0]).read_bytes() == b"hello file content"
        # no model configured → honest failure message, not a fake summary
        assert t["status"] == "failed"
    run(go())


def test_voice_without_key_explains(make_services):
    async def go():
        svc, tg = setup(make_services)
        await svc.bot.handle_update(msg(f"/pair {svc.bot.new_pairing_code()}"))
        await svc.bot.handle_update(msg("", voice={"file_id": "V1", "duration": 2}))
        assert "مفتاح OpenAI" in tg.texts()[-1]
    run(go())
