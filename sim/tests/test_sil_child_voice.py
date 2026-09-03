"""
The child's voice, in a REAL browser — the half of PR #82 that assertions could not reach.

`speakClipOnly` shipped with 770 hermetic assertions behind it (`test_fallback_coverage.mjs`:
the clips exist, the manifest lists them, the session leaves each line room to finish). Every
one of those reads a FILE. Not one of them ever loaded the page, and Web Audio is stubbed in
the node tests, so the claim that actually matters — *the browser plays her* — had no evidence
at all: a `decodeAudioData` that rejects, a source node wired to nothing, an autoplay-suspended
context, or a manifest key that no longer matches the session string would each leave the demo
exactly as silent as it was before #82 and pass all 770.

So this file drives the shipped `/sim.html` in the same real Chromium the rest of `test_sil*`
uses, replays the demo, and asserts what the audio graph DID:

  1. both child MP3s are fetched over real HTTP, 200, with their real byte counts;
  2. each decodes to a real AudioBuffer — non-zero duration, and a peak/RMS well above
     silence, so a clip that decoded to a valid-but-empty buffer still fails;
  3. each is `start()`ed on a node whose connect-graph REACHES `ctx.destination` — the
     difference between "played" and "played into a disconnected analyser";
  4. neither is cut off: `ended` fires naturally, `stop()` is never called on it, and the
     wall-clock it occupied covers its own duration. This is the truncation regression the
     asymmetric ordering in `speakClipOnly` exists to prevent (`speak()` calls `stop()`,
     so Moxie answering too early would silence the child mid-word);
  5. Moxie's reply clip starts only after the child's has finished — the same rule seen
     from the other side, and the one `sessions/demo.json`'s timing has to keep true.

WHAT THIS DOES NOT PROVE. A headless browser has no speaker, so nothing here is "verified by
ear". What it proves is that real PCM with real amplitude reached `AudioDestinationNode` and
ran for its full length. Everything between that node and a human ear — the OS mixer, the
volume, the hardware — is outside any automated test, and the honest word for the remaining
gap is *unverified*, not *verified*.

RULE 11 (orchestration-plan.md): every assertion below reads a RECORD the page accumulated
and the test waits for the replay to COMPLETE first. Nothing samples a live value in a window.
"""
import json
import os

import pytest

from conftest import ConsoleErrors, _CAPABILITY_PROBE, _is_benign

WEB = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "web"))

#: The two clips `audio/index.json` maps the scripted child lines to. Byte size is the
#: correlation key between a network response and a decoded buffer: `playUrl` does
#: `r.arrayBuffer()` then `decodeAudioData(buf.slice(0))`, which preserves byteLength.
CHILD_LINES = {
    "Guess what, it's my birthday today!": "child/5506a77777bb3885.mp3",
    "Thank you Moxie!": "child/02b74eca0d7e1c2c.mp3",
}

#: Silence floors. A decoded-but-empty buffer has peak 0.0; speech normalised by Piper sits
#: near 1.0 peak and well above 0.01 RMS. Deliberately loose — this separates "audio" from
#: "no audio", it is not a quality metric.
PEAK_FLOOR, RMS_FLOOR = 0.05, 0.005

#: How far short of its own duration a clip may fall and still count as FINISHED.
#:
#: There has to be one, and the reason is a clock mismatch rather than slack. The recorder
#: timestamps `startedAt`/`stoppedAt` with `performance.now()`, while the audio itself runs
#: on the AudioContext clock, and `startedAt` is taken just BEFORE `src.start(0)` — which
#: begins at the next render quantum, not instantly. So a clip that played in full can
#: still measure a few ms short against `startedAt + duration`.
#:
#: 120 ms is far inside one phoneme: a stop that late cannot take a word off the end. It is
#: also two orders of magnitude away from either case this test has actually seen — the
#: real cut it caught was 282 ms EARLY on a 2519 ms clip, and CI's benign stop landed 58 ms
#: AFTER a 1207 ms clip had finished. Nothing here needs fine tuning, and widening it to
#: make a red run go green would destroy the only thing the test is for.
COMPLETION_TOLERANCE_MS = 120


def truncated_by(play, tol_ms=COMPLETION_TOLERANCE_MS):
    """How many ms of `play` never reached the speakers — 0.0 when it finished.

    The distinction the first version of this test got wrong: `stop()` HAPPENING is not
    truncation. `speak()` calls `stop()` unconditionally, so Moxie answering even a
    moment after the child's clip ends still records a stop against it. What matters is
    WHEN it landed relative to the clip's own length — the quantity the failure message
    was already printing while the assertion ignored it.

    Both the stop and the natural end are measured, and the worse (earlier) one is
    reported, so a clip is "finished" only if it was neither cut short nor ended early.
    """
    natural_ms = play["duration"] * 1000.0
    lost = 0.0
    if play.get("stoppedAt"):
        lost = max(lost, natural_ms - (play["stoppedAt"] - play["startedAt"]))
    if play.get("endedAt"):
        lost = max(lost, natural_ms - (play["endedAt"] - play["startedAt"]))
    return 0.0 if lost <= tol_ms else lost


# --------------------------------------------------------------------------- #
# The recorder. Installed before any page script runs, so it sees the first call.
# --------------------------------------------------------------------------- #
RECORDER = r"""
(() => {
  const R = { fetches: [], decodes: [], plays: [], edges: [], destIds: [] };
  window.__childVoice = R;

  // Node identity, so a connect-graph can be walked in the test rather than guessed at.
  let nid = 0;
  const ids = new WeakMap();
  const idOf = (n) => { if (!ids.has(n)) ids.set(n, ++nid); return ids.get(n); };

  const origConnect = AudioNode.prototype.connect;
  AudioNode.prototype.connect = function (dst) {
    try {
      if (dst instanceof AudioNode) {
        const to = idOf(dst);
        R.edges.push([idOf(this), to]);
        // `ctx.destination` is the only AudioDestinationNode there is; remember it by id
        // so reachability is a plain graph walk with no instanceof at assert time.
        if (dst instanceof AudioDestinationNode && R.destIds.indexOf(to) < 0)
          R.destIds.push(to);
      }
    } catch (e) {}
    return origConnect.apply(this, arguments);
  };

  // Which URL produced which byte count. The clone keeps the page's own response intact.
  const origFetch = window.fetch;
  window.fetch = function (input) {
    const url = (typeof input === "string" ? input : (input && input.url) || "");
    const p = origFetch.apply(this, arguments);
    if (/\.mp3(\?|$)/.test(url))
      p.then((res) => {
        const c = res.clone();
        c.arrayBuffer().then(
          (b) => R.fetches.push({ url: url, bytes: b.byteLength, status: res.status }),
          () => {});
      }, () => {});
    return p;
  };

  // The decode itself: duration, rate, and the amplitude of the PCM that came out.
  const origDecode = BaseAudioContext.prototype.decodeAudioData;
  BaseAudioContext.prototype.decodeAudioData = function (buf) {
    const bytes = (buf && buf.byteLength) || 0;
    const out = origDecode.apply(this, arguments);
    if (out && typeof out.then === "function")
      return out.then((audio) => {
        let peak = 0, sum = 0, n = 0;
        try {
          const ch = audio.getChannelData(0);
          for (let i = 0; i < ch.length; i += 7) {   // every 7th sample: plenty, and fast
            const v = ch[i]; const a = v < 0 ? -v : v;
            if (a > peak) peak = a;
            sum += v * v; n++;
          }
        } catch (e) {}
        const rec = { bytes: bytes, duration: audio.duration, sampleRate: audio.sampleRate,
                      channels: audio.numberOfChannels, peak: peak,
                      rms: Math.sqrt(sum / Math.max(1, n)) };
        R.decodes.push(rec);
        try { audio.__moxieBytes = bytes; audio.__moxiePeak = peak; } catch (e) {}
        return audio;
      });
    return out;
  };

  // Playback: when it started, whether it was CUT (stop()) or ran to its natural end.
  const origStart = AudioBufferSourceNode.prototype.start;
  const origStop = AudioBufferSourceNode.prototype.stop;
  AudioBufferSourceNode.prototype.start = function () {
    const b = this.buffer;
    const rec = { node: idOf(this), bytes: (b && b.__moxieBytes) || 0,
                  peak: (b && b.__moxiePeak) || 0, duration: b ? b.duration : 0,
                  startedAt: performance.now(), endedAt: 0, stoppedAt: 0, ended: false };
    R.plays.push(rec);
    try {
      this.addEventListener("ended", () => {
        rec.endedAt = performance.now(); rec.ended = true;
      });
    } catch (e) {}
    return origStart.apply(this, arguments);
  };
  AudioBufferSourceNode.prototype.stop = function () {
    const me = idOf(this);
    for (let i = R.plays.length - 1; i >= 0; i--)
      if (R.plays[i].node === me && !R.plays[i].stoppedAt) {
        R.plays[i].stoppedAt = performance.now(); break;
      }
    return origStop.apply(this, arguments);
  };
})();
"""


def _reaches_destination(edges, dest_ids, node_id):
    """Is `node_id` connected, through any chain, to `ctx.destination`?"""
    adj = {}
    for a, b in edges:
        adj.setdefault(a, []).append(b)
    seen, stack = set(), [node_id]
    while stack:
        n = stack.pop()
        if n in dest_ids:
            return True
        if n in seen:
            continue
        seen.add(n)
        stack.extend(adj.get(n, ()))
    return False


@pytest.fixture(scope="module")
def replayed(browser, server):
    """Load `/sim.html`, replay the shipped demo to completion, hand back the record.

    MODULE-scoped, and that is a performance decision worth naming: the shipped session
    now runs 14.6 s, so a per-test fixture would replay it six times and add ~90 s to the
    SIL job — the slowest tier and the one the merge gate waits on (rule 16). One replay,
    six assertions over the record it left behind, which is also exactly the shape rule 11
    asks for. `page` is function-scoped, so this makes its own and records console errors
    the same way `conftest.page` does.
    """
    page = browser.new_page()
    # Console capture, wired exactly as `conftest.page` wires it — including the
    # access-time view that forgives the optional `/api/health` probe's 404 but no other.
    raw, unexpected = [], []
    page.on("console", lambda m: raw.append(m.text)
            if m.type == "error" and not _is_benign(m.text) else None)
    page.on("pageerror", lambda e: raw.append(f"PAGEERR {e}"))
    page.on("response", lambda r: unexpected.append(r.url)
            if r.status == 404 and _CAPABILITY_PROBE not in r.url else None)
    page.add_init_script(RECORDER)
    responses = []
    page.on("response",
            lambda r: responses.append((r.url, r.status)) if ".mp3" in r.url else None)
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(f"{server}/sim.html", wait_until="domcontentloaded")
    page.wait_for_function(
        "window.moxie && window.moxieAudio && document.getElementById('rec-demo')")
    # Pause the ambient life loop: it can fire its own clips, and this test is about the
    # scripted turns. (It is also a user gesture, which the autoplay policy is happy with.)
    page.click("#alive-toggle")
    page.click("#rec-demo")
    # WAIT FOR COMPLETION, then assert the record (rule 11). The shipped session's last
    # event is at t=11400 ms; the predicate is the record itself, not a fixed sleep.
    page.wait_for_function(
        """() => {
             const r = window.__childVoice;
             if (!r) return false;
             const child = r.plays.filter((p) => p.bytes && p.duration > 0.5);
             return child.length >= 4 && child.every((p) => p.ended || p.stoppedAt);
           }""",
        timeout=30000)
    page.wait_for_timeout(500)          # let the last `ended` land in the record
    rec = page.evaluate("() => window.__childVoice")
    rec["responses"] = responses
    rec["console"] = ConsoleErrors(raw, unexpected)
    page.close()
    return rec


# --------------------------------------------------------------------------- #
# 1. The bytes really left the server.
# --------------------------------------------------------------------------- #
def test_the_demo_replay_requests_both_child_clips(replayed):
    got = {url.rsplit("/audio/", 1)[-1]: status
           for url, status in replayed["responses"] if "/audio/" in url}
    for line, rel in CHILD_LINES.items():
        assert rel in got, (f"nothing ever asked for the child's clip for {line!r} "
                            f"({rel}); requested: {sorted(got)}")
        assert got[rel] == 200, f"{rel} answered {got[rel]}"
    # …and with the size the repo actually ships, so a truncated deploy is not a pass.
    sizes = {f["bytes"] for f in replayed["fetches"]}
    for rel in CHILD_LINES.values():
        on_disk = os.path.getsize(os.path.join(WEB, "audio", rel))
        assert on_disk in sizes, f"{rel} ({on_disk} B) never reached the page whole"


# --------------------------------------------------------------------------- #
# 2. They decoded into real, non-silent PCM.
# --------------------------------------------------------------------------- #
def test_both_child_clips_decode_to_audible_pcm(replayed):
    by_bytes = {d["bytes"]: d for d in replayed["decodes"]}
    for line, rel in CHILD_LINES.items():
        n = os.path.getsize(os.path.join(WEB, "audio", rel))
        d = by_bytes.get(n)
        assert d, (f"{rel} was never decoded — the browser fetched it and threw it away "
                   f"(decoded byte counts: {sorted(by_bytes)})")
        assert d["duration"] > 0.5, f"{rel} decoded to {d['duration']:.3f}s of audio"
        assert d["channels"] >= 1 and d["sampleRate"] >= 8000, d
        assert d["peak"] > PEAK_FLOOR, (
            f"{rel} decoded to SILENCE (peak {d['peak']:.4f}) — a valid buffer with "
            f"nothing in it is exactly the failure this test exists to catch")
        assert d["rms"] > RMS_FLOOR, f"{rel} rms {d['rms']:.5f} — near-silent"
        print(f"   {rel}  {d['duration']:.2f}s  {d['sampleRate']} Hz  "
              f"peak={d['peak']:.3f} rms={d['rms']:.4f}")


# --------------------------------------------------------------------------- #
# 3. They were routed to the speakers, not into a dangling node.
# --------------------------------------------------------------------------- #
def test_each_child_clip_is_wired_through_to_the_destination(replayed):
    edges, dests = replayed["edges"], set(replayed["destIds"])
    assert dests, "no AudioNode ever connected to ctx.destination — nothing could be heard"
    for line, rel in CHILD_LINES.items():
        n = os.path.getsize(os.path.join(WEB, "audio", rel))
        plays = [p for p in replayed["plays"] if p["bytes"] == n]
        assert plays, f"{rel} decoded but was never start()ed — {line!r} stayed silent"
        for p in plays:
            assert _reaches_destination(edges, dests, p["node"]), (
                f"{rel} played into a node that does not reach ctx.destination")


# --------------------------------------------------------------------------- #
# 4. THE TRUNCATION RULE: Moxie must not cut the child off mid-word.
# --------------------------------------------------------------------------- #
def test_the_reply_does_not_truncate_the_child(replayed):
    for line, rel in CHILD_LINES.items():
        n = os.path.getsize(os.path.join(WEB, "audio", rel))
        plays = [p for p in replayed["plays"] if p["bytes"] == n]
        assert plays, f"{rel} never played"
        p = plays[0]
        assert p["ended"], f"{rel} never reached its natural end"
        lost = truncated_by(p)
        assert not lost, (
            f"{line!r} was CUT: {lost:.0f} ms of its {p['duration'] * 1000:.0f} ms never "
            f"played (stop() at "
            f"{(p['stoppedAt'] - p['startedAt']) if p['stoppedAt'] else 0:.0f} ms, ended at "
            f"{p['endedAt'] - p['startedAt']:.0f} ms). `speak()` calls stop(), so this is "
            f"Moxie answering before the child finished — retime sessions/demo.json.")
        held = p["endedAt"] - p["startedAt"]
        late = (f", stop() {p['stoppedAt'] - p['startedAt'] - p['duration'] * 1000:.0f} ms "
                f"after it ended" if p["stoppedAt"] else "")
        print(f"   {rel}  played {held:.0f} ms of {p['duration'] * 1000:.0f} ms, "
              f"complete{late}")


def test_moxie_answers_only_after_the_child_has_finished(replayed):
    """The same rule from the other side, and the one `sessions/demo.json` must keep true.

    Compared against the child's NATURAL end (`startedAt + duration`), never against the
    recorded `endedAt`: the `ended` event fires on a `stop()` too, so an `endedAt`
    comparison would report "Moxie waited politely" about a clip she had just cut off.
    """
    sizes = {os.path.getsize(os.path.join(WEB, "audio", rel))
             for rel in CHILD_LINES.values()}
    child = sorted((p for p in replayed["plays"] if p["bytes"] in sizes),
                   key=lambda p: p["startedAt"])
    moxie = sorted((p for p in replayed["plays"]
                    if p["bytes"] and p["bytes"] not in sizes and p["duration"] > 0.5),
                   key=lambda p: p["startedAt"])
    assert len(child) >= 2, f"expected both scripted child lines, got {len(child)}"
    assert moxie, "Moxie never spoke in the replay — the comparison would be vacuous"
    for c in child:
        after = [m for m in moxie if m["startedAt"] > c["startedAt"]]
        if not after:
            continue
        natural_end = c["startedAt"] + c["duration"] * 1000
        gap = after[0]["startedAt"] - natural_end
        # Same tolerance, same reason (the two clocks): an overlap inside it is not one.
        assert gap > -COMPLETION_TOLERANCE_MS, (
            f"Moxie started {-gap:.0f} ms BEFORE the child's clip would have finished — "
            f"she is talking over her")
        print(f"   child clip would end → Moxie starts {gap:.0f} ms later")


# --------------------------------------------------------------------------- #
# 4b. The predicate itself, in both directions — no browser, no timing luck.
#
# `truncated_by` has to separate two things that look identical in the record: a stop that
# CUT the clip and a stop that merely landed after it finished. Whether a given replay
# produces the second one is up to how fast the machine is, so a browser test cannot be
# relied on to exercise it — this is exactly how the first version of this file passed
# locally and went red in CI. These cases are the record shapes themselves, with the
# numbers CI and this box actually produced, so both directions are covered on every run.
# --------------------------------------------------------------------------- #
def _play(duration_ms, *, stopped_at=0.0, ended_at=None):
    """One `plays` record as the recorder writes it, started at t=1000."""
    return {"bytes": 1, "duration": duration_ms / 1000.0, "startedAt": 1000.0,
            "stoppedAt": (1000.0 + stopped_at) if stopped_at else 0.0,
            "endedAt": 1000.0 + (duration_ms if ended_at is None else ended_at),
            "ended": True}


def test_a_stop_after_the_clip_ended_is_not_a_truncation():
    """CI's real numbers: stop() 1265 ms into a 1207 ms clip — 58 ms AFTER the audio
    finished. The old assertion (`not p["stoppedAt"]`) called that a cut and failed the
    build; nothing was lost, and the message it printed said so."""
    assert truncated_by(_play(1207, stopped_at=1265, ended_at=1265)) == 0.0
    # …and an ordinary uncut play, this box's own: no stop at all.
    assert truncated_by(_play(2519)) == 0.0


def test_a_stop_before_the_clip_ended_is_still_caught():
    """The regression this file exists for, with the numbers it was found at: stop() 2237
    ms into a 2519 ms clip — 282 ms of "…it's my birthd—" never played."""
    lost = truncated_by(_play(2519, stopped_at=2237, ended_at=2237))
    assert lost > 0 and round(lost) == 282, lost
    # A cut is caught through `endedAt` too, in case a stop is never recorded.
    assert round(truncated_by(_play(1207, ended_at=900))) == 307
    # And the tolerance is a boundary, not a slope: just outside it still fails.
    assert truncated_by(_play(2519, stopped_at=2519 - COMPLETION_TOLERANCE_MS - 1)) > 0
    assert truncated_by(_play(2519, stopped_at=2519 - COMPLETION_TOLERANCE_MS + 1)) == 0.0


# --------------------------------------------------------------------------- #
# 5. No console errors along the way (the audio path throws quietly otherwise).
# --------------------------------------------------------------------------- #
def test_the_replay_is_console_clean(replayed):
    real = [e for e in replayed["console"] if "favicon" not in e]
    assert not real, f"console errors during the child's turn: {real[:3]}"
    assert replayed["decodes"], "no audio was decoded at all"
    print(f"   {len(replayed['fetches'])} clip fetch(es), {len(replayed['decodes'])} "
          f"decode(s), {len(replayed['plays'])} playback(s), "
          f"{json.dumps(sorted(replayed['destIds']))} destination node(s)")
