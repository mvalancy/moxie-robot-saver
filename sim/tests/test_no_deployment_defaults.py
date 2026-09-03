"""
THE CLASS: no hostname belonging to a specific deployment may be a default in shipped code.

`mqtt/config.py` shipped `https://<the maintainer's gateway>/v1` as the fallback for
`MOXIE_LLM_BASE_URL`, so a stranger who cloned this public repo got a supervisor pointed
at someone else's server. Fixing that one line fixes one line. This file forbids the
*shape* of it anywhere in the code and the configuration this repo ships:

  * Python — every string literal that is not a docstring, in `mqtt/`, `server/`,
    `scripts/`, `tools/` and the non-test parts of `sim/`;
  * JavaScript — every line of `functions/` and `sim/web/` with comments removed
    (`sim/web/vendor/` excluded: those are third-party bundles we do not author);
  * the two compose files and `.env.example` — VALUES and `${VAR:-default}` defaults only,
    which is where the same defect lived a second and third time.

**Comments and docstrings are stripped before scanning** (playbook rule 17: a guard that
matches over a whole file fires on the prose explaining it — and the prose here has to be
free to name the gateway it is warning about, as the docstring you are reading does).

What is allowed: this machine (`127.0.0.1`, `localhost`, `0.0.0.0`,
`host.docker.internal`), a single-label name with no dot (a compose service such as
`http://supervisor:8931/status` cannot be a public DNS name), the reserved-for-docs
suffixes of RFC 2606/6761 (`*.example`, `example.com`, `*.invalid`, `*.test`), and one
named exception with its reason: `huggingface.co`, the public model registry the Piper
voice is downloaded from — a registry anyone can fetch from, in the same class as PyPI,
not anybody's appliance.

The negative controls at the bottom plant each violation and require the checker to find
it, and `test_the_scanners_actually_scanned_something` fails if the file lists ever come
back empty — a guard that scans nothing passes forever.
"""
import ast
import os
import re

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

# --------------------------------------------------------------------------- policy --

#: A host that is nowhere in particular. Anything else is somebody's deployment.
_NOWHERE = re.compile(
    r"^(?:127\.\d+\.\d+\.\d+|0\.0\.0\.0|\[::1\]|::1"
    r"|[A-Za-z0-9_-]+"                                   # single label: a compose service
    r"|(?:[A-Za-z0-9-]+\.)*(?:example|invalid|test|localhost|local)"
    r"|(?:[A-Za-z0-9-]+\.)*example\.(?:com|net|org)"
    r"|host\.docker\.internal)$", re.IGNORECASE)

#: Public registries this repo legitimately fetches from, each with its reason. A new
#: entry here is a decision someone has to argue for in review, which is the point.
ALLOWED_HOSTS = {
    "huggingface.co": "the public Piper voice registry (a model download, like PyPI)",
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
    """Every string literal in a Python file EXCEPT docstrings.

    `ast` drops `#` comments for free, and the docstring pass drops the prose. What is
    left is the code's own data — where a default endpoint would actually live.
    """
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
    """`//` and `/* */` removed, string and template literals left intact.

    A character-level pass rather than a regex, because `"http://x"` inside a string and
    `// http://x` in a comment are the same characters to a regex and opposite things to
    this guard.
    """
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
    """The right-hand sides of a dotenv / compose-`environment:` file, comments dropped.

    A `# note` — whole-line or trailing — is documentation and may name whatever it needs
    to (`mqtt/.env.example` documents our own gateway that way, beside an EMPTY value). A
    VALUE, including a `${VAR:-default}` default, is what a deployment actually runs with,
    and that is what this guard is about.

    (Trailing comments are dropped for SCANNING only. Whether the readers of these files
    also drop them is a different question with a different answer per file — docker
    compose does not, which is why `test_compose.py::test_env_example_has_no_trailing_comments`
    forbids them in the root `.env.example` — and it is not this guard's business.)
    """
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


def _walk(root: str, suffixes, skip_tests=True):
    base = os.path.join(REPO, root)
    for dirpath, dirnames, filenames in os.walk(base):
        rel = os.path.relpath(dirpath, REPO)
        if any(part in rel.split(os.sep) for part in ("node_modules", "__pycache__",
                                                      "docs-bundle", "vendor")):
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
    """The same defect lived three times: `mqtt/config.py`, and the `${MOXIE_LLM_BASE_URL:-…}`
    default in BOTH compose files, and the value in `.env.example`. Fixing only the Python
    would have left `docker compose up` pointed at the same server."""
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
    """A guard that walked an empty tree would be green forever. Names the file that
    started all this, so a rename cannot quietly empty the sweep."""
    py = [os.path.relpath(p, REPO) for p in shipped_python()]
    js = [os.path.relpath(p, REPO) for p in shipped_js()]
    assert len(py) > 40, py
    assert len(js) > 5, js
    assert os.path.join("mqtt", "config.py") in py
    assert os.path.join("functions", "api", "_lib", "env.js") in js


# --------------------------------------------------------- NEGATIVE CONTROLS ---------
# Each plants the violation and requires the checker to report it. Without these the
# three tests above are indistinguishable from three tests that always pass.

def test_the_python_scanner_bites(tmp_path):
    f = tmp_path / "planted.py"
    f.write_text('"""A docstring may say https://gateway.graphlings.net/v1 freely."""\n'
                 '# and so may a comment: https://moxie.mattvalancy.com\n'
                 'URL = os.environ.get("X", "https://gateway.graphlings.net/v1")\n')
    hosts = [h for lit in python_literals(str(f)) for h in offenders(lit)]
    assert hosts == ["gateway.graphlings.net"], hosts


def test_the_python_scanner_exempts_prose(tmp_path):
    """Rule 17's half: the docstring and the comment above must NOT have fired."""
    f = tmp_path / "prose.py"
    f.write_text('"""Never default to https://gateway.graphlings.net/v1."""\n'
                 '# see https://moxie.mattvalancy.com for why\n'
                 'URL = os.environ.get("MOXIE_LLM_BASE_URL", "")\n')
    assert [h for lit in python_literals(str(f)) for h in offenders(lit)] == []


def test_the_js_scanner_bites():
    src = ('/* C3: never hard-code https://gateway.graphlings.net/v1 */\n'
           '// nor https://moxie.mattvalancy.com\n'
           'const B = env.DEMO_GATEWAY_BASE_URL || "https://gateway.graphlings.net/v1";\n')
    assert offenders(strip_js_comments(src)) == ["gateway.graphlings.net"]


def test_the_js_scanner_keeps_a_url_that_lives_inside_a_string():
    """The stripper must not treat `//` inside `"http://…"` as the start of a comment —
    that would silently blind the guard to every URL it exists to catch."""
    assert "http://supervisor:8931" in strip_js_comments('const u = "http://supervisor:8931/status";')


@pytest.mark.parametrize("line,expected", [
    ("MOXIE_LLM_BASE_URL=https://gateway.graphlings.net/v1", ["gateway.graphlings.net"]),
    ("      MOXIE_LLM_BASE_URL: ${MOXIE_LLM_BASE_URL:-https://gateway.graphlings.net/v1}",
     ["gateway.graphlings.net"]),
    ("# set it to https://gateway.graphlings.net/v1 if you want ours", []),
    ("MOXIE_VOICE_BASE_URL=          # e.g. https://gateway.graphlings.net/v1", []),
    ("MOXIE_LLM_BASE_URL=https://gateway.graphlings.net/v1  # ours", ["gateway.graphlings.net"]),
    ("MOXIE_LLM_BASE_URL=", []),
    ("      MOXIE_SUPERVISOR_STATUS: http://supervisor:8931/status", []),
    ("MOXIE_LLM_BASE_URL=http://127.0.0.1:11434/v1", []),
    ("MOXIE_LLM_BASE_URL=https://your-gateway.example/v1", []),
])
def test_the_configuration_scanner_bites(line, expected):
    assert [h for v in config_values(line) for h in offenders(v)] == expected


def test_the_allowlist_is_not_a_loophole():
    """Every exemption carries a reason, and none of them is a deployment of ours."""
    assert ALLOWED_HOSTS and all(ALLOWED_HOSTS.values())
    for host in ALLOWED_HOSTS:
        assert not host.endswith(("graphlings.net", "mattvalancy.com")), host
