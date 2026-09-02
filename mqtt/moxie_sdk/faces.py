"""
🎨 Moxie's look — the child's face/appearance selection, and how it reaches the robot.

Pure (stdlib only), JSON-safe, no I/O. The config path in `cloud_config.py` renders a
selection into the pushed `RobotCloudConfig`; the console picks from `face_catalog()`.

WHAT OUR RECOVERED DOCS ESTABLISH (and what they do not)
--------------------------------------------------------
**The carrier.** Appearance rides down inside the child profile, not beside it:
`RobotCloudConfig.child_pii` is a `ChildDecrypted`, and `ChildDecrypted` carries
**`repeated string face_options = 17`**
(`docs/reverse-engineering/protocol/recovered-proto/embodied/logging/Cloud.proto`:166,
catalogued at `docs/reverse-engineering/protocol/proto-catalog.md`:334; the sealed twin
`ChildEncrypted.face_options = 16` is Cloud.proto:144 · proto-catalog.md:313). It is a
*list of layer labels*, so the face is composited from independent layers rather than
picked as one whole picture. `device-config-and-telemetry.md`:53 lists `face_options`
among the child-profile fields a server fills in, and
`docs/architecture/mqtt-and-conversation.md`:332 names it as one of the `child_pii`
fields "where our `server/` child profile feeds in". It is **not** one of the sealed
fields: `device-config-and-telemetry.md`:52-54 and `crypto-and-keys.md`:506-508 both list
`face_options` among the *clear* metadata that "sits alongside" the `*_encrypted` blobs, so
a server fills it in directly without touching the crypto path.

**The slot vocabulary — 14 layers, cited.** `docs/reverse-engineering/runtime/
unity-face-animation.md`:34-42 records `MoxieCustomizationType` as **"14 independent,
swappable slots"** and names every one: `EyeColor` · `EyeDesign` · `EyeLid` · `Brows` ·
`Mouth` · `Nose` · `Mustache` · `FaceColor` · `FaceDesign` · `Hair` · `Glasses` ·
`Stickers` · `Extras` · `Misc`. That is the whole anatomy; `FACE_SLOTS` below is it,
in the document's order.

**The option vocabulary — two origins, kept apart.** The catalog is *data*:
`moxie_sdk/face_assets.json`, loaded at import by `load_face_assets()` and turned into
slots by `build_face_slots(catalog=)` — one seam, so a test can hand in its own table.
Every option carries an `origin`, and there are exactly two of those.

`origin: "recovered-enum"` — **12 options across 2 slots, from our own corpus, with hex**,
which makes them the only ones a picker can truly *preview*:
  * `EyeColor{green, blue, purple, brown, gold, teal}` — green `42D02B`, blue `8491EF`,
    purple `9437DE`, brown `443319`, gold `F4BF03`, teal `38ADAE`
  * `FaceColor{blue, yellow, green, teal, pink, purple}` — blue `BBCFE1`, yellow `F0F055`,
    green `9BDB9B`, teal `7ED6DD`, pink `E1A2A2`, purple `C395D4`
(`docs/features/robot-lifecycle.md`:280-283 = the `Robot.java` `EYE_COLORS`/`FACE_COLORS`
constants; repeated at `docs/features/feature-catalog.md`:238-241, which also gives the
Channel-1 spelling `ChildrenModel.eye-color`/`face-color` → `PUT children/{id}`, gated by
the account flags `supports-eye-color`/`supports-face-color`, and
`docs/reverse-engineering/phone/rest-api.md`:412 lists both keys on `ChildrenModel`.)

`origin: "openmoxie-manifest"` — **60 asset ids ingested as data** from OpenMoxie (MIT),
`site/hive/content/data.py::MOXIE_CUSTOMIZATIONS`, commit `c8c2d380`, cited in full in the
JSON's own `source` block and in `ATTRIBUTION.md`. These are real
`MX_<nnn>_<Group>_<Detail>` labels harvested from a robot that project's authors could
run — precisely the thing our corpus structurally *cannot* give us, because the
customization art is loaded by `MoxieCustomizationAsset` / `MoxieCustomizationPreview`
out of a **streamed** bundle (`content-delivery.md`:79, source `REMOTE_ASSETBUNDLES`), not
the base APK, which is why the UnityPy inventory of `sharedassets1` in
`unity-assets.md`:19-67 found none of them. We took **the id strings and nothing else** —
no code, no comments, no function bodies. The slot mapping (each `MX_<nnn>_<Group>_`
prefix → exactly one recovered `MoxieCustomizationType`) and every human-readable label
are ours, and an id whose prefix does not map with confidence goes to the JSON's
`unmapped` list rather than being guessed into a slot. All 60 mapped; `unmapped` is empty.

That is **72 options across 11 of the 14 slots**. `Stickers`, `Extras` and `Misc` are
still named and empty — neither source lists a single id for them, and we invent none.

**Nothing here is hardware-proven, and the manifest half carries an explicit warning.**
Upstream's own note above that list records that some of these assets crashed Unity on a
real Moxie, and that problem assets should be removed once found; it does not say which,
so **every manifest-origin entry carries `caution: true`**. Our own corpus says the same
thing independently — `mqtt-and-conversation.md`:824, "some face customization assets
crash Unity and are excluded". And the id space is genuinely open: `behavior-markup.md`
:161-163 records that the generators "accept **any** id the loaded bundle defines", so a
robot whose streamed bundle differs may not carry any of these. An owner who knows their
own robot's labels passes them verbatim through `custom`, which this module never
rewrites; we validate a custom label's *shape* and nothing more, and the console says so.

**The wire spelling depends on the origin.** A `recovered-enum` option is an enum *member*
name, so `face_option_label()` joins it to the slot's `MoxieCustomizationType` spelling
(the ASSUMPTION below) → `EyeColor_teal`. An `openmoxie-manifest` option is already a
whole asset label, so it travels **verbatim** → `MX_010_Eyes_Hazel`. A value typed into
one of the three still-empty slots is joined too, because we have nothing better to do
with it.

**ASSUMPTION — the label format.** `face_options` is `repeated string`; nothing in our
corpus records what those strings look like. `face_option_label()` joins two *cited*
spellings with an underscore — the `MoxieCustomizationType` slot name and the enum member
name — e.g. `EyeColor` + `teal` → `"EyeColor_teal"`. Every character comes from a quoted
doc; only the join is ours. It is one function, so a capture that contradicts it is a
one-line fix, and `custom` labels bypass it entirely. (This is the same treatment
`cloud_config.UNPAIRED_PAIRING_STATUS` and `WAKE_DAY_NAMES` get.)

**ASSUMPTION — the cache-buster.** A layered face is composited into a texture, and a
robot that has already composited one has no reason to redo the work. Our corpus does not
record the cache key: it gives `ChildDecrypted.id = 14` (Cloud.proto:163 · proto-catalog.md
:331) as the child's identity inside the pushed config, `SwitchUserConfig{action,
restore_id, child_id, force, child_name}` as the config's user-switch lever (Cloud.proto
:177-184 · proto-catalog.md:341-347), and `USER_DATA_UPDATE` as both the cloud-visible
lifecycle state (`device-config-and-telemetry.md`:88) and the on-device disengage reason
for "the child's data/profile is being updated" (`power-and-system-events.md`:85) — a
profile edit is a *user-level* event on this robot, not a cosmetic one. It does **not**
say the texture cache is keyed on `child_pii.id`.

OpenMoxie's face editor does say so, from a server that drives real robots: it writes a
fresh `uuid4` into `child_pii["id"]` with the comment that Moxie-Unity keeps a cached face
record keyed on that field. So this is **field-proven, not capture-proven**, exactly like
`UNPAIRED_PAIRING_STATUS`. We take the mechanism and improve it: instead of a random uuid
(which churns the child's identity on every save, even a no-op one), `face_child_id()`
derives a **deterministic UUIDv5** from the child key + the rendered layer list. Same face
⇒ same id ⇒ an idempotent re-push does not disturb the robot; different face ⇒ different
id ⇒ the stale texture record cannot match. With no face chosen the field is not emitted
at all, so a faceless config is byte-for-byte what it was before this module existed.

**Nobody has watched a physical Moxie render any of this.** We have no robot. What is
cited is cited; what is assumed is flagged here, in
`docs/architecture/config-and-telemetry-contract.md`, and in the two constants below.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from typing import Optional

FACE_CATALOG_VERSION = 2

#: Where the option table lives. It is *data*, shipped in the wheel by the
#: `moxie_sdk = ["*.json"]` package-data glob (`sim/tests/test_package_contents.py`
#: guards that), and it is the only place an asset id is written down.
_ASSETS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "face_assets.json")

#: The 14 `MoxieCustomizationType` slots, in the order unity-face-animation.md:34-42
#: lists them. `id` is the parent-facing key we accept and store; `type` is the recovered
#: `MoxieCustomizationType` spelling, which is half of the wire label for a
#: `recovered-enum` option (see the ASSUMPTION above); `label`/`note` are ours. This is
#: the anatomy and nothing else — the options come from `face_assets.json`.
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

#: The two `origin` values `face_assets.json` may use. Anything else is a data bug and
#: the loader says so rather than shipping an option with unknown provenance.
OPTION_ORIGINS = ("recovered-enum", "openmoxie-manifest")

#: The origin whose ids are *whole asset labels* and therefore ride the wire verbatim.
VERBATIM_ORIGIN = "openmoxie-manifest"


def face_assets_path() -> str:
    """The table on disk. `MOXIE_FACE_ASSETS` overrides it, the same escape hatch
    `safety.rules_path()` gives the safety table — an owner who has read their own
    robot's bundle can point us at their own list without forking the SDK."""
    return os.environ.get("MOXIE_FACE_ASSETS", "").strip() or _ASSETS_PATH


def load_face_assets(path: Optional[str] = None) -> dict:
    """Read the option table. Loud on a missing/broken file: it ships in the wheel, so
    its absence is a packaging bug, not a runtime condition to paper over."""
    with open(path or face_assets_path(), encoding="utf-8") as fh:
        return json.load(fh)


def build_face_slots(catalog: Optional[dict] = None) -> tuple:
    """The 14-slot spine ⊕ the option table → `FACE_SLOTS`. The `catalog=` seam is how a
    test substitutes its own table; production passes nothing and gets the shipped one.

    A slot name the spine does not know is refused rather than silently dropped — the
    slots are the 14 recovered `MoxieCustomizationType` names and a fifteenth would mean
    the data and our documents disagree."""
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
    """The catalog as plain JSON (lists, not tuples) — what the console renders and what
    `status_snapshot` publishes. Each option keeps its `origin` (and `caution`, and `hex`
    where we have one), so a UI can say where a choice came from. A slot with
    `options: []` is one neither source lists an id for; `cited` says so out loud so the
    UI never implies we have art we do not."""
    return [{"id": s["id"], "type": s["type"], "label": s["label"], "note": s["note"],
             "options": [dict(o) for o in s["options"]],
             "cited": bool(s["options"])}
            for s in FACE_SLOTS]


def face_option_label(slot_id: str, option_id: str) -> str:
    """One `face_options` entry — see the module docstring, "the wire spelling depends on
    the origin".

    An `openmoxie-manifest` option **is** a whole asset label (`MX_010_Eyes_Hazel`), so it
    goes down untouched. Anything else is an enum member name or a value the parent typed,
    and gets **the assumed spelling**: the `MoxieCustomizationType` slot name
    (unity-face-animation.md:34-42) joined to it (robot-lifecycle.md:281-282 for the
    member names). Both halves are quoted from our docs; only the underscore is ours."""
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

    Accepts the selection object (`{slot: option_id, …}`, optionally with a `custom` list
    of verbatim asset labels), a bare list of custom labels, or an empty/None value.
    A slot mapped to `None`/`""` is *cleared* — which is what a fleet-default face needs
    so one robot can opt a single layer back out (`merge_config_layers` deep-merges the
    face object key-by-key, so the layers stack per slot).

    Raises ValueError on an unknown slot, an option a *cited* slot does not offer, or a
    custom label that is not a plausible asset name — the console turns that into a 400."""
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
            # A slot neither source lists an id for (Stickers/Extras/Misc): we cannot
            # check the value against a catalog we do not have, so we check only that it
            # is a plausible asset label. We never invent one; the parent supplies it.
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
    """A validated selection → the `repeated string face_options` we put in `child_pii`.

    Slot layers first, in `FACE_SLOTS` order (a stable composite order beats the order a
    form happened to submit), then any verbatim `custom` labels. `[]` for no selection,
    and the caller then omits the field entirely."""
    sel = selection if isinstance(selection, dict) else validate_face(selection)
    labels = [face_option_label(sid, sel[sid]) for sid in SLOT_IDS if sid in sel]
    labels.extend(sel.get(CUSTOM_KEY, []))
    return labels


def face_child_id(labels, child_key: str = "") -> str:
    """The cache-buster: a **deterministic** `child_pii.id` for this exact face.

    See the module docstring's second ASSUMPTION. UUIDv5 over `child_key` + the rendered
    layer list, so the value is a real RFC-4122 uuid string (what the field otherwise
    carries), the same face always yields the same id (an idempotent re-push does not
    disturb the robot), and any change of any layer yields a different one — which is the
    whole job: a cached texture record keyed on the old id cannot match the new one."""
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
