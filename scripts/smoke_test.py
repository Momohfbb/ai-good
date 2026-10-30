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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8765")
    ap.add_argument("--data", required=True)
    ap.add_argument("--user", default="ci-user")
    ap.add_argument("--password", default="ci-password-123")
    ap.add_argument("--wait", type=int, default=90)
    ap.add_argument("--setup-only", action="store_true")
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
    st, r = call("POST", "/api/backups")
    check("backup created", st == 200 and r.get("name", "").endswith(".zip"))

    failed = [n for n, ok, _ in results if not ok]
    print(json.dumps({"passed": len(results) - len(failed), "failed": failed}, ensure_ascii=False))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
