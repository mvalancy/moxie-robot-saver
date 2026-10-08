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


#: `pairings.consumed`: a code is OPEN until a robot completes it (USED), or VOID when a
#: robot on the account was unpaired before anyone used it.
PAIRING_OPEN, PAIRING_USED, PAIRING_VOID = 0, 1, 2


def unpair_robot(rid, uid):
    """Take one robot off an account in one transaction: delete its record and void every
    pairing code the account still had open, so a code made before the unpair cannot bind
    the robot back. `(deleted row, codes voided)`; `(None, 0)` when this account has no
    robot with that id, and then nothing changes."""
    with _LOCK, _C:
        row = _C.execute("SELECT * FROM robots WHERE id=? AND user_id=?", (rid, uid)).fetchone()
        if not row:
            return None, 0
        _C.execute("DELETE FROM robots WHERE id=? AND user_id=?", (rid, uid))
        voided = _C.execute("UPDATE pairings SET consumed=? WHERE user_id=? AND consumed=?",
                            (PAIRING_VOID, uid, PAIRING_OPEN)).rowcount
        return row, voided


def device_id_of(row) -> str:
    """The MQTT identity (`d_<uuid>`) a robot record names, or `""`."""
    return str(json.loads(row["attributes"]).get("mqtt-device-id") or "").strip()


def bound_device_ids() -> set:
    """Every MQTT identity some account's robot record names."""
    return {d for d in map(device_id_of, q("SELECT attributes FROM robots")) if d}


def claim_robot(uid, device_id, attributes: dict, robot_setting: dict, child_attrs: dict):
    """Bind the robot behind one MQTT identity to an account, in one transaction, keeping
    two rules: a robot is on one account, and an account has one robot (the web app shows
    `robots[0]`). `(outcome, row)`, and only `"created"` changes anything:

    * `"exists"`: this account's record already names it (`row`);
    * `"taken"`: another account's record names it (`row` is None);
    * `"occupied"`: this account has a different robot (`row`);
    * `"created"`: `row` is the new record, bound to the account's first child. An
      account with no child gets one from `child_attrs` first, as pairing does."""
    with _LOCK, _C:
        rows = _C.execute("SELECT * FROM robots").fetchall()
        mine = [r for r in rows if r["user_id"] == uid]
        same = next((r for r in mine if device_id_of(r) == device_id), None)
        if same is not None:
            return "exists", same
        if any(device_id_of(r) == device_id for r in rows):
            return "taken", None
        if mine:
            return "occupied", mine[0]
        kid = _C.execute("SELECT id FROM children WHERE user_id=?", (uid,)).fetchone()
        if kid:
            child_id = kid["id"]
        else:
            child_id = new_id()
            _C.execute("INSERT INTO children(id,user_id,attributes,created_at) VALUES(?,?,?,?)",
                       (child_id, uid, json.dumps(child_attrs), now_s()))
            user = json.loads(_C.execute("SELECT attributes FROM users WHERE id=?",
                                         (uid,)).fetchone()["attributes"])
            if not user.get("active-child-id"):      # the first child is the active one
                user["active-child-id"] = child_id
                _C.execute("UPDATE users SET attributes=? WHERE id=?", (json.dumps(user), uid))
        rid = new_id()
        _C.execute("INSERT INTO robots(id,user_id,child_id,attributes,robot_setting,"
                   "last_seen_at,created_at) VALUES(?,?,?,?,?,?,?)",
                   (rid, uid, child_id, json.dumps({"embodied-robot-id": rid, **attributes,
                                                    "mqtt-device-id": device_id}),
                    json.dumps(robot_setting), now_s(), now_s()))
        return "created", _C.execute("SELECT * FROM robots WHERE id=?", (rid,)).fetchone()
