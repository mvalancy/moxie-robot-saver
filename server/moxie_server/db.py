"""
SQLite persistence for the local Moxie parent-app server. One file, no ORM.
The server is largely a zero-knowledge store: child PII and sealed seeds are
kept as opaque blobs exactly as the app/robot exchange them.
"""
from __future__ import annotations
import json, os, sqlite3, threading, time, uuid

_LOCK = threading.RLock()
DB_PATH = os.environ.get("MOXIE_DB", os.path.join(os.path.dirname(__file__), "..", "moxie.db"))


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


_C = _conn()


def now_s() -> int:
    return int(time.time())


def new_id() -> str:
    return str(uuid.uuid4())


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY, email TEXT UNIQUE, attributes TEXT NOT NULL, created_at INTEGER
);
CREATE TABLE IF NOT EXISTS tokens (
    access_token TEXT PRIMARY KEY, refresh_token TEXT UNIQUE, user_id TEXT,
    token_type TEXT, scope TEXT, created_at INTEGER, expires_in INTEGER
);
CREATE TABLE IF NOT EXISTS login_codes (
    email TEXT, code TEXT, redirect_uri TEXT, created_at INTEGER
);
CREATE TABLE IF NOT EXISTS children (
    id TEXT PRIMARY KEY, user_id TEXT, attributes TEXT NOT NULL, created_at INTEGER
);
CREATE TABLE IF NOT EXISTS robots (
    id TEXT PRIMARY KEY, user_id TEXT, child_id TEXT, attributes TEXT NOT NULL,
    robot_setting TEXT, last_seen_at INTEGER, created_at INTEGER
);
CREATE TABLE IF NOT EXISTS pairings (
    id_hash TEXT PRIMARY KEY, user_id TEXT, child_id TEXT, restore INTEGER,
    consumed INTEGER DEFAULT 0, created_at INTEGER,
    seed_hex TEXT, phrase TEXT
);
CREATE TABLE IF NOT EXISTS secret_keys (
    user_id TEXT, pubkey_b64 TEXT, sealed_b64 TEXT, PRIMARY KEY (user_id, pubkey_b64)
);
CREATE TABLE IF NOT EXISTS mobile_devices (
    id TEXT PRIMARY KEY, user_id TEXT, attributes TEXT
);
"""


def init():
    with _LOCK:
        _C.executescript(SCHEMA)
        _C.commit()


# ---- generic helpers ----
def q(sql, args=()):
    with _LOCK:
        return _C.execute(sql, args).fetchall()


def q1(sql, args=()):
    with _LOCK:
        return _C.execute(sql, args).fetchone()


def ex(sql, args=()):
    with _LOCK:
        _C.execute(sql, args)
        _C.commit()


# ---- domain helpers ----
def get_user_by_email(email):
    return q1("SELECT * FROM users WHERE email=?", (email,))


def get_user(uid):
    return q1("SELECT * FROM users WHERE id=?", (uid,))


def create_user(email, attributes):
    uid = new_id()
    ex("INSERT INTO users(id,email,attributes,created_at) VALUES(?,?,?,?)",
       (uid, email, json.dumps(attributes), now_s()))
    return uid


def update_user_attrs(uid, patch: dict):
    u = get_user(uid)
    attrs = json.loads(u["attributes"])
    attrs.update({k: v for k, v in patch.items() if v is not None})
    ex("UPDATE users SET attributes=? WHERE id=?", (json.dumps(attrs), uid))
    return attrs


def user_by_token(access_token):
    return q1("SELECT u.* FROM users u JOIN tokens t ON t.user_id=u.id WHERE t.access_token=?",
              (access_token,))


def children_of(uid):
    return q("SELECT * FROM children WHERE user_id=?", (uid,))


def robots_of(uid):
    return q("SELECT * FROM robots WHERE user_id=?", (uid,))
