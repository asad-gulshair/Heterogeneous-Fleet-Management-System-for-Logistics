"""Login and roles.

    operator   watches the fleet, acknowledges alarms, sends orders and missions, pauses the fleet,
               closes aisles, sends robots to charge / repair
    admin      everything: layouts, running shifts, settings, users, fault injection, WMS key

Passwords: PBKDF2-SHA256, 200 000 rounds, random salt. Sessions: random token in an HttpOnly cookie
(the database keeps only its SHA-256), 12 h, renewed while used. 5 wrong passwords lock the name for 60 s.
A WMS uses an API key (header X-API-Key) and can only reach /api/wms/*.

First start: users "admin" / "admin" and "operator" / "operator" are created and must change their
password at first login (or set TUSCORE_ADMIN_PASSWORD before the first start).
Set TUSCORE_AUTH=off to switch login off (single-user demo, tests).
"""
from __future__ import annotations
import hashlib
import hmac
import os
import re
import secrets
import time

ENABLED = os.environ.get("TUSCORE_AUTH", "on").lower() not in ("off", "0", "false", "no")
ROLES = {"operator": 1, "admin": 2}
COOKIE = "tuscore_session"
SESSION_S = 12 * 3600
ROUNDS = 200_000
LOCAL = {"id": 0, "username": "local", "role": "admin", "must_change": 0}

PUBLIC = {"/api/auth/login", "/api/auth/me", "/api/system", "/openapi.json"}
OPERATOR_WRITES = [re.compile(p) for p in (
    r"^/api/orders$", r"^/api/robots/[^/]+/(goto|charge|repair)$", r"^/api/fleet/pause$", r"^/api/aisles/\d+$",
    r"^/api/alarms(/.*)?$", r"^/api/missions(/.*)?$", r"^/api/sim/(speed|auto-orders)$", r"^/api/auth/(logout|password)$",
    r"^/api/designs/(check|suggest-traffic)$", r"^/api/wms/orders(/.*)?$")]
ADMIN_READS = [re.compile(p) for p in (r"^/api/users", r"^/api/audit", r"^/api/wms/key")]
WMS_PATHS = re.compile(r"^/api/wms/(orders(/.*)?|events)$")


def hash_pw(pw: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), ROUNDS).hex()
    return f"pbkdf2${ROUNDS}${salt}${h}"


def check_pw(pw: str, stored: str) -> bool:
    try:
        _, rounds, salt, h = stored.split("$")
        got = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), int(rounds)).hex()
        return hmac.compare_digest(got, h)
    except (ValueError, TypeError):
        return False


def required(method: str, path: str) -> str | None:
    """None = public, 'wms' = API key or a logged-in user, else the minimum role."""
    if path == "/ws":
        return "operator"
    if not path.startswith("/api/") or path in PUBLIC:
        return None
    if WMS_PATHS.match(path):
        return "wms"
    if method in ("GET", "HEAD", "OPTIONS"):
        return "admin" if any(p.match(path) for p in ADMIN_READS) else "operator"
    return "operator" if any(p.match(path) for p in OPERATOR_WRITES) else "admin"


class Auth:
    def __init__(self, store):
        self.store = store
        self.fails: dict = {}
        if not store.q("SELECT id FROM users LIMIT 1"):
            admin_pw = os.environ.get("TUSCORE_ADMIN_PASSWORD")
            self._create("admin", admin_pw or "admin", "admin", must_change=not admin_pw)
            self._create("operator", "operator", "operator", must_change=True)
        key = os.environ.get("TUSCORE_WMS_KEY") or store.get("wms_key")
        if not key:
            key = "wms_" + secrets.token_urlsafe(24)
        store.set("wms_key", key)
        self.wms_key = key

    # ------------------------------------------------------------ users
    def _create(self, username, pw, role, must_change=False):
        return self.store.x("INSERT INTO users(username, pw, role, created, must_change) VALUES(?,?,?,?,?)",
                            (username, hash_pw(pw), role, time.time(), int(must_change)))

    def users(self) -> list:
        return self.store.q("SELECT id, username, role, created, active, must_change FROM users ORDER BY id")

    def create_user(self, username: str, pw: str, role: str) -> dict:
        if not re.match(r"^[a-zA-Z0-9_.-]{3,32}$", username or ""):
            raise ValueError("user name: 3-32 letters, digits or _ . -")
        if role not in ROLES:
            raise ValueError("role must be operator or admin")
        self._pw_ok(pw)
        if self.store.q("SELECT id FROM users WHERE username=?", (username,)):
            raise ValueError("this user name is taken")
        uid = self._create(username, pw, role, must_change=True)
        return {"id": uid, "username": username, "role": role}

    def update_user(self, uid: int, role: str | None = None, active: bool | None = None, pw: str | None = None, by: dict | None = None):
        u = self.store.q("SELECT * FROM users WHERE id=?", (uid,))
        if not u:
            raise KeyError(uid)
        u = u[0]
        if role is not None:
            if role not in ROLES:
                raise ValueError("role must be operator or admin")
            if u["role"] == "admin" and role != "admin" and self._admins() <= 1:
                raise ValueError("there must be at least one admin")
            self.store.x("UPDATE users SET role=? WHERE id=?", (role, uid))
        if active is not None:
            if not active and u["role"] == "admin" and self._admins() <= 1:
                raise ValueError("there must be at least one admin")
            if not active and by and by.get("id") == uid:
                raise ValueError("you cannot switch off your own account")
            self.store.x("UPDATE users SET active=? WHERE id=?", (int(active), uid))
            if not active:
                self.store.x("DELETE FROM sessions WHERE user_id=?", (uid,))
        if pw is not None:
            self._pw_ok(pw)
            self.store.x("UPDATE users SET pw=?, must_change=1 WHERE id=?", (hash_pw(pw), uid))

    def _admins(self) -> int:
        return self.store.q("SELECT COUNT(*) n FROM users WHERE role='admin' AND active=1")[0]["n"]

    @staticmethod
    def _pw_ok(pw: str):
        if not pw or len(pw) < 8:
            raise ValueError("password: at least 8 characters")

    def change_password(self, user: dict, old: str, new: str):
        u = self.store.q("SELECT * FROM users WHERE id=?", (user["id"],))[0]
        if not check_pw(old or "", u["pw"]):
            raise ValueError("current password is wrong")
        if new == old:
            raise ValueError("the new password must be different")
        self._pw_ok(new)
        self.store.x("UPDATE users SET pw=?, must_change=0 WHERE id=?", (hash_pw(new), u["id"]))

    # ------------------------------------------------------------ sessions
    def login(self, username: str, pw: str) -> tuple[str, dict]:
        now = time.time()
        n, until = self.fails.get(username, (0, 0))
        if until > now:
            raise PermissionError(f"too many wrong passwords, try again in {until - now:.0f} s")
        u = self.store.q("SELECT * FROM users WHERE username=? AND active=1", (username,))
        if not u or not check_pw(pw or "", u[0]["pw"]):
            n += 1
            self.fails[username] = (0, now + 60) if n >= 5 else (n, 0)
            raise PermissionError("wrong user name or password")
        self.fails.pop(username, None)
        token = secrets.token_urlsafe(32)
        self.store.x("INSERT INTO sessions VALUES(?,?,?,?)", (self._h(token), u[0]["id"], now, now + SESSION_S))
        self.store.x("DELETE FROM sessions WHERE expires < ?", (now,))
        return token, self._public(u[0])

    def user(self, token: str | None) -> dict | None:
        if not token:
            return None
        now = time.time()
        r = self.store.q("SELECT u.*, s.expires FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=? AND u.active=1",
                         (self._h(token),))
        if not r or r[0]["expires"] < now:
            return None
        if r[0]["expires"] - now < SESSION_S - 300:           # sliding expiry, written at most every 5 min
            self.store.x("UPDATE sessions SET expires=? WHERE token=?", (now + SESSION_S, self._h(token)))
        return self._public(r[0])

    def logout(self, token: str | None):
        if token:
            self.store.x("DELETE FROM sessions WHERE token=?", (self._h(token),))

    def rotate_wms_key(self) -> str:
        self.wms_key = "wms_" + secrets.token_urlsafe(24)
        self.store.set("wms_key", self.wms_key)
        return self.wms_key

    def wms_ok(self, key: str | None) -> bool:
        return bool(key) and hmac.compare_digest(key, self.wms_key)

    @staticmethod
    def _h(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    @staticmethod
    def _public(u: dict) -> dict:
        return {"id": u["id"], "username": u["username"], "role": u["role"], "must_change": bool(u["must_change"])}
