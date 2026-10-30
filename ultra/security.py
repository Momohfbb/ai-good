"""Security: encrypted secret vault, dashboard access, risk and injection checks."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import struct
import time
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from . import config
from .db import Database


# ---------------------------------------------------------------- vault
class Vault:
    """API keys and tokens, encrypted at rest with a key kept in data/secret.key.

    Environment variables (ANTHROPIC_API_KEY, ...) are used when no value is saved.
    """

    def __init__(self, db: Database, key_path: Path = config.KEY_PATH):
        self.db = db
        if not key_path.exists():
            key_path.write_bytes(Fernet.generate_key())
            try:
                os.chmod(key_path, 0o600)
            except OSError:
                pass
        self._fernet = Fernet(key_path.read_bytes().strip())

    def set(self, name: str, value: str) -> None:
        if name not in config.SECRET_NAMES:
            raise KeyError(name)
        if not value:
            self.db.execute("DELETE FROM secrets WHERE name=?", (name,))
            return
        token = self._fernet.encrypt(value.encode())
        self.db.execute("INSERT INTO secrets(name,value,updated_at) VALUES(?,?,?) "
                        "ON CONFLICT(name) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                        (name, token, time.time()))

    def get(self, name: str) -> str:
        row = self.db.one("SELECT value FROM secrets WHERE name=?", (name,))
        if row:
            try:
                return self._fernet.decrypt(row["value"]).decode()
            except InvalidToken:
                return ""
        return os.environ.get(config.SECRET_NAMES.get(name, ""), "")

    def status(self) -> dict:
        """Which secrets are set, masked. Never returns the values."""
        out = {}
        for name in config.SECRET_NAMES:
            v = self.get(name)
            out[name] = {"set": bool(v), "hint": (v[:4] + "…" + v[-3:]) if len(v) > 10 else ("set" if v else "")}
        return out


# ---------------------------------------------------------------- accounts & devices
def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(dk).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt_b64, dk_b64 = stored.split("$")
        dk = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt_b64), n=2**14, r=8, p=1, dklen=32)
        return hmac.compare_digest(dk, base64.b64decode(dk_b64))
    except Exception:
        return False


def totp_now(secret_b32: str, at: float | None = None, step: int = 30) -> str:
    """RFC 6238 TOTP (works with Google Authenticator, Microsoft Authenticator, Aegis...)."""
    key = base64.b32decode(secret_b32.upper() + "=" * (-len(secret_b32) % 8))
    counter = int((at or time.time()) // step)
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    o = mac[-1] & 0x0F
    code = (struct.unpack(">I", mac[o:o + 4])[0] & 0x7FFFFFFF) % 1_000_000
    return f"{code:06d}"


def totp_verify(secret_b32: str, code: str) -> bool:
    code = (code or "").strip().replace(" ", "")
    now = time.time()
    return any(hmac.compare_digest(totp_now(secret_b32, now + d * 30), code) for d in (-1, 0, 1))


class Access:
    """One owner account (username + password, optional 2FA code).

    The account can only be created with the setup key written to data/ADMIN_KEY.txt
    on first run, so nobody else on the network can claim the system first.
    Every logged-in browser / phone / PC app gets its own device token that can be
    revoked from the Security page."""

    def __init__(self, db: Database, admin_key_path: Path = config.ADMIN_KEY_PATH):
        self.db = db
        if not admin_key_path.exists():
            admin_key_path.write_text(secrets.token_urlsafe(18), encoding="utf-8")
            try:
                os.chmod(admin_key_path, 0o600)
            except OSError:
                pass
        self.setup_key = admin_key_path.read_text(encoding="utf-8").strip()
        self._fails: list[float] = []

    # -- account
    def has_account(self) -> bool:
        return bool(self.db.one("SELECT id FROM users LIMIT 1"))

    def create_account(self, setup_key: str, username: str, password: str) -> tuple[bool, str]:
        if self.has_account():
            return False, "account_exists"
        if not hmac.compare_digest(setup_key.strip(), self.setup_key):
            self.db.audit("auth", "setup_failed", username, "warning")
            return False, "bad_setup_key"
        if len(username.strip()) < 3 or len(password) < 8:
            return False, "weak_credentials"
        self.db.execute("INSERT INTO users(username,pw_hash,created_at) VALUES(?,?,?)",
                        (username.strip(), hash_password(password), time.time()))
        self.db.audit("auth", "account_created", username.strip())
        return True, "ok"

    def reset_with_setup_key(self, setup_key: str, password: str) -> bool:
        """Forgotten password: whoever can read ADMIN_KEY.txt on this computer may set a new one.
        Two-factor is turned off and every device is signed out."""
        if not hmac.compare_digest(setup_key.strip(), self.setup_key) or len(password) < 8:
            self.db.audit("auth", "reset_failed", "", "warning")
            return False
        self.db.execute("UPDATE users SET pw_hash=?, totp_secret=NULL", (hash_password(password),))
        self.db.execute("UPDATE devices SET revoked=1")
        self.db.audit("auth", "password_reset_with_setup_key", "", "critical")
        return True

    def change_password(self, user_id: int, old: str, new: str) -> bool:
        u = self.db.one("SELECT * FROM users WHERE id=?", (user_id,))
        if not u or not verify_password(old, u["pw_hash"]) or len(new) < 8:
            return False
        self.db.execute("UPDATE users SET pw_hash=? WHERE id=?", (hash_password(new), user_id))
        self.db.audit("auth", "password_changed", u["username"], "warning")
        return True

    def start_2fa(self, user_id: int) -> str:
        secret = base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")
        self.db.set_setting(f"pending_totp_{user_id}", secret)
        return secret

    def confirm_2fa(self, user_id: int, code: str) -> bool:
        secret = self.db.get_setting(f"pending_totp_{user_id}")
        if not secret or not totp_verify(secret, code):
            return False
        self.db.execute("UPDATE users SET totp_secret=? WHERE id=?", (secret, user_id))
        self.db.set_setting(f"pending_totp_{user_id}", None)
        self.db.audit("auth", "2fa_enabled", str(user_id))
        return True

    def disable_2fa(self, user_id: int, password: str) -> bool:
        u = self.db.one("SELECT * FROM users WHERE id=?", (user_id,))
        if not u or not verify_password(password, u["pw_hash"]):
            return False
        self.db.execute("UPDATE users SET totp_secret=NULL WHERE id=?", (user_id,))
        self.db.audit("auth", "2fa_disabled", u["username"], "warning")
        return True

    # -- login / devices
    def login(self, username: str, password: str, device_name: str, kind: str = "browser",
              code: str = "") -> tuple[str | None, str]:
        now = time.time()
        self._fails = [t for t in self._fails if now - t < 300]
        if len(self._fails) >= 10:
            self.db.audit("auth", "login_blocked", "too many failed attempts", "warning")
            return None, "locked"
        u = self.db.one("SELECT * FROM users WHERE username=?", (username.strip(),))
        if not u or not verify_password(password, u["pw_hash"]):
            self._fails.append(now)
            self.db.audit("auth", "login_failed", f"{username} / {device_name}", "warning")
            return None, "bad_credentials"
        if u["totp_secret"]:
            if not code:
                return None, "2fa_required"
            if not totp_verify(u["totp_secret"], code):
                self._fails.append(now)
                self.db.audit("auth", "login_failed_2fa", username, "warning")
                return None, "bad_2fa"
        token = secrets.token_urlsafe(32)
        self.db.execute("INSERT INTO devices(user_id,name,kind,token_hash,created_at,last_seen) VALUES(?,?,?,?,?,?)",
                        (u["id"], device_name[:60] or kind, kind[:20], _hash(token), now, now))
        self.db.audit("auth", "device_added", f"{device_name} ({kind})")
        return token, "ok"

    def check(self, token: str | None) -> dict | None:
        if not token:
            return None
        row = self.db.one("SELECT * FROM devices WHERE token_hash=? AND revoked=0", (_hash(token),))
        if row and time.time() - (row["last_seen"] or 0) > 60:
            self.db.execute("UPDATE devices SET last_seen=? WHERE id=?", (time.time(), row["id"]))
        return row

    def revoke(self, device_id: int) -> None:
        self.db.execute("UPDATE devices SET revoked=1 WHERE id=?", (device_id,))
        self.db.audit("auth", "device_revoked", str(device_id), "warning")


# ---------------------------------------------------------------- risk & injection
INJECTION_PATTERNS = [
    r"ignore (all |any )?(previous|prior|above) (instructions|prompts)",
    r"disregard (the |all )?(previous|above|system)",
    r"you are now (in )?(developer|dan|god) mode",
    r"system prompt",
    r"reveal (your|the) (instructions|prompt|api key|secret)",
    r"(send|upload|exfiltrate) .*(password|api key|token|secret)",
    r"تجاهل (كل |جميع )?(التعليمات|الأوامر) (السابقة|الأولى)",
    r"انس(َ)? (كل )?التعليمات",
]
_INJ = [re.compile(p, re.IGNORECASE) for p in INJECTION_PATTERNS]


def detect_injection(text: str) -> list[str]:
    """Phrases in untrusted content (files, web pages) that try to give the assistant orders."""
    return [m.group(0) for p in _INJ if (m := p.search(text or ""))]


DANGEROUS_COMMAND_PATTERNS = [
    r"\brm\s+-rf\b", r"\bformat\s+[a-z]:", r"\bdel\s+/[sq]", r"\bmkfs\b", r"\bshutdown\b",
    r"\breg\s+delete\b", r"disable (the )?(antivirus|defender|firewall)",
    r"(اح?ذف|امسح|مسح|نحي) (كل|جميع|ga3|قاع)", r"فورمات",
    r"(credit card|carte bancaire|بطاقة (الائتمان|بنكية)|كلمة (السر|المرور))",
]
_DANGER = [re.compile(p, re.IGNORECASE) for p in DANGEROUS_COMMAND_PATTERNS]


def suspicious_command(text: str) -> list[str]:
    return [m.group(0) for p in _DANGER if (m := p.search(text or ""))]


SECRET_PATTERNS = [
    r"sk-[A-Za-z0-9_\-]{16,}", r"sk-ant-[A-Za-z0-9_\-]{16,}", r"\b\d{9,10}:[A-Za-z0-9_\-]{30,}\b",
    r"(password|passwd|mot de passe|كلمة (السر|المرور)|المودباس)\s*[:=]?\s*\S+",
    r"\b(?:\d[ -]?){13,19}\b",
]
_SECRET = [re.compile(p, re.IGNORECASE) for p in SECRET_PATTERNS]


def contains_secret(text: str) -> bool:
    """Used to refuse saving passwords / keys / card numbers into ordinary memory."""
    return any(p.search(text or "") for p in _SECRET)


BLOCKED_UPLOAD_EXT = {".exe", ".bat", ".cmd", ".com", ".scr", ".ps1", ".vbs", ".js", ".jar", ".msi",
                      ".dll", ".lnk", ".hta", ".reg", ".apk", ".sh"}


def safe_upload_name(name: str) -> str | None:
    """Clean a received file name; None when the type is refused (executables, scripts)."""
    base = Path(name or "file").name
    base = re.sub(r"[^\w.\- ؀-ۿ]", "_", base).strip(" .") or "file"
    if Path(base).suffix.lower() in BLOCKED_UPLOAD_EXT:
        return None
    return base[:120]


SUSPICIOUS_URL = re.compile(r"https?://(\d{1,3}\.){3}\d{1,3}|bit\.ly|tinyurl|\.zip(/|$)|@", re.IGNORECASE)


def suspicious_links(text: str) -> list[str]:
    urls = re.findall(r"https?://\S+", text or "")
    return [u for u in urls if SUSPICIOUS_URL.search(u)]
