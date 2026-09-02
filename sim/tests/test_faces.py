"""
🎨 Moxie's look — face/appearance customization (openmoxie-feature-audit.md §4.1 ADOPT #9).

The child picks layers; the layers ride down inside `child_pii` as
`ChildDecrypted.face_options` (a *clear* repeated-string field, Cloud.proto:166); and the
pushed `child_pii.id` is re-derived from the chosen layers so the robot cannot composite
from a stale cached texture. Four things are worth a test and they are all here:

  * **the catalog is honest** — every slot and every option in `moxie_sdk/faces.py` traces
    to a line in `docs/`, and this file re-asserts the counts and the exact ids from those
    lines, so an id that appears here without a citation fails the build. The single
    highest-value test in the file is `test_the_catalog_invents_nothing`;
  * **validate / sanitize** — what a parent may send, and what must be refused;
  * **the render** — `face_options` + the cache-buster in the built `RobotCloudConfig`,
    and the byte-for-byte no-change when no look is chosen;
  * **the runtime seam** — a face edit re-pushes, a fleet look layers under a per-robot
    one, and the snapshot shows the texture key (no broker: `helpers_runtime.FakeClient`).

Nothing here has been observed on a physical robot; see `faces.py` for exactly which
parts are cited and which two are flagged assumptions.
"""
import json
import os
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "mqtt"))

from moxie_sdk import faces                                      # noqa: E402
from moxie_sdk.cloud_config import (build_robot_cloud_config,    # noqa: E402
                                    child_pii_from_profile,
                                    merge_config_layers,
                                    sanitize_config_overrides)
from moxie_sdk.store import JsonStore                            # noqa: E402
from moxie_sdk.types import ChildProfile                         # noqa: E402


# --------------------------------------------------------------------------- #
# ① The catalog is exactly what our documents say — and nothing more
# --------------------------------------------------------------------------- #

# `MoxieCustomizationType`, verbatim and in order from
# docs/reverse-engineering/runtime/unity-face-animation.md:34-42 ("14 independent,
# swappable slots"). Written out here as a second, independent copy so a slot silently
# added to or dropped from the SDK fails rather than redefining "what our docs say".
CITED_SLOT_TYPES = ("EyeColor", "EyeDesign", "EyeLid", "Brows", "Mouth", "Nose",
                    "Mustache", "FaceColor", "FaceDesign", "Hair", "Glasses",
                    "Stickers", "Extras", "Misc")

# docs/features/robot-lifecycle.md:281-282 (`Robot.java` EYE_COLORS / FACE_COLORS),
# repeated at docs/features/feature-catalog.md:239-242. Enum member order preserved.
CITED_EYE_COLORS = {"green": "#42D02B", "blue": "#8491EF", "purple": "#9437DE",
                    "brown": "#443319", "gold": "#F4BF03", "teal": "#38ADAE"}
CITED_FACE_COLORS = {"blue": "#BBCFE1", "yellow": "#F0F055", "green": "#9BDB9B",
                     "teal": "#7ED6DD", "pink": "#E1A2A2", "purple": "#C395D4"}


def test_the_slots_are_the_fourteen_our_docs_name():
    assert len(faces.FACE_SLOTS) == 14
    assert tuple(s["type"] for s in faces.FACE_SLOTS) == CITED_SLOT_TYPES


def test_the_catalog_invents_nothing():
    """**The guard that matters.** Our corpus lists concrete options for exactly two of
    the fourteen slots — the eye and face colour enums, with hex. Every other slot is
    named and nothing more (the art lives in a streamed bundle we have never read;
    `unity-assets.md`:19-67, `content-delivery.md`:79). So: an option id that is not in
    one of the two cited enums does not belong in this catalog, however plausible it
    looks. This test fails the moment somebody invents one."""
    cited = {"eye_color": CITED_EYE_COLORS, "face_color": CITED_FACE_COLORS}
    for slot in faces.FACE_SLOTS:
        expected = cited.get(slot["id"], {})
        assert {o["id"]: o["hex"] for o in slot["options"]} == expected, slot["id"]
    assert sum(len(s["options"]) for s in faces.FACE_SLOTS) == 12
    # exactly two slots can be previewed; the other twelve say so out loud
    catalog = faces.face_catalog()
    assert [s["id"] for s in catalog if s["cited"]] == ["eye_color", "face_color"]
    assert sum(1 for s in catalog if not s["cited"]) == 12


def test_the_catalog_is_json_safe_and_stable():
    catalog = faces.face_catalog()
    assert json.loads(json.dumps(catalog)) == catalog        # crosses two processes
    assert faces.face_catalog() is not catalog               # a fresh copy each call
    catalog[0]["options"].clear()
    assert faces.face_catalog()[0]["options"], "the frozen catalog was mutated"


def test_every_slot_has_a_parent_facing_label_and_a_recovered_type():
    for slot in faces.face_catalog():
        assert slot["id"] and slot["label"] and slot["type"]
        assert slot["type"] in CITED_SLOT_TYPES


# --------------------------------------------------------------------------- #
# ② validate_face / the wire labels
# --------------------------------------------------------------------------- #

def test_validate_accepts_a_selection_and_canonicalizes_the_order():
    """Whatever order the form submitted in, the stored selection is in slot order — the
    composite is a stack of layers, and a stack with an unstable order would re-key the
    texture for no reason."""
    out = faces.validate_face({"face_color": "pink", "eye_color": "teal"})
    assert list(out) == ["eye_color", "face_color"]
    assert out == {"eye_color": "teal", "face_color": "pink"}


def test_empty_selections_all_mean_the_default_look():
    for empty in (None, False, "", {}, []):
        assert faces.validate_face(empty) == {}
    assert faces.face_options_list({}) == []


def test_a_slot_set_to_null_is_cleared_not_stored():
    """A robot layer must be able to opt one layer back out of a fleet-default look."""
    assert faces.validate_face({"eye_color": None, "face_color": "teal"}) == {
        "face_color": "teal"}


def test_validate_rejects_an_unknown_slot():
    with pytest.raises(ValueError) as e:
        faces.validate_face({"eyeballs": "teal"})
    assert "eyeballs" in str(e.value)


def test_validate_rejects_an_option_a_cited_slot_does_not_offer():
    with pytest.raises(ValueError) as e:
        faces.validate_face({"eye_color": "chartreuse"})
    assert "chartreuse" in str(e.value) and "teal" in str(e.value)


def test_an_uncited_slot_takes_an_asset_label_because_we_have_no_catalog_for_it():
    """We cannot check a value against a catalog we do not have, so for the twelve slots
    our docs list no options for we check only that it is a plausible asset name. Never
    an invented id — the parent supplies it. (The placeholder below is deliberately not
    a real Moxie asset name: we do not have any, and we do not put one in our tree.)"""
    assert faces.validate_face({"hair": "Whatever_The_Parent_Typed"}) == {
        "hair": "Whatever_The_Parent_Typed"}
    with pytest.raises(ValueError) as e:
        faces.validate_face({"hair": "red shag; DROP TABLE"})
    assert "hair" in str(e.value)


def test_custom_labels_pass_through_untouched_and_deduplicated():
    out = faces.validate_face({"custom": ["Layer_A", "Layer_A", " Layer_B ", ""]})
    assert out == {"custom": ["Layer_A", "Layer_B"]}
    assert faces.validate_face(["Layer_A"]) == {"custom": ["Layer_A"]}


def test_custom_labels_are_bounded_and_shape_checked():
    with pytest.raises(ValueError):
        faces.validate_face({"custom": ["ok", "not ok!"]})
    with pytest.raises(ValueError):
        faces.validate_face({"custom": ["L%d" % i for i in range(50)]})
    with pytest.raises(ValueError):
        faces.validate_face({"custom": {"nope": 1}})
    with pytest.raises(ValueError):
        faces.validate_face("teal")


def test_the_wire_label_is_two_cited_spellings_joined():
    """ASSUMPTION, isolated in one function (`faces.py`, "the label format"): the slot's
    `MoxieCustomizationType` name and the enum member name, joined. Both halves are
    quoted from our docs; only the join is ours."""
    assert faces.face_option_label("eye_color", "teal") == "EyeColor_teal"
    assert faces.face_option_label("face_color", "pink") == "FaceColor_pink"
    with pytest.raises(ValueError):
        faces.face_option_label("nope", "teal")


def test_face_options_lists_slot_layers_first_then_customs():
    labels = faces.face_options_list(
        {"face_color": "pink", "eye_color": "teal", "custom": ["Layer_A"]})
    assert labels == ["EyeColor_teal", "FaceColor_pink", "Layer_A"]
    assert all(isinstance(x, str) for x in labels)


def test_describe_face_is_readable_or_empty():
    assert faces.describe_face({}) == ""
    text = faces.describe_face({"eye_color": "teal", "custom": ["Layer_A"]})
    assert "Teal" in text and "1 custom layer" in text


# --------------------------------------------------------------------------- #
# ③ The cache-buster
# --------------------------------------------------------------------------- #

def test_the_same_look_always_yields_the_same_texture_key():
    """Deterministic on purpose: an idempotent re-push (a reconnect, a volume change)
    must not churn the child's id and make the robot recomposite for nothing."""
    a = faces.face_child_id(["EyeColor_teal"], "Sam")
    assert a == faces.face_child_id(["EyeColor_teal"], "Sam")


def test_any_change_of_any_layer_yields_a_different_texture_key():
    base = faces.face_child_id(["EyeColor_teal"], "Sam")
    assert faces.face_child_id(["EyeColor_gold"], "Sam") != base
    assert faces.face_child_id(["EyeColor_teal", "Hair_X"], "Sam") != base
    assert faces.face_child_id(["EyeColor_teal"], "Alex") != base   # per child, too
    assert faces.face_child_id([], "Sam") != base


def test_the_texture_key_looks_like_the_uuid_the_field_otherwise_carries():
    import uuid
    value = faces.face_child_id(["EyeColor_teal"], "Sam")
    assert str(uuid.UUID(value)) == value


# --------------------------------------------------------------------------- #
# ④ The rendered RobotCloudConfig
# --------------------------------------------------------------------------- #

CHILD = ChildProfile(nickname="Sam")


def test_no_face_is_byte_for_byte_the_document_we_pushed_before_this_existed():
    pii = child_pii_from_profile(CHILD)
    assert pii == {"nickname": "Sam"}
    assert child_pii_from_profile(CHILD, None) == pii
    assert child_pii_from_profile(CHILD, {}) == pii
    cfg = build_robot_cloud_config(CHILD)
    assert "face_options" not in cfg["child_pii"] and "id" not in cfg["child_pii"]


def test_a_chosen_face_renders_into_child_pii_with_the_cache_buster():
    cfg = build_robot_cloud_config(CHILD, face={"eye_color": "teal",
                                                "face_color": "pink"})
    pii = cfg["child_pii"]
    assert pii["face_options"] == ["EyeColor_teal", "FaceColor_pink"]
    assert pii["id"] == faces.face_child_id(pii["face_options"], "Sam")
    assert json.loads(json.dumps(cfg)) == cfg          # the whole document stays JSON


def test_changing_one_layer_changes_the_pushed_id():
    a = build_robot_cloud_config(CHILD, face={"eye_color": "teal"})["child_pii"]["id"]
    b = build_robot_cloud_config(CHILD, face={"eye_color": "gold"})["child_pii"]["id"]
    c = build_robot_cloud_config(CHILD, face={"eye_color": "teal"})["child_pii"]["id"]
    assert a != b and a == c


def test_the_builder_validates_what_it_is_handed():
    with pytest.raises(ValueError):
        build_robot_cloud_config(CHILD, face={"eye_color": "chartreuse"})


# --------------------------------------------------------------------------- #
# ⑤ sanitize_config_overrides — the console's whitelist
# --------------------------------------------------------------------------- #

def test_sanitize_accepts_a_face_and_keeps_it_json_safe():
    out = sanitize_config_overrides({"face": {"eye_color": "teal", "custom": ["L_A"]}})
    assert out == {"face": {"eye_color": "teal", "custom": ["L_A"]}}
    assert json.loads(json.dumps(out)) == out
    # and it feeds the builder unchanged
    cfg = build_robot_cloud_config(CHILD, **out)
    assert cfg["child_pii"]["face_options"] == ["EyeColor_teal", "L_A"]


def test_sanitize_clears_a_face_with_null():
    assert sanitize_config_overrides({"face": None}) == {"face": None}
    assert sanitize_config_overrides({"face": {}}) == {"face": None}


def test_sanitize_rejects_a_bad_face():
    for bad in ({"eye_color": "chartreuse"}, {"nope": "x"}, {"custom": ["bad label!"]}):
        with pytest.raises(ValueError):
            sanitize_config_overrides({"face": bad})


def test_sanitize_still_drops_unknown_keys_beside_a_face():
    assert sanitize_config_overrides(
        {"face": {"eye_color": "gold"}, "secret_key": "nope"}) == {
            "face": {"eye_color": "gold"}}


# --------------------------------------------------------------------------- #
# ⑥ Fleet ⊕ per-robot layering (PR #24's rule, applied to appearance)
# --------------------------------------------------------------------------- #

def test_a_house_look_and_a_robot_look_stack_layer_by_layer():
    merged = merge_config_layers({"face": {"eye_color": "teal"}},
                                 {"face": {"face_color": "pink"}})
    assert merged["face"] == {"eye_color": "teal", "face_color": "pink"}
    assert faces.face_options_list(merged["face"]) == ["EyeColor_teal",
                                                       "FaceColor_pink"]


def test_a_robot_wins_the_slot_it_sets_and_inherits_the_rest():
    merged = merge_config_layers({"face": {"eye_color": "teal", "face_color": "pink"}},
                                 {"face": {"eye_color": "gold"}})
    assert merged["face"] == {"eye_color": "gold", "face_color": "pink"}


def test_a_robot_clearing_the_face_beats_the_house_look():
    """Same rule as `weekday_bedtime`: an explicit null from the robot layer clears an
    inherited value, so "this robot wears nothing" stays expressible."""
    assert merge_config_layers({"face": {"eye_color": "teal"}},
                               {"face": None})["face"] is None


# --------------------------------------------------------------------------- #
# ⑦ The runtime seam — a face edit re-pushes, and the snapshot shows the key
# --------------------------------------------------------------------------- #

CONFIG_TOPIC = "/devices/{d}/config"


def _runtime(tmp_path, devices=("d_one", "d_two"), allow=True):
    pytest.importorskip("paho.mqtt.client", reason="the runtime imports paho")
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from helpers_runtime import make_runtime
    from moxie_sdk.app import MoxieApp
    from moxie_sdk.types import RobotContext

    class _App(MoxieApp):
        name = "content"

    rt, _first = make_runtime(_App(), device_id=devices[0],
                              allow_unverified_bots=allow)
    rt.store = JsonStore(root=str(tmp_path))
    for d in devices[1:]:
        rt.robots[d] = RobotContext(device_id=d, child=rt.child)
    return rt


def _pushed(rt, device_id):
    msgs = rt.client.on(CONFIG_TOPIC.format(d=device_id))
    assert msgs, f"no config pushed to {device_id}"
    return msgs[-1]


def test_a_face_edit_re_pushes_the_config_with_the_new_layers(tmp_path):
    rt = _runtime(tmp_path)
    before = len(rt.client.on(CONFIG_TOPIC.format(d="d_one")))
    rt.update_config("d_one", face={"eye_color": "teal"})
    assert len(rt.client.on(CONFIG_TOPIC.format(d="d_one"))) == before + 1
    cfg = _pushed(rt, "d_one")
    assert cfg["child_pii"]["face_options"] == ["EyeColor_teal"]
    assert cfg["child_pii"]["id"]


def test_a_second_face_edit_moves_the_cache_buster(tmp_path):
    rt = _runtime(tmp_path)
    rt.update_config("d_one", face={"eye_color": "teal"})
    first = _pushed(rt, "d_one")["child_pii"]["id"]
    rt.update_config("d_one", face={"eye_color": "teal", "face_color": "pink"})
    second = _pushed(rt, "d_one")["child_pii"]["id"]
    assert second != first
    # and re-pushing the *same* look does not churn it
    rt.update_config("d_one", audio_volume=0.5)
    assert _pushed(rt, "d_one")["child_pii"]["id"] == second


def test_a_house_look_reaches_every_robot_and_one_can_restyle_a_layer(tmp_path):
    rt = _runtime(tmp_path)
    rt.update_fleet_config(face={"eye_color": "teal"})
    for d in ("d_one", "d_two"):
        assert _pushed(rt, d)["child_pii"]["face_options"] == ["EyeColor_teal"]
    rt.update_config("d_one", face={"eye_color": "gold", "face_color": "pink"})
    assert _pushed(rt, "d_one")["child_pii"]["face_options"] == ["EyeColor_gold",
                                                                "FaceColor_pink"]
    assert _pushed(rt, "d_two")["child_pii"]["face_options"] == ["EyeColor_teal"]
    # two robots wearing different looks must not share a texture key
    assert (_pushed(rt, "d_one")["child_pii"]["id"]
            != _pushed(rt, "d_two")["child_pii"]["id"])


def test_clearing_the_face_returns_the_document_to_the_pre_face_shape(tmp_path):
    rt = _runtime(tmp_path)
    rt.update_config("d_one", face={"eye_color": "teal"})
    rt.update_config("d_one", face=None)
    pii = _pushed(rt, "d_one")["child_pii"]
    assert "face_options" not in pii and "id" not in pii


def test_a_pending_robot_is_told_nothing_about_the_look(tmp_path):
    """The un-paired document carries no `child_pii` at all, so it cannot carry a face —
    the pairing gate has to keep holding once appearance rides inside the child profile."""
    rt = _runtime(tmp_path, devices=("d_one",), allow=False)
    rt.update_fleet_config(face={"eye_color": "teal"})
    cfg = _pushed(rt, "d_one")
    assert "child_pii" not in cfg and cfg["pairing_status"] == "unpairing"
    assert "face_options" not in json.dumps(cfg)


def test_status_snapshot_publishes_the_catalog_and_the_texture_key(tmp_path):
    rt = _runtime(tmp_path, devices=("d_one",))
    snap = rt.status_snapshot()
    assert [s["id"] for s in snap["face_catalog"]] == list(faces.SLOT_IDS)
    assert snap["robots"][0]["face_cache_id"] == ""        # no look chosen yet
    rt.update_config("d_one", face={"eye_color": "teal"})
    snap = rt.status_snapshot()
    assert snap["robots"][0]["face_cache_id"] == _pushed(rt, "d_one")["child_pii"]["id"]
    assert json.loads(json.dumps(snap)) == snap            # the console reads it as JSON


def test_the_console_normalizer_carries_the_catalog_and_the_key(tmp_path):
    """`server/moxie_server/fleet.py` is the console's half of the seam: it must pass the
    catalog through without inventing rows, and survive a supervisor that has none."""
    sys.path.insert(0, os.path.join(REPO, "server"))
    from moxie_server.fleet import normalize_fleet

    rt = _runtime(tmp_path, devices=("d_one",))
    rt.update_config("d_one", face={"eye_color": "teal"})
    view = normalize_fleet(rt.status_snapshot())
    assert [s["id"] for s in view["face_catalog"]] == list(faces.SLOT_IDS)
    assert view["robots"][0]["face_cache_id"] == _pushed(rt, "d_one")["child_pii"]["id"]
    assert view["robots"][0]["config_sources"]["face"] == "robot"

    # a pre-face supervisor (and a dead one) must not take the Moxie tab down
    old = normalize_fleet({"ok": True, "app": "content", "robots": [{"device_id": "x"}]})
    assert old["face_catalog"] == [] and old["robots"][0]["face_cache_id"] == ""
    assert normalize_fleet(None)["face_catalog"] == []


def test_the_normalizer_drops_junk_rows_rather_than_rendering_them(tmp_path):
    sys.path.insert(0, os.path.join(REPO, "server"))
    from moxie_server.fleet import normalize_fleet
    view = normalize_fleet({"ok": True, "face_catalog": [
        {"id": "eye_color", "label": "Eyes", "type": "EyeColor",
         "options": [{"id": "teal", "hex": "#38ADAE"}, {"label": "no id"}, "junk",
                     # the swatch colour lands in an inline `style=`, so a value that is
                     # not a plain hex is dropped rather than interpolated
                     {"id": "sneaky", "hex": '"><script>alert(1)</script>'}]},
        {"no_id": True}, "junk"]})
    assert len(view["face_catalog"]) == 1
    slot = view["face_catalog"][0]
    assert slot["options"] == [{"id": "teal", "label": "teal", "hex": "#38ADAE"},
                               {"id": "sneaky", "label": "sneaky"}]
    assert slot["cited"] is True
