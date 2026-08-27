"""
The rest of the parent-app REST surface (docs/reverse-engineering/rest-api.md §3),
implemented faithfully so the original APK — or our own future UI — can navigate the
whole app without hitting missing endpoints. Most are read stubs returning the shapes
the app expects; a few (GRL, mobile-devices, change-email) carry light state.
"""
from __future__ import annotations
import json, secrets, time
from fastapi import Header, Request, Response, HTTPException

from . import db



def _tok(authorization):
    if not authorization:
        raise HTTPException(401, "missing token")
    tok = authorization.split(" ", 1)[-1].strip()
    u = db.user_by_token(tok)
    if not u:
        raise HTTPException(401, "invalid token")
    return u


async def _json(request):
    try:
        return await request.json()
    except Exception:
        return {}



def register(app):
    # ---- user: change email ----
    @app.post("/api/users/me/change-email-request")
    async def change_email_request(request: Request, authorization: str = Header(None)):
        _tok(authorization)
        code = "".join(secrets.choice("0123456789") for _ in range(6))
        print(f"\n[CHANGE-EMAIL CODE] {code}\n")
        return {"code": code, "code_length": 6, "message": "verification code sent"}


    @app.post("/api/users/me/change-email")
    async def change_email(request: Request, authorization: str = Header(None)):
        u = _tok(authorization)
        body = await _json(request)
        if body.get("new_email"):
            db.update_user_attrs(u["id"], {"email": body["new_email"]})
        return Response(status_code=204)


    # ---- children extras ----
    @app.post("/api/children/{cid}/resend-email")
    def resend_email(cid: str, authorization: str = Header(None)):
        _tok(authorization); return Response(status_code=204)


    @app.get("/api/children/{cid}/rewards")
    def child_rewards(cid: str, authorization: str = Header(None)):
        _tok(authorization); return {"data": {"badges": [], "missions": [], "rewards-choices": []}}


    @app.get("/api/children/{cid}/sensitive-conversations/list")
    def sensitive_list(cid: str, authorization: str = Header(None)):
        _tok(authorization); return {"data": []}


    @app.post("/api/children/{cid}/sensitive-conversations/schedule")
    async def sensitive_schedule(cid: str, request: Request, authorization: str = Header(None)):
        _tok(authorization); return Response(status_code=204)


    @app.post("/api/children/{cid}/sensitive-conversations/unschedule")
    async def sensitive_unschedule(cid: str, request: Request, authorization: str = Header(None)):
        _tok(authorization); return Response(status_code=204)


    @app.get("/api/child-family-members")
    def family_members(authorization: str = Header(None)):
        _tok(authorization); return {"data": []}


    # ---- GRL (guest / remote login codes) ----
    @app.post("/api/grl/code")
    async def grl_code(request: Request, authorization: str = Header(None)):
        u = _tok(authorization)
        code = "".join(secrets.choice("0123456789") for _ in range(6))
        db.update_user_attrs(u["id"], {"last-grl-code": code, "grl-code-status": "unused"})
        return {"data": {"grl_code": code, "expires_at": db.now_s() + 3600}}


    @app.post("/api/grl/revoke-all")
    def grl_revoke(authorization: str = Header(None)):
        u = _tok(authorization)
        db.update_user_attrs(u["id"], {"grl-code-status": "expired"})
        return Response(status_code=204)


    # ---- mobile devices (push registration) ----
    @app.post("/api/mobile-devices")
    async def create_mobile_device(request: Request, authorization: str = Header(None)):
        u = _tok(authorization)
        body = await _json(request)
        attrs = body.get("mobile-device", body)
        mid = attrs.get("mobile-device-id") or db.new_id()
        db.ex("INSERT OR REPLACE INTO mobile_devices(id,user_id,attributes) VALUES(?,?,?)",
              (mid, u["id"], json.dumps(attrs)))
        return {"data": {"id": mid, "type": "mobile-devices", "attributes": attrs}}


    @app.put("/api/mobile-devices/{mid}")
    async def update_mobile_device(mid: str, request: Request, authorization: str = Header(None)):
        u = _tok(authorization)
        body = await _json(request)
        attrs = body.get("mobile-device", body)
        db.ex("INSERT OR REPLACE INTO mobile_devices(id,user_id,attributes) VALUES(?,?,?)",
              (mid, u["id"], json.dumps(attrs)))
        return {"data": {"id": mid, "type": "mobile-devices", "attributes": attrs}}


    # ---- notifications ----
    @app.get("/api/notifications")
    def notifications(authorization: str = Header(None)):
        _tok(authorization); return {"data": [], "meta": {"unread": 0}}


    @app.get("/api/notifications/{nid}")
    def notification(nid: str, authorization: str = Header(None)):
        _tok(authorization); return {"data": {"id": nid, "type": "notifications", "attributes": {}}}


    @app.post("/api/notifications/{nid}/{archive}")
    def notification_archive(nid: str, archive: str, authorization: str = Header(None)):
        _tok(authorization); return Response(status_code=204)


    # ---- content / help ----
    @app.get("/api/calendar-holidays")
    def calendar_holidays(authorization: str = Header(None)):
        _tok(authorization); return {"data": []}


    @app.get("/api/help")
    def help_root(authorization: str = Header(None)):
        _tok(authorization); return {"data": [], "encrypted_auids": []}


    @app.get("/api/help/{path}")
    def help_path(path: str, authorization: str = Header(None)):
        _tok(authorization); return {"data": [], "path": path}


    @app.post("/api/help/pronounce")
    async def help_pronounce(request: Request, authorization: str = Header(None)):
        _tok(authorization); return Response(status_code=204)


    @app.post("/api/help/share-auid")
    async def help_share_auid(request: Request, authorization: str = Header(None)):
        _tok(authorization); return Response(status_code=204)


    # ---- language support (drives robots/{id}/set-language) ----
    @app.get("/api/language-support")
    def language_support(authorization: str = Header(None)):
        _tok(authorization)
        return {"data": {
            "input_languages": [{"id": "en-US", "name": "English (US)"}],
            "output_languages": [{"id": "en-US", "name": "English (US)"}],
            "output_voices": [{"id": "moxie-default", "name": "Moxie"}],
        }}


    # ---- network speed tests ----
    @app.get("/api/network-tests")
    def network_tests_get(authorization: str = Header(None)):
        _tok(authorization)
        return {"data": {"download_url": None, "upload_url": None, "ping_host": None}}


    @app.post("/api/network-tests")
    async def network_tests_post(request: Request, authorization: str = Header(None)):
        _tok(authorization); return Response(status_code=204)


    # ---- analytics / insights (populated once the MQTT layer feeds activity data) ----
    @app.get("/api/analytics/pages/details")
    def analytics_details(authorization: str = Header(None)):
        _tok(authorization); return {"data": {"pages": []}}


    @app.get("/api/analytics/pages/insights")
    def analytics_insights(authorization: str = Header(None)):
        _tok(authorization); return {"data": {"pages": []}}


    @app.get("/api/analytics/pages/{page_id}")
    def analytics_page(page_id: str, authorization: str = Header(None)):
        _tok(authorization); return {"data": {"id": page_id, "pages": []}}


    # ---- teletherapy / clinician ----
    @app.put("/api/teletherapy/patient-status")
    async def teletherapy_status(request: Request, authorization: str = Header(None)):
        _tok(authorization); return Response(status_code=204)


    @app.post("/api/teletherapy/therapists-list")
    async def teletherapy_therapists(request: Request, authorization: str = Header(None)):
        _tok(authorization); return {"data": []}


    @app.post("/api/teletherapy/request-access-moxie")
    async def teletherapy_request(request: Request, authorization: str = Header(None)):
        _tok(authorization); return Response(status_code=204)

