"""Shared builders for the content-pack and authoring suites (`test_content*.py`)."""
import json
import urllib.error

from moxie_sdk.content import packs as P

FREE_CHAT = "conversation:FREE_CHAT/default"


def boot_runtime(store_dir, shipped_json, chat):
    """`(rt, device_id)`: a real `MoxieRuntime` over a real `ContentApp`, booted the way
    `config.build_content_app()` boots (shipped defaults, then the overlay on disk) — so
    two calls over one `store_dir` are a faithful restart."""
    from helpers_runtime import make_runtime
    from moxie_sdk.content import ContentApp
    from moxie_sdk.store import JsonStore

    store = JsonStore(str(store_dir))
    shipped = P.shipped_items(shipped_json)
    stored = store.read_shared("content_items", {}) or {}
    overlay = stored.get("items") if isinstance(stored, dict) else None
    app = ContentApp(P.build_module(shipped, overlay if isinstance(overlay, dict) else {}),
                     chat, memory=False, content_defaults=shipped)
    return make_runtime(app, store=store)


def free_chat_pack(prompt, *, version=2, opener="Hi!", name="A pack", pack_id="a-pack",
                   author="", now=1788400000, **data) -> dict:
    """A one-item pack replacing `FREE_CHAT/default`, built the way an exporter builds one."""
    item = {"kind": "conversation", "key": "FREE_CHAT/default", "source_version": version,
            "data": dict({"name": "Free Chat", "module_id": "FREE_CHAT",
                          "content_id": "default", "prompt": prompt, "opener": opener},
                         **data)}
    return P.export_pack([item], name=name, pack_id=pack_id, author=author, now=now)


def post_status(base, path, body=None, *, method="POST"):
    """`(status, payload)` — `http_json` raises on 4xx/5xx; a refusal is a value here."""
    from helpers_runtime import http_json
    try:
        return 200, http_json(base + path, method=method, body={} if body is None else body)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def recording_brain(reply="Sure!"):
    """`(seen, chat)`: a brain that records the system prompt it was handed in
    `seen["system"]` and answers `reply`."""
    seen = {}

    def chat(messages):
        seen["system"] = messages[0]["content"]
        return reply
    return seen, chat
