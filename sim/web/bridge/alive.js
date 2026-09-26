/* bridge/alive.js — window.moxieAlive: reactions and thinking, as the loading bar.
 *
 * Between the button and the answer (recording, then a ~1-4 s round trip) she reacts the
 * way a person asked a question does. The rules that keep it from being annoying:
 *   · SUBTLE — `Gesture_Think_Subtle` and small head moves, never a celebration.
 *   · NEVER THE SAME TWICE RUNNING — `pickDifferent`, like `filler.py::pick_filler`.
 *   · LATE — nothing for `THINK_DELAY_MS`, since most turns answer inside it.
 *   · IT YIELDS — `settled()` ends every wait and the real reply's markup takes over.
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
  /* A thinking FACE at 900 ms; a spoken filler only past 1800 ms (beyond the median turn),
   * so a quick answer is never talked over. */
  var SPEAK_FILLER_AFTER_MS = 1800;
  var thinkTimer = null, thinkStage = 0;
  var lastPick = {};

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
  }

  var alive = {
    /** Recorded facts for the tests: how many fillers were actually spoken. */
    stats: { spoke: 0 },
    /** The visitor just opened the microphone: she notices and leans in. Immediate on
     *  purpose — this IS the acknowledgement that the tap landed. */
    listening: function () {
      clearThink();
      try {
        if (window.moxie) {
          window.moxie.setFace(pickDifferent("listen", ["curious", "happy"]));
          B.gesture(pickDifferent("listenGesture", ["Gesture_Think_Subtle", "Gesture_None"]));
          B.set(4, pickDifferent("listenHead", [17600, 15400]));   // a small nod or tilt
        }
      } catch (e) {}
    },

    /** A turn is in flight: beat 1 (THINK_DELAY_MS) is a thinking face, beat 2 a spoken
     *  filler, beat 3 one more small move so a long wait keeps moving. */
    thinking: function () {
      clearThink();
      var beat = function () {
        thinkStage++;
        try {
          if (!window.moxie) return;
          if (thinkStage === 1) {
            window.moxie.setFace(pickDifferent("thinkFace", ["thinking", "curious"]));
            B.gesture("Gesture_Think_Subtle");
          } else if (thinkStage === 2) {
            /* The "ambient" group is the one a reply may cut off mid-word
             * (voice/core.js::heldBy) — what a person does when they finish thinking. A
             * missing clip is silence, so this degrades to the face-only cue. */
            var line = pickDifferent("filler", FILLERS);
            B.gesture(pickDifferent("thinkAgain", ["Gesture_Think", "Gesture_Think_Subtle"]));
            try {
              if (window.moxieAudio && window.moxieAudio.speak) window.moxieAudio.speak(line, "ambient");
              alive.stats.spoke++;
            } catch (e) {}
          } else {
            B.gesture(pickDifferent("thinkAgain", ["Gesture_Think", "Gesture_Think_Subtle"]));
            B.set(5, pickDifferent("thinkYaw", [17400, 15600]));
          }
        } catch (e) {}
        if (thinkStage < 3) thinkTimer = setTimeout(beat, thinkStage === 1 ? (SPEAK_FILLER_AFTER_MS - THINK_DELAY_MS) : 2200);
        else thinkTimer = null;
      };
      thinkTimer = setTimeout(beat, THINK_DELAY_MS);
    },

    /** The wait is over (answer, refusal, cancelled recording…). Hands the body back but
     *  sets NO face: the reply's own markup is about to. */
    settled: function () {
      clearThink();
      try { B.armsHome(); } catch (e) {}
    },

    /** Recorded state for the tests (playbook rule 11). */
    __state: function () { return { stage: thinkStage, armed: thinkTimer !== null, last: lastPick }; },
  };
  window.moxieAlive = alive;
})();
