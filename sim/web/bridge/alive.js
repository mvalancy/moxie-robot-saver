/* bridge/alive.js — window.moxieAlive: reactions and thinking, as the loading bar.
 *
 * Between the button and the answer (recording, then a ~1-4 s round trip) she reacts the
 * way a person asked a question does. The rules that keep it from being annoying:
 *   · SUBTLE — `Gesture_Think_Subtle` and small head moves, never a celebration.
 *   · NEVER THE SAME TWICE RUNNING — `pickDifferent`, like `filler.py::pick_filler`.
 *   · LATE — nothing for `THINK_DELAY_MS`, since most turns answer inside it.
 *   · IT YIELDS — `settled()` ends every wait and the real reply's markup takes over.
 *   · IT LASTS UNTIL HER VOICE — the cue is over when the reply STARTS, not when the brain
 *     answers: `answered()` keeps her visibly working, quietly, through chunk 0's synthesis,
 *     and `transcribing()` through a clip's upload (W4-S1).
 */
(function () {
  "use strict";
  const B = window.__moxieBridge;
  var THINK_DELAY_MS = 900;
  /* `mqtt/moxie_sdk/filler.py`'s eight lines, pre-rendered. Text must match the clip
   * manifest key CHARACTER FOR CHARACTER (sim/test_ambient.mjs pins the pair). */
  var FILLERS = [
    "Hmm, let me think about that one.",
    "Ooh, good question! Give me a second.",
    "One moment — my thinking gears are spinning.",
    "Hold on, I'm still working that out.",
    "That's a big one. I'm thinking hard!",
    "Just a sec — I want to get this right.",
    "Hmmmm. Almost got it.",
    "Thinking, thinking… nearly there."
  ];
  /* A thinking FACE at 900 ms; a spoken filler only once her VOICE is late — past the
   * expected time-to-voice of a live turn (chat 2.0–2.3 s + speech 1.6–1.9 s, measured), so an
   * ordinary answer is never talked over (at 1.8 s a filler preceded 3 of 3 measured answers).
   * And never over a voice of hers still in the air or just ended (`isMoxieBusy`, ambient's
   * grace beat), nor while her answer waits in the voice queue — no speaking predicate sees a
   * queued chunk, and the filler's `speak()` would flush it. */
  var SPEAK_FILLER_AFTER_MS = 3500;
  var FILLER_GRACE_MS = 1600;
  /* Once the brain has ANSWERED, her voice is imminent (chunk 0 is being synthesised) and
   * nothing may be said — a filler cut short by her own voice is the one-voice defect above
   * — but she must not freeze either: measured on production (N=5 typed turns, 2026-10-08)
   * the body settled at chat return and stood still, status blank, for the 2.0-2.6 s until
   * her voice. A QUIET beat every VOICE_WAIT_MS (a small turn of her body, no sound) covers
   * it, and the same beat covers the 2-3 s a clip is being transcribed. */
  var VOICE_WAIT_MS = 2000;
  var thinkTimer = null, thinkStage = 0;
  var thinkingOn = false;  // a turn's thinking cue is armed (`thinking()`), not yet ended
  var answered = false;    // the chat response is in: no spoken filler from here
  var pose = null;         // what the body holds: "listening", "thinking", "voice-wait", "transcribing", or nothing
  var lastPick = {};

  /** May the filler make a sound now? */
  function mayFill() {
    var a = window.moxieAudio;
    if (!a || !a.speak) return false;
    try {
      if (a.isMoxieBusy && a.isMoxieBusy(FILLER_GRACE_MS)) return false;
      if (a.ttsPending && a.ttsPending() > 0) return false;
    } catch (e) { return false; }
    return true;
  }

  /** One of `list`, never the one this channel returned last. */
  function pickDifferent(channel, list) {
    var options = list.filter(function (x) { return x !== lastPick[channel]; });
    if (!options.length) options = list;
    var choice = options[Math.floor(Math.random() * options.length)];
    lastPick[channel] = choice;
    return choice;
  }

  function clearThink() {
    if (thinkTimer !== null) { clearTimeout(thinkTimer); thinkTimer = null; }
    thinkStage = 0;
    thinkingOn = false;
    answered = false;
    pose = null;
  }

  /** The quiet beat: a small turn of her body (yaw, as beat 3), no sound, every
   *  VOICE_WAIT_MS until `settled()` — or until a new cue (`listening`, `thinking`) takes over. */
  function quietBeat() {
    thinkTimer = null;
    try { if (window.moxie) B.set(5, pickDifferent("waitYaw", [17200, 15600])); } catch (e) {}
    thinkTimer = setTimeout(quietBeat, VOICE_WAIT_MS);
  }

  var alive = {
    /** Recorded facts for the tests: fillers actually spoken, and fillers withheld because
     *  she was still talking (or her answer was queued). */
    stats: { spoke: 0, held: 0 },
    /** The visitor just opened the microphone: she notices and leans in. Immediate on
     *  purpose — this IS the acknowledgement that the tap landed. */
    listening: function () {
      clearThink();
      pose = "listening";
      try {
        if (window.moxie) {
          window.moxie.setFace(pickDifferent("listen", ["curious", "happy"]));
          B.gesture(pickDifferent("listenGesture", ["Gesture_Think_Subtle", "Gesture_None"]));
          B.set(4, pickDifferent("listenHead", [17600, 15400]));   // a small nod or tilt
        }
      } catch (e) {}
    },

    /** A turn is in flight: beat 1 (THINK_DELAY_MS) is a thinking face, beat 2 a spoken
     *  filler, beat 3 one more small move so a long wait keeps moving. Once `answered()`,
     *  the quiet beat takes over after beat 1. */
    thinking: function () {
      clearThink();
      thinkingOn = true;
      var beat = function () {
        thinkStage++;
        try {
          if (!window.moxie) return;
          if (thinkStage === 1) {
            pose = "thinking";
            window.moxie.setFace(pickDifferent("thinkFace", ["thinking", "curious"]));
            B.gesture("Gesture_Think_Subtle");
          } else if (thinkStage === 2) {
            /* The "ambient" group is the one a reply may cut off mid-word
             * (voice/core.js::heldBy) — what a person does when they finish thinking. A
             * missing clip is silence, so this degrades to the face-only cue. */
            B.gesture(pickDifferent("thinkAgain", ["Gesture_Think", "Gesture_Think_Subtle"]));
            if (!mayFill()) alive.stats.held++;
            else {
              try {
                window.moxieAudio.speak(pickDifferent("filler", FILLERS), "ambient");
                alive.stats.spoke++;
              } catch (e) {}
            }
          } else {
            B.gesture(pickDifferent("thinkAgain", ["Gesture_Think", "Gesture_Think_Subtle"]));
            B.set(5, pickDifferent("thinkYaw", [17400, 15600]));
          }
        } catch (e) {}
        if (answered) { pose = "voice-wait"; thinkTimer = setTimeout(quietBeat, VOICE_WAIT_MS); return; }
        if (thinkStage < 3) thinkTimer = setTimeout(beat, thinkStage === 1 ? (SPEAK_FILLER_AFTER_MS - THINK_DELAY_MS) : 2200);
        else thinkTimer = null;
      };
      thinkTimer = setTimeout(beat, THINK_DELAY_MS);
    },

    /** The brain has answered and her voice is on its way (a reply holding voice tickets,
     *  told at chat return): no spoken filler from here — her voice is imminent, and a filler
     *  it cut short was the double voice — but she keeps visibly working: the thinking face
     *  and pose stay, and the quiet beat moves her every VOICE_WAIT_MS until `settled()`. Beat
     *  1 still due fires first (a fast brain never flashes a pose), then the quiet beat follows
     *  it. Only a turn's thinking cue is carried on: with none armed (the ears took the body,
     *  or the turn armed none) there is nothing to quiet and nothing moves. */
    answered: function () {
      if (!thinkingOn) return;
      answered = true;
      if (thinkStage === 0 && thinkTimer !== null) return;
      if (thinkTimer !== null) clearTimeout(thinkTimer);
      pose = "voice-wait";
      thinkTimer = setTimeout(quietBeat, VOICE_WAIT_MS);
    },

    /** The clip is being transcribed (mic.js, at the recording's end): the listening face
     *  and the head's tilt are HELD — she is still working on what she heard — with the quiet
     *  beat so a long upload keeps moving. The transcript's send arms `thinking()`; a dropped
     *  clip or a failed upload calls `settled()`. */
    transcribing: function () {
      clearThink();
      pose = "transcribing";
      thinkTimer = setTimeout(quietBeat, VOICE_WAIT_MS);
    },

    /** The wait is over (the reply is starting, a refusal, a dropped clip…). Hands the body
     *  back but sets NO face: the reply's own markup is about to. */
    settled: function () {
      clearThink();
      try { B.armsHome(); } catch (e) {}
    },

    /** Recorded state for the tests (playbook rule 11) and the journey instrument's cue
     *  tracker: `stage` the thinking beat reached, `pose` what the body holds right now. */
    __state: function () { return { stage: thinkStage, armed: thinkTimer !== null, answered: answered, pose: pose, last: lastPick }; },
  };
  window.moxieAlive = alive;
})();
