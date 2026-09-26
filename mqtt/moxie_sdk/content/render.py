"""
Prompt rendering — a content module's `prompt` is Jinja2-templated over the
volley/session (docs/architecture/content-module-contract.md).

* **With jinja2** (the shipped container): a counting `SandboxedEnvironment`.
* **Without jinja2** (a bare `pip install moxie-cloud-sdk`; jinja2 is the `content`
  extra): `_minimal_render`, dependency-free.

The fallback's hard rule: **nothing template-shaped may reach the brain** — the output is
a system prompt. It resolves what it can, removes the rest, and counts each removal in
`STRIPPED`.
"""
from __future__ import annotations
import re

# ---------------------------------------------------------------- the grammar --
#: A bare dotted path — the one expression form the fallback can evaluate.
_PATH = re.compile(r"^[a-zA-Z_][\w.]*$")

#: An `{% if %}`/`{% elif %}` condition the fallback can decide: `path` or `not path`.
_COND = re.compile(r"^(?P<neg>not\s+)?(?P<path>[a-zA-Z_][\w.]*)$")

#: Jinja2's boolean/none literals — they parse as names under `_COND`, but jinja2 treats
#: them as constants (pinned by the differential test in test_render_fallback.py).
_LITERALS = {"true": True, "True": True, "false": False, "False": False,
             "none": False, "None": False}

#: `{% raw %}…{% endraw %}`, removed first: a raw body is verbatim template syntax, and
#: its contents are not tags.
_RAW_BLOCK = re.compile(r"\{%-?\s*raw\s*-?%\}.*?\{%-?\s*endraw\s*-?%\}", re.S)

#: One expression, statement or comment. Non-greedy, DOTALL: a block tag may wrap lines.
_TOKEN = re.compile(r"\{\{(?P<var>.*?)\}\}"
                    r"|\{%(?P<tag>.*?)%\}"
                    r"|\{#(?P<comment>.*?)#\}", re.S)

#: Delimiter debris left by an unterminated construct, swept last so the
#: no-template-syntax guarantee is total.
_STRAY = re.compile(r"\{\{-?|-?\}\}|\{%-?|-?%\}|\{#-?|-?#\}")


def _resolve(path: str, context: dict):
    """Walk a dotted path over dicts/objects; missing → ''.

    **A segment beginning with `_` is refused** (counted in `BLOCKED`) — a security
    boundary, the fallback's half of the sandbox's `is_safe_attribute`. A prompt is
    untrusted pack input and this is `getattr` over live objects, so without the guard
    `{{ session.__class__.__repr__.__globals__…os.environ }}` leaks the environment
    (test_content_pack_sandbox.py). The documented grammar never needs a private name.
    """
    global BLOCKED
    cur = context
    for part in path.split("."):
        if part.startswith("_"):
            BLOCKED += 1
            return ""
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            cur = getattr(cur, part, None)
        if cur is None:
            return ""
    return cur


def _condition(cond: str, context: dict):
    """`(truth, decided)` for an `{% if %}`/`{% elif %}` condition. Anything richer than
    `path`/`not path`/a literal is undecided, and the caller treats it as false (as jinja2
    treats an undefined name)."""
    m = _COND.match(cond.strip())
    if not m:
        return False, False
    path = m.group("path")
    val = _LITERALS[path] if path in _LITERALS else _resolve(path, context)
    return ((not val) if m.group("neg") else bool(val)), True


#: Incremented whenever a renderer refuses an attribute (the jinja2 sandbox, or a
#: `_`-segment in the fallback). Answers "did somebody try?" — a pack that trips it is
#: broken or hostile; the output is safe either way.
BLOCKED = 0

#: STRIPPED: incremented per construct the dependency-free fallback removed because it
#: could not evaluate it. Non-zero means this process lacks jinja2 but renders content
#: that needs it (`pip install moxie-cloud-sdk[content]`). Comments are not counted.
STRIPPED = 0


def _tally(counts: dict, fn, template: str, context: dict) -> str:
    """Run `fn(template, context)` and add its `BLOCKED`/`STRIPPED` delta to `counts`.

    Advisory: the counters are process-global and concurrent turns move them too, and a
    lock is forbidden (content-authoring.md §5.1) — hence `counts_advisory: true`.
    """
    before = (BLOCKED, STRIPPED)
    try:
        return fn(template, context)
    finally:
        counts["blocked"] = counts.get("blocked", 0) + BLOCKED - before[0]
        counts["stripped"] = counts.get("stripped", 0) + STRIPPED - before[1]


def _minimal_render(template: str, context: dict, counts: dict = None) -> str:
    """Render `template` with no jinja2 installed, emitting **no template syntax**.

    A single-pass scanner, not a second engine. Principle: resolve what you can and treat
    everything else as *absent* (empty / false / empty sequence) — jinja2's own answer for
    undefined names, so the fallback stays a subset of the real renderer. Per construct
    (✚ = counted in `STRIPPED`):

    * ``{{ dotted.path }}`` — resolved.
    * ``{{ richer }}`` (filters, calls, literals…) — ``""`` ✚ (never a Python repr).
    * ``{% if path %}``/``not path``/``elif``/``else``/``endif`` — evaluated; `_LITERALS`
      honoured.
    * ``{% if <richer> %}`` — false ✚: body out, ``else`` kept (never assert an unknown
      condition as fact to the model).
    * ``{% for %}`` — empty sequence ✚: body out, ``else`` kept.
    * ``{# comment #}`` — removed, not counted (jinja2 drops it too).
    * ``{% raw %}`` — whole block removed ✚ (a verbatim body is template syntax).
    * other block tags (detected by a matching ``{% end<tag> %}``) — whole block ✚.
    * other bodyless tags (``set``, ``include``…) — removed ✚.
    * whitespace control ``{%- -%}`` — honoured, not counted.
    * unbalanced/unterminated syntax — removed ✚ (`_STRAY`).
    """
    global STRIPPED
    if counts is not None:
        return _tally(counts, _minimal_render, template, context)
    if not template:
        return ""

    src, raw_blocks = _RAW_BLOCK.subn("", template)
    STRIPPED += raw_blocks

    out: list[str] = []
    #: One frame per open block; a chunk is written only when every frame's `emit` is true.
    stack: list[dict] = []
    lstrip_next = False
    pos = 0

    def live() -> bool:
        return all(f["emit"] for f in stack)

    def write(chunk: str, lstrip: bool) -> None:
        if lstrip:
            chunk = chunk.lstrip()
        if chunk and live():
            out.append(chunk)

    for m in _TOKEN.finditer(src):
        raw = m.group("var")
        kind = "var"
        if raw is None:
            raw = m.group("tag")
            kind = "tag"
        if raw is None:
            raw = m.group("comment")
            kind = "comment"

        # Whitespace-control markers sit *inside* the delimiters: `{%- if x -%}`.
        left = raw[:1] in ("-", "+")
        right = raw[-1:] in ("-", "+")
        expr = raw[1:] if left else raw
        expr = (expr[:-1] if right else expr).strip()

        write(src[pos:m.start()], lstrip_next)
        pos = m.end()
        lstrip_next = right
        if left and out:
            out[-1] = out[-1].rstrip()

        # Liveness outside any frame this token opens: constructs in an untaken branch
        # are removed uncounted (no divergence from jinja2).
        parent_live = live()

        if kind == "comment":
            continue                            # jinja2 drops it too — nothing to report

        if kind == "var":
            if not parent_live:
                continue
            if _PATH.match(expr):
                out.append(str(_resolve(expr, context)))
            else:
                STRIPPED += 1
            continue

        keyword = expr.split(None, 1)[0] if expr else ""
        rest = expr[len(keyword):].strip()

        if keyword == "if":
            truth, decided = _condition(rest, context)
            if not decided and parent_live:
                STRIPPED += 1
            stack.append({"name": "if", "emit": parent_live and truth,
                          "chosen": truth, "parent": parent_live})
        elif keyword == "elif" and stack and stack[-1]["name"] == "if":
            frame = stack[-1]
            truth, decided = _condition(rest, context)
            if not decided and frame["parent"]:
                STRIPPED += 1
            frame["emit"] = frame["parent"] and not frame["chosen"] and truth
            frame["chosen"] = frame["chosen"] or truth
        elif keyword == "else" and stack and stack[-1]["name"] in ("if", "for"):
            frame = stack[-1]
            frame["emit"] = frame["parent"] and not frame["chosen"]
            frame["chosen"] = True
        elif keyword == "for":
            # The sequence is unavailable, so it is empty: body out, `{% else %}` in.
            if parent_live:
                STRIPPED += 1
            stack.append({"name": "for", "emit": False, "chosen": False,
                          "parent": parent_live})
        elif keyword.startswith("end"):
            name = keyword[3:]
            for i in range(len(stack) - 1, -1, -1):
                if stack[i]["name"] == name:
                    del stack[i:]
                    break
            else:                               # a closer with nothing open
                if parent_live:
                    STRIPPED += 1
        elif re.search(r"\{%-?\s*end" + re.escape(keyword) + r"\s*-?%\}", src[pos:]):
            # An unevaluable wrapper (`filter`, `with`, `macro`, …): the block goes whole.
            if parent_live:
                STRIPPED += 1
            stack.append({"name": keyword, "emit": False, "chosen": True,
                          "parent": parent_live})
        else:                                   # `set`, `do`, `include`, … — no body
            if parent_live:
                STRIPPED += 1

    write(src[pos:], lstrip_next)

    text = "".join(out)
    text, strays = _STRAY.subn("", text)
    STRIPPED += strays
    return text


def _sandbox():
    """A `SandboxedEnvironment` that counts what it refuses (`ImportError` without
    jinja2)."""
    from jinja2.sandbox import SandboxedEnvironment
    from jinja2 import ChainableUndefined

    class _CountingSandbox(SandboxedEnvironment):
        def is_safe_attribute(self, obj, attr, value):
            ok = super().is_safe_attribute(obj, attr, value)
            if not ok:
                global BLOCKED
                BLOCKED += 1
            return ok

    return _CountingSandbox(undefined=ChainableUndefined, autoescape=False,
                            keep_trailing_newline=True)


def render_prompt(template: str, context: dict, counts: dict = None) -> str:
    """Render `template` over `context` (e.g. {'volley': v, 'session': s}): jinja2 when
    available, else `_minimal_render`.

    **The sandbox is load-bearing**: `prompt`/`opener` arrive in untrusted content packs,
    and a plain `jinja2.Environment` is server-side code execution
    (`{{ cycler.__init__.__globals__['os'] }}`; pinned by test_render_sandbox.py). An
    unsafe attribute renders as empty (`ChainableUndefined`) and is counted in `BLOCKED`,
    so the turn is never interrupted; a `SecurityError` on an unsafe operation falls back
    to the minimal renderer.

    `counts`, when given, receives this call's `blocked`/`stripped` delta (`_tally`).
    """
    if counts is not None:
        return _tally(counts, render_prompt, template, context)
    if not template:
        return ""
    try:
        env = _sandbox()
    except ImportError:
        return _minimal_render(template, context)
    try:
        return env.from_string(template).render(**context)
    except Exception:                           # SecurityError, TemplateSyntaxError, …
        return _minimal_render(template, context)
