"""PC agent (phase 4): open allowed programs, take screenshots, type and press keys on Windows.

Safety:
- every action that touches the screen, keyboard or programs needs the owner's approval
  (one approval covers all PC steps of the same task, listed in full before it is given);
- only programs on the allowed list can be opened or closed, never a shell;
- the emergency stop is checked before each keystroke batch, and moving the mouse into a
  screen corner aborts typing at once (pyautogui fail-safe);
- the whole agent can be switched off in Settings (pc_control_enabled).

The real desktop calls live in `Desktop`; tests swap in a fake with the same methods.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import ntpath
import os
import re
import subprocess
import sys
import time
import webbrowser
from urllib.parse import urlparse

from .agents import ActionError, StepResult
from .config import DEFAULT_SETTINGS

DEFAULT_APPS = DEFAULT_SETTINGS["pc_allowed_apps"]

# Programs that can run anything are never allowed, even if added by hand.
FORBIDDEN = {"cmd.exe", "powershell.exe", "pwsh.exe", "wscript.exe", "cscript.exe", "mshta.exe",
             "regedit.exe", "reg.exe", "rundll32.exe", "regsvr32.exe", "bash.exe", "wsl.exe", "taskkill.exe",
             "format.com", "diskpart.exe", "bcdedit.exe", "schtasks.exe", "msiexec.exe"}

KEY_NAMES = {"ctrl", "alt", "shift", "win", "enter", "tab", "esc", "space", "backspace", "delete", "up", "down",
             "left", "right", "home", "end", "pageup", "pagedown"} | {f"f{i}" for i in range(1, 13)}
BLOCKED_KEYS = {frozenset({"ctrl", "alt", "delete"}), frozenset({"win", "r"}), frozenset({"win", "x"}),
                frozenset({"win", "l"})}


class Desktop:
    """Real Windows desktop through pyautogui. Imported lazily so the hub also runs headless."""

    def __init__(self):
        self._gui = None

    def check(self) -> None:
        if sys.platform != "win32":
            raise ActionError("pc_windows_only")
        if self._gui is None:
            try:
                import pyautogui
            except Exception:
                raise ActionError("pc_support_missing")
            pyautogui.FAILSAFE = True      # mouse in a screen corner raises FailSafeException
            pyautogui.PAUSE = 0.05
            self._gui = pyautogui

    def launch(self, command: str) -> None:
        self.check()
        if os.path.isabs(command) and not os.path.isfile(command):
            raise ActionError("pc_program_not_found")
        try:
            subprocess.Popen([command], close_fds=True)
        except FileNotFoundError:
            raise ActionError("pc_program_not_found")

    def processes(self) -> set[str]:
        import psutil
        out = set()
        for p in psutil.process_iter(["name"]):
            if p.info.get("name"):
                out.add(p.info["name"].lower())
        return out

    def kill(self, process: str) -> int:
        import psutil
        n = 0
        for p in psutil.process_iter(["name"]):
            if (p.info.get("name") or "").lower() == process.lower():
                try:
                    p.terminate()
                    n += 1
                except psutil.Error:
                    pass
        return n

    def focus(self, process: str) -> bool:
        """Bring the first visible window of `process` to the front, so typing goes into it."""
        self.check()
        import ctypes
        from ctypes import wintypes

        import psutil
        user32 = ctypes.windll.user32
        pids = {p.pid for p in psutil.process_iter(["name"]) if (p.info.get("name") or "").lower() == process.lower()}
        found: list = []
        proc_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def each(hwnd, _):
            if user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd) > 0:
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value in pids:
                    found.append(hwnd)
                    return False
            return True

        user32.EnumWindows(proc_type(each), 0)
        if not found:
            return False
        hwnd = found[0]
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)               # SW_RESTORE
        # Holding Alt lets a background program move the focus (Windows' foreground lock).
        user32.keybd_event(0x12, 0, 0, 0)
        user32.SetForegroundWindow(hwnd)
        user32.keybd_event(0x12, 0, 2, 0)
        time.sleep(0.3)
        return user32.GetForegroundWindow() == hwnd

    def screenshot(self, path: str) -> None:
        self.check()
        self._gui.screenshot().save(path)

    def paste(self, text: str) -> None:
        """Type any text (Arabic included) by pasting it, then put the old clipboard back."""
        self.check()
        import pyperclip
        try:
            old = pyperclip.paste()
        except Exception:
            old = None
        pyperclip.copy(text)
        self._gui.hotkey("ctrl", "v")
        time.sleep(0.3)
        if old is not None:
            pyperclip.copy(old)

    def hotkey(self, keys: list[str]) -> None:
        self.check()
        self._gui.hotkey(*keys)

    def open_url(self, url: str) -> bool:
        return webbrowser.open(url)

    def fail_safe_error(self) -> type:
        return getattr(self._gui, "FailSafeException", ()) if self._gui else ()


# ====================================================================== helpers
def allowed_apps(db) -> list[dict]:
    apps = db.get_setting("pc_allowed_apps") or DEFAULT_APPS
    return [a for a in apps if isinstance(a, dict) and a.get("name") and a.get("command")
            and ntpath.basename(str(a["command"])).lower() not in FORBIDDEN]


def find_app(db, name: str) -> dict:
    key = (name or "").strip().lower()
    for a in allowed_apps(db):
        if key in (a["name"].lower(), str(a.get("label", "")).lower()) or \
                any(key == part.strip().lower() for part in str(a.get("label", "")).split("/")):
            return a
    raise ActionError("pc_app_not_allowed")


def _guard(ctx) -> Desktop:
    if not ctx.db.get_setting("pc_control_enabled", True):
        raise ActionError("pc_disabled")
    if ctx.engine.emergency:
        raise ActionError("pc_emergency")
    return ctx.engine.desktop


async def _wait_for(desktop, process: str, seconds: float = 8) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        if process.lower() in desktop.processes():
            return True
        await asyncio.sleep(0.4)
    return False


# ====================================================================== actions
async def pc_list_apps(ctx) -> StepResult:
    try:
        running = ctx.engine.desktop.processes()
    except Exception:
        running = set()
    lines = []
    for a in allowed_apps(ctx.db):
        on = str(a.get("process") or "").lower() in running
        lines.append(f"{'🟢' if on else '⚪'} {a['name']} — {a.get('label', '')}")
    return StepResult("\n".join(lines) or ctx.t("pc_no_apps"), data=allowed_apps(ctx.db))


async def pc_open_app(ctx, app: str) -> StepResult:
    d = _guard(ctx)
    a = find_app(ctx.db, app)
    d.launch(a["command"])
    proc = a.get("process") or ntpath.basename(a["command"])
    ok = await _wait_for(d, proc)
    if not ok:
        raise ActionError("pc_app_did_not_start")
    focused = False
    for _ in range(10):             # the window appears a moment after the process
        focused = d.focus(proc)
        if focused:
            break
        await asyncio.sleep(0.5)
    ctx.log("info", f"pc: opened {a['name']} ({proc}, window in front={focused})")
    ctx.pc_window = proc            # later typing in this task goes into this program only
    return StepResult(ctx.t("pc_opened", app=a.get("label") or a["name"]), verified=True)


def _focus_target(ctx, d) -> None:
    proc = getattr(ctx, "pc_window", None)
    if proc and not d.focus(proc):
        raise ActionError("pc_window_lost")      # never type into some other window by mistake


async def pc_close_app(ctx, app: str) -> StepResult:
    d = _guard(ctx)
    a = find_app(ctx.db, app)
    proc = a.get("process") or ntpath.basename(a["command"])
    if proc.lower() == "explorer.exe":
        raise ActionError("pc_cannot_close")       # would take the taskbar down with it
    n = d.kill(proc)
    end = time.time() + 6
    while proc.lower() in d.processes() and time.time() < end:
        await asyncio.sleep(0.3)
    gone = proc.lower() not in d.processes()
    return StepResult(ctx.t("pc_closed", app=a.get("label") or a["name"], n=n), verified=gone)


async def pc_open_url(ctx, url: str) -> StepResult:
    d = _guard(ctx)
    url = url.strip()
    if re.match(r"^[a-z][a-z0-9+.\-]*:(?!\d)", url, re.I) and not re.match(r"^https?://", url, re.I):
        raise ActionError("pc_bad_url")          # javascript:, file:, ms-settings: ...
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.netloc or " " in url:
        raise ActionError("pc_bad_url")
    ok = d.open_url(url)
    return StepResult(ctx.t("pc_url_opened", url=url), verified=bool(ok))


async def pc_screenshot(ctx) -> StepResult:
    d = _guard(ctx)
    folder = ctx.ws.path("screenshots")
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / f"screen-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}.png"
    d.screenshot(str(p))
    ok = p.is_file() and p.stat().st_size > 0
    return StepResult(ctx.t("pc_screenshot", path=ctx.ws.rel(p)), verified=ok, files=[ctx.ws.rel(p)] if ok else [])


async def pc_type_text(ctx, text: str) -> StepResult:
    d = _guard(ctx)
    if not text:
        raise ActionError("pc_nothing_to_type")
    if len(text) > 5000:
        raise ActionError("pc_text_too_long")
    _focus_target(ctx, d)
    try:
        # Long texts go in pieces so the emergency stop can cut in between.
        for i in range(0, len(text), 400):
            if ctx.engine.emergency:
                raise ActionError("pc_emergency")
            d.paste(text[i:i + 400])
    except d.fail_safe_error():
        raise ActionError("pc_failsafe")
    return StepResult(ctx.t("pc_typed", n=len(text)), verified=True)


def parse_keys(keys: str) -> list[str]:
    parts = [k.strip().lower() for k in re.split(r"[+\s]+", keys or "") if k.strip()]
    alias = {"control": "ctrl", "return": "enter", "escape": "esc", "windows": "win", "del": "delete"}
    parts = [alias.get(k, k) for k in parts]
    if not parts or len(parts) > 4:
        raise ActionError("pc_bad_keys")
    for k in parts:
        if k not in KEY_NAMES and not re.fullmatch(r"[a-z0-9]", k):
            raise ActionError("pc_bad_keys")
    if frozenset(parts) in BLOCKED_KEYS:
        raise ActionError("pc_keys_blocked")
    return ["winleft" if k == "win" else k for k in parts]


async def pc_press_keys(ctx, keys: str) -> StepResult:
    d = _guard(ctx)
    combo = parse_keys(keys)
    _focus_target(ctx, d)
    try:
        d.hotkey(combo)
    except d.fail_safe_error():
        raise ActionError("pc_failsafe")
    return StepResult(ctx.t("pc_keys", keys="+".join(combo).replace("winleft", "win")), verified=True)


def register(agent) -> None:
    from .agents import Action, P
    A = Action
    agent.status = "partial"
    agent.phase = 4
    agent.note_ar = ("يفتح البرامج المسموحة، يأخذ لقطة شاشة، يكتب النص ويضغط الاختصارات على Windows. "
                     "كل خطوة بموافقتك، وتحريك الماوس إلى زاوية الشاشة يوقف الكتابة فوراً.")
    agent.note_en = ("Opens allowed programs, takes screenshots, types text and presses shortcuts on Windows. "
                     "Every step needs your approval; moving the mouse into a screen corner stops typing at once.")
    agent.actions.update({
        "list_apps": A("list_apps", "List the programs the PC agent is allowed to open", P(), "safe", pc_list_apps),
        "open_app": A("open_app", "Open an allowed program on the PC", P(app="allowed app name"), "approval", pc_open_app),
        "close_app": A("close_app", "Close an allowed program on the PC (unsaved text in it is lost)", P(app="allowed app name"), "approval", pc_close_app),
        "open_url": A("open_url", "Open a web page in the PC's default browser", P(url="https://..."), "approval", pc_open_url),
        "screenshot": A("screenshot", "Take a screenshot of the PC screen and save it", P(), "approval", pc_screenshot),
        "type_text": A("type_text", "Type text into the window that is open on the PC", P(text="text"), "approval", pc_type_text),
        "press_keys": A("press_keys", "Press a keyboard shortcut on the PC, e.g. ctrl+s or enter", P(keys="ctrl+s"), "approval", pc_press_keys),
    })
