"""
🎨 Moxie's look — the child's face/appearance selection, and how it reaches the robot.

Pure (stdlib only), JSON-safe, no I/O. The config path in `cloud_config.py` renders a
selection into the pushed `RobotCloudConfig`; the console picks from `face_catalog()`.

**Carrier.** `RobotCloudConfig.child_pii.face_options` — `repeated string face_options = 17`
on `ChildDecrypted` (recovered Cloud.proto:166; proto-catalog.md:334). A list of layer
labels, composited on the robot. It is clear metadata, not a sealed field
(device-config-and-telemetry.md:44), so a server fills it in directly.

**Slots.** The 14 `MoxieCustomizationType` slots (runtime/unity-face-animation.md:34-42),
in `SLOT_SPINE` order.

**Options** come from `face_assets.json` (`build_face_slots(catalog=)` is the test seam),
each tagged with one of two origins:
  * `recovered-enum` — 12 colours with hex across EyeColor/FaceColor
    (features/robot-lifecycle.md:280-283).
  * `openmoxie-manifest` — 60 `MX_<nnn>_<Group>_<Detail>` asset ids from OpenMoxie (MIT,
    commit `c8c2d380`; see ATTRIBUTION.md). Ids only; slot mapping and labels are ours,
    and anything unmappable goes to the JSON's `unmapped` list. Upstream notes some of
    these crashed Unity (also mqtt-and-conversation.md:780), so each carries
    `caution: true`. The id space is open (behavior-markup.md:161-163): an owner can
    pass their own robot's labels verbatim via `custom` (shape-checked only).
Stickers, Extras and Misc stay empty — no source lists an id and we invent none.

**Wire spelling.** A manifest id travels verbatim (`MX_010_Eyes_Hazel`); anything else is
joined to its slot type (`EyeColor_teal`). ASSUMPTION: that join (`face_option_label`) —
nothing records the label format, so it is one function to fix.

**Cache-buster.** ASSUMPTION, field-proven by OpenMoxie rather than captured: Moxie-Unity
caches a composited face keyed on `child_pii.id`. `face_child_id()` derives a
deterministic UUIDv5 from child key + layers: same face → same id (idempotent re-push),
new face → new id. No face → the field is not emitted.

Nothing here has been observed on a physical Moxie.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from typing import Optional

FACE_CATALOG_VERSION = 2

#: The option table — data shipped in the wheel (test_package_contents.py guards it).
_ASSETS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "face_assets.json")

#: The 14 slots. `id` is our parent-facing key; `type` is the recovered spelling (half of
#: a joined wire label); `label`/`note` are ours.
SLOT_SPINE = (
    {"id": "eye_color", "type": "EyeColor", "label": "Eye colour",
     "note": "the expressive core — colour"},
    {"id": "eye_design", "type": "EyeDesign", "label": "Eye design",
     "note": "iris / shape design"},
    {"id": "eye_lid", "type": "EyeLid", "label": "Eyelids",
     "note": "lid style"},
    {"id": "brows", "type": "Brows", "label": "Eyebrows",
     "note": "expression amplifier"},
    {"id": "mouth", "type": "Mouth", "label": "Mouth",
     "note": "lower-face feature"},
    {"id": "nose", "type": "Nose", "label": "Nose",
     "note": "lower-face feature"},
    {"id": "mustache", "type": "Mustache", "label": "Moustache",
     "note": "lower-face feature"},
    {"id": "face_color", "type": "FaceColor", "label": "Face colour",
     "note": "base head colour"},
    {"id": "face_design", "type": "FaceDesign", "label": "Face design",
     "note": "surface pattern"},
    {"id": "hair", "type": "Hair", "label": "Hair",
     "note": "cosmetic add-on layer"},
    {"id": "glasses", "type": "Glasses", "label": "Glasses",
     "note": "cosmetic add-on layer"},
    {"id": "stickers", "type": "Stickers", "label": "Stickers",
     "note": "cosmetic add-on layer"},
    {"id": "extras", "type": "Extras", "label": "Extras",
     "note": "cosmetic add-on layer"},
    {"id": "misc", "type": "Misc", "label": "Misc",
     "note": "cosmetic add-on layer"},
)

#: The two `origin` values `face_assets.json` may use (anything else is a load error).
OPTION_ORIGINS = ("recovered-enum", "openmoxie-manifest")

#: The origin whose ids are *whole asset labels* and therefore ride the wire verbatim.
VERBATIM_ORIGIN = "openmoxie-manifest"


def face_assets_path() -> str:
    """The table on disk; `MOXIE_FACE_ASSETS` overrides it (like `safety.rules_path()`)."""
    return os.environ.get("MOXIE_FACE_ASSETS", "").strip() or _ASSETS_PATH


def load_face_assets(path: Optional[str] = None) -> dict:
    """Read the option table. Loud on a missing/broken file (a packaging bug)."""
    with open(path or face_assets_path(), encoding="utf-8") as fh:
        return json.load(fh)


def build_face_slots(catalog: Optional[dict] = None) -> tuple:
    """The 14-slot spine ⊕ the option table → `FACE_SLOTS` (`catalog=` is the test seam).
    An unknown slot, duplicate id, bad origin or missing label raises."""
    data = load_face_assets() if catalog is None else catalog
    by_type = data.get("slots") or {}
    known = {s["type"] for s in SLOT_SPINE}
    stray = [k for k in by_type if k not in known]
    if stray:
        raise ValueError(f"face_assets.json names slot(s) our docs do not: "
                         f"{', '.join(sorted(stray))}")
    slots = []
    for spine in SLOT_SPINE:
        options = []
        seen = set()
        for entry in by_type.get(spine["type"]) or ():
            oid = str(entry["id"])
            if oid in seen:
                raise ValueError(f"duplicate {spine['id']} option id {oid!r}")
            seen.add(oid)
            origin = str(entry.get("origin") or "")
            if origin not in OPTION_ORIGINS:
                raise ValueError(f"{spine['id']} option {oid!r} has unknown origin "
                                 f"{origin!r} (known: {', '.join(OPTION_ORIGINS)})")
            row = {"id": oid, "label": str(entry.get("label") or ""), "origin": origin}
            if not row["label"]:
                raise ValueError(f"{spine['id']} option {oid!r} has no label")
            if entry.get("hex"):
                row["hex"] = str(entry["hex"])
            if entry.get("caution"):
                row["caution"] = True
            options.append(row)
        slots.append(dict(spine, options=tuple(options)))
    return tuple(slots)


FACE_ASSETS = load_face_assets()
FACE_SLOTS = build_face_slots(FACE_ASSETS)

SLOT_IDS = tuple(s["id"] for s in FACE_SLOTS)
_SLOT_BY_ID = {s["id"]: s for s in FACE_SLOTS}

CUSTOM_KEY = "custom"                  # verbatim asset labels, never rewritten by us
MAX_CUSTOM_LABELS = len(FACE_SLOTS)    # one hand-entered layer per slot is already plenty
_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}$")

# The one place the assumed wire spelling lives (module docstring, "the label format").
FACE_LABEL_JOIN = "_"

# Fixed namespace for the deterministic cache-buster. Arbitrary but frozen: changing it
# would re-key every household's face at once for no reason.
FACE_CACHE_NAMESPACE = uuid.UUID("6f1a3d5e-2c94-4f7b-9a10-8d5b2e0c7a31")


def face_catalog() -> list:
    """The catalog as plain JSON for the console / `status_snapshot`. Options keep
    `origin`/`caution`/`hex`; `cited: False` marks a slot with no known options."""
    return [{"id": s["id"], "type": s["type"], "label": s["label"], "note": s["note"],
             "options": [dict(o) for o in s["options"]],
             "cited": bool(s["options"])}
            for s in FACE_SLOTS]


def face_option_label(slot_id: str, option_id: str) -> str:
    """One `face_options` entry: a manifest id verbatim, anything else joined to its slot
    type (the ASSUMED spelling — see the module docstring)."""
    slot = _SLOT_BY_ID.get(slot_id)
    if slot is None:
        raise ValueError(f"unknown face slot {slot_id!r}")
    for opt in slot["options"]:
        if opt["id"] == option_id:
            return option_id if opt["origin"] == VERBATIM_ORIGIN else (
                f"{slot['type']}{FACE_LABEL_JOIN}{option_id}")
    return f"{slot['type']}{FACE_LABEL_JOIN}{option_id}"


def validate_face(selection) -> dict:
    """Parent input → a canonical, JSON-safe face selection, or `{}` for "default look".

    Accepts `{slot: option_id, custom?: [labels]}`, a bare list of custom labels, or
    empty/None. A slot mapped to `None`/`""` is cleared (so one robot can opt a layer out
    of a fleet face). Raises ValueError on an unknown slot, an uncatalogued option for a
    cited slot, or an implausible label (the console answers 400)."""
    if selection is None or selection is False or selection == "" or selection == []:
        return {}
    if isinstance(selection, (list, tuple)):
        selection = {CUSTOM_KEY: list(selection)}
    if not isinstance(selection, dict):
        raise ValueError("face must be an object of {slot: option} (or a list of labels)")

    unknown = [k for k in selection if k != CUSTOM_KEY and k not in _SLOT_BY_ID]
    if unknown:
        raise ValueError(f"unknown face slot(s): {', '.join(sorted(map(str, unknown)))} "
                         f"(known: {', '.join(SLOT_IDS)})")

    out = {}
    for slot in FACE_SLOTS:                        # canonical order, not the caller's
        sid = slot["id"]
        if sid not in selection:
            continue
        raw = selection[sid]
        if raw is None or raw is False or raw == "":
            continue                               # an explicit clear → default layer
        option_id = str(raw).strip()
        allowed = [o["id"] for o in slot["options"]]
        if allowed:
            if option_id not in allowed:
                raise ValueError(
                    f"unknown {sid} option {option_id!r} (offered: {', '.join(allowed)}"
                    f"; an id we do not catalogue goes in face.custom)")
        elif not _LABEL_RE.match(option_id):
            # Uncatalogued slot: only the label's shape can be checked.
            raise ValueError(f"bad {sid} value {option_id!r} — neither our recovered docs "
                             f"nor the ingested manifest list options for this slot, so "
                             f"it must be an asset label (letters, digits, . _ -; "
                             f"max 64)")
        out[sid] = option_id

    customs = selection.get(CUSTOM_KEY)
    if customs not in (None, "", [], ()):
        if isinstance(customs, str):
            customs = [customs]
        if not isinstance(customs, (list, tuple)):
            raise ValueError("face.custom must be a list of asset labels")
        if len(customs) > MAX_CUSTOM_LABELS:
            raise ValueError(f"too many custom face labels (max {MAX_CUSTOM_LABELS})")
        clean = []
        for label in customs:
            text = str(label).strip()
            if not text:
                continue
            if not _LABEL_RE.match(text):
                raise ValueError(f"bad face asset label {label!r} "
                                 "(letters, digits, . _ -; max 64 chars)")
            if text not in clean:
                clean.append(text)
        if clean:
            out[CUSTOM_KEY] = clean
    return out


def face_options_list(selection) -> list:
    """A selection → `child_pii.face_options`: slot layers in `FACE_SLOTS` order, then
    `custom` labels. `[]` for none (the caller then omits the field)."""
    sel = selection if isinstance(selection, dict) else validate_face(selection)
    labels = [face_option_label(sid, sel[sid]) for sid in SLOT_IDS if sid in sel]
    labels.extend(sel.get(CUSTOM_KEY, []))
    return labels


def face_child_id(labels, child_key: str = "") -> str:
    """The cache-buster: a deterministic UUIDv5 `child_pii.id` for this exact face (see
    the module docstring)."""
    joined = "\x1f".join([str(child_key or "")] + [str(x) for x in (labels or [])])
    return str(uuid.uuid5(FACE_CACHE_NAMESPACE, joined))


def describe_face(selection) -> str:
    """A one-line, parent-readable summary ("teal eyes · pink face · 2 custom layers")
    for a console status line or a log note. Empty string for the default look."""
    sel = selection if isinstance(selection, dict) else validate_face(selection)
    parts = []
    for slot in FACE_SLOTS:
        sid = slot["id"]
        if sid not in sel:
            continue
        chosen = sel[sid]
        label = next((o["label"] for o in slot["options"] if o["id"] == chosen), chosen)
        parts.append(f"{slot['label'].lower()}: {label}")
    n = len(sel.get(CUSTOM_KEY, []))
    if n:
        parts.append(f"{n} custom layer{'' if n == 1 else 's'}")
    return " · ".join(parts)
