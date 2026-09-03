"""
Prompt rendering — a content module's `prompt` is Jinja2-templated over the
volley/session (docs/architecture/content-module-contract.md).

Two renderers, one contract:

* **With jinja2** — the real thing, in a `SandboxedEnvironment` (see `render_prompt`).
  This is what the shipped container runs: `mqtt/requirements.txt` lists `jinja2>=3.0`
  and `mqtt/Dockerfile` installs from that file.
* **Without jinja2** — `_minimal_render`, a dependency-free renderer, so the SDK still
  imports and unit-tests with nothing heavy installed (`pyproject.toml` keeps jinja2 in
  the optional `content` extra). A bare `pip install moxie-cloud-sdk` lands here.

**The fallback's one hard rule: nothing template-shaped may reach the brain.** Its output
is a *system prompt*, so a leftover `{% if presence.face_present %}` is not a cosmetic
glitch — it is instructions-shaped noise in the place the model takes instructions from.
So the fallback resolves what it can and **removes** what it cannot, and every removal is
counted in `STRIPPED`. See `_minimal_render` for the construct-by-construct behaviour and
its justification.
"""
from __future__ import annotations
import re

# ---------------------------------------------------------------- the grammar --
#: A bare dotted path — the one expression form the fallback can evaluate.
_PATH = re.compile(r"^[a-zA-Z_][\w.]*$")

#: An `{% if %}`/`{% elif %}` condition the fallback can decide: `path` or `not path`.
_COND = re.compile(r"^(?P<neg>not\s+)?(?P<path>[a-zA-Z_][\w.]*)$")

#: Jinja2's boolean/none **literals**, which look exactly like bare names to `_COND` but
#: are not names — jinja2 resolves `{% if true %}` to True no matter what the context
#: holds. Missing this made `{% if true %}` render the `{% else %}` branch, a divergence
#: the counter could not even report because the condition parsed as a path. Caught by
#: the differential half of `sim/tests/test_render_fallback.py`.
_LITERALS = {"true": True, "True": True, "false": False, "False": False,
             "none": False, "None": False}

#: `{% raw %}…{% endraw %}`. Removed before anything else is scanned, for two reasons:
#: jinja2 emits a raw body *verbatim* — the one thing this renderer must never do — and
#: its contents are not tags, so they must not be parsed as tags.
_RAW_BLOCK = re.compile(r"\{%-?\s*raw\s*-?%\}.*?\{%-?\s*endraw\s*-?%\}", re.S)

#: One expression, statement or comment. Non-greedy, DOTALL: a block tag may wrap lines.
_TOKEN = re.compile(r"\{\{(?P<var>.*?)\}\}"
                    r"|\{%(?P<tag>.*?)%\}"
                    r"|\{#(?P<comment>.*?)#\}", re.S)

#: Delimiter debris an *unterminated* construct would otherwise leave behind (`{{ oops`
#: with no `}}` never matches `_TOKEN`, so the literal `{{` would survive into the
#: prompt). Swept last so the no-template-syntax guarantee is total rather than
#: best-effort.
_STRAY = re.compile(r"\{\{-?|-?\}\}|\{%-?|-?%\}|\{#-?|-?#\}")


def _resolve(path: str, context: dict):
    """Walk a dotted path over dicts/objects; missing → ''.

    **A segment beginning with `_` is refused.** That is a security boundary, not a style
    rule, and it is the fallback's half of the guarantee `SandboxedEnvironment` gives the
    jinja2 path (its `is_safe_attribute` refuses underscore-leading attributes for exactly
    this reason). A `prompt` is untrusted input — it arrives inside a content pack
    (`packs.py`) — and this walk is `getattr` over live objects, so without the guard a
    bare dotted path *is* an attribute-chain escape: on a jinja2-less install,
    `{{ session.__class__.__repr__.__globals__.inspect.os.environ }}` rendered the whole
    process environment, `MOXIE_LLM_API_KEY` included, straight into the system prompt
    the brain is handed. Measured, not theorised — `sim/tests/test_content_pack_sandbox.py`
    is the probe that found it and the fence that keeps it shut.

    Nothing legitimate is lost: the documented grammar
    (`docs/architecture/content-module-contract.md`) is `{{ volley.config.child_pii.nickname }}`
    and its kin, and no shipped module names a private attribute.
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
    """`(truth, decided)` for an `{% if %}`/`{% elif %}` condition.

    `decided` is False for anything richer than `path` / `not path` (or a `_LITERALS`
    keyword) — a comparison, a filter, a test, `and`/`or`, a call. The caller then treats
    the branch as **false**, which is the same answer `_resolve` already gives a path that
    is not there, and the same answer jinja2's `ChainableUndefined` gives an undefined
    name.
    """
    m = _COND.match(cond.strip())
    if not m:
        return False, False
    path = m.group("path")
    val = _LITERALS[path] if path in _LITERALS else _resolve(path, context)
    return ((not val) if m.group("neg") else bool(val)), True


#: Incremented whenever a template asks for something a renderer refuses to give it —
#: an attribute the jinja2 sandbox rejects, or a private/dunder path segment the
#: dependency-free fallback rejects (`_resolve`). One counter for both, because it
#: answers one question: *did somebody try?* A content pack that trips this is either
#: broken or hostile, so it is worth seeing rather than swallowing — `render_prompt`
#: still returns safe text either way.
BLOCKED = 0

#: Incremented once per construct the **dependency-free fallback** removed because it
#: could not evaluate it. `BLOCKED`'s sibling, and for the same reason: the degradation
#: is invisible in the output by design (the whole point is that nothing
#: template-shaped comes out), so without a counter a deployment could serve
#: block-using modules with silently thinner prompts forever.
#:
#: A non-zero `STRIPPED` means exactly one thing: **this process has no jinja2 and is
#: rendering content that needs it.** The fix is `pip install moxie-cloud-sdk[content]`
#: (the container already ships it). Constructs jinja2 itself would also have removed —
#: `{# comments #}` — are deliberately *not* counted, so the number stays a signal.
STRIPPED = 0


def _minimal_render(template: str, context: dict) -> str:
    """Render `template` with no jinja2 installed, emitting **no template syntax**.

    A tiny single-pass scanner rather than a second template engine. One principle
    decides every case: *resolve what you can, and treat everything else as **absent***
    — empty string, false, empty sequence. That is not an invented rule; it is what
    `_resolve` already does for a missing path and what jinja2 + `ChainableUndefined`
    already does for an undefined name, so the fallback stays a subset of the real
    renderer instead of a divergent dialect.

    Construct by construct, and why:

    ``{{ dotted.path }}``
        Resolved, exactly as before. The case the fallback exists for.

    ``{{ anything richer }}`` (filters, subscripts, calls, literals, arithmetic,
    inline-if, tests)
        Removed → ``""``, and counted. Not "resolve the base and ignore the filter":
        ``{{ facts | join('; ') }}`` would then emit a Python list *repr*, and
        ``{{ facts | length }}`` a list where a number was meant. A repr is not English
        either. Empty is a degradation; a repr is noise.

    ``{% if path %}`` / ``{% if not path %}`` / ``{% elif … %}`` / ``{% else %}`` /
    ``{% endif %}``
        **Evaluated**, and the taken branch kept — the documented form works here, using
        only `_resolve`, which the fallback already had. `true`/`false`/`none` are
        recognised as jinja2 *literals* rather than as names (`_LITERALS`). Not counted:
        the semantics match jinja2 (truthiness of the same resolved value), so nothing
        was lost.

    ``{% if <richer condition> %}``
        Treated as **false**: the `if` body goes, the `{% else %}` body stays. Counted.
        The alternative — keep the body, drop the tags — renders the branch
        *unconditionally*, which tells the brain "Sam is here" when nobody is. A
        conditional whose condition is unknown must not be reported to the model as a
        fact, and "absent ⇒ falsey" is the renderer's existing answer for the unknown.
        Keeping the `else` body is also what stops an `if/else` from emptying the prompt.

    ``{% for … %}…{% else %}…{% endfor %}``
        The sequence is treated as **empty**: the loop body goes, the `{% else %}` body
        stays. Counted. Emitting a loop body *once* is worse than dropping it — the body
        is a per-item fragment, so ``{{ loop.index }}. {{ f }}`` becomes a dangling
        ``". "`` describing an item that does not exist. Iterating for real would mean
        binding loop variables and a nested scope, i.e. building the second template
        engine this function refuses to be; and empty-sequence is, again, jinja2's own
        answer for a sequence that is not there.

    ``{# comment #}``
        Removed, and **not** counted — jinja2 removes it too, so there is no divergence
        to report and counting it would only add noise to the signal.

    ``{% raw %}…{% endraw %}``
        The whole block goes, counted. The deliberate divergence: jinja2 emits a raw body
        *verbatim*, and a verbatim body is template syntax, which is the one thing that
        may not reach the brain.

    Any other **block** tag (``filter``, ``with``, ``block``, ``macro``, ``call``,
    ``trans``, ``autoescape``, anything jinja2 adds later)
        The whole block goes, counted. "Block" is decided by looking for a matching
        ``{% end<tag> %}`` in the source rather than from a hardcoded list, so an
        unfamiliar tag is classified by the template itself. The body's meaning depends
        on a wrapper that cannot be applied, so emitting the body alone would be a
        different instruction from the one the author wrote.

    Any other **bodyless** tag (``set``, ``do``, ``include``, ``import``, ``extends``)
        Removed, counted. Nothing to emit; a name `{% set %}` would have bound simply
        stays unresolvable, and therefore ``""``.

    Whitespace control (``{%- … -%}``, ``{{- … -}}``)
        Honoured, so the text that survives a removal does not collapse into doubled
        spaces. Uncounted — it is layout, not meaning.

    Unbalanced or unterminated syntax
        Removed, counted. `_STRAY` sweeps the debris an unterminated construct leaves,
        which is what makes "no template syntax in the output" a guarantee and not a
        hope.
    """
    global STRIPPED
    if not template:
        return ""

    src, raw_blocks = _RAW_BLOCK.subn("", template)
    STRIPPED += raw_blocks

    out: list[str] = []
    #: One frame per open block. `emit` is this frame's own verdict; a chunk is written
    #: only when every frame says yes, which is how nesting works without a parse tree.
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

        # `parent_live` is the liveness *outside* any frame this token opens: a construct
        # inside a branch jinja2 would not have taken is no divergence, so it is removed
        # without being counted.
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
    """A `SandboxedEnvironment` that counts what it refuses. Raises `ImportError` when
    jinja2 is absent — no longer the shipped container's situation (`mqtt/requirements.txt`
    lists `jinja2>=3.0`), but still the situation for a bare-metal `pip install
    moxie-cloud-sdk` without the `content` extra."""
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


def render_prompt(template: str, context: dict) -> str:
    """Render `template` over `context` (e.g. {'volley': v, 'session': s}).

    Prefers Jinja2 if available; the minimal fallback handles `{{ dotted.path }}` and
    simple `{% if dotted.path %}` truthiness, and removes every construct it cannot
    evaluate rather than passing it through (`_minimal_render`, `STRIPPED`).

    **The environment is sandboxed, and that is load-bearing.** A template here is
    *untrusted input*: `prompt` and `opener` travel inside a content pack
    (`moxie_sdk/content/packs.py`), so anyone who can hand a parent a pack can choose
    this string. Under a plain `jinja2.Environment` that is server-side code
    execution — proven by execution on jinja2 3.1.2, where
    `{{ cycler.__init__.__globals__['os'].name }}` returned the host's platform and
    `''.__class__.__mro__[1].__subclasses__()` enumerated 364 host classes.
    `SandboxedEnvironment` refuses that attribute walk; `sim/tests/test_render_sandbox.py`
    pins each probe. The sandbox is also what made it safe to put jinja2 *into* the
    shipped image, which is why those two changes landed together.

    **How the refusal actually behaves** (measured, not assumed): the sandbox does not
    raise on an unsafe attribute. `is_safe_attribute` returns False and Jinja substitutes
    an *undefined* object, which with `ChainableUndefined` renders as an empty string. So
    a hostile template comes back inert and the turn is never interrupted — Moxie keeps
    talking. That silence is good for the child and bad for us, so `_CountingSandbox`
    counts every refusal in `BLOCKED`: a pack that trips it is broken or hostile, and
    either way it is worth seeing. A `SecurityError` can still be raised for unsafe
    *operations* rather than attributes, and that falls through to the minimal renderer,
    which reaches nothing and emits no template syntax."""
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
