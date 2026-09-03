"""
Which brain answers this child — the registry behind "any AI wears the shell".

`ai-seam.md` §2 says Moxie's body is a shell and everything that makes a given Moxie
*think* enters through one seam. Until now that claim was true of the architecture and
false of the appliance: a brain was chosen **once, globally**, by `MOXIE_APP` at import
time, and `config.build_app()` branched on four literal strings. One box, one brain, no
per-child anything — and an unrecognised value silently became the LLM app.

This module is the missing half. It answers the same two questions the 🎚️ voice picker's
`voice_settings.py` answers, in the same shape, for the brain:

  1. **What can this appliance run?** A *positive list* — `BRAINS` — in the idiom this
     codebase already relies on (`SPEC`/`FIELDS` in `content/packs.py`, the frozen
     `OPS`/`STATEMENTS` in `content/ext.py`, the frozen catalog in `vocab.py`). A name in
     the table resolves to a brain; a name that is not in it is **refused, never guessed**.
     There is no deny-list: nothing is "everything except", so nothing can be added by
     forgetting to exclude it.
  2. **Which one is in force for THIS robot?** `defaults ⊕ fleet ⊕ per-robot` — the
     layering that already exists for every other parent-set value (audit ADOPT #6,
     `cloud_config.merge_config_layers`, `fleet/config.json`, `POST /config?scope=fleet`).
     `brain` is simply another key in those layers, so there is exactly one layering in
     this codebase and this is not a second one. `resolve_brain` is the scalar case of
     that merge plus the one thing a merge cannot say: *which layer decided*, which is
     what a console card has to render and what a boot line has to print.

**The operator's environment wins.** An explicit `MOXIE_APP` **pins** the appliance's
brain and a per-child pick may not overrule it — the standing owner rule PR #77 wrote
into `voice_settings.pin_for_env` for `MOXIE_TTS`/`MOXIE_STT`. See "the environment's
pin" below for why the pin reads the *raw environment* rather than `config.MOXIE_APP`,
and which value would otherwise have pinned every unconfigured box by accident.

Deliberately dependency-free: no HTTP, no `openai`, no MQTT, no `config` import — the
same rule `voice_settings.py` follows, so every test here runs with no gateway, no key
and no model wheels. The *builders* live in `config.BrainEngines`, which is the only
thing that knows what a `MOXIE_LLM_BASE_URL` is.
"""
from __future__ import annotations

from typing import List, Optional, Sequence

#: The environment variable that selects — and pins — the appliance's brain.
ENV_VAR = "MOXIE_APP"

#: The key a brain choice occupies in the ordinary config layers (`fleet/config.json`,
#: the per-robot overrides). Named once, because `cloud_config`, the runtime and the
#: console all have to agree about it.
CONFIG_KEY = "brain"

#: The brain a box falls back to when nothing anywhere names one. It matches
#: `config.MOXIE_APP`'s own default, and the two are pinned together by a test.
DEFAULT_BRAIN = "llm"

#: **The positive list.** `{id: {label, group, blurb, needs}}` — closed, ordered, and
#: frozen as a literal in `sim/tests/test_brains.py`, so adding a brain requires a test
#: edit and a reviewer (the rule `content/ext.py::OPS` states for its own table).
#:
#: `needs` names the environment variables that brain cannot run without; it is what the
#: console card shows under an option and what a refusal quotes, so an operator reads
#: *which variable to set* rather than "could not build". It is documentation, not
#: enforcement: the builders (`config.build_brain`) are the ones that exit, and they name
#: the same variables, because a check in two places is a disagreement waiting to happen.
BRAINS = {
    "llm": {
        "label": "Free-form companion",
        "group": "Conversation",
        "blurb": "An OpenAI-compatible model answering in Moxie's persona, streamed a "
                 "sentence at a time.",
        "needs": ("MOXIE_LLM_BASE_URL",),
    },
    "content": {
        "label": "Content modules",
        "group": "Conversation",
        "blurb": "The data-driven activity engine — conversations, globals and the day "
                 "plan — answered through the same model seam.",
        "needs": ("MOXIE_LLM_BASE_URL",),
    },
    "webhook": {
        "label": "Your own service",
        "group": "External",
        "blurb": "Every turn is handed to an HTTP endpoint you run; the reply comes back "
                 "over the wire. No model code lives here.",
        "needs": ("MOXIE_WEBHOOK_ENDPOINT",),
    },
    "echo": {
        "label": "Echo (no model)",
        "group": "Built-in",
        "blurb": "Repeats what it hears. Needs no brain at all — the way to bring a box "
                 "up, and what the smokes run.",
        "needs": (),
    },
}

#: Every brain id, in table order. The dropdown's order and the refusal's order.
BRAIN_IDS = tuple(BRAINS)


# ------------------------------------------------------------------ one brain --
def is_brain(name) -> bool:
    """Whether `name` is a brain this appliance knows. The whole membership rule."""
    return isinstance(name, str) and name.strip().lower() in BRAINS


def sanitize_brain(value) -> str:
    """The brain `value` names, or `""` — the positive list applied.

    Used on every path a name can arrive by: the environment, a hand-edited
    `fleet/config.json`, a console POST, a stale page. `""` means "this is not a brain
    we know", and every caller treats that as *fall through to the layer underneath* or
    *refuse and say what is offered* — never as "assume the default", which is the
    behaviour this module exists to remove (`config.build_app` used to return the LLM app
    for `MOXIE_APP=gpt5`, `MOXIE_APP=Echo` and `MOXIE_APP=llm # the brain` alike).
    """
    if not isinstance(value, str):
        return ""
    name = value.strip().lower()
    return name if name in BRAINS else ""


def brain_label(name) -> str:
    """The human name for a brain — `""` for one we do not know."""
    return (BRAINS.get(sanitize_brain(name)) or {}).get("label", "")


def brain_needs(name) -> tuple:
    """The environment variables this brain cannot run without (possibly empty)."""
    return tuple((BRAINS.get(sanitize_brain(name)) or {}).get("needs", ()))


def describe_brain(name) -> str:
    """What the card shows: `Content modules (content)`.

    The id is repeated on purpose — it is what an operator puts in `MOXIE_APP` and what
    `POST /brain` takes, so a parent choosing between two entries can see the thing they
    would type. Unknown names are echoed verbatim rather than translated into an invented
    label: a card that renders `gpt5` as "Free-form companion" would be lying.
    """
    key = sanitize_brain(name)
    if not key:
        return str(name or "")
    return f"{BRAINS[key]['label']} ({key})"


def offered() -> str:
    """`llm, content, webhook, echo` — the sentence-tail every refusal ends with."""
    return ", ".join(BRAIN_IDS)


# ------------------------------------------------------------- the dropdown --
def option(name, *, is_default: bool = False) -> dict:
    """One card entry: `{id, label, group, blurb, needs, default}`."""
    key = sanitize_brain(name)
    spec = BRAINS.get(key) or {}
    return {"id": key, "label": spec.get("label", ""), "group": spec.get("group", ""),
            "blurb": spec.get("blurb", ""), "needs": list(spec.get("needs", ())),
            "default": bool(is_default)}


def options(*, default: str = "") -> List[dict]:
    """Every brain, in table order, with `default: true` on the one that would be used
    if nobody picked anything."""
    wanted = sanitize_brain(default)
    return [option(b, is_default=(b == wanted)) for b in BRAIN_IDS]


def option_ids(entries) -> List[str]:
    """Every `id` in an entry list, in order."""
    return [e.get("id", "") for e in entries or () if isinstance(e, dict)]


def find_option(entries, name) -> Optional[dict]:
    """The entry whose id is `name`, else None."""
    wanted = sanitize_brain(name)
    for e in entries or ():
        if isinstance(e, dict) and e.get("id") == wanted and wanted:
            return e
    return None


def filter_options(entries: Sequence[dict], pin: str) -> List[dict]:
    """`entries` reduced to the pinned brain (untouched when nothing is pinned).

    Filtering here rather than in the browser is what makes the two halves agree by
    construction (`config.VoiceEngines.available`'s reasoning, exactly): the card cannot
    show an entry this appliance would then refuse to install, and a stale page that
    posts one is refused by the ordinary availability check with the pin note saying why.
    """
    pinned = sanitize_brain(pin)
    if not pinned:
        return [dict(e) for e in entries or ()]
    return [dict(e) for e in entries or () if e.get("id") == pinned]


# --------------------------------------------------- the environment's pin --
# `MOXIE_APP=content` is an OPERATOR'S statement about this box, and a per-child dropdown
# must not be able to talk them out of it — the standing owner rule PR #77 enforced for
# `MOXIE_TTS`/`MOXIE_STT`. So an explicit value PINS the appliance's brain: the pinned
# brain is the only entry the card offers, `resolve_brain` returns it whatever the layers
# say, and a stale page's cross-brain pick is refused with the variable NAMED.
#
# PR #77's lesson, applied honestly rather than copied: **a value that is a PERMISSION
# rather than a SELECTION must not pin.** `MOXIE_TTS=tone` is excluded there because
# `build_synthesizer` treats `tone` as the last *rung* under a gateway and under Piper —
# it opts an engine in, it does not choose one — and because both compose files default
# to it, so pinning would have silently reduced every `docker compose up` deployment's
# dropdown to one entry.
#
# `MOXIE_APP` has no permission-shaped value: `build_app()` branches on the four names and
# each one returns exactly that app, so all four are selections and all four pin. What it
# *does* have is a **fall-through**: `config.MOXIE_APP` is `os.environ.get("MOXIE_APP",
# "llm")`, so an unset environment already reads as `llm`. Pinning that resolved value
# would have pinned every box where nobody said anything — the same accident in a
# different costume. So the pin is computed from the RAW environment
# (`config.brain_pin()` passes `os.environ.get("MOXIE_APP", "")`, never `config.MOXIE_APP`),
# and `""` pins nothing.
#
# `any` and `auto` are the explicit "decide per child" values — `voice_settings`' `auto`,
# spelled for a brain. They select nothing and pin nothing, which is what a deployment
# that wants the per-child picker sets. An unrecognised value pins nothing either, because
# it is refused outright at build time; pinning a name we cannot build would turn a typo
# into a locked-down appliance.
#
# KNOWN CONSEQUENCE, deliberate and documented: our own `docker-compose.yml` interpolates
# `MOXIE_APP: ${MOXIE_APP:-content}`, so a `docker compose up` with nothing set arrives
# here as an explicit `content` and pins. The compose default is *our* choice of the best
# out-of-box brain, not the operator's, so this is the shape #77 warned about — but the
# escape is named on the card and in one line of `.env` (`MOXIE_APP=any`), and the
# alternative (excluding `content` from the table) would silently ignore the operator who
# really did write `MOXIE_APP=content` themselves. Told loudly beats guessed quietly.

#: Values that pin nothing — the ones that mean "decide for me".
NO_PIN_VALUES = ("", "any", "auto")

#: `{raw value: the brain it pins}`. Every brain pins itself; nothing else is in the
#: table, so `any`, `auto`, `""` and a typo all pin nothing.
ENV_PIN = {b: b for b in BRAIN_IDS}


def pin_for_env(value) -> str:
    """The brain `MOXIE_APP` pins right now, or `""` for none.

    `value` is the RAW environment string — pass `os.environ.get("MOXIE_APP", "")`, not
    `config.MOXIE_APP`, whose `llm` default would pin every box nobody configured.
    """
    return ENV_PIN.get(str(value or "").strip().lower(), "")


def honours_pin(name, pin) -> bool:
    """Whether `name` may be installed under `pin`. No pin ⇒ every brain may."""
    pinned = sanitize_brain(pin)
    return True if not pinned else sanitize_brain(name) == pinned


def pin_note(value) -> str:
    """The one sentence the card prints when the environment has pinned the brain.

    Empty when nothing is pinned, so a caller can render it unconditionally. It names the
    variable, the brain, and the value that hands the choice back — a refusal that only
    listed the surviving option would read as "the appliance lost your brain".
    """
    pin = pin_for_env(value)
    if not pin:
        return ""
    raw = str(value or "").strip().lower()
    return (f"{ENV_VAR}={raw} pins this appliance's brain to {describe_brain(pin)}; "
            f"only its entry is offered here. Set {ENV_VAR}=any to choose per child.")


# ------------------------------------------------------------ the resolution --
#: What decided, weakest first. The card renders it and the boot line prints it.
SOURCES = ("default", "fleet", "robot", "pin")


def resolve_brain(*, default: str = DEFAULT_BRAIN, fleet=None, robot=None,
                  pin: str = "") -> dict:
    """The brain in force for one robot, and **which layer said so**.

    `default ⊕ fleet ⊕ robot`, later wins — the scalar case of
    `cloud_config.merge_config_layers`, which is where the merge itself happens; a test
    pins the two against each other so this can never drift into a second layering. Over
    the top of all three sits the environment's `pin`, which wins outright.

    A layer naming something that is not a brain (a hand-edited `fleet/config.json`, a
    record written by a newer version) **falls through to the layer underneath** and says
    so in `note`, rather than blanking the appliance or installing something nobody named
    — `voice_settings.read_settings`' rule, for the same reason: a broken file must never
    stop a box from talking.

    Returns `{brain, source, requested, pinned, note}`:
      * `brain` — the id in force, always a member of `BRAINS`;
      * `source` — one of `SOURCES`;
      * `requested` — what the layers asked for when the pin overruled them (else `""`);
      * `pinned` — the pin, `""` when none;
      * `note` — one plain sentence when something was ignored, else `""`.
    """
    pinned = sanitize_brain(pin)
    notes = []
    chosen, source = sanitize_brain(default) or DEFAULT_BRAIN, "default"
    for layer, value in (("fleet", fleet), ("robot", robot)):
        if value is None or value == "":
            continue                      # not set at this layer — the one underneath wins
        name = sanitize_brain(value)
        if not name:
            notes.append(f"the {layer} layer names {str(value)!r}, which is not a brain "
                         f"this appliance knows ({offered()}) — ignored")
            continue
        chosen, source = name, layer
    requested = ""
    if pinned and chosen != pinned:
        requested, chosen, source = chosen, pinned, "pin"
        notes.append(f"{ENV_VAR} pins the brain to {pinned} — the {requested} chosen "
                     f"here is not installed")
    elif pinned:
        source = "pin" if source == "default" else source
    return {"brain": chosen, "source": source, "requested": requested,
            "pinned": pinned, "note": " ".join(notes)}


def normalize_brain_patch(patch, *, pin: str = "") -> Optional[str]:
    """A console pick → the brain id to store, or `None` to clear the layer.

    Raises `ValueError` with the sentence the card shows when the pick is not a brain, or
    when the environment has pinned a different one. Raising rather than returning
    `{"ok": False}` matches `voice_settings.normalize_voice_settings` and
    `telehealth.validate_mood`: the caller owns the HTTP shape, this module owns the rule.
    """
    if isinstance(patch, dict):
        if CONFIG_KEY not in patch:
            raise ValueError(f"Nothing to change — send {{'{CONFIG_KEY}': "
                             f"'<{'|'.join(BRAIN_IDS)}>'}}.")
        value = patch[CONFIG_KEY]
    else:
        value = patch
    if value is None or (isinstance(value, str) and value.strip().lower()
                         in ("", "default", "inherit")):
        return None                        # unset → the layer underneath takes over
    name = sanitize_brain(value)
    if not name:
        raise ValueError(f"{str(value)!r} is not a brain this appliance knows. "
                         f"Choose one of: {offered()}.")
    if not honours_pin(name, pin):
        raise ValueError(f"{name!r} cannot be chosen here. {pin_note_for_pin(pin)}")
    return name


def pin_note_for_pin(pin: str) -> str:
    """The pin sentence when all you hold is the pinned brain (not the raw env value).

    `pin_note` takes what the environment said; a refusal deep in the runtime only knows
    what it resolved to. Both name the variable, which is the part that matters.
    """
    pinned = sanitize_brain(pin)
    if not pinned:
        return ""
    return (f"{ENV_VAR} pins this appliance's brain to {describe_brain(pinned)}; "
            f"set {ENV_VAR}=any to choose per child.")


def boot_line(resolved: dict, *, device_id: str = "") -> str:
    """The supervisor's one-line report — `brain: content (fleet)` /
    `brain: echo (MOXIE_APP pins it) — the llm chosen here is not installed`.

    Says *what* is answering and *why* it is that one, because "the wrong brain" and "not
    the brain I picked" are the two outcomes an operator needs to read off a log.
    """
    r = resolved or {}
    who = f"{device_id}: " if device_id else ""
    src = {"pin": f"{ENV_VAR} pins it", "robot": "this robot", "fleet": "house rule",
           "default": "appliance default"}.get(r.get("source", ""), r.get("source", ""))
    tail = f" — {r['note']}" if r.get("note") else ""
    return f"brain: {who}{r.get('brain', '')} ({src}){tail}"
