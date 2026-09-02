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

**The option vocabulary — thin, and honestly so.** Our corpus lists concrete choices for
exactly **two** of the fourteen slots, and it lists them *with hex*, so they are the two a
picker can actually preview:
  * `EyeColor{green, blue, purple, brown, gold, teal}` — green `42D02B`, blue `8491EF`,
    purple `9437DE`, brown `443319`, gold `F4BF03`, teal `38ADAE`
  * `FaceColor{blue, yellow, green, teal, pink, purple}` — blue `BBCFE1`, yellow `F0F055`,
    green `9BDB9B`, teal `7ED6DD`, pink `E1A2A2`, purple `C395D4`
(`docs/features/robot-lifecycle.md`:280-283 = the `Robot.java` `EYE_COLORS`/`FACE_COLORS`
constants; repeated at `docs/features/feature-catalog.md`:238-241, which also gives the
Channel-1 spelling `ChildrenModel.eye-color`/`face-color` → `PUT children/{id}`, gated by
the account flags `supports-eye-color`/`supports-face-color`, and
`docs/reverse-engineering/phone/rest-api.md`:412 lists both keys on `ChildrenModel`.)

For the other **twelve** slots our corpus names the slot and stops. It does not contain a
single concrete face asset label, and it is structurally clear about why: the
customization art is loaded by `MoxieCustomizationAsset` / `MoxieCustomizationPreview`
out of a **streamed** bundle (`content-delivery.md`:79, source `REMOTE_ASSETBUNDLES`),
not the base APK — which is exactly why the UnityPy inventory of `sharedassets1` in
`unity-assets.md`:19-67 found none of them, and why that file's own gap note ends at the
streamed `rig3animations` bundle ("the last in-scope clean-room gap"). Worse for
guessing: `behavior-markup.md`:161-163 records that the generators "accept **any** id the
loaded bundle defines", so the id space is bundle-defined and cannot be inferred at all.
So this catalog ships **12 cited options across 2 slots and zero invented ids**. The
remaining slots are listed (a parent should see the whole anatomy) but carry no options,
and say so.

OpenMoxie (MIT) ships a ~60-entry table of real `MX_*` asset labels harvested from a robot
its authors could run. We did not copy it, and we do not reproduce ids we cannot cite —
see ATTRIBUTION.md. An owner who *does* know their robot's labels can pass them verbatim
through `custom`, which this module never rewrites. Two warnings travel with that
freedom, both from our own docs: `mqtt-and-conversation.md`:824 records that **"some face
customization assets crash Unity and are excluded"**, and the id space is bundle-defined
(above) — so an unrecognised label is a real risk on a real robot, not a typo. We
therefore validate a custom label's *shape* and nothing more, and the console says so.

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

import re
import uuid

FACE_CATALOG_VERSION = 1

# The 14 `MoxieCustomizationType` slots, in the order unity-face-animation.md:34-42 lists
# them. `id` is the parent-facing key we accept and store; `type` is the recovered
# `MoxieCustomizationType` spelling, which is half of the wire label (see the ASSUMPTION
# above); `options` are the choices our corpus actually lists, with the hex it gives.
#
# Twelve of the fourteen have `options: ()`. That is not an oversight — see the module
# docstring. Never add an id here that cannot be cited to a line in docs/.
FACE_SLOTS = (
    {"id": "eye_color", "type": "EyeColor", "label": "Eye colour",
     "note": "the expressive core — colour",
     "options": (
         {"id": "green", "label": "Green", "hex": "#42D02B"},
         {"id": "blue", "label": "Blue", "hex": "#8491EF"},
         {"id": "purple", "label": "Purple", "hex": "#9437DE"},
         {"id": "brown", "label": "Brown", "hex": "#443319"},
         {"id": "gold", "label": "Gold", "hex": "#F4BF03"},
         {"id": "teal", "label": "Teal", "hex": "#38ADAE"},
     )},
    {"id": "eye_design", "type": "EyeDesign", "label": "Eye design",
     "note": "iris / shape design", "options": ()},
    {"id": "eye_lid", "type": "EyeLid", "label": "Eyelids",
     "note": "lid style", "options": ()},
    {"id": "brows", "type": "Brows", "label": "Eyebrows",
     "note": "expression amplifier", "options": ()},
    {"id": "mouth", "type": "Mouth", "label": "Mouth",
     "note": "lower-face feature", "options": ()},
    {"id": "nose", "type": "Nose", "label": "Nose",
     "note": "lower-face feature", "options": ()},
    {"id": "mustache", "type": "Mustache", "label": "Moustache",
     "note": "lower-face feature", "options": ()},
    {"id": "face_color", "type": "FaceColor", "label": "Face colour",
     "note": "base head colour",
     "options": (
         {"id": "blue", "label": "Blue", "hex": "#BBCFE1"},
         {"id": "yellow", "label": "Yellow", "hex": "#F0F055"},
         {"id": "green", "label": "Green", "hex": "#9BDB9B"},
         {"id": "teal", "label": "Teal", "hex": "#7ED6DD"},
         {"id": "pink", "label": "Pink", "hex": "#E1A2A2"},
         {"id": "purple", "label": "Purple", "hex": "#C395D4"},
     )},
    {"id": "face_design", "type": "FaceDesign", "label": "Face design",
     "note": "surface pattern", "options": ()},
    {"id": "hair", "type": "Hair", "label": "Hair",
     "note": "cosmetic add-on layer", "options": ()},
    {"id": "glasses", "type": "Glasses", "label": "Glasses",
     "note": "cosmetic add-on layer", "options": ()},
    {"id": "stickers", "type": "Stickers", "label": "Stickers",
     "note": "cosmetic add-on layer", "options": ()},
    {"id": "extras", "type": "Extras", "label": "Extras",
     "note": "cosmetic add-on layer", "options": ()},
    {"id": "misc", "type": "Misc", "label": "Misc",
     "note": "cosmetic add-on layer", "options": ()},
)

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
    `status_snapshot` publishes. A slot with `options: []` is one our docs name but list
    no choices for; `cited` says so out loud so the UI never implies we have art."""
    return [{"id": s["id"], "type": s["type"], "label": s["label"], "note": s["note"],
             "options": [dict(o) for o in s["options"]],
             "cited": bool(s["options"])}
            for s in FACE_SLOTS]


def face_option_label(slot_id: str, option_id: str) -> str:
    """One `face_options` entry — **the assumed spelling** (see the module docstring).

    Both halves are quoted from our docs: the `MoxieCustomizationType` slot name
    (unity-face-animation.md:34-42) and the enum member name (robot-lifecycle.md:281-282).
    Only the underscore between them is ours."""
    slot = _SLOT_BY_ID.get(slot_id)
    if slot is None:
        raise ValueError(f"unknown face slot {slot_id!r}")
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
                    f"unknown {sid} option {option_id!r} (offered: {', '.join(allowed)})")
        elif not _LABEL_RE.match(option_id):
            # A slot our docs name but list no options for: we cannot check the value
            # against a catalog we do not have, so we check only that it is a plausible
            # asset label. We never invent one; the parent supplies it.
            raise ValueError(f"bad {sid} value {option_id!r} — our recovered docs list no "
                             f"options for this slot, so it must be an asset label "
                             f"(letters, digits, . _ -; max 64)")
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
