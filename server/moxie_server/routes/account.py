"""Auth, the user, children, and the read-stub remainder of the parent-app REST surface.

Contract: `docs/architecture/rest-api-contract.md` (§2 auth, §3.2 user, §3.3 children).
"""
from __future__ import annotations
import json
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from .. import child_profile, db
from ..auth import current_user, digits, mint_tokens, read_json
from ..serializers import user_document

router = APIRouter()
NO_CONTENT = 204

NEW_USER = {"first-name": "", "last-name": "", "iot-endpoint": 0, "user-type": None,
            "coppa-consent-status": "granted", "max-children": 4,
            "timezone-id": "America/Los_Angeles"}


def user_id_for(email: str, **attrs) -> str:
    """The id of the user with this email, creating the account on first sight."""
    u = db.get_user_by_email(email)
    return u["id"] if u else db.create_user(email, {"email": email, **NEW_USER, **attrs})


# --- auth (§2) -------------------------------------------------------------------------
@router.post("/api/login/start")
async def login_start(request: Request):
    email = ((await read_json(request)).get("email") or "").strip().lower()
    if not email:
        raise HTTPException(400, "email required")
    redirect_uri, code = secrets.token_urlsafe(8), digits()
    db.ex("INSERT INTO login_codes(email,code,redirect_uri,created_at) VALUES(?,?,?,?)",
          (email, code, redirect_uri, db.now_s()))
    print(f"\n[LOGIN CODE] {email} -> {code}\n")     # no mail server on a LAN appliance
    return {"redirect_uri": redirect_uri, "login_code": code}   # login_code: local extra


@router.post("/api/login/finish")
async def login_finish(request: Request):
    code = ((await read_json(request)).get("code") or "").strip()
    row = db.q1("SELECT * FROM login_codes WHERE code=? ORDER BY created_at DESC LIMIT 1", (code,))
    if not row:
        raise HTTPException(400, "invalid code")
    uid = user_id_for(row["email"])
    db.ex("DELETE FROM login_codes WHERE code=?", (code,))
    return mint_tokens(uid)


@router.post("/api/oauth/token")
async def oauth_token(request: Request):
    form = await request.form()
    grant = form.get("grant_type")
    if grant == "refresh_token":
        rt = form.get("refresh_token")
        row = db.q1("SELECT * FROM tokens WHERE refresh_token=?", (rt,))
        if not row:
            raise HTTPException(401, "invalid refresh token")
        db.ex("DELETE FROM tokens WHERE refresh_token=?", (rt,))
        return mint_tokens(row["user_id"])
    if grant == "password":     # legacy/testing path (contract §2 step 6)
        u = db.get_user_by_email((form.get("username") or "").lower())
        if not u:
            raise HTTPException(401, "no such user")
        return mint_tokens(u["id"])
    raise HTTPException(400, "unsupported grant_type")


@router.post("/api/login/register")
async def login_register(request: Request, u=Depends(current_user)):
    body = await read_json(request)
    db.update_user_attrs(u["id"], {"user-type": "clinician",
                                   "pro-registration-code": body.get("pro_registration_code")})
    return Response(status_code=NO_CONTENT)


# --- user (§3.2) -----------------------------------------------------------------------
@router.get("/api/users/me")
def users_me(include: str = "", u=Depends(current_user)):
    return user_document(u, db.children_of(u["id"]), db.robots_of(u["id"]))


@router.put("/api/users/me")
async def update_user(request: Request, u=Depends(current_user)):
    body = await read_json(request)
    attrs = db.update_user_attrs(u["id"], body.get("user", body))
    return {"data": {"id": u["id"], "type": "users", "attributes": attrs}}


@router.delete("/api/users/me")
def delete_user(u=Depends(current_user)):
    """Everything keyed to the account goes — including the pairing rows, which hold the
    seed and the recovery phrase in clear, and the sealed key collection."""
    for t in ("children", "robots", "pairings", "secret_keys", "mobile_devices", "tokens"):
        db.ex(f"DELETE FROM {t} WHERE user_id=?", (u["id"],))
    db.ex("DELETE FROM login_codes WHERE email=?", (u["email"],))
    db.ex("DELETE FROM users WHERE id=?", (u["id"],))
    return Response(status_code=NO_CONTENT)


@router.put("/api/secret-key-collection")
async def secret_key_collection(request: Request, u=Depends(current_user)):
    body = await read_json(request)
    coll = (body.get("secret_key_collection") or {}).get("secret-keys-indexed-by-public-keys", {})
    for pub, sealed in coll.items():
        db.ex("INSERT OR REPLACE INTO secret_keys(user_id,pubkey_b64,sealed_b64) VALUES(?,?,?)",
              (u["id"], pub, sealed))
    return Response(status_code=NO_CONTENT)


@router.post("/api/users/me/change-email-request")
def change_email_request(u=Depends(current_user)):
    code = digits()
    print(f"\n[CHANGE-EMAIL CODE] {code}\n")
    return {"code": code, "code_length": 6, "message": "verification code sent"}


@router.post("/api/users/me/change-email")
async def change_email(request: Request, u=Depends(current_user)):
    new = (await read_json(request)).get("new_email")
    if new:
        db.update_user_attrs(u["id"], {"email": new})
    return Response(status_code=NO_CONTENT)


# --- children (§3.3) -------------------------------------------------------------------
def _child(cid: str, user_id: str):
    row = db.q1("SELECT * FROM children WHERE id=? AND user_id=?", (cid, user_id))
    if not row:
        raise HTTPException(404, "no such child")
    return row


def create_child_row(user_id: str, attrs: dict) -> str:
    """Insert a child; the first one becomes the account's active child."""
    cid = db.new_id()
    db.ex("INSERT INTO children(id,user_id,attributes,created_at) VALUES(?,?,?,?)",
          (cid, user_id, json.dumps(attrs), db.now_s()))
    if not json.loads(db.get_user(user_id)["attributes"]).get("active-child-id"):
        db.update_user_attrs(user_id, {"active-child-id": cid})
    return cid


def _refuse_unsayable(name: str) -> None:
    """A 400 carrying the supervisor's reason, in the parent's words, when it would refuse
    `name` (the name rule and Moxie's safety table, `child_profile.refusal_for`): the
    record is then left as it was. A supervisor that cannot be asked refuses nothing."""
    why = child_profile.refusal_for(name)
    if why:
        raise HTTPException(400, why)


@router.post("/api/children")
async def create_child(request: Request, u=Depends(current_user)):
    """A new child record. A name Moxie would refuse to say is a 400 and nothing is made."""
    body = await read_json(request)
    attrs = body.get("child", body)
    _refuse_unsayable(child_profile.name_in(attrs))
    cid = create_child_row(u["id"], attrs)
    return {"data": {"id": cid, "type": "children", "attributes": attrs}}


@router.put("/api/children/{cid}")
async def update_child(cid: str, request: Request, u=Depends(current_user)):
    """Update the record, then send its name to every robot of this account bound to this
    child (the Wi-Fi tab's name field is the rename). A NEW name Moxie would refuse to say
    is a 400 and the record is left as it was; otherwise `child_pushed` and `reason` say
    whether the robots got it, and the record is saved either way."""
    row = _child(cid, u["id"])
    body = await read_json(request)
    before = json.loads(row["attributes"])
    attrs = {**before, **body.get("child", body)}
    name = child_profile.name_in(attrs)
    if name != child_profile.name_in(before):    # another setting never re-judges the name
        _refuse_unsayable(name)
    db.ex("UPDATE children SET attributes=? WHERE id=?", (json.dumps(attrs), cid))
    return {"data": {"id": cid, "type": "children", "attributes": attrs},
            **child_profile.push_to_robots_of_child(u["id"], cid)}


@router.delete("/api/children/{cid}")
def delete_child(cid: str, u=Depends(current_user)):
    """The record goes, and with it the name on any robot of this account still bound to
    it (the web app unpairs first, so there is usually none)."""
    child_profile.clear_from_robots_of_child(u["id"], cid)
    db.ex("DELETE FROM children WHERE id=? AND user_id=?", (cid, u["id"]))
    return Response(status_code=NO_CONTENT)


@router.get("/api/children/{cid}/pending-info")
def child_pending(cid: str, u=Depends(current_user)):
    return {"consent_status": "granted", "consent_url": "", "parent_email": ""}


# --- light-state extras ----------------------------------------------------------------
@router.post("/api/grl/code")
def grl_code(u=Depends(current_user)):
    code = digits()
    db.update_user_attrs(u["id"], {"last-grl-code": code, "grl-code-status": "unused"})
    return {"data": {"grl_code": code, "expires_at": db.now_s() + 3600}}


@router.post("/api/grl/revoke-all")
def grl_revoke(u=Depends(current_user)):
    db.update_user_attrs(u["id"], {"grl-code-status": "expired"})
    return Response(status_code=NO_CONTENT)


@router.post("/api/mobile-devices")
@router.put("/api/mobile-devices/{mid}")
async def upsert_mobile_device(request: Request, mid: str = "", u=Depends(current_user)):
    body = await read_json(request)
    attrs = body.get("mobile-device", body)
    mid = mid or attrs.get("mobile-device-id") or db.new_id()
    db.ex("INSERT OR REPLACE INTO mobile_devices(id,user_id,attributes) VALUES(?,?,?)",
          (mid, u["id"], json.dumps(attrs)))
    return {"data": {"id": mid, "type": "mobile-devices", "attributes": attrs}}


# --- pure read/ack stubs: authenticated, fixed answer (None = 204) ---------------------
# The shapes the original app's DataManager expects, so it navigates without a 404.
STUBS = [
    ("GET", "/api/user-options", {"pro_positions": [], "organization_state": [],
                                  "organization_type": []}),
    ("GET", "/api/content-preferences", {"data": []}),
    ("POST", "/api/children/{cid}/resend-email", None),
    ("GET", "/api/children/{cid}/rewards", {"data": {"badges": [], "missions": [],
                                                    "rewards-choices": []}}),
    ("GET", "/api/children/{cid}/sensitive-conversations/list", {"data": []}),
    ("POST", "/api/children/{cid}/sensitive-conversations/schedule", None),
    ("POST", "/api/children/{cid}/sensitive-conversations/unschedule", None),
    ("GET", "/api/child-family-members", {"data": []}),
    ("GET", "/api/notifications", {"data": [], "meta": {"unread": 0}}),
    ("POST", "/api/notifications/{nid}/{archive}", None),
    ("GET", "/api/calendar-holidays", {"data": []}),
    ("GET", "/api/help", {"data": [], "encrypted_auids": []}),
    ("POST", "/api/help/pronounce", None),
    ("POST", "/api/help/share-auid", None),
    ("GET", "/api/language-support", {"data": {
        "input_languages": [{"id": "en-US", "name": "English (US)"}],
        "output_languages": [{"id": "en-US", "name": "English (US)"}],
        "output_voices": [{"id": "moxie-default", "name": "Moxie"}]}}),
    ("GET", "/api/network-tests", {"data": {"download_url": None, "upload_url": None,
                                            "ping_host": None}}),
    ("POST", "/api/network-tests", None),
    ("GET", "/api/analytics/pages/details", {"data": {"pages": []}}),
    ("GET", "/api/analytics/pages/insights", {"data": {"pages": []}}),
    ("PUT", "/api/teletherapy/patient-status", None),
    ("POST", "/api/teletherapy/therapists-list", {"data": []}),
    ("POST", "/api/teletherapy/request-access-moxie", None),
]


def _stub(answer):
    def endpoint(u=Depends(current_user)):
        return Response(status_code=NO_CONTENT) if answer is None else answer
    return endpoint


for _method, _path, _answer in STUBS:
    router.add_api_route(_path, _stub(_answer), methods=[_method])


# Parameterized GETs last, so `/api/analytics/pages/details` is not taken for a page id.
@router.get("/api/notifications/{nid}")
def notification(nid: str, u=Depends(current_user)):
    return {"data": {"id": nid, "type": "notifications", "attributes": {}}}


@router.get("/api/help/{path}")
def help_path(path: str, u=Depends(current_user)):
    return {"data": [], "path": path}


@router.get("/api/analytics/pages/{page_id}")
def analytics_page(page_id: str, u=Depends(current_user)):
    return {"data": {"id": page_id, "pages": []}}
