"""Bearer-token auth + the small request helpers every router shares."""
from __future__ import annotations
import secrets
from typing import Optional

from fastapi import Header, HTTPException, Request

from . import db

TOKEN_TTL_S = 7200


def current_user(authorization: Optional[str] = Header(None)):
    """FastAPI dependency: the user behind `Authorization: Bearer <token>`, else 401."""
    if not authorization:
        raise HTTPException(401, "missing token")
    u = db.user_by_token(authorization.split(" ", 1)[-1].strip())
    if not u:
        raise HTTPException(401, "invalid token")
    return u


def mint_tokens(user_id: str) -> dict:
    at, rt = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    created = db.now_s()
    db.ex("INSERT INTO tokens(access_token,refresh_token,user_id,token_type,scope,created_at,expires_in)"
          " VALUES(?,?,?,?,?,?,?)", (at, rt, user_id, "Bearer", "openid", created, TOKEN_TTL_S))
    return {"access_token": at, "token_type": "Bearer", "expires_in": TOKEN_TTL_S,
            "refresh_token": rt, "scope": "openid", "created_at": created}


async def read_json(request: Request) -> dict:
    """The request body as a dict; `{}` for an empty or non-JSON body."""
    try:
        body = await request.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


def digits(n: int = 6) -> str:
    return "".join(secrets.choice("0123456789") for _ in range(n))
