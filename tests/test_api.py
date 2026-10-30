import time

from fastapi.testclient import TestClient

from ultra.server import create_app


def client(tmp_path):
    return TestClient(create_app(tmp_path / "d", start_background=False))


def login(c, tmp_path):
    key = (tmp_path / "d" / "ADMIN_KEY.txt").read_text()
    assert c.post("/api/auth/setup", json={"setup_key": key, "username": "momo", "password": "password123"}).status_code == 200
    r = c.post("/api/auth/login", json={"username": "momo", "password": "password123", "device_name": "test", "kind": "android"})
    assert r.status_code == 200
    return r.json()["token"]


def wait_task(c, tid):
    for _ in range(200):
        t = c.get(f"/api/tasks/{tid}").json()
        if t["status"] not in ("pending", "running"):
            return t
        time.sleep(0.02)


def test_requires_login(tmp_path):
    with client(tmp_path) as c:
        assert c.get("/api/health").json()["setup_needed"]
        assert c.get("/api/state").status_code == 401
        assert c.post("/api/tasks", json={"text": "x"}).status_code == 401
        assert c.get("/").status_code == 200 and "ULTRA AI" in c.get("/").text


def test_bearer_token_for_apps_and_revoke(tmp_path):
    with client(tmp_path) as c:
        token = login(c, tmp_path)
        c.cookies.clear()
        h = {"Authorization": f"Bearer {token}"}
        assert c.get("/api/state", headers=h).status_code == 200
        dev = c.get("/api/me", headers=h).json()["device"]
        assert dev["kind"] == "android"
        c.delete(f"/api/devices/{dev['id']}", headers=h)
        assert c.get("/api/state", headers=h).status_code == 401


def test_full_dashboard_flow(tmp_path):
    with client(tmp_path) as c:
        login(c, tmp_path)
        tid = c.post("/api/tasks", json={"text": "أنشئ مشروع متجر"}).json()["id"]
        t = wait_task(c, tid)
        assert t["status"] == "completed" and t["logs"]
        state = c.get("/api/state").json()
        assert state["counts"]["completed"] == 1 and "manager" in state["agents"]
        assert len(c.get("/api/agents").json()) == 14
        pid = c.get("/api/projects").json()[0]["id"]
        iid = c.post(f"/api/projects/{pid}/items", json={"title": "الشعار", "priority": "high"}).json()["id"]
        c.patch(f"/api/items/{iid}", json={"status": "done"})
        assert c.get("/api/projects").json()[0]["items"][0]["status"] == "done"
        # memory CRUD + secret refusal
        mid = c.post("/api/memory", json={"content": "أفضل الردود القصيرة"}).json()["id"]
        assert c.post("/api/memory", json={"content": "password: hunter2"}).status_code == 400
        c.put(f"/api/memory/{mid}", json={"content": "أفضل الردود المختصرة"})
        assert c.get("/api/memory?q=المختصرة").json()[0]["id"] == mid
        c.delete(f"/api/memory/{mid}")
        assert c.get("/api/memory").json() == []
        # secrets never come back in clear
        c.put("/api/secrets", json={"name": "anthropic_api_key", "value": "sk-ant-abcdefghijklmnop"})
        assert "abcdefghijkl" not in c.get("/api/secrets").text
        # settings: unknown keys are ignored
        r = c.put("/api/settings", json={"theme": "light", "evil": 1}).json()
        assert r["changed"] == ["theme"]
        # upload + blocked type
        assert c.post("/api/upload", files={"file": ("x.exe", b"MZ")}).status_code == 400
        up = c.post("/api/upload", files={"file": ("a.txt", b"hi")}).json()
        assert up["path"].startswith("inbox/uploads/")
        assert c.get("/api/files/download", params={"path": up["path"]}).content == b"hi"
        assert c.get("/api/files/download", params={"path": "../ultra.db"}).status_code in (400, 404, 500)


def test_emergency_and_approval_via_api(tmp_path):
    with client(tmp_path) as c:
        login(c, tmp_path)
        tid = c.post("/api/tasks", json={"text": "رتب الملفات"}).json()["id"]
        assert wait_task(c, tid)["status"] == "needs_approval"
        assert c.post(f"/api/tasks/{tid}/approve").status_code == 200
        assert wait_task(c, tid)["status"] == "completed"
        assert c.post(f"/api/tasks/{tid}/approve").status_code == 409
        assert c.post("/api/emergency/stop").status_code == 200
        assert c.post("/api/tasks", json={"text": "المشاريع"}).status_code == 409
        c.post("/api/emergency/release")
        assert c.post("/api/tasks", json={"text": "المشاريع"}).status_code == 200


def test_backup_restore_reports_sync(tmp_path):
    with client(tmp_path) as c:
        login(c, tmp_path)
        name = c.post("/api/backups").json()["name"]
        assert c.get("/api/backups").json()[0]["name"] == name
        assert c.post(f"/api/backups/{name}/restore").json()["restart_required"]
        assert (tmp_path / "d" / "restore_pending.db").exists()
        rep = c.get("/api/reports").json()
        assert "no_model" in rep["suggestions"]
        assert "server_time" in c.get("/api/sync?since=0").json()
        code = c.post("/api/telegram/code").json()["code"]
        assert len(code) == 8


def test_two_factor_via_api(tmp_path):
    from ultra.security import totp_now
    with client(tmp_path) as c:
        login(c, tmp_path)
        secret = c.post("/api/auth/2fa/start").json()["secret"]
        assert c.post("/api/auth/2fa/confirm", json={"text": totp_now(secret)}).status_code == 200
        r = c.post("/api/auth/login", json={"username": "momo", "password": "password123"})
        assert r.status_code == 403 and r.json()["error"] == "2fa_required"
        r = c.post("/api/auth/login", json={"username": "momo", "password": "password123", "code": totp_now(secret)})
        assert r.status_code == 200


def test_password_reset_with_setup_key(tmp_path):
    with client(tmp_path) as c:
        token = login(c, tmp_path)
        assert c.post("/api/auth/reset", json={"setup_key": "nope", "password": "newpassword1"}).status_code == 400
        key = (tmp_path / "d" / "ADMIN_KEY.txt").read_text()
        assert c.post("/api/auth/reset", json={"setup_key": key, "password": "newpassword1"}).status_code == 200
        c.cookies.clear()
        assert c.get("/api/state", headers={"Authorization": f"Bearer {token}"}).status_code == 401   # all devices signed out
        assert c.post("/api/auth/login", json={"username": "momo", "password": "password123"}).status_code == 401
        assert c.post("/api/auth/login", json={"username": "momo", "password": "newpassword1"}).status_code == 200


def test_setup_key_offered_only_locally_and_only_before_setup(tmp_path):
    app = create_app(tmp_path / "d", start_background=False)
    with TestClient(app, client=("127.0.0.1", 5000)) as local:
        remote = TestClient(app, client=("192.168.1.50", 5000))
        assert remote.get("/api/auth/local-setup-key").status_code == 404
        key = local.get("/api/auth/local-setup-key").json()["setup_key"]
        assert key == (tmp_path / "d" / "ADMIN_KEY.txt").read_text().strip()
        assert local.post("/api/auth/setup", json={"setup_key": key, "username": "momo",
                                                   "password": "password123"}).status_code == 200
        assert local.get("/api/auth/local-setup-key").status_code == 404
