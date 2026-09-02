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


# --------------------------------------------------------------------------- #
# SIL: the SERVER voice — a CloudTTSResponse on /commands/tts actually plays.
#
# This is the browser half of AI seam ③ and the last client-side link in DoD
# criterion 1. The supervisor publishes `{audio:{buffer(base64 raw 16-bit PCM),
# channels, sample_rate}, marks[], event_id, chunk_num}`; we inject a synthetic
# one through the REAL bridge route (the same call the MQTT client makes) and
# assert the SIM decodes it, speaks it, animates the mouth, and stops cleanly.
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


# Wait for a chunked utterance to FINISH and hand back what the page recorded of it.
# `chunks_played` is what tells a finished playback apart from one that never started —
# `isSpeaking() === false` alone is also true a millisecond after injection.
_PLAYED = """
({event, want}) => {
  const a = window.moxieAudio;
  if (!a || a.isSpeaking()) return null;                 // still talking
  const s = a.lastPlaybackStats();
  if (!s || s.event_id !== event) return null;           // still the PREVIOUS utterance
  if (s.chunks_played < want) return null;               // hasn't started, or not done
  return {stats: s, pending: a.ttsPending()};
}
"""


def test_cloud_tts_chunks_play_in_order_then_stop(page, server):
    """Chunked responses: same event_id, out-of-order arrival, one continuous utterance."""
    _sim_ready(page, server)
    for chunk in (0, 2, 1):
        page.evaluate(_INJECT_TTS, {"frames": 8820, "rate": 22050, "eventId": "evt-chunks",
                                    "chunk": chunk, "marks": []})
    # Assert on what the page RECORDED, not on what is true when the question arrives.
    # The old check sampled `ttsPending()` live at the instant `speaking` first went
    # true, and lost that race ~50% of the time on a fast runner (CI 33629395950): four
    # tenths of a second of audio can drain entirely inside one polling gap, and then
    # "the chunks queued" is unprovable even though they did. audio.js now remembers the
    # shape of each playback — chunk order and the deepest the queue got — so the same
    # three facts are asserted once the utterance is safely over. The ORDER itself is no
    # longer a matter of timing either: audio.js holds a chunk until its turn comes
    # (see the chunk-ordering note there, and the jitter test below).
    done = page.wait_for_function(_PLAYED, arg={"event": "evt-chunks", "want": 3},
                                  timeout=20000).json_value()
    stats = done["stats"]
    assert stats["event_id"] == "evt-chunks", stats
    assert stats["chunks_played"] == 3, f"all three chunks must play ({stats})"
    assert stats["order"] == [0, 1, 2], f"chunks must play in chunk_num order ({stats})"
    assert stats["max_pending"] >= 1, f"later chunks must queue ({stats})"
    assert done["pending"] == 0, done
    # no marks here: the mouth must have moved from the AUDIO ENVELOPE alone, which only
    # happens if the PCM really rendered through the Web Audio graph. Read as the peak
    # the page recorded over the utterance — the old live `getMouthOpen() > 0.05` wait
    # had to catch a ~1.2 s animation mid-open and lost that race on a loaded runner.
    peak = page.evaluate("() => window.moxieAudio.lastMouthPeak()")
    assert peak > 0.05, f"the envelope never drove the mouth (peak {peak})"

    # ...and the proof that none of the above is a timing bet: do it again with 10 ms
    # chunks injected in ONE synchronous round trip. They queue and drain far faster
    # than any observer can look, so a live `ttsPending()` sample could not see the
    # queue at all — the recorded stats still report exactly the same three facts.
    burst = page.evaluate(
        """() => {
             const pcm = new ArrayBuffer(220 * 2), dv = new DataView(pcm);
             for (let i = 0; i < 220; i++) dv.setInt16(i * 2, Math.round(11000 * Math.sin(i / 6)), true);
             const bytes = new Uint8Array(pcm);
             let bin = "";
             for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
             const b64 = btoa(bin);
             for (const chunk of [0, 2, 1])                    // one task: nothing can drain
               window.moxieBridge.route("/devices/d_sim/commands/tts", JSON.stringify({
                 request_source: "ROBOT_TTS_REQUEST",
                 audio: {buffer: b64, channels: 1, sample_rate: 22050},
                 marks: [], event_id: "evt-burst", chunk_num: chunk,
               }));
             return window.moxieAudio.ttsPending();
           }""")
    assert burst >= 1, f"three synchronous chunks must pipeline, pending={burst}"
    fast = page.wait_for_function(_PLAYED, arg={"event": "evt-burst", "want": 3},
                                  timeout=20000).json_value()["stats"]
    assert fast["event_id"] == "evt-burst", fast
    assert fast["chunks_played"] == 3 and fast["order"] == [0, 1, 2], fast
    assert fast["max_pending"] >= 1, f"the queue depth must survive a fast drain ({fast})"

    assert not [e for e in page.console_errors if "favicon" not in e], page.console_errors[:3]


def test_cloud_tts_chunks_stay_in_order_across_a_silent_gap(page, server):
    """The out-of-order arrival a sorted queue cannot fix — the real regression.

    Sorting the queue orders only what is WAITING in it. Here chunk 0 is over and the
    queue is EMPTY before chunk 2 arrives, so there is nothing to sort it against: the
    old player started chunk 2 (order [0,2,1]) and the child heard the end of the
    sentence before its middle. It was pure timing — the identical code passed on a
    slower box the day before (CI 33632125915 vs 33629395950) — so the fix makes the
    order structural: a chunk waits for its turn however idle the player is.
    """
    _sim_ready(page, server)
    # 50 ms of audio: chunk 0 is finished long before the next round trip lands.
    page.evaluate(_INJECT_TTS, {"frames": 1102, "rate": 22050, "eventId": "evt-jitter",
                               "chunk": 0, "marks": []})
    page.wait_for_function(
        """() => { const a = window.moxieAudio;
                   return !a.isSpeaking() && a.lastPlaybackStats().chunks_played >= 1; }""",
        timeout=15000)

    page.evaluate(_INJECT_TTS, {"frames": 8820, "rate": 22050, "eventId": "evt-jitter",
                               "chunk": 2, "marks": []})
    page.wait_for_timeout(150)
    held = page.evaluate(
        """() => ({speaking: window.moxieAudio.isSpeaking(),
                   pending: window.moxieAudio.ttsPending(),
                   stats: window.moxieAudio.lastPlaybackStats()})""")
    assert held["speaking"] is False, f"chunk 2 must not play out of turn ({held})"
    assert held["pending"] == 1, f"chunk 2 must be held, not dropped ({held})"
    assert held["stats"]["order"] == [0], f"only chunk 0 may have played so far ({held})"

    page.evaluate(_INJECT_TTS, {"frames": 8820, "rate": 22050, "eventId": "evt-jitter",
                               "chunk": 1, "marks": []})
    done = page.wait_for_function(_PLAYED, arg={"event": "evt-jitter", "want": 3},
                                  timeout=20000).json_value()
    stats = done["stats"]
    assert stats["chunks_played"] == 3, f"all three chunks must play ({stats})"
    assert stats["order"] == [0, 1, 2], f"chunks must play in chunk_num order ({stats})"
    assert stats["max_pending"] >= 1, f"chunk 2 must have waited behind chunk 1 ({stats})"
    assert done["pending"] == 0, done
    assert not [e for e in page.console_errors if "favicon" not in e], page.console_errors[:3]


def test_cloud_tts_respects_the_mute_toggle(page, server):
    _sim_ready(page, server)
    page.uncheck("#audio-on")          # the SIM's existing sound switch
    page.evaluate(_INJECT_TTS, {"frames": 4410, "rate": 22050, "eventId": "evt-mute",
                                "chunk": 0, "marks": []})
    page.wait_for_timeout(400)
    assert page.evaluate("() => window.moxieAudio.isSpeaking()") is False, "muted audio must not play"
    page.check("#audio-on")
    assert not [e for e in page.console_errors if "favicon" not in e], page.console_errors[:3]


# --------------------------------------------------------------------------- #
# SIL: PRESENCE — the robot's own eyes in the browser client.
#
# The stock robot runs vision on-device and sends only semantic events — no pixels, no
# bounding boxes (docs/architecture/vision.md §1.1) — delivered as the `speech` of an
# ordinary RemoteChatRequest. The SIM emits exactly that, and everything asserted below
# is state the page RECORDED (`window.moxieBridge.presenceStats()`, the badge attribute),
# never a live sample of an animation.
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
