"""Static site + browser SIM in real Chromium: every page at every resolution loads clean,
and the SIM's controls and server voice work end to end. Pure bridge/voice logic is covered
without a browser by test_audio.mjs and test_presence_bridge.mjs; the docs explorer and the
SIM's per-viewport reachability by test_docs_explorer.mjs and test_responsive.mjs."""
import pytest

from conftest import RESOLUTIONS, PAGES


def _no_hscroll(page):
    return page.evaluate(
        "() => document.documentElement.scrollWidth <= window.innerWidth + 2"
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
    idxs = page.evaluate(
        """() => [...document.querySelectorAll('#motors .motor')].map((wrap) => {
                const idx = parseInt(wrap.querySelector('label span').textContent.trim(), 10);
                const s = wrap.querySelector('input[type=range]');
                s.value = 22000; s.dispatchEvent(new Event('input'));
                return idx;
            })""")
    # the joint eases toward the slider's target; every one must actually get there
    page.wait_for_function(
        "(idxs) => idxs.every(i => Math.abs(window.moxie.getMotor(i) - 22000) < 50)",
        arg=idxs, timeout=8000)
    assert not [e for e in page.console_errors if "favicon" not in e], page.console_errors[:3]


# --------------------------------------------------------------------------- #
# SIL: the remaining controls and the speech path throw nothing.
# --------------------------------------------------------------------------- #
def test_scene_and_toggle_controls(page, server):
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(f"{server}/sim.html", wait_until="domcontentloaded")
    page.wait_for_function("window.moxie && window.moxieAudio")
    page.click("#center-btn")
    page.check("#led-on")
    page.evaluate("() => { const c=document.getElementById('led-color'); c.value='#33ddff'; c.dispatchEvent(new Event('input')); }")
    page.check("#axes-on")
    assert page.evaluate("() => !document.getElementById('axis-legend').hidden")
    page.uncheck("#axes-on")
    page.uncheck("#audio-on")
    page.check("#audio-on")
    # the speech path: a pre-cached chip (no server needed) and free text
    page.wait_for_function("document.querySelectorAll('#speech-chips .chip').length > 0",
                           timeout=6000)
    page.click("#speech-chips .chip")
    page.fill("#speech-input", "Hello Moxie")
    page.click("#speech-btn")
    assert not [e for e in page.console_errors if "favicon" not in e], page.console_errors[:3]


# --------------------------------------------------------------------------- #
# SIL: the SERVER voice — a CloudTTSResponse on /commands/tts actually plays.
#
# A synthetic CloudTTSResponse through the REAL bridge route, in real Web Audio: decoded,
# spoken, mouth animated, cleared. Queue/order/mute logic is test_audio.mjs §4-6.
# --------------------------------------------------------------------------- #

# Build a tone-shaped CloudTTSResponse IN THE PAGE and route it like the broker would.
_INJECT_TTS = """
(args) => {
  const {frames, rate, eventId, chunk, marks} = args;
  const buf = new ArrayBuffer(frames * 2);
  const dv = new DataView(buf);
  for (let i = 0; i < frames; i++)
    dv.setInt16(i * 2, Math.round(11000 * Math.sin(i / 6)), true);   // little-endian, like the wire
  const bytes = new Uint8Array(buf);
  let bin = "";
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
  const payload = {
    request_source: "ROBOT_TTS_REQUEST",
    audio: {buffer: btoa(bin), channels: 1, sample_rate: rate},
    marks: marks, event_id: eventId, chunk_num: chunk,
  };
  window.moxieBridge.route("/devices/d_sim/commands/tts", JSON.stringify(payload));
  return window.moxieAudio.decodeCloudTTS(payload).frames;
}
"""


def _sim_ready(page, server):
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(f"{server}/sim.html", wait_until="domcontentloaded")
    page.wait_for_function("window.moxie && window.moxieAudio && window.moxieBridge")
    page.keyboard.press("Shift")       # a real user gesture (unlocks audio if the policy needs one)


def test_cloud_tts_plays_and_animates_the_mouth(page, server):
    _sim_ready(page, server)
    assert page.evaluate("() => window.moxieAudio.isSpeaking()") is False
    # ~1.2 s of audio so there is time to observe the speaking state and the mouth
    frames = page.evaluate(_INJECT_TTS, {"frames": 26460, "rate": 22050, "eventId": "evt-sil",
                                         "chunk": 0,
                                         "marks": [{"time": 0, "start": 0, "end": 5,
                                                    "type": "viseme", "value": "a"}]})
    assert frames == 26460, f"the page decoded {frames} frames, expected 26460"
    # WAIT for the whole speaking state to be up, don't sample it at an instant:
    # the audio graph, the mouth pump and the status line all come up async, and
    # env.js probes its sidecars in parallel. Everything asserted below is read
    # from ONE atomic snapshot taken while the utterance is live.
    live = page.wait_for_function(
        """() => {
             const a = window.moxieAudio;
             if (!a || !a.isSpeaking()) return null;
             const info = a.speakingInfo();
             if (!info) return null;
             const st = document.getElementById("tts-status");
             const status = st ? (st.textContent || "") : "";
             if (!status.includes("speaking")) return null;
             return {speaking: a.isSpeaking(), status: status,
                     sampleRate: info.sampleRate, channels: info.channels,
                     duration: info.duration,
                     body: document.body.classList.contains("tts-speaking")};
           }""",
        timeout=10000).json_value()
    assert live["speaking"] is True, live
    assert live["sampleRate"] == 22050 and live["channels"] == 1, live
    assert abs(live["duration"] - 1.2) < 0.01, live
    assert "speaking" in live["status"], live
    assert live["body"] is True, live
    # ...and everything clears when the buffer ends
    page.wait_for_function("() => window.moxieAudio.isSpeaking() === false", timeout=15000)
    # The face visibly spoke. Asserted on the peak the PAGE recorded across the whole
    # utterance, not on a live sample: a mouth that is open for ~1.2 s is not reliably
    # caught mid-open by an observer sharing a loaded CI box with the audio thread.
    peak = page.evaluate("() => window.moxieAudio.lastMouthPeak()")
    assert peak > 0.05, f"the mouth never opened while speaking (peak {peak})"
    # The page's own record of what it played — the same "assert on what was recorded,
    # not on what happens to be true right now" rule. (The live snapshot above stays a
    # snapshot on purpose: `#tts-status` and `body.tts-speaking` only EXIST while an
    # utterance is live, and it is a wait for a ~1.2 s window, not an instant sample.)
    stats = page.evaluate("() => window.moxieAudio.lastPlaybackStats()")
    assert stats["event_id"] == "evt-sil", stats
    assert stats["chunks_played"] == 1 and stats["order"] == [0], stats
    page.wait_for_function(
        """() => {
             const st = document.getElementById("tts-status");
             return window.moxie.getMouthOpen() === 0 &&
                    !document.body.classList.contains("tts-speaking") &&
                    !((st ? st.textContent : "") || "").includes("speaking");
           }""",
        timeout=5000)
    assert not [e for e in page.console_errors if "favicon" not in e], page.console_errors[:3]


# --------------------------------------------------------------------------- #
# SIL: PRESENCE — the badge and toggle wired in the real page (the bridge's event/greeting
# logic is test_presence_bridge.mjs, which no CI tier runs yet, so the greeting and the
# comms-log exclusion stay pinned here).
# --------------------------------------------------------------------------- #

# Answer a face event the way the supervisor does, through the REAL bridge route.
_ANSWER_FACE_EVENT = """
({eventId, text}) => {
  window.moxieBridge.route("/devices/d_sim/commands/remote_chat", JSON.stringify({
    command: "remote_chat", result: "SUCCESS", backend: "router", event_id: eventId,
    output: {text: text,
             markup: '<mark name="cmd:playback-mood,data:{+mood+:1,+intensity+:1}"/>' + text},
  }));
  return window.moxieBridge.presenceStats();
}
"""


def _badge(page):
    return page.evaluate(
        """() => {
             const el = document.getElementById("presence-badge");
             const label = document.getElementById("presence-state");
             return {state: el ? el.getAttribute("data-presence") : null,
                     label: label ? label.textContent : null,
                     button: (document.getElementById("presence-toggle") || {}).textContent};
           }""")


def test_presence_badge_starts_unknown_and_follows_the_walk_in_toggle(page, server):
    _sim_ready(page, server)
    assert page.evaluate("() => window.moxieBridge.presenceStats().present") is None
    assert _badge(page) == {"state": "unknown", "label": "UNKNOWN", "button": "Walk in"}

    page.click("#presence-toggle")                      # someone walks in
    page.wait_for_function("() => window.moxieBridge.presenceStats().present === true")
    assert _badge(page) == {"state": "here", "label": "HERE", "button": "Walk away"}

    page.click("#presence-toggle")                      # ...and walks away again
    page.wait_for_function("() => window.moxieBridge.presenceStats().present === false")
    assert _badge(page) == {"state": "away", "label": "AWAY", "button": "Walk in"}

    stats = page.evaluate("() => window.moxieBridge.presenceStats()")
    assert stats["arrivals"] == 1 and stats["departures"] == 1, stats
    assert stats["events"] == ["eb-found-face", "eb-lost-target"], stats
    assert not [e for e in page.console_errors if "favicon" not in e], page.console_errors[:3]


def test_a_child_walking_back_in_gets_a_greeting_the_page_records(page, server):
    """The delight, end to end in the browser: away → back → the server answers THAT
    event_id with a spoken line, and the page files it as a greeting rather than as an
    ordinary reply to something the child said."""
    _sim_ready(page, server)
    page.evaluate("() => window.moxieBridge.faceEvent('lost')")
    event_id = page.evaluate("() => window.moxieBridge.faceEvent('found')")
    assert event_id.startswith("sim-face-"), event_id

    hello = "Hey Sam, there you are! I missed you."
    stats = page.evaluate(_ANSWER_FACE_EVENT, {"eventId": event_id, "text": hello})
    assert stats["greetings"] == [hello], stats
    assert stats["present"] is True and stats["arrivals"] == 1, stats
    assert _badge(page)["state"] == "here"

    # ...and a silent acknowledgement to a later event is NOT a greeting
    quiet_id = page.evaluate("() => window.moxieBridge.faceEvent('lost')")
    page.evaluate(
        """(eventId) => window.moxieBridge.route("/devices/d_sim/commands/remote_chat",
             JSON.stringify({command: "remote_chat", result: "NOREPLY_ACK",
                             event_id: eventId, output: {text: "", markup: ""}}))""",
        quiet_id)
    assert page.evaluate("() => window.moxieBridge.presenceStats().greetings") == [hello]
    assert not [e for e in page.console_errors if "favicon" not in e], page.console_errors[:3]


def test_a_vision_event_never_appears_in_the_comms_log(page, server):
    """`eb-found-face` arrives in the `speech` slot, but it is Moxie's eye, not the
    child's voice — a transcript row for it would be a lie about who spoke."""
    _sim_ready(page, server)
    page.evaluate("() => window.moxieBridge.faceEvent('found')")
    page.evaluate("() => window.moxieBridge.faceEvent('lost')")
    page.wait_for_function("() => window.moxieBridge.presenceStats().events.length === 2")
    rows = page.evaluate(
        """() => [...document.querySelectorAll('#transcript .turn')].map(r => r.textContent)""")
    assert not any("eb-" in r for r in rows), rows
    assert not [e for e in page.console_errors if "favicon" not in e], page.console_errors[:3]
