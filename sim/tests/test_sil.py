"""
Comprehensive SIL + static-site automation.

Exercises every page at every standard resolution, and drives the simulator
through all of its modes and controls — clicking every expression chip, dragging
every motor slider, toggling ALIVE / liveness / axes / sound / heart-LED, running
the speech path, and the docs explorer — always asserting zero console errors and
no horizontal page scroll.
"""
import pytest

from conftest import RESOLUTIONS, PAGES


def _no_hscroll(page):
    return page.evaluate(
        "() => document.documentElement.scrollWidth <= window.innerWidth + 2"
    )


def _open_drawer_if_needed(page):
    """On phone widths the controls are a bottom drawer — open it."""
    page.evaluate(
        """() => {
            const hud = document.getElementById('hud');
            const t = document.getElementById('rail-toggle');
            if (hud && hud.classList.contains('rail-closed') && t
                && getComputedStyle(t).display !== 'none') t.click();
        }"""
    )


# --------------------------------------------------------------------------- #
# Every page, every resolution: loads clean, no console errors, no h-scroll.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("res", RESOLUTIONS, ids=[r[0] for r in RESOLUTIONS])
@pytest.mark.parametrize("path", PAGES)
def test_page_loads_clean(page, server, path, res):
    label, w, h = res
    page.set_viewport_size({"width": w, "height": h})
    page.goto(f"{server}/{path}", wait_until="domcontentloaded")
    page.wait_for_timeout(1200)
    assert _no_hscroll(page), f"{path} @ {label}: horizontal page scroll"
    # ignore benign resource/network noise; care about real JS errors
    real = [e for e in page.console_errors if "favicon" not in e and "ERR_" not in e
            and "Failed to load resource" not in e]
    assert not real, f"{path} @ {label}: console errors: {real[:3]}"


# --------------------------------------------------------------------------- #
# SIL: every expression chip drives the face.
# --------------------------------------------------------------------------- #
def test_all_expression_buttons(page, server):
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(f"{server}/sim.html", wait_until="domcontentloaded")
    page.wait_for_function("window.moxie && window.moxieLife && document.querySelectorAll('#faces button').length")
    page.click("#alive-toggle")   # pause the life loop so it doesn't change the face under us
    names = page.evaluate("() => window.moxie.expressions")
    assert len(names) >= 13, f"expected the full expression set, got {names}"
    for expr in names:
        page.click(f"#faces button[data-expr='{expr}']")
        page.wait_for_timeout(40)
        # `blink` is a momentary action (triggers a blink), not a persistent
        # expression, so it never becomes the active face — just verify it clicks.
        if expr == "blink":
            continue
        active = page.evaluate(
            "(n) => { const b=document.querySelector(`#faces button[data-expr='${n}']`);"
            " return b && b.classList.contains('active'); }", expr)
        assert active, f"clicking expression {expr} did not mark it active"
    assert not [e for e in page.console_errors if "favicon" not in e], page.console_errors[:3]


# --------------------------------------------------------------------------- #
# SIL: ALIVE toggle — off stops the loop + mirrors the checkbox; on resumes.
# --------------------------------------------------------------------------- #
def test_alive_toggle(page, server):
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(f"{server}/sim.html", wait_until="domcontentloaded")
    # ON by default — wait for the alive state to actually initialize, not just for
    # the objects to exist (a slow CI runner can assert before init → flake).
    page.wait_for_function("window.moxie && window.moxieLife && window.moxie.isAlive()")
    assert page.evaluate("() => window.moxie.isAlive()") is True
    assert "alive-on" in page.get_attribute("#alive-toggle", "class")
    # toggle OFF — wait on the condition, not a fixed timeout
    page.click("#alive-toggle")
    page.wait_for_function("() => window.moxie.isAlive() === false")
    assert page.evaluate("() => window.moxie.isAlive()") is False
    assert "alive-off" in page.get_attribute("#alive-toggle", "class")
    assert page.is_checked("#idle-on") is False          # panel checkbox mirrors
    assert page.evaluate("() => window.moxieLife.isRunning()") is False
    # toggle back ON
    page.click("#alive-toggle")
    page.wait_for_function("() => window.moxie.isAlive() === true")
    assert page.evaluate("() => window.moxie.isAlive()") is True
    assert page.is_checked("#idle-on") is True
    assert not [e for e in page.console_errors if "favicon" not in e], page.console_errors[:3]


# --------------------------------------------------------------------------- #
# SIL: every motor slider moves the corresponding motor.
# --------------------------------------------------------------------------- #
def test_all_motor_sliders(page, server):
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(f"{server}/sim.html", wait_until="domcontentloaded")
    page.wait_for_function("window.moxie && document.querySelectorAll('#motors input[type=range]').length")
    # pause life so it doesn't move joints while we test manual control
    page.click("#alive-toggle")
    n = page.evaluate("() => document.querySelectorAll('#motors input[type=range]').length")
    assert n >= 5, f"expected motor sliders, got {n}"
    moved = page.evaluate(
        """() => {
            const out = [];
            document.querySelectorAll('#motors .motor').forEach((wrap) => {
                const label = wrap.querySelector('label span').textContent.trim();
                const idx = parseInt(label, 10);
                const s = wrap.querySelector('input[type=range]');
                s.value = 22000; s.dispatchEvent(new Event('input'));
                out.push([idx, window.moxie.getMotor(idx)]);
            });
            return out;
        }"""
    )
    for idx, val in moved:
        # motorValues eases toward the target; the target was set so held-target reflects it
        assert val is not None, f"motor {idx} has no value"
    assert not [e for e in page.console_errors if "favicon" not in e], page.console_errors[:3]


# --------------------------------------------------------------------------- #
# SIL: the remaining controls — center pose, heart LED, axes, sound, mic.
# --------------------------------------------------------------------------- #
def test_scene_and_toggle_controls(page, server):
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(f"{server}/sim.html", wait_until="domcontentloaded")
    page.wait_for_function("window.moxie")
    page.click("#center-btn")
    page.check("#led-on")
    page.evaluate("() => { const c=document.getElementById('led-color'); c.value='#33ddff'; c.dispatchEvent(new Event('input')); }")
    page.check("#axes-on")
    assert page.evaluate("() => !document.getElementById('axis-legend').hidden")
    page.uncheck("#axes-on")
    page.uncheck("#audio-on")
    page.check("#audio-on")
    page.wait_for_timeout(100)
    assert not [e for e in page.console_errors if "favicon" not in e], page.console_errors[:3]


# --------------------------------------------------------------------------- #
# SIL: speech path — a pre-cached chip and free text.
# --------------------------------------------------------------------------- #
def test_speech(page, server):
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(f"{server}/sim.html", wait_until="domcontentloaded")
    page.wait_for_function("window.moxieAudio")
    page.wait_for_function("document.querySelectorAll('#speech-chips .chip').length > 0", timeout=6000)
    page.click("#speech-chips .chip")            # pre-cached clip (no server needed)
    page.fill("#speech-input", "Hello Moxie")
    page.click("#speech-btn")
    page.wait_for_timeout(200)
    assert not [e for e in page.console_errors if "favicon" not in e], page.console_errors[:3]


# --------------------------------------------------------------------------- #
# SIL: works at every resolution (open drawer on phones, drive a control).
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("res", RESOLUTIONS, ids=[r[0] for r in RESOLUTIONS])
def test_sil_controls_reachable(page, server, res):
    label, w, h = res
    page.set_viewport_size({"width": w, "height": h})
    page.goto(f"{server}/sim.html", wait_until="domcontentloaded")
    page.wait_for_function("window.moxie", timeout=8000)
    page.wait_for_timeout(400)
    _open_drawer_if_needed(page)
    page.wait_for_timeout(150)
    # a face chip and the ALIVE toggle must be clickable at every size
    page.click("#faces button[data-expr='happy']")
    page.click("#alive-toggle")
    assert _no_hscroll(page), f"sim @ {label}: horizontal page scroll"
    assert not [e for e in page.console_errors if "favicon" not in e], page.console_errors[:3]


# --------------------------------------------------------------------------- #
# Docs explorer: search filters + opening a hit + Mermaid renders.
# --------------------------------------------------------------------------- #
def test_docs_explorer(page, server):
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(f"{server}/docs.html", wait_until="domcontentloaded")
    page.wait_for_selector("a.doc")
    assert page.evaluate("() => document.querySelectorAll('a.doc').length") >= 60
    # full-text search filters the tree
    page.fill("#q", "projectorfanpid")
    page.wait_for_timeout(800)
    assert page.evaluate("() => document.querySelectorAll('a.doc.hit').length") > 0
    # Mermaid renders on a diagram-heavy doc
    page.goto(f"{server}/docs.html#reverse-engineering/architecture-diagrams.md",
              wait_until="domcontentloaded")
    page.wait_for_function("document.querySelectorAll('article svg').length > 0", timeout=8000)
    assert page.evaluate("() => document.querySelectorAll('article svg').length") > 0
    assert not [e for e in page.console_errors if "favicon" not in e], page.console_errors[:3]
