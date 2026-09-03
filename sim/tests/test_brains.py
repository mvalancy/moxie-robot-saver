"""
🧠 The brain registry — the pure half: the positive list, the layering, and the pin.

`moxie_sdk/brains.py` is what makes `ai-seam.md` §2's "any AI wears the shell" an
operation rather than a diagram. Until it existed a brain was chosen once, globally, by
`MOXIE_APP`, and `build_app()` returned the LLM app for **anything it did not recognise**.

What is asserted here is the three properties the feature stands on, each in a way that
fails if the property is removed rather than if the code is merely reworded:

  * **positive list** — the table is frozen as a literal, every path that takes a name is
    swept with things that are *nearly* a brain (`llm # the brain`, `gpt5`, `chatgpt`, a
    dict), and each one is refused rather than resolved to a default — while `Echo` and
    ` echo ` are normalised, because normalising a name is not guessing one;
  * **one layering** — `resolve_brain` is checked *against `cloud_config.merge_config_layers`
    itself* over generated layer combinations, so this cannot quietly become a second
    layering with its own precedence;
  * **the pin** — an explicit `MOXIE_APP` beats every stored pick, and the value that
    would have pinned every unconfigured box by accident (`config.MOXIE_APP`'s own `llm`
    default) is proven not to.

Dependency-free, like the module: no `openai`, no gateway, no store, no runtime. The
runtime half — the live swap, the routes, the pushed document — is `test_brain_runtime.py`.
"""
import importlib
import itertools
import os
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
MQTT = os.path.join(REPO, "mqtt")
for _p in (MQTT, os.path.join(MQTT, "supervisor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from moxie_sdk import brains                                        # noqa: E402
from moxie_sdk.cloud_config import (SERVER_ONLY_KEYS,               # noqa: E402
                                    build_robot_cloud_config,
                                    merge_config_layers,
                                    robot_config_kwargs,
                                    sanitize_config_overrides)
from moxie_sdk.types import ChildProfile                            # noqa: E402

#: The four brains, written out. A fifth one is a test edit and a reviewer — the rule
#: `content/ext.py::OPS` states for its own closed table, and the reason this literal
#: exists at all: a registry that is asserted with `set(BRAINS) == set(BRAINS)` asserts
#: nothing about what is in it.
EXPECTED = ("llm", "content", "webhook", "echo")

#: Strings that are *nearly* a brain. Every one of them used to resolve to `LLMApp`.
#: `llm # the brain` is not hypothetical: the dotenv loader once handed exactly that
#: string through as a value (`config._dotenv_value`'s docstring).
NEARLY = ("llm # the brain", "gpt5", "content-modules", "", "none", "webhooks",
          "chatgpt", "llm2", "any", "auto")


# ------------------------------------------------------------- the positive list --

def test_the_table_is_exactly_these_four_brains():
    assert tuple(brains.BRAINS) == EXPECTED
    assert brains.BRAIN_IDS == EXPECTED


def test_every_brain_carries_what_a_card_and_a_refusal_need():
    """A label to render, a group to sort into, a blurb to explain it and the variables
    it cannot run without. `needs` is the part an operator acts on."""
    for name, spec in brains.BRAINS.items():
        assert spec["label"] and spec["group"] and spec["blurb"], name
        assert isinstance(spec["needs"], tuple), name
        for var in spec["needs"]:
            assert var.startswith("MOXIE_"), (name, var)
    assert brains.brain_needs("echo") == (), "echo is the brain that needs nothing"
    assert "MOXIE_LLM_BASE_URL" in brains.brain_needs("llm")


@pytest.mark.parametrize("value", NEARLY)
def test_a_name_that_is_not_in_the_table_is_refused_never_guessed(value):
    """The whole defect, in one assertion: `sanitize_brain` answers `""` — *not* the
    default — for everything the table does not contain, so no caller can turn a typo
    into a silently different brain."""
    assert brains.sanitize_brain(value) == ""
    assert brains.is_brain(value) is False


@pytest.mark.parametrize("value", [None, 3, {"brain": "llm"}, ["llm"], object()])
def test_a_name_that_is_not_even_a_string_is_refused(value):
    assert brains.sanitize_brain(value) == ""


@pytest.mark.parametrize("value", ["Echo", " echo ", "ECHO", "\techo\n"])
def test_case_and_surrounding_space_are_normalised_not_refused(value):
    """Normalising a name is not guessing one: an env line, a dropdown and a hand-written
    curl all spell the same brain slightly differently, and every one of them names a
    member of the table. What is refused is a name that is not IN the table."""
    assert brains.sanitize_brain(value) == "echo"


def test_a_known_name_is_normalised_but_never_invented():
    assert brains.sanitize_brain("llm") == "llm"
    assert brains.describe_brain("content") == "Content modules (content)"
    assert brains.describe_brain("gpt5") == "gpt5", \
        "an unknown name is echoed, never given an invented label"


def test_the_offer_lists_every_brain_so_a_refusal_can_quote_it():
    for name in EXPECTED:
        assert name in brains.offered()


# --------------------------------------------------------------- the dropdown --

def test_options_are_the_table_in_order_with_one_marked_default():
    entries = brains.options(default="content")
    assert brains.option_ids(entries) == list(EXPECTED)
    assert [e["id"] for e in entries if e["default"]] == ["content"]


def test_a_pin_reduces_the_offer_to_exactly_the_pinned_brain():
    entries = brains.filter_options(brains.options(), "echo")
    assert brains.option_ids(entries) == ["echo"]


def test_no_pin_leaves_the_offer_whole():
    entries = brains.filter_options(brains.options(), "")
    assert brains.option_ids(entries) == list(EXPECTED)


def test_filtering_copies_rather_than_mutating_the_caller_s_list():
    entries = brains.options()
    out = brains.filter_options(entries, "")
    out[0]["label"] = "clobbered"
    assert entries[0]["label"] != "clobbered"


# ------------------------------------------------------------------- the pin --

@pytest.mark.parametrize("name", EXPECTED)
def test_every_brain_pins_itself(name):
    """`MOXIE_APP` has no permission-shaped value: `build_app()` branches on the four
    names and each returns exactly that app, so unlike `MOXIE_TTS=tone` all four are
    selections and all four pin (PR #77's lesson, checked rather than copied)."""
    assert brains.pin_for_env(name) == name


@pytest.mark.parametrize("value", ["", "any", "auto", "gpt5", None, "  "])
def test_the_values_that_mean_decide_for_me_pin_nothing(value):
    assert brains.pin_for_env(value) == ""


def test_an_unset_environment_pins_nothing_even_though_the_default_is_a_brain():
    """The accident this design exists to avoid. `config.MOXIE_APP` is
    `os.environ.get("MOXIE_APP", "llm")`, so an unset variable already *reads* as `llm`;
    pinning that resolved value would have locked every box nobody configured out of the
    per-child picker. The pin is computed from the RAW environment instead."""
    assert brains.pin_for_env(os.environ.get("MOXIE_APP_DEFINITELY_UNSET", "")) == ""


def test_the_pin_note_names_the_variable_and_the_way_out():
    note = brains.pin_note("content")
    assert brains.ENV_VAR in note and "content" in note
    assert "any" in note, "a refusal must say how to hand the choice back"
    assert brains.pin_note("any") == "", "nothing pinned, nothing to explain"


def test_honours_pin_is_the_membership_rule_and_nothing_more():
    assert brains.honours_pin("echo", "") is True          # no pin ⇒ anything
    assert brains.honours_pin("echo", "echo") is True
    assert brains.honours_pin("llm", "echo") is False


# --------------------------------------------------------------- the layering --

def test_the_layers_stack_defaults_then_fleet_then_robot():
    r = brains.resolve_brain(default="llm", fleet="content", robot="echo")
    assert (r["brain"], r["source"]) == ("echo", "robot")
    r = brains.resolve_brain(default="llm", fleet="content")
    assert (r["brain"], r["source"]) == ("content", "fleet")
    r = brains.resolve_brain(default="llm")
    assert (r["brain"], r["source"]) == ("llm", "default")


def test_an_unset_layer_is_not_a_choice_of_the_default():
    """`None` and `""` mean *this layer says nothing* — the layer underneath decides.
    Clearing a per-robot pick must fall back to the house rule, not to the appliance."""
    for empty in (None, ""):
        r = brains.resolve_brain(default="llm", fleet="content", robot=empty)
        assert (r["brain"], r["source"]) == ("content", "fleet"), empty


@pytest.mark.parametrize("layers", list(itertools.product(
    ("llm", "content"), (None, "echo", "webhook"), (None, "echo", "content"))))
def test_the_resolution_agrees_with_the_config_merge_it_claims_to_be(layers):
    """The guard against a SECOND layering.

    `brain` is an ordinary key in the ordinary config layers, so resolving it must give
    the same answer as running those layers through `cloud_config.merge_config_layers` —
    the function the pushed document already goes through. If someone gives the brain its
    own precedence (robot under fleet, say, or a default that wins), this fails.
    """
    default, fleet, robot = layers
    merged = merge_config_layers(
        {brains.CONFIG_KEY: default},
        {brains.CONFIG_KEY: fleet} if fleet else {},
        {brains.CONFIG_KEY: robot} if robot else {})
    assert brains.resolve_brain(default=default, fleet=fleet,
                                robot=robot)["brain"] == merged[brains.CONFIG_KEY]


def test_a_layer_naming_something_that_is_not_a_brain_falls_through_and_says_so():
    """A hand-edited `fleet/config.json`, or a record written by a newer version. The
    appliance must keep talking with the layer underneath — `voice_settings.read_settings`'
    rule — and the reason must be readable, or nobody will ever know it happened."""
    r = brains.resolve_brain(default="llm", fleet="gpt5", robot=None)
    assert (r["brain"], r["source"]) == ("llm", "default")
    assert "gpt5" in r["note"] and "llm, content, webhook, echo" in r["note"]


def test_the_environment_s_pin_beats_every_stored_pick():
    """The owner rule. A parent's per-child pick is a *preference*; `MOXIE_APP` is the
    operator's statement about the box, and it wins — with the overruled pick reported,
    so the card can say what happened instead of silently showing something else."""
    r = brains.resolve_brain(default="llm", fleet="content", robot="webhook", pin="echo")
    assert (r["brain"], r["source"], r["requested"]) == ("echo", "pin", "webhook")
    assert brains.ENV_VAR in r["note"]


def test_a_pick_that_agrees_with_the_pin_is_not_reported_as_overruled():
    r = brains.resolve_brain(default="llm", fleet="echo", pin="echo")
    assert (r["brain"], r["requested"], r["note"]) == ("echo", "", "")


def test_the_resolution_always_returns_a_brain_that_exists():
    for default in ("", "gpt5", None, "llm"):
        for fleet in (None, "nonsense", "echo"):
            r = brains.resolve_brain(default=default, fleet=fleet)
            assert r["brain"] in brains.BRAINS, (default, fleet)


def test_the_boot_line_says_what_is_answering_and_why():
    line = brains.boot_line(brains.resolve_brain(default="llm", fleet="content"))
    assert "content" in line and "house rule" in line
    pinned = brains.boot_line(
        brains.resolve_brain(default="llm", robot="content", pin="echo"), device_id="d_1")
    assert "echo" in pinned and "d_1" in pinned and brains.ENV_VAR in pinned


# ------------------------------------------------------------ the console patch --

def test_a_patch_returns_the_brain_to_store():
    assert brains.normalize_brain_patch({"brain": "echo"}) == "echo"
    assert brains.normalize_brain_patch("content") == "content"


@pytest.mark.parametrize("clear", [None, "", "default", "inherit", {"brain": None}])
def test_clearing_a_layer_is_expressible(clear):
    assert brains.normalize_brain_patch(clear) is None


def test_a_patch_naming_something_that_is_not_a_brain_is_refused_with_the_offer():
    with pytest.raises(ValueError) as exc:
        brains.normalize_brain_patch({"brain": "gpt5"})
    for name in EXPECTED:
        assert name in str(exc.value)


def test_a_patch_with_no_brain_key_is_refused_rather_than_ignored():
    with pytest.raises(ValueError):
        brains.normalize_brain_patch({"speech": "gateway:piper-amy"})


def test_a_patch_the_environment_has_pinned_away_is_refused_naming_the_variable():
    with pytest.raises(ValueError) as exc:
        brains.normalize_brain_patch({"brain": "llm"}, pin="echo")
    assert brains.ENV_VAR in str(exc.value)
    assert brains.normalize_brain_patch({"brain": "echo"}, pin="echo") == "echo"


# --------------------------------------------- `brain` as an ordinary config key --

def test_the_config_whitelist_takes_a_brain_and_validates_it():
    assert sanitize_config_overrides({"brain": "content"}) == {"brain": "content"}
    assert sanitize_config_overrides({"brain": None}) == {"brain": None}
    with pytest.raises(ValueError):
        sanitize_config_overrides({"brain": "gpt5"})


def test_the_brain_never_reaches_the_document_pushed_to_the_robot():
    """`brain` rides the config layers because they are the one layering this codebase
    has — and the robot has no field for it. `robot_config_kwargs` is where that
    difference is written down; without it `build_robot_cloud_config` raises `TypeError`
    on the unexpected keyword, which is a crash on every config push."""
    assert brains.CONFIG_KEY in SERVER_ONLY_KEYS
    effective = {"brain": "echo", "audio_volume": 0.4}
    kwargs = robot_config_kwargs(effective)
    assert kwargs == {"audio_volume": 0.4}
    doc = build_robot_cloud_config(ChildProfile(nickname="Sam"), **kwargs)

    def keys(node):
        """Every key in the document, at every depth (`settings.props` included)."""
        for k, v in (node or {}).items():
            yield k
            if isinstance(v, dict):
                yield from keys(v)

    assert brains.CONFIG_KEY not in set(keys(doc)), \
        "the robot must never be told which brain answers it"
    assert "echo" not in repr(doc), "nor the value, under some other name"
    with pytest.raises(TypeError):
        build_robot_cloud_config(ChildProfile(nickname="Sam"), **effective)


# --------------------------------------------------------- registry ↔ builders --

def test_every_brain_in_the_registry_can_actually_be_built_here(monkeypatch):
    """The two tables are halves of one thing: the registry says which names exist, and
    `config.BRAIN_BUILDERS` says how to make one on this box. A brain in one and not the
    other is either a name the card offers and the appliance cannot build, or a builder
    nobody can reach."""
    monkeypatch.setenv("MOXIE_SKIP_DOTENV", "1")
    import config as _c
    c = importlib.reload(_c)
    assert set(c.BRAIN_BUILDERS) == set(brains.BRAINS)
    assert c.default_brain() in brains.BRAINS


def test_an_unknown_moxie_app_now_exits_naming_the_four_real_brains(monkeypatch):
    """It used to return `LLMApp`. On a box with no `MOXIE_LLM_BASE_URL` that typo
    surfaced as the brain-endpoint refusal, naming a variable the operator never meant
    to use."""
    monkeypatch.setenv("MOXIE_SKIP_DOTENV", "1")
    monkeypatch.setenv("MOXIE_APP", "gpt5")
    import config as _c
    c = importlib.reload(_c)
    with pytest.raises(SystemExit) as exc:
        c.build_app()
    for name in EXPECTED:
        assert name in str(exc.value)


def test_building_a_brain_by_name_refuses_one_that_is_not_in_the_registry(monkeypatch):
    """`build_brain` is the seam the *runtime* builds through (`BrainEngines.build`, on
    the first turn of a child whose layer names another brain), so the refusal lives
    there too rather than being trusted to the caller. Without this the check is only
    ever exercised through `default_brain`, and deleting it changes nothing."""
    monkeypatch.setenv("MOXIE_SKIP_DOTENV", "1")
    monkeypatch.delenv("MOXIE_APP", raising=False)
    import config as _c
    c = importlib.reload(_c)
    with pytest.raises(SystemExit) as exc:
        c.build_brain("gpt5")
    for name in EXPECTED:
        assert name in str(exc.value)
    assert c.build_brain("Echo").name == "echo", "…while a known name is still normalised"


def test_an_explicit_moxie_app_pins_and_an_unset_one_does_not(monkeypatch):
    """`config.brain_pin()` reads the RAW variable, which is the whole point: `MOXIE_APP`
    resolved is `llm` on a box where nobody said anything."""
    monkeypatch.setenv("MOXIE_SKIP_DOTENV", "1")
    monkeypatch.delenv("MOXIE_APP", raising=False)
    import config as _c
    c = importlib.reload(_c)
    assert c.MOXIE_APP == "llm", "the resolved value still defaults, as it always did"
    assert c.brain_pin() == "", "…and it does NOT pin"
    assert len(c.brain_engines().available()["available"]) == len(EXPECTED)
    monkeypatch.setenv("MOXIE_APP", "echo")
    c = importlib.reload(_c)
    assert c.brain_pin() == "echo"
    offer = c.brain_engines().available()
    assert brains.option_ids(offer["available"]) == ["echo"]
    assert brains.ENV_VAR in offer["pin_note"]


def test_moxie_app_any_boots_a_brain_but_pins_nothing(monkeypatch):
    """The escape hatch the pin note advertises has to be real — it is what a deployment
    that wants the per-child picker sets, and what our own compose default's operator can
    write in one line of `.env`."""
    monkeypatch.setenv("MOXIE_SKIP_DOTENV", "1")
    monkeypatch.setenv("MOXIE_APP", "any")
    import config as _c
    c = importlib.reload(_c)
    assert c.brain_pin() == ""
    assert c.default_brain() == brains.DEFAULT_BRAIN
    assert brains.option_ids(c.brain_engines().available()["available"]) == list(EXPECTED)


def test_the_default_layer_matches_the_module_s_own_default(monkeypatch):
    """Two places name a fallback brain; they must be the same one, or a box that reads
    the registry and a box that reads `config` disagree about what "nothing set" means."""
    monkeypatch.setenv("MOXIE_SKIP_DOTENV", "1")
    monkeypatch.delenv("MOXIE_APP", raising=False)
    import config as _c
    c = importlib.reload(_c)
    assert c.MOXIE_APP == brains.DEFAULT_BRAIN
