"""No hostname belonging to a specific deployment may be a default in shipped code.

A fallback `MOXIE_LLM_BASE_URL` pointing at the maintainer's gateway once sent every
stranger's clone at someone else's server — in `mqtt/config.py`, BOTH compose files and
`.env.example`. So: Python string literals (docstrings excluded), JS with comments
stripped, and config VALUES (not comments) may name only this machine, a single-label
compose service, RFC 2606/6761 names, or an `ALLOWED_HOSTS` entry with its reason.
"""
import ast
import os
import re

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

# --------------------------------------------------------------------------- policy --

#: A host that is nowhere in particular; anything else is somebody's deployment.
_NOWHERE = re.compile(
    r"^(?:127\.\d+\.\d+\.\d+|0\.0\.0\.0|\[::1\]|::1"
    r"|[A-Za-z0-9_-]+"                                   # single label: a compose service
    r"|(?:[A-Za-z0-9-]+\.)*(?:example|invalid|test|localhost|local)"
    r"|(?:[A-Za-z0-9-]+\.)*example\.(?:com|net|org)"
    r"|host\.docker\.internal)$", re.IGNORECASE)

#: Public endpoints this repo legitimately uses, each with its reason.
ALLOWED_HOSTS = {
    "huggingface.co": "the public Piper voice registry (a model download, like PyPI)",
    "challenges.cloudflare.com": "Turnstile's widget + siteverify: a platform endpoint, the "
                                 "same for every user, and valid only from this host",
}

_URL = re.compile(r"https?://([A-Za-z0-9_.\-]+)")


def offenders(text) -> list:
    """Deployment hostnames in `text` — `[]` when it names nobody."""
    out = []
    for host in _URL.findall(str(text)):
        if host in ALLOWED_HOSTS or _NOWHERE.match(host):
            continue
        out.append(host)
    return out


# ------------------------------------------------------------------------- scanners --

def python_literals(path: str) -> list:
    """Every string literal in a Python file except docstrings (ast drops comments)."""
    tree = ast.parse(open(path, encoding="utf-8").read(), path)
    docs = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)) and body:
            first = body[0]
            if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                docs.add(id(first.value))
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in docs]


def strip_js_comments(src: str) -> str:
    """`//` and `/* */` removed, string/template literals intact (a regex cannot tell
    `"http://x"` from `// http://x`)."""
    out, i, n, quote = [], 0, len(src), ""
    while i < n:
        c, nxt = src[i], src[i + 1] if i + 1 < n else ""
        if quote:
            if c == "\\":
                out.append(src[i:i + 2]); i += 2; continue
            out.append(c)
            if c == quote:
                quote = ""
            i += 1
            continue
        if c == "/" and nxt == "/":
            while i < n and src[i] != "\n":
                i += 1
            continue
        if c == "/" and nxt == "*":
            i += 2
            while i + 1 < n and not (src[i] == "*" and src[i + 1] == "/"):
                i += 1
            i += 2
            continue
        if c in "\"'`":
            quote = c
        out.append(c)
        i += 1
    return "".join(out)


#: A `#` that starts a trailing comment: at the start of the value, or after whitespace.
#: `https://host/v1#frag` is therefore still a value, `v1  # why` is not.
_TRAILING_COMMENT = re.compile(r"(?:^|\s)#.*$")


def config_values(text: str) -> list:
    """The VALUES (incl. `${VAR:-default}`) of a dotenv/compose file; comments dropped."""
    values = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        for sep in ("=", ":"):
            if sep in line:
                values.append(_TRAILING_COMMENT.sub("", line.split(sep, 1)[1]).strip())
                break
    return values


#: Never OUR shipped code (build/venv dirs are full of third-party URLs).
NOT_OUR_CODE = ("node_modules", "__pycache__", "docs-bundle", "vendor",
                ".venv", "site-packages", "build", "dist", ".eggs")


def _walk(root: str, suffixes, skip_tests=True):
    base = os.path.join(REPO, root)
    for dirpath, dirnames, filenames in os.walk(base):
        rel = os.path.relpath(dirpath, REPO)
        dirnames[:] = [d for d in dirnames
                       if d not in NOT_OUR_CODE and not d.endswith(".egg-info")]
        if any(part in rel.split(os.sep) for part in NOT_OUR_CODE):
            continue
        if skip_tests and (rel.endswith("tests") or os.sep + "tests" + os.sep in rel + os.sep):
            continue
        for name in sorted(filenames):
            if skip_tests and (name.startswith("test_") or name.startswith("helpers_")):
                continue
            if name.endswith(suffixes):
                yield os.path.join(dirpath, name)


def shipped_python():
    for root in ("mqtt", "server", "scripts", "tools", "sim"):
        yield from _walk(root, (".py",))


def shipped_js():
    for root in ("functions", os.path.join("sim", "web")):
        yield from _walk(root, (".js", ".mjs"))


CONFIG_FILES = ("docker-compose.yml", "docker-compose.images.yml", ".env.example",
                os.path.join("mqtt", ".env.example"),
                os.path.join("sim", "compose-smoke.env"))


# ---------------------------------------------------------------------- the guard ----

def test_no_shipped_python_defaults_to_a_deployment():
    bad = {}
    for path in shipped_python():
        for literal in python_literals(path):
            for host in offenders(literal):
                bad.setdefault(os.path.relpath(path, REPO), set()).add(host)
    assert bad == {}, (
        "shipped Python names somebody's deployment outside a docstring — read it from "
        "the environment with no default instead:\n  "
        + "\n  ".join(f"{f}: {sorted(h)}" for f, h in sorted(bad.items())))


def test_no_shipped_js_defaults_to_a_deployment():
    bad = {}
    for path in shipped_js():
        for host in offenders(strip_js_comments(open(path, encoding="utf-8").read())):
            bad.setdefault(os.path.relpath(path, REPO), set()).add(host)
    assert bad == {}, (
        "shipped JavaScript names somebody's deployment outside a comment:\n  "
        + "\n  ".join(f"{f}: {sorted(h)}" for f, h in sorted(bad.items())))


def test_no_shipped_configuration_defaults_to_a_deployment():
    bad = {}
    for rel in CONFIG_FILES:
        path = os.path.join(REPO, rel)
        if not os.path.exists(path):
            continue
        for value in config_values(open(path, encoding="utf-8").read()):
            for host in offenders(value):
                bad.setdefault(rel, set()).add(host)
    assert bad == {}, (
        "a shipped configuration VALUE names somebody's deployment (a comment may; a "
        "value may not):\n  "
        + "\n  ".join(f"{f}: {sorted(h)}" for f, h in sorted(bad.items())))


def test_the_scanners_actually_scanned_something():
    py = [os.path.relpath(p, REPO) for p in shipped_python()]
    js = [os.path.relpath(p, REPO) for p in shipped_js()]
    assert len(py) > 40, py
    assert len(js) > 5, js
    assert os.path.join("mqtt", "config.py") in py
    assert os.path.join("functions", "api", "_lib", "env.js") in js


# --------------------------------------------------------- negative controls ---------
_OURS = "https://gateway.graphlings.net/v1"


def test_the_python_scanner_bites_on_a_literal_and_exempts_prose(tmp_path):
    f = tmp_path / "planted.py"
    f.write_text(f'"""A docstring may say {_OURS}."""\n# so may a comment: {_OURS}\n'
                 f'URL = os.environ.get("X", "{_OURS}")\n')
    assert [h for lit in python_literals(str(f)) for h in offenders(lit)] == \
        ["gateway.graphlings.net"]


@pytest.mark.parametrize("src, expected", [
    (f"/* never hard-code {_OURS} */\n// nor it\nconst B = env.X || \"{_OURS}\";",
     ["gateway.graphlings.net"]),
    ('const u = "http://supervisor:8931/status"; // a // inside a string is not a comment',
     []),
    ('const u = "https://evil.example.org/"; const v = "http://deploy.acme.io/x";',
     ["deploy.acme.io"]),
])
def test_the_js_scanner_strips_comments_but_not_strings(src, expected):
    assert offenders(strip_js_comments(src)) == expected


@pytest.mark.parametrize("line,expected", [
    (f"MOXIE_LLM_BASE_URL={_OURS}", ["gateway.graphlings.net"]),
    (f"      MOXIE_LLM_BASE_URL: ${{MOXIE_LLM_BASE_URL:-{_OURS}}}", ["gateway.graphlings.net"]),
    (f"# set it to {_OURS} if you want ours", []),
    (f"MOXIE_VOICE_BASE_URL=          # e.g. {_OURS}", []),
    (f"MOXIE_LLM_BASE_URL={_OURS}  # ours", ["gateway.graphlings.net"]),
    ("      MOXIE_SUPERVISOR_STATUS: http://supervisor:8931/status", []),
    ("MOXIE_LLM_BASE_URL=http://127.0.0.1:11434/v1", []),
    ("MOXIE_LLM_BASE_URL=https://your-gateway.example/v1", []),
])
def test_the_configuration_scanner_bites(line, expected):
    assert [h for v in config_values(line) for h in offenders(v)] == expected
