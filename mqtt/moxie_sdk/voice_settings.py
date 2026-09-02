"""
Which voice speaks and which ears listen — the record behind the console's two dropdowns.

`audio_models.py` answers *"of the ids this gateway serves, which are voices and which
are ears"*. This module answers the next two questions a parent's console actually asks:

  1. **What can this appliance use right now?** The gateway's audio models, the local
     Piper voices installed on the box, local whisper, and the two built-ins (`tone`,
     `off`). One flat, ordered, grouped list per dropdown.
  2. **Which one is in force?** A stored choice when a parent made one, otherwise the
     default computed *at read time* from that same availability — so a voice the gateway
     starts serving tomorrow becomes selectable with no migration and no restart.

Three rules are load-bearing and each is pinned by a test:

  * **`piper-amy` when possible.** Moxie's own voice, whenever the gateway lists it;
    `stt-whisper` for the ears (`audio_models.DEFAULT_*_MODEL`).
  * **Local engines are first class.** An explicit local choice wins even when a gateway
    is fully configured — the same statement `MOXIE_TTS=piper` / `MOXIE_STT=whisper` make
    in `mqtt/config.py`. A home appliance that keeps a child's voice inside the house is
    a supported deployment, not a degraded one.
  * **An outage never blanks the card.** A stored choice is honoured on READ even when
    discovery cannot currently confirm it (the gateway is down); only a *write* is
    checked against what is available, because that is the moment a parent can be told.

Deliberately dependency-free: no HTTP, no `openai`, no MQTT, no `config` import. The one
concession to the real world is `GatewayCatalog`, which caches a listing behind a
`list_models()` callable — a seam, so every test in `sim/tests/test_voice_settings.py`
runs with a fake and spends no request.

The persisted record (`fleet/voice.json` via `moxie_sdk.store.JsonStore`)::

    {"speech":    {"engine": "gateway", "model": "piper-amy"},
     "listening": {"engine": "gateway", "model": "stt-whisper"},
     "updated_at": 1788400000}

A missing side means "use the default", which is why the file stays valid across a
gateway that gains or loses models. See `docs/architecture/backlog/voice-picker.md`.
"""
from __future__ import annotations

import os
import re
import threading
import time
from typing import Callable, Dict, List, Optional, Sequence

from .audio_models import (DEFAULT_STT_MODEL, DEFAULT_TTS_MODEL,  # noqa: F401
                           classify_audio_models)

#: The two sides of the picker. Used as dict keys everywhere, so they are named once.
SPEECH = "speech"
LISTENING = "listening"
KINDS = (SPEECH, LISTENING)

#: `JsonStore` collection for the fleet-level record (`fleet/voice.json`).
COLLECTION = "voice"

#: How long a gateway listing is trusted before a background refresh is kicked off.
#: Five minutes: long enough that a busy console costs one request per window, short
#: enough that a model added to the gateway shows up in the dropdown the same session.
DEFAULT_TTL_S = 300.0

#: Engines each side accepts. `tone` and `off` are the built-ins that always exist.
SPEECH_ENGINES = ("gateway", "piper", "tone")
LISTENING_ENGINES = ("gateway", "whisper", "off")
ENGINES = {SPEECH: SPEECH_ENGINES, LISTENING: LISTENING_ENGINES}

#: The `<optgroup>` an engine's entries belong to, in the console.
GATEWAY_GROUP, LOCAL_GROUP, BUILTIN_GROUP = "Gateway", "Local", "Built-in"
ENGINE_GROUP = {"gateway": GATEWAY_GROUP, "piper": LOCAL_GROUP, "whisper": LOCAL_GROUP,
                "tone": BUILTIN_GROUP, "off": BUILTIN_GROUP}

#: How an engine is named to a human, in a label and in the boot log.
ENGINE_LABEL = {"gateway": "gateway", "piper": "local Piper", "whisper": "local whisper",
                "tone": "built-in", "off": "built-in"}

#: The engine that must exist for a side even when nothing else does.
BUILTIN_ENGINE = {SPEECH: "tone", LISTENING: "off"}


# ---------------------------------------------------------------- one choice --
def make_choice(engine, model="") -> dict:
    """`{"engine", "model"}` with both fields trimmed — the canonical choice shape."""
    return {"engine": str(engine or "").strip().lower(),
            "model": str(model or "").strip()}


def choice_id(choice) -> str:
    """The dropdown's `<option value>`: `gateway:piper-amy`, `piper:en_US-amy-medium`,
    `tone`, `off`. One string, so the console posts back exactly what it rendered."""
    c = as_choice(choice) or {"engine": "", "model": ""}
    return f"{c['engine']}:{c['model']}" if c["model"] else c["engine"]


def parse_choice(value) -> Optional[dict]:
    """A choice from either shape the console may send: the `id` string
    (`"gateway:piper-amy"`) or the dict (`{"engine": …, "model": …}`). None when the
    value is neither."""
    if isinstance(value, dict):
        return make_choice(value.get("engine"), value.get("model"))
    if isinstance(value, str):
        engine, _, model = value.partition(":")
        return make_choice(engine, model)
    return None


def as_choice(value) -> Optional[dict]:
    """`parse_choice` for internal callers that already hold a choice-ish thing."""
    return parse_choice(value)


def sanitize_choice(kind: str, value) -> Optional[dict]:
    """A well-formed choice for `kind`, or None.

    **Well-formed, not available**: this is the READ-side check. A stored
    `gateway:piper-amy` stays the current choice while the gateway is unreachable — the
    console keeps rendering it instead of silently reverting the parent's pick. Writes go
    through `normalize_voice_settings`, which *does* demand availability.
    """
    c = parse_choice(value)
    if not c or c["engine"] not in ENGINES.get(kind, ()):
        return None
    if c["engine"] in ("tone", "off"):
        c["model"] = ""                     # the built-ins have no model to name
    elif not c["model"]:
        return None                         # `gateway` / `piper` without a model is noise
    return c


# --------------------------------------------------------------- the labels --
#: Segments that name the *plumbing*, not the voice — dropped from a human label.
_NOISE = frozenset({"piper", "tts", "stt", "graphling", "moxie", "voice", "model"})
#: Piper's quality suffix (`en_US-amy-medium`), likewise not part of the voice's name.
_QUALITY = frozenset({"low", "medium", "high", "x_low", "xlow"})
#: A leading locale segment: `en`, `en_US`, `en_GB`. Two letters + an optional region, so
#: a three-letter voice name (`amy`, `ryan`) can never be mistaken for one.
_LOCALE = re.compile(r"^[a-z]{2}(_[A-Za-z]{2,3})?$")


def voice_title(model: str) -> str:
    """The human name inside a model id — `piper-amy` → `Amy`, `en_US-amy-medium` →
    `Amy`, `graphling-tts-narrator` → `Narrator`, `stt-whisper-base` → `Whisper base`.

    A best-effort *label*, never an identifier: when every segment is plumbing
    (`graphling-stt`) the raw id is returned rather than an invented word.
    """
    raw = (model or "").strip()
    if not raw:
        return ""
    parts = [p for p in raw.split("-") if p]
    if parts and _LOCALE.match(parts[0]):
        parts = parts[1:]
    words = [p for p in parts if p.lower() not in _NOISE and p.lower() not in _QUALITY]
    if not words:
        return raw
    return " ".join(words).replace("_", " ").strip().capitalize()


def describe_choice(choice) -> str:
    """What the dropdown shows: `Amy (gateway, piper-amy)` · `Amy (local Piper)` ·
    `base.en (local whisper)` · `Tone (built-in)` · `Off (built-in)`.

    The gateway form repeats the model id on purpose — two gateways can both serve an
    "Amy" and a parent choosing between them deserves to see which one they are picking.
    A local whisper size is left verbatim: `base.en` is the thing to recognise, and
    title-casing it into "Base.en" would only obscure it.
    """
    c = as_choice(choice)
    if not c or not c["engine"]:
        return ""
    engine, model = c["engine"], c["model"]
    if engine == "tone":
        return "Tone (built-in)"
    if engine == "off":
        return "Off (built-in)"
    if engine == "whisper":
        return f"{model or DEFAULT_STT_MODEL} (local whisper)"
    if engine == "piper":
        return f"{voice_title(model) or model} (local Piper)"
    if engine == "gateway":
        return f"{voice_title(model) or model} (gateway, {model})"
    return f"{model or engine} ({ENGINE_LABEL.get(engine, engine)})"


def boot_line(kind: str, choice, *, chosen: bool, note: str = "") -> str:
    """The supervisor's startup line — `speech: piper-amy (gateway, chosen)`, or
    `speech: tone (built-in, default — gateway unreachable)`.

    Says *what* is installed and *why* it is, because "no voice" and "not the voice you
    picked" are the two boot outcomes a parent needs to be able to read off the log.
    """
    c = as_choice(choice) or make_choice(BUILTIN_ENGINE.get(kind, "off"))
    what = c["model"] or c["engine"]
    why = "chosen" if chosen else "default"
    tail = f" — {note}" if note else ""
    return f"{kind}: {what} ({ENGINE_LABEL.get(c['engine'], c['engine'])}, {why}{tail})"


# ------------------------------------------------------------- availability --
def option(engine, model="", *, is_default: bool = False) -> dict:
    """One dropdown entry: `{id, engine, model, group, label, default}`."""
    c = make_choice(engine, model)
    return {"id": choice_id(c), "engine": c["engine"], "model": c["model"],
            "group": ENGINE_GROUP.get(c["engine"], BUILTIN_GROUP),
            "label": describe_choice(c), "default": bool(is_default)}


def speech_options(gateway_models: Sequence[str] = (),
                   piper_voices: Sequence[str] = ()) -> List[dict]:
    """Gateway voices, then installed local Piper voices, then the built-in tone.

    Input order is preserved inside each group (`classify_audio_models` guarantees it for
    the gateway half) — a picker whose entries shuffle between page loads is a bug report.
    """
    opts = [option("gateway", m) for m in gateway_models or ()]
    opts += [option("piper", v) for v in piper_voices or ()]
    opts.append(option("tone"))
    return opts


def listening_options(gateway_models: Sequence[str] = (),
                      whisper_models: Sequence[str] = ()) -> List[dict]:
    """Gateway ears, then local whisper sizes, then `off` (text turns still work)."""
    opts = [option("gateway", m) for m in gateway_models or ()]
    opts += [option("whisper", m) for m in whisper_models or ()]
    opts.append(option("off"))
    return opts


def build_available(gateway_ids: Sequence[str] = (), *,
                    piper_voices: Sequence[str] = (),
                    whisper_models: Sequence[str] = ()) -> Dict[str, List[dict]]:
    """`{"speech": [...], "listening": [...]}` from one gateway listing plus what is
    installed locally. The gateway half is classified by name — the listing itself never
    says which id is a voice and which is a pair of ears (`audio_models`)."""
    audio = classify_audio_models(gateway_ids or ())
    return {SPEECH: speech_options(audio["tts"], piper_voices),
            LISTENING: listening_options(audio["stt"], whisper_models)}


def option_ids(entries) -> List[str]:
    """Every `id` in one side's entry list, in order."""
    return [e.get("id", "") for e in entries or () if isinstance(e, dict)]


def find_option(entries, value) -> Optional[dict]:
    """The entry whose `id` matches `value` (a choice or an id string), else None."""
    wanted = choice_id(value) if not isinstance(value, str) else value
    for e in entries or ():
        if isinstance(e, dict) and e.get("id") == wanted:
            return e
    return None


def mark_defaults(available: dict, defaults: dict) -> dict:
    """Return `available` with `default: true` on the entry each side would fall back to
    — the "Default" marker the console prints beside one option."""
    out = {}
    for kind in KINDS:
        wanted = choice_id(defaults.get(kind)) if defaults else ""
        out[kind] = [dict(e, default=(e.get("id") == wanted))
                     for e in (available.get(kind) or [])]
    return out


# ------------------------------------------------------------------ defaults --
def _default_for(entries, *, gateway_preferred: str, local_engine: str,
                 local_preferred: str, builtin: str) -> dict:
    gateway = [e for e in entries or () if e.get("engine") == "gateway"]
    for e in gateway:
        if e.get("model") == gateway_preferred:
            return make_choice("gateway", e["model"])
    if gateway:
        return make_choice("gateway", gateway[0].get("model"))
    local = [e for e in entries or () if e.get("engine") == local_engine]
    for e in local:
        if local_preferred and local_preferred in str(e.get("model", "")).lower():
            return make_choice(local_engine, e["model"])
    if local:
        return make_choice(local_engine, local[0].get("model"))
    return make_choice(builtin)


def resolve_defaults(available: dict) -> Dict[str, dict]:
    """The "when possible" rule, per side, from what is available *right now*.

    Speech: `piper-amy` if the gateway lists it → else the first gateway voice → else a
    local Piper Amy → else any local Piper voice → else `tone`.
    Listening: `stt-whisper` → first gateway ears → local whisper → `off`.

    Computed at read time rather than frozen into the record, so a gateway that starts
    serving `piper-amy` tomorrow becomes the default with no migration.
    """
    available = available or {}
    return {
        SPEECH: _default_for(available.get(SPEECH), gateway_preferred=DEFAULT_TTS_MODEL,
                             local_engine="piper", local_preferred="amy",
                             builtin="tone"),
        LISTENING: _default_for(available.get(LISTENING),
                                gateway_preferred=DEFAULT_STT_MODEL,
                                local_engine="whisper", local_preferred="",
                                builtin="off"),
    }


# ------------------------------------------------------------- the settings --
def normalize_voice_settings(patch, available: dict, *, current=None,
                             now: Optional[float] = None) -> dict:
    """Merge a console patch into the stored record, or raise `ValueError` saying why not.

    `patch` is `{"speech": <choice-or-id>, "listening": <choice-or-id>}`; either side may
    be omitted (left alone) or `None` (cleared back to the computed default). A value that
    is not one of `available[kind]`'s ids is **refused with a sentence the console shows** —
    a dropdown can only be wrong when the page is stale or someone is hand-posting, and in
    both cases silently installing something else is worse than an error.

    Raising rather than returning `{"ok": False}` matches `telehealth.validate_mood`: the
    caller (the runtime) owns the HTTP shape, this module owns the rule.
    """
    if patch is None:
        patch = {}
    if not isinstance(patch, dict):
        raise ValueError("Expected a JSON object with 'speech' and/or 'listening'.")
    unknown = [k for k in patch if k not in KINDS]
    if unknown:
        raise ValueError(f"Unknown field(s) {', '.join(sorted(unknown))} — "
                         f"expected 'speech' and/or 'listening'.")
    out = {}
    for kind in KINDS:
        keep = sanitize_choice(kind, (current or {}).get(kind))
        if keep:
            out[kind] = keep
    touched = False
    for kind in KINDS:
        if kind not in patch:
            continue
        touched = True
        value = patch[kind]
        if value is None or value == "" or value == "default":
            out.pop(kind, None)             # unset → the computed default takes over
            continue
        entries = (available or {}).get(kind) or []
        shown = value if isinstance(value, str) else choice_id(parse_choice(value) or {})
        entry = find_option(entries, shown)
        if entry is None:
            raise ValueError(
                f"{shown!r} is not one of this appliance's {kind} options right now. "
                f"Choose one of: {', '.join(option_ids(entries)) or '(none)'}.")
        out[kind] = make_choice(entry["engine"], entry["model"])
    if not touched:
        raise ValueError("Nothing to change — send 'speech' and/or 'listening'.")
    out["updated_at"] = int(now if now is not None else time.time())
    return out


def resolve_settings(stored, available: dict) -> dict:
    """`{"current", "defaults", "chosen"}` — what is in force and how we got there.

    `chosen[kind]` is True when a parent's stored pick is what is in force (as opposed to
    the computed default), which is exactly what the boot log and the card's "Default"
    marker need to say.
    """
    defaults = resolve_defaults(available)
    current, chosen = {}, {}
    for kind in KINDS:
        pick = sanitize_choice(kind, (stored or {}).get(kind)
                               if isinstance(stored, dict) else None)
        chosen[kind] = pick is not None
        current[kind] = pick or defaults[kind]
    return {"current": current, "defaults": defaults, "chosen": chosen}


# ------------------------------------------------------------- persistence --
def read_settings(store) -> dict:
    """The stored record from `fleet/voice.json` — `{}` when there is none.

    Sanitizing on read means a hand-edited or half-written file degrades to "use the
    defaults" instead of installing an engine nobody named.
    """
    raw = {}
    try:
        raw = store.read_shared(COLLECTION, {}) or {}
    except Exception:                       # a broken file must never stop a boot
        raw = {}
    if not isinstance(raw, dict):
        return {}
    out = {}
    for kind in KINDS:
        pick = sanitize_choice(kind, raw.get(kind))
        if pick:
            out[kind] = pick
    if raw.get("updated_at"):
        try:
            out["updated_at"] = int(raw["updated_at"])
        except (TypeError, ValueError):
            pass
    return out


def write_settings(store, settings: dict) -> bool:
    """Persist the record (atomically — `JsonStore` writes a temp file and renames)."""
    return bool(store.write_shared(COLLECTION, settings))


# ------------------------------------------------------- local Piper voices --
_ONNX = ".onnx"


def voice_name(path: str) -> str:
    """`/models/en_US-amy-medium.onnx` → `en_US-amy-medium` (the name the picker uses)."""
    base = os.path.basename((path or "").strip())
    return base[:-len(_ONNX)] if base.endswith(_ONNX) else base


def piper_voices(model_path: str = "", voices_dir: str = "") -> List[str]:
    """Local Piper voice names, best first: whatever `MOXIE_PIPER_MODEL` points at, then
    every `*.onnx` in `voices_dir`, de-duplicated.

    File presence only — whether the `piper` package is importable is
    `PiperSynthesizer.available()`'s question, and the caller must ask both (a voice file
    with no runtime cannot speak, and a runtime with no voice file has nothing to say it
    with). The voices are git-ignored (63 MB each), so an empty list is the normal state
    of a fresh clone, not an error.
    """
    names: List[str] = []
    seen = set()

    def _add(name):
        if name and name not in seen:
            seen.add(name)
            names.append(name)

    if model_path:
        _add(voice_name(model_path))
    if voices_dir:
        try:
            for entry in sorted(os.listdir(voices_dir)):
                if entry.endswith(_ONNX):
                    _add(entry[:-len(_ONNX)])
        except OSError:
            pass
    return names


def piper_voice_path(name: str, model_path: str = "", voices_dir: str = "") -> str:
    """The `.onnx` file behind a voice name, or "" when it cannot be found.

    `MOXIE_PIPER_MODEL` wins when it names the same voice, so an operator who pointed at a
    file outside the voices directory keeps that exact file.
    """
    name = (name or "").strip()
    if not name:
        return ""
    if model_path and voice_name(model_path) == name:
        return model_path
    if voices_dir:
        candidate = os.path.join(voices_dir, name + _ONNX)
        if os.path.isfile(candidate):
            return candidate
    return ""


# ------------------------------------------------------- gateway discovery --
def _thread_submit(fn: Callable[[], None]) -> None:
    """Run `fn` on a throwaway daemon thread — the default for `GatewayCatalog`."""
    threading.Thread(target=fn, daemon=True).start()


class GatewayCatalog:
    """A cached `GET /v1/models`, refreshed in the background, that never blocks a turn.

    Listing a gateway's models is a network call to someone else's box, and the console
    asks for it on every page load. So: the answer is cached for `ttl_s`, a stale cache is
    refreshed **off the calling thread**, and the caller always gets an immediate answer —
    the last good list, or an empty one plus `discovering: True` on the very first ask.

    A failure keeps the previous good list and reports the exception's class name in
    `gateway_error`, because a card that empties itself the moment a proxy hiccups is
    worse than a card that says "gateway unreachable" beside the options it already had.

    `list_models` is the only seam: any `() -> [ids]` callable, so every test here runs
    with a fake and spends no request. `submit=lambda fn: fn()` makes it synchronous,
    which is how the TTL is asserted with a fake clock.
    """

    def __init__(self, list_models: Optional[Callable[[], Sequence[str]]] = None, *,
                 ttl_s: float = DEFAULT_TTL_S, clock=time.time, submit=None):
        self._list = list_models
        self.ttl_s = float(ttl_s)
        self._clock = clock
        self._submit = submit or _thread_submit
        self._lock = threading.Lock()
        self._ids: List[str] = []
        self._error = ""
        self._fetched_at = 0.0
        self._inflight = False
        self._ever = False
        self.calls = 0                      # how many listings were actually spent

    @property
    def configured(self) -> bool:
        """True when there is a gateway to ask at all."""
        return self._list is not None

    def snapshot(self, *, refresh: bool = False) -> dict:
        """`{ids, gateway_error, discovering, fetched_at}` — immediately, always."""
        self._maybe_start(refresh)
        return self._read()

    # -- internals ----------------------------------------------------------
    def _read(self) -> dict:
        with self._lock:
            return {"ids": list(self._ids), "gateway_error": self._error,
                    "discovering": bool(self._inflight and not self._ever),
                    "fetched_at": self._fetched_at}

    def _maybe_start(self, refresh: bool) -> None:
        if self._list is None:
            return
        with self._lock:
            if self._inflight:
                return
            stale = (refresh or not self._ever
                     or (self._clock() - self._fetched_at) >= self.ttl_s)
            if not stale:
                return
            self._inflight = True
            self.calls += 1
        self._submit(self._refresh)

    def _refresh(self) -> None:
        ids: List[str] = []
        error = ""
        try:
            ids = [str(m).strip() for m in (self._list() or []) if str(m).strip()]
        except Exception as exc:            # noqa: BLE001 — any failure is a downgrade
            error = type(exc).__name__
        with self._lock:
            if error and self._ids:
                self._error = error         # keep the last good list — never blank it
            else:
                self._ids = ids
                self._error = error
            self._fetched_at = self._clock()
            self._ever = True
            self._inflight = False
