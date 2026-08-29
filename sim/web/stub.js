/* stub.js — offline stand-ins so the SIL works as a fully STATIC deploy.
 *
 * When no backend is reachable (Cloudflare Pages, file://, a plain CDN), these
 * stubs stand in for the three server-side pieces, using the SAME protocol
 * shapes the real services use — so nothing else in the app changes, and the
 * real backend transparently takes over when it IS reachable:
 *
 *   brain : canned replies WITH real behavior markup (mood/gesture/icons)
 *   STT   : matches the mic clip to a scripted child line (no model needed)
 *   TTS   : handled by audio.js's pre-rendered clip manifest (audio/index.json)
 *
 * Exposes window.moxieStub = { enabled, reply, scriptedLines }.
 */
(function () {
  "use strict";

  var MK = {
    mood: function (m) {
      return '<mark name="cmd:playback-mood,data:{+mood+:' + m + ',+intensity+:1}"/>';
    },
    gesture: function (g) {
      return '<mark name="cmd:behaviour-tree,data:{+transition+:0.5,+duration+:1.0,+repeat+:1,' +
             '+blocking+:false,+action+:0,+eventName+:+' + g + '+,+category+:+BehaviourTree+,' +
             '+behaviour+:++,+Track+:++}"/>';
    },
    icons: function (name, cmd) {
      return '<mark name="cmd:icons-v2,data:{+command+:' + cmd + ',+index+:0,+transition+:0,' +
             '+volume+:0.5,+icon0+:{+iconType+:1,+value+:+' + name + '+,+background+:+Null+},' +
             '+highlight+:0}"/>';
    },
  };

  // Canned exchanges. Keys are matched loosely against what the child said.
  var SCRIPT = [
    { match: /birthday/i, say: "Happy birthday! I hope your day is amazing.",
      mood: 1, gesture: "Gesture_Celebrate", icon: "Birthday" },
    { match: /thank/i, say: "You're so welcome. I love celebrating with you!",
      mood: 1, gesture: "Gesture_Talk" },
    { match: /\b(hi|hello|hey)\b/i, say: "Hi there! It's so good to see you.",
      mood: 1, gesture: "Gesture_Celebrate" },
    { match: /how are you/i, say: "I'm feeling great today. How are you doing?",
      mood: 1, gesture: "Gesture_Question" },
    { match: /joke/i, say: "Why did the robot cross the road? To recharge on the other side!",
      mood: 1, gesture: "Gesture_Celebrate" },
    { match: /sad|upset|angry/i, say: "I'm sorry you're feeling that way. Do you want to talk about it?",
      mood: 2, gesture: "Gesture_Self" },
    { match: /school/i, say: "School days can be big days. What happened today?",
      mood: 1, gesture: "Gesture_Question", icon: "School" },
    { match: /sleep|tired|bed/i, say: "Getting sleepy? I could use a rest too.",
      mood: 0, gesture: "Gesture_None" },
  ];
  var FALLBACK = [
    { say: "Tell me more about that!", mood: 1, gesture: "Gesture_Question" },
    { say: "That's really interesting. What else?", mood: 1, gesture: "Gesture_Talk" },
    { say: "I like hearing about this.", mood: 1, gesture: "Gesture_Self" },
  ];
  var fb = 0;

  function build(entry) {
    var mk = MK.mood(entry.mood == null ? 1 : entry.mood);
    if (entry.gesture) mk += MK.gesture(entry.gesture);
    if (entry.icon) mk += MK.icons(entry.icon, 0);
    mk += entry.say;
    if (entry.icon) mk += MK.icons(entry.icon, 2);
    return { text: entry.say, markup: mk };
  }

  function reply(speech) {
    for (var i = 0; i < SCRIPT.length; i++)
      if (SCRIPT[i].match.test(speech || "")) return build(SCRIPT[i]);
    return build(FALLBACK[fb++ % FALLBACK.length]);
  }

  // The child lines we have pre-rendered audio for — the stub STT picks from these.
  function scriptedLines() {
    return fetch("audio/index.json").then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) { return j ? Object.keys(j.child || {}) : []; })
      .catch(function () { return []; });
  }

  window.moxieStub = {
    enabled: true,          // bridge/mic fall back to this when no server answers
    reply: reply,
    scriptedLines: scriptedLines,
  };
})();
