"""The console's `api()` helper turns a refusal into a sentence, not a JSON dump.

Every `/local/*` route keeps the supervisor's status code (400 with `reason`, 404 for an
unknown robot), and `api()` throws on any non-2xx. The cards print `e.message`, so the
thrown message must be the refusal's `reason` (else `error`) — not the raw payload.
Runs `api()` itself, lifted out of `server/static/js/core.js`, under node with a fake
`fetch`.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CORE = os.path.join(REPO, "server", "static", "js", "core.js")


def _api_source() -> str:
    src = open(CORE).read()
    m = re.search(r"^async function api\(.*?^}\n", src, re.S | re.M)
    assert m, "api() vanished from core.js"
    return m.group(0)


def _thrown(status: int, body: str) -> str:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    script = (
        "let TOKEN=null;\n" + _api_source() +
        f"globalThis.fetch=async()=>({{ok:false,status:{status},"
        f"text:async()=>{json.dumps(body)},headers:{{get:()=>'application/json'}}}});\n"
        "api('/x',{method:'POST',body:{}}).then(()=>console.log('NO THROW'),"
        "e=>console.log(e.message));\n")
    out = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


def test_a_refusal_shows_its_reason():
    body = json.dumps({"ok": False, "error": "not offered", "reason": "Pick another voice."})
    assert _thrown(400, body) == "Pick another voice."


def test_a_refusal_without_a_reason_shows_its_error():
    assert _thrown(404, json.dumps({"ok": False, "error": "unknown device_id 'd_x'"})) \
        == "unknown device_id 'd_x'"


def test_a_non_json_refusal_shows_its_text_and_an_empty_one_its_status():
    assert _thrown(502, "Bad Gateway") == "Bad Gateway"
    assert _thrown(500, "") == "500"
