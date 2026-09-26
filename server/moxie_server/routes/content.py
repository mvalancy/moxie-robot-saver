"""`/local/content*` — 📦 content packs and ✍️ authoring, proxied to the supervisor.

Fleet-level (content belongs to the appliance, not one robot). A refusal keeps its code:
409 when the file changed between review and import (or a stale `local_rev`), 413 too
big, 400 unreadable. Nothing is validated here on purpose: `validate_item` belongs to
the supervisor route that WRITES, so a direct `curl` at the supervisor cannot skip it,
and a second check in this proxy would be a second validation path.
"""
from __future__ import annotations
import json
from urllib.parse import urlencode

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from .. import fleet, supervisor as sv

router = APIRouter()
TIMEOUT_S = 15          # a pack is up to 1 MiB of JSON and the review diffs it


async def _post(path: str, normalize, request: Request):
    return sv.proxy("POST", "/content" + path, normalize,
                    data=await request.body() or b"{}", timeout=TIMEOUT_S)


@router.get("/local/content")
def content_view():
    """Every installed item, its version and pack, whether edited here, undo armed."""
    return sv.proxy("GET", "/content", fleet.normalize_content_view, data=None, timeout=5)


@router.get("/local/content/export")
def content_export(items: str = "", name: str = "", id: str = "", details: str = "",
                   author: str = ""):
    """One pack file from the ticked items, as a download (`curl -OJ` works too)."""
    query = urlencode({"items": items, "name": name, "id": id, "details": details,
                       "author": author})
    out, code = sv.call("GET", "/content/export?" + query, timeout=TIMEOUT_S)
    if code != 200:
        return JSONResponse(status_code=code, content=fleet.normalize_content_result(out))
    filename = (out.get("id") or "moxie-content") + ".moxiepack.json"
    return Response(content=json.dumps(out, indent=2, ensure_ascii=False) + "\n",
                    media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.post("/local/content/review")
async def content_review(request: Request):
    """What WOULD change, per item, with the field-level diff. Writes nothing."""
    return await _post("/review", fleet.normalize_content_review, request)


@router.post("/local/content/import")
async def content_import(request: Request):
    """Install the accepted items — `{"pack", "accept": [...], "expect_digest"}`."""
    return await _post("/import", fleet.normalize_content_result, request)


@router.post("/local/content/item")
async def content_item(request: Request):
    """Save one authored item (the card's ✏️ / ＋ New); snapshots into the undo slot."""
    return await _post("/item", fleet.normalize_content_item_result, request)


@router.post("/local/content/render")
async def content_render(request: Request):
    """Resolve a draft prompt against a sample context — no model call, no write."""
    return await _post("/render", fleet.normalize_content_render, request)


@router.post("/local/content/undo")
def content_undo():
    """Put back what the last import replaced."""
    return sv.proxy("POST", "/content/undo", fleet.normalize_content_result, data=b"{}",
                    timeout=TIMEOUT_S)
