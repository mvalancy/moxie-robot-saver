"""
Which brain answers this child — the registry behind "any AI wears the shell".

`ai-seam.md` §2 says everything that makes a Moxie *think* enters through one seam. This
module makes that true per child, answering the same two questions `voice_settings.py`
answers for voice:

  1. **What can this appliance run?** A closed *positive list*, `BRAINS` (the codebase's
     idiom: `content/packs/::SPEC`, `content/ext/::OPS`, `vocab.py`). A name outside
     it is **refused, never guessed** — no deny-list, no silent fallback to `llm`.
  2. **Which one is in force for THIS robot?** `defaults ⊕ fleet ⊕ per-robot`, the same
     layering as every other parent-set value (`cloud_config.merge_config_layers`);
     `brain` is just another key. `resolve_brain` also reports *which layer decided*.

**The operator's environment wins.** An explicit `MOXIE_APP` **pins** the brain; a
per-child pick may not overrule it (see "the environment's pin" below).

Dependency-free (no HTTP, `openai`, MQTT or `config` import). The builders live in
`config.BrainEngines`.
"""
from __future__ import annotations

from typing import List, Optional, Sequence

#: The environment variable that selects — and pins — the appliance's brain.
ENV_VAR = "MOXIE_APP"

#: The key a brain choice occupies in the ordinary config layers.
CONFIG_KEY = "brain"

#: The brain a box falls back to when nothing anywhere names one. It matches
#: `config.MOXIE_APP`'s own default, and the two are pinned together by a test.
DEFAULT_BRAIN = "llm"

#: **The positive list.** `{id: {label, group, blurb, needs}}` — closed, ordered, frozen as
#: a literal in `sim/tests/test_brains.py` (adding a brain needs a test edit). `needs` is
#: documentation of required env vars (the builders enforce them).
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

    `""` means "not a brain we know"; callers fall through to the layer underneath or
    refuse — never "assume the default".
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

    The id is repeated (it is what one types in `MOXIE_APP`); unknown names are echoed
    verbatim rather than given an invented label.
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

    Done server-side so the card never shows an entry the appliance would refuse.
    """
    pinned = sanitize_brain(pin)
    if not pinned:
        return [dict(e) for e in entries or ()]
    return [dict(e) for e in entries or () if e.get("id") == pinned]


# --------------------------------------------------- the environment's pin --
# An explicit `MOXIE_APP` is the OPERATOR'S statement about this box, so it PINS the brain
# (as `MOXIE_TTS`/`MOXIE_STT` pin voice): only that entry is offered, `resolve_brain`
# returns it, and a cross-brain pick is refused with the variable named. All four names
# are selections, so all four pin.
#
# The pin is computed from the RAW environment (`config.brain_pin()`), never from
# `config.MOXIE_APP`, whose `llm` fallback would pin every unconfigured box. `""`, `any`
# and `auto` ("decide per child") pin nothing, nor does a typo (refused at build time).
#
# Known consequence: `docker-compose.yml` defaults `MOXIE_APP` to `content`, which pins;
# the card and `.env` name the escape (`MOXIE_APP=any`).

#: Values that pin nothing — the ones that mean "decide for me".
NO_PIN_VALUES = ("", "any", "auto")

#: `{raw value: the brain it pins}`. Every brain pins itself; nothing else does.
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
    """The sentence the card prints when the environment pinned the brain ("" if not):
    names the variable, the brain, and the value that hands the choice back."""
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

    `default ⊕ fleet ⊕ robot`, later wins (the scalar case of
    `cloud_config.merge_config_layers`; a test pins them together), with the env `pin`
    over all. A layer naming a non-brain **falls through** and says so in `note`.

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

    Raises `ValueError` with the card's sentence for a non-brain or a pin conflict.
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
    """The pin sentence when all you hold is the resolved pin (not the raw env value)."""
    pinned = sanitize_brain(pin)
    if not pinned:
        return ""
    return (f"{ENV_VAR} pins this appliance's brain to {describe_brain(pinned)}; "
            f"set {ENV_VAR}=any to choose per child.")


def boot_line(resolved: dict, *, device_id: str = "") -> str:
    """The supervisor's one-line report — `brain: content (fleet)` /
    `brain: echo (MOXIE_APP pins it) — the llm chosen here is not installed`.

    Says *what* is answering and *why*.
    """
    r = resolved or {}
    who = f"{device_id}: " if device_id else ""
    src = {"pin": f"{ENV_VAR} pins it", "robot": "this robot", "fleet": "house rule",
           "default": "appliance default"}.get(r.get("source", ""), r.get("source", ""))
    tail = f" — {r['note']}" if r.get("note") else ""
    return f"brain: {who}{r.get('brain', '')} ({src}){tail}"
