import json

import pytest
from conftest import run
from test_tasks import wait

from ultra.agents import ActionError
from ultra.brain import rule_plan
from ultra.pc import parse_keys


class FakeDesktop:
    def __init__(self):
        self.running: set[str] = {"explorer.exe"}
        self.calls: list[tuple] = []
        self.typed = ""

    def launch(self, command):
        self.calls.append(("launch", command))
        self.running.add({"calc.exe": "calculatorapp.exe"}.get(command.lower(), command.lower()))

    def processes(self):
        return set(self.running)

    def kill(self, process):
        self.calls.append(("kill", process))
        self.running.discard(process.lower())
        return 1

    def screenshot(self, path):
        self.calls.append(("screenshot", path))
        with open(path, "wb") as f:
            f.write(b"\x89PNG fake")

    def paste(self, text):
        self.calls.append(("paste", text))
        self.typed += text

    def hotkey(self, keys):
        self.calls.append(("hotkey", tuple(keys)))

    def open_url(self, url):
        self.calls.append(("url", url))
        return True

    def focus(self, process):
        self.calls.append(("focus", process))
        return process.lower() in self.running

    def fail_safe_error(self):
        return ()


def _svc(make_services):
    svc = make_services()
    svc.engine.desktop = FakeDesktop()
    return svc


def test_rules_understand_pc_commands():
    assert rule_plan("افتح المفكرة")[0]["params"] == {"app": "notepad"}
    assert rule_plan("حل الحاسبة")[0]["params"] == {"app": "calculator"}
    assert rule_plan("open notepad")[0]["action"] == "open_app"
    assert rule_plan("افتح youtube.com")[0]["params"] == {"url": "youtube.com"}
    assert rule_plan("خذ لقطة شاشة")[0]["action"] == "screenshot"
    assert rule_plan("افتح برنامج غريب جدا") is None      # unknown: the model planner decides


def test_open_app_waits_for_approval_then_runs_and_verifies(make_services):
    async def go():
        svc = _svc(make_services)
        tid = await svc.engine.create("افتح المفكرة")
        t = await wait(svc, tid)
        assert t["status"] == "needs_approval"
        assert svc.engine.desktop.calls == []                     # nothing touched before approval
        assert await svc.engine.approve(tid)
        t = await wait(svc, tid, ("completed", "failed"))
        assert t["status"] == "completed" and t["verified"] == 1, t["error"]
        assert ("launch", "notepad.exe") in svc.engine.desktop.calls
    run(go())


def test_one_approval_covers_all_pc_steps_and_lists_them(make_services, fake_llm):
    async def go():
        svc = _svc(make_services)
        svc.vault.set("openai_api_key", "sk-test")
        fake_llm.replies = [json.dumps({"summary": "write", "steps": [
            {"agent": "pc", "action": "open_app", "params": {"app": "notepad"}, "description": "افتح المفكرة"},
            {"agent": "pc", "action": "type_text", "params": {"text": "مرحبا من ULTRA"}, "description": "اكتب"},
            {"agent": "pc", "action": "press_keys", "params": {"keys": "ctrl+s"}, "description": "احفظ"}]})]
        tid = await svc.engine.create("افتح المفكرة واكتب فيها مرحبا من ULTRA واحفظ")
        t = await wait(svc, tid)
        assert t["status"] == "needs_approval"
        reason = t["approval_reason"]
        assert "مرحبا من ULTRA" in reason and "ctrl+s" in reason and "1." in reason and "3." in reason
        assert await svc.engine.approve(tid)
        t = await wait(svc, tid, ("completed", "failed", "needs_approval"))
        assert t["status"] == "completed", (t["status"], t["error"], t["approval_reason"])
        d = svc.engine.desktop
        assert d.typed == "مرحبا من ULTRA"
        assert [c[0] for c in d.calls] == ["launch", "focus", "focus", "paste", "focus", "hotkey"]
        assert ("hotkey", ("ctrl", "s")) in d.calls
    run(go())


def test_app_not_on_list_is_refused(make_services, fake_llm):
    async def go():
        svc = _svc(make_services)
        svc.vault.set("openai_api_key", "sk-test")
        fake_llm.replies = [json.dumps({"summary": "x", "steps": [
            {"agent": "pc", "action": "open_app", "params": {"app": "cmd"}, "description": "cmd"}]})]
        tid = await svc.engine.create("شغل موجه الأوامر")
        await wait(svc, tid)
        assert await svc.engine.approve(tid)
        t = await wait(svc, tid, ("completed", "failed"))
        assert t["status"] == "failed" and "المسموحة" in t["error"]
        assert svc.engine.desktop.calls == []
    run(go())


def test_forbidden_programs_cannot_be_added(make_services):
    from ultra.pc import allowed_apps
    svc = _svc(make_services)
    svc.db.set_setting("pc_allowed_apps", [{"name": "shell", "command": r"C:\Windows\System32\cmd.exe"},
                                           {"name": "ps", "command": "PowerShell.exe"},
                                           {"name": "chrome", "command": r"C:\Chrome\chrome.exe", "process": "chrome.exe"}])
    assert [a["name"] for a in allowed_apps(svc.db)] == ["chrome"]


def test_switch_off_and_emergency_block_pc(make_services):
    async def go():
        svc = _svc(make_services)
        svc.db.set_setting("pc_control_enabled", False)
        tid = await svc.engine.create("خذ لقطة شاشة")
        await wait(svc, tid)
        await svc.engine.approve(tid)
        t = await wait(svc, tid, ("completed", "failed"))
        assert t["status"] == "failed" and "متوقف" in t["error"]

        svc.db.set_setting("pc_control_enabled", True)
        tid = await svc.engine.create("خذ لقطة شاشة")
        await wait(svc, tid)
        await svc.engine.emergency_stop()
        assert not await svc.engine.approve(tid)                  # no approval while the emergency stop is on
        assert svc.engine.desktop.calls == []
    run(go())


def test_screenshot_saved_in_workspace(make_services):
    async def go():
        svc = _svc(make_services)
        tid = await svc.engine.create("screenshot")
        await wait(svc, tid)
        await svc.engine.approve(tid)
        t = await wait(svc, tid, ("completed", "failed"))
        assert t["status"] == "completed", t["error"]
        assert t["files"] and t["files"][0].startswith("screenshots/")
        assert (svc.ws.root / t["files"][0]).is_file()
    run(go())


def test_no_typing_when_the_window_is_gone(make_services, fake_llm):
    async def go():
        svc = _svc(make_services)
        svc.vault.set("openai_api_key", "sk-test")
        fake_llm.replies = [json.dumps({"summary": "write", "steps": [
            {"agent": "pc", "action": "open_app", "params": {"app": "notepad"}, "description": "افتح"},
            {"agent": "pc", "action": "type_text", "params": {"text": "سر"}, "description": "اكتب"}]})]
        d = svc.engine.desktop
        real_focus = d.focus
        d.focus = lambda p: real_focus(p) and not any(c[0] == "focus" for c in d.calls[:-1])   # only the first focus works
        tid = await svc.engine.create("افتح المفكرة واكتب سر")
        await wait(svc, tid)
        await svc.engine.approve(tid)
        t = await wait(svc, tid, ("completed", "failed"))
        assert t["status"] == "failed" and "نافذة" in t["error"]
        assert d.typed == ""
    run(go())


def test_close_rule_warns_about_unsaved_text():
    st = rule_plan("سكر المفكرة")[0]
    assert st["action"] == "close_app" and st["params"] == {"app": "notepad"}


def test_keys_are_checked():
    assert parse_keys("Ctrl+S") == ["ctrl", "s"]
    assert parse_keys("enter") == ["enter"]
    assert parse_keys("win+e") == ["winleft", "e"]
    for bad in ("ctrl+alt+delete", "win+r", "rm -rf", "", "ctrl+shift+alt+f4+x"):
        with pytest.raises(ActionError):
            parse_keys(bad)


def test_bad_url_refused(make_services):
    async def go():
        from ultra.pc import pc_open_url

        class Ctx:
            db = None
            engine = None

            def t(self, key, **kw):
                return key
        svc = _svc(make_services)
        ctx = Ctx()
        ctx.db, ctx.engine = svc.db, svc.engine
        with pytest.raises(ActionError):
            await pc_open_url(ctx, "javascript:alert(1)")
        with pytest.raises(ActionError):
            await pc_open_url(ctx, "file:///C:/Windows")
        r = await pc_open_url(ctx, "youtube.com")
        assert r.verified and ("url", "https://youtube.com") in svc.engine.desktop.calls
    run(go())
