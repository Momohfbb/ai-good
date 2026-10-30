"""End-to-end smoke test against a running ULTRA AI brain (used by CI on Windows and for Android).

    python scripts/smoke_test.py --url http://127.0.0.1:8765 --data <data dir> [--user ci-user --password ...]

Creates the owner account with the setup key (first run), signs in, runs offline
commands through the real task engine and checks the verified results. Standard
library only, so it runs on a fresh Windows machine.
"""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def notepad_text() -> str | None:
    """Text inside the open Notepad window, read with the Windows API (proves the typing really landed)."""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    proc_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    texts: list[str] = []

    def cls(h):
        b = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(h, b, 256)
        return b.value

    def child(h, _):
        if cls(h) in ("Edit", "RichEditD2DPT"):
            n = user32.SendMessageW(h, 0x000E, 0, 0)            # WM_GETTEXTLENGTH
            b = ctypes.create_unicode_buffer(n + 1)
            user32.SendMessageW(h, 0x000D, n + 1, b)             # WM_GETTEXT
            texts.append(b.value)
        return True

    def top(h, _):
        if cls(h) == "Notepad":
            user32.EnumChildWindows(h, proc_type(child), 0)
        return True

    user32.EnumWindows(proc_type(top), 0)
    return "\n".join(texts) if texts else None


def running(name: str) -> bool:
    import subprocess
    out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {name}"], capture_output=True, text=True).stdout
    return name.lower() in out.lower()


def pc_checks(call, run_task, check, data: Path) -> None:
    def approve_and_wait(t):
        call("POST", f"/api/tasks/{t['id']}/approve")
        for _ in range(150):
            st, t2 = call("GET", f"/api/tasks/{t['id']}")
            if t2["status"] in ("completed", "failed", "stopped", "needs_approval"):
                return t2
            time.sleep(0.2)
        return t2

    words = "مرحبا من ULTRA AI 123"
    t = run_task("اكتب في المفكرة: " + words, "needs_approval")
    reason = (t or {}).get("approval_reason") or ""
    check("PC: asks approval once, listing every step", t and t["status"] == "needs_approval"
          and "notepad" in reason and words in reason, reason)
    check("PC: nothing opened before approval", not running("notepad.exe"))
    t = approve_and_wait(t)
    check("PC: Notepad opened and text typed", t["status"] == "completed", t.get("error") or t["status"])
    time.sleep(1)
    got = notepad_text()
    check("PC: the text is really inside Notepad (Arabic too)", got is not None and words in got, repr(got))

    t = run_task("خذ لقطة شاشة", "needs_approval")
    t = approve_and_wait(t) if t and t["status"] == "needs_approval" else t
    shot = data / "workspace" / (t.get("files") or ["?"])[0]
    ok = t["status"] == "completed" and shot.is_file() and shot.read_bytes()[:4] == b"\x89PNG" and shot.stat().st_size > 2000
    check("PC: screenshot saved as a real PNG", ok, f"{t.get('error') or t['status']} {shot}")

    t = run_task("سكر المفكرة", "needs_approval")
    t = approve_and_wait(t) if t and t["status"] == "needs_approval" else t
    time.sleep(1)
    check("PC: Notepad closed", t["status"] == "completed" and not running("notepad.exe"), t.get("error") or t["status"])

    st, _ = call("PUT", "/api/settings", {"pc_control_enabled": False})
    t = run_task("افتح المفكرة", "needs_approval")
    t = approve_and_wait(t) if t and t["status"] == "needs_approval" else t
    check("PC: switched off in settings = refused", t["status"] == "failed" and not running("notepad.exe"), t["status"])
    call("PUT", "/api/settings", {"pc_control_enabled": True})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8765")
    ap.add_argument("--data", required=True)
    ap.add_argument("--user", default="ci-user")
    ap.add_argument("--password", default="ci-password-123")
    ap.add_argument("--wait", type=int, default=90)
    ap.add_argument("--setup-only", action="store_true")
    ap.add_argument("--ollama", action="store_true", help="also check the local model path (needs scripts/fake_ollama.py)")
    ap.add_argument("--pc", action="store_true", help="also control the Windows desktop: Notepad, typing, screenshot (needs --ollama)")
    a = ap.parse_args()
    base = a.url.rstrip("/")
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    def call(method, path, body=None):
        req = urllib.request.Request(base + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json"})
        try:
            with opener.open(req, timeout=20) as r:
                return r.status, json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"null")

    results = []

    def check(name, ok, detail=""):
        results.append((name, bool(ok), detail))
        print(("PASS " if ok else "FAIL ") + name + (f" — {detail}" if detail and not ok else ""), flush=True)

    end = time.time() + a.wait
    health = None
    while time.time() < end:
        try:
            health = call("GET", "/api/health")[1]
            break
        except Exception:
            time.sleep(1)
    check("brain answers /api/health", health and health.get("ok"), str(health))
    if not health:
        return 1
    if health.get("setup_needed"):
        key = (Path(a.data) / "ADMIN_KEY.txt").read_text(encoding="utf-8").strip()
        st, _ = call("POST", "/api/auth/setup", {"setup_key": "wrong", "username": a.user, "password": a.password})
        check("wrong setup key refused", st == 400)
        st, r = call("POST", "/api/auth/setup", {"setup_key": key, "username": a.user, "password": a.password})
        check("owner account created", st == 200, str(r))
    if a.setup_only:
        return 0 if all(ok for _, ok, _ in results) else 1
    st, _ = call("GET", "/api/state")
    check("API refuses requests without login", st == 401)
    st, _ = call("POST", "/api/auth/login", {"username": a.user, "password": "bad-password"})
    check("wrong password refused", st == 401)
    st, r = call("POST", "/api/auth/login", {"username": a.user, "password": a.password, "device_name": "smoke-test"})
    check("login", st == 200, str(r))

    def run_task(text, expect):
        st, r = call("POST", "/api/tasks", {"text": text})
        if st != 200:
            return None
        for _ in range(150):
            st, t = call("GET", f"/api/tasks/{r['id']}")
            if t["status"] == expect or t["status"] in ("completed", "failed", "stopped"):
                return t
            time.sleep(0.2)
        return t

    t = run_task("أنشئ مشروع اختبار ويندوز", "completed")
    check("task: create project (Arabic)", t and t["status"] == "completed" and t["verified"], t and (t["error"] or t["status"]))
    t = run_task("اكتب ملاحظة اختبار الملفات", "completed")
    check("task: write note file", t and t["status"] == "completed" and t["files"], t and t["error"])
    t = run_task("تذكر أن اللون المفضل أزرق", "completed")
    check("task: save memory", t and t["status"] == "completed")
    t = run_task("رتب الملفات", "needs_approval")
    check("risky task waits for approval", t and t["status"] == "needs_approval")
    if t:
        call("POST", f"/api/tasks/{t['id']}/approve")
        for _ in range(100):
            st, t2 = call("GET", f"/api/tasks/{t['id']}")
            if t2["status"] in ("completed", "failed"):
                break
            time.sleep(0.2)
        check("approved task completes", t2["status"] == "completed", t2.get("error"))
    st, r = call("POST", "/api/emergency/stop")
    check("emergency stop", st == 200)
    st, _ = call("POST", "/api/tasks", {"text": "المشاريع"})
    check("new tasks refused during emergency", st == 409)
    call("POST", "/api/emergency/release")
    st, r = call("GET", "/api/state")
    check("state after release", st == 200 and not r["emergency"])
    if a.ollama:
        st, r = call("GET", "/api/models/ollama")
        check("Ollama found on this PC", st == 200 and r["available"] and "gemma4:12b" in r["models"], str(r))
        st, r = call("POST", "/api/models/test", {"text": "chat"})
        check("Ollama model answers", st == 200 and r.get("ok") and r.get("provider") == "ollama", str(r))
        t = run_task("اكتب لي 3 أفكار لمشروع صغير في الجزائر", "completed")
        answer = " ".join(s.get("result") or "" for s in (t or {}).get("steps", []))
        check("free-form request answered by the local model", t and t["status"] == "completed" and "مخبزة" in answer,
              t and (t.get("error") or t["status"]))
    if a.pc:
        pc_checks(call, run_task, check, Path(a.data))
    st, r = call("POST", "/api/backups")
    check("backup created", st == 200 and r.get("name", "").endswith(".zip"))

    failed = [n for n, ok, _ in results if not ok]
    print(json.dumps({"passed": len(results) - len(failed), "failed": failed}, ensure_ascii=False))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
