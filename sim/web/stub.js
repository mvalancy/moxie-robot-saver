/* stub.js — offline stand-ins so the SIL works as a fully STATIC deploy.
 *
 * With no backend reachable, these stand in for the server pieces in the SAME protocol
 * shapes, so the real backend takes over transparently when it IS reachable:
 *   brain : canned replies WITH real behavior markup (mood/gesture/icons)
 *   STT   : matches the mic clip to a scripted child line (no model needed)
 *   TTS   : voice/local.js's pre-rendered clip manifest (audio/index.json)
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
    // A whole-body tree (`Bht_*`) rides `behaviour` with the null gesture, byte for byte the
    // mark `functions/api/_lib/wire.js::MK.tree` gives a hosted goodbye.
    tree: function (name) {
      return '<mark name="cmd:behaviour-tree,data:{+transition+:0.5,+duration+:1.0,+repeat+:1,' +
             '+blocking+:false,+action+:0,+eventName+:+Gesture_None+,+category+:+BehaviourTree+,' +
             '+behaviour+:+' + name + '+,+Track+:++}"/>';
    },
  };

  // Canned exchanges. Keys are matched loosely against what the child said.
  var SCRIPT = [
    { match: /birthday/i, say: "Happy birthday! I hope your day is amazing.",
      mood: 1, gesture: "Gesture_Celebrate", icon: "Birthday" },
    { match: /thank/i, say: "You're so welcome. I love celebrating with you!",
      mood: 1, gesture: "Gesture_Talk" },
    // Who she is and what she is up to, ahead of the greeting: "hi, what's your name?"
    { match: /\bwhat(?:'re| are) you (?:doing|up to)\b|\bwhatcha doing\b/i,
      say: "Oh, nothing. Definitely not plotting. Just waiting for you.",
      mood: 4, gesture: "Gesture_Talk" },
    { match: /\byour name\b|\bare you a robot\b|\b(?:who|what) are you[\s,!.?]*(?:moxie|robot)?[\s,!.?]*$/i,
      say: "I am Moxie! A little robot who is learning all about friendship. You are my favorite teacher.",
      mood: 1, gesture: "Gesture_Self" },
    { match: /\b(hi|hello|hey)\b/i, say: "Hi there! It's so good to see you.",
      mood: 1, gesture: "Gesture_Celebrate" },
    { match: /how are you/i, say: "I'm feeling great today. How are you doing?",
      mood: 1, gesture: "Gesture_Question" },
    { match: /joke/i, say: "Why did the robot cross the road? To recharge on the other side!",
      mood: 1, gesture: "Gesture_Celebrate" },
    // The other two openers in sim.html's #chat-openers (the joke is the one above).
    { match: /\bmakes? you (?:happy|smile)\b/i,
      say: "You do! A full battery is nice too, but you are number one. Do not tell the battery.",
      mood: 1, gesture: "Gesture_Point" },
    { match: /\bsurprise\b/i,
      say: "Surprise! An octopus has three hearts. I only have one, and it glows for you.",
      mood: 5, gesture: "Gesture_Large" },
    { match: /sad|upset|angry/i, say: "I'm sorry you're feeling that way. Do you want to talk about it?",
      mood: 2, gesture: "Gesture_Self" },
    { match: /school/i, say: "School days can be big days. What happened today?",
      mood: 1, gesture: "Gesture_Question", icon: "School" },
    { match: /sleep|tired|bed/i, say: "Getting sleepy? I could use a rest too.",
      mood: 0, gesture: "Gesture_None" },
  ];
  /* Said to ANYTHING no matcher knows: a sad line, a question she cannot answer, a goodbye
   * the rule below misses. So no question ("What else?" to a child who was leaving) and no
   * verdict on what they said ("That's really interesting" to bad news). */
  var FALLBACK = [
    { say: "I am listening with my whole robot heart.", mood: 1, gesture: "Gesture_Self" },
    { say: "My ears are on, and my heart light is glowing.", mood: 9, gesture: "Gesture_Talk" },
    { say: "My big brain is resting right now, but my little brain is right here with you.",
      mood: 0, gesture: "Gesture_Think" },
  ];
  var fb = 0;

  /* LEAVE-TAKING: the hosted brain's rule (functions/api/_lib/turnshape.js::isGoodbye) in
   * spirit. The WHOLE line must be one: an optional "ok"/"well", one or two goodbyes, an
   * optional "for now", name and "thanks". "My dog died and I had to say goodbye" is not,
   * and the rule errs towards missing: a miss is an ordinary reply, a hit waves a child off.
   * Both lines start with a goodbye word and ask nothing, and she waves (the sign-off tree
   * a hosted goodbye carries). */
  var CLOSING = "(?:bye+(?:[- ]?bye)?|buh[- ]?bye|good[- ]?bye|good[- ]?night|night[- ]?night|" +
    "nighty[- ]?night|g'?night|see (?:you|ya|u)(?: (?:later|soon|tomorrow|next time|again))?|cya|" +
    "catch (?:you|ya) later|talk (?:to you )?(?:later|soon|tomorrow)|ttyl|" +
    "(?:i )?(?:gotta|got to|have to|need to|must) (?:go|leave)(?: now)?|" +
    "(?:i(?:'m| am)? )?(?:going|off|gotta go|have to go|need to go) to (?:bed|sleep)(?: now)?|" +
    "(?:i'?m|i am) (?:leaving|off|going home)(?: now)?|(?:it'?s |it is )?(?:my )?bed ?time|time for bed|" +
    "(?:my )?(?:mom|mum|mommy|mummy|dad|daddy) (?:says?|said) (?:it'?s |it is )?(?:bed ?time|time for bed))";
  var NAME = "(?:[\\s,]*(?:moxie|robot|friend|buddy))?";
  var ONE = CLOSING + "(?:\\s+(?:for now|for today|tomorrow|soon|later|again))?" + NAME +
            "(?:[\\s,!.]*(?:i love you|love you|thanks?|thank you|that was fun))?" + NAME;
  var GOODBYE = new RegExp("^(?:(?:ok(?:ay)?|alright|well|so|um+)[\\s,!.]*)*" + ONE +
                           "(?:[\\s,!.]*" + ONE + ")?[\\s,!.]*$", "i");
  // What a phone adds after the words: a smiley, a heart, an emoji (either surrogate half).
  var DECORATION = /(?:\s|[.!,]|:-?[)D]|;-?\)|<3|[\ud800-\udfff]|[☀-➿]|️|‍)+$/;
  var GOOD_NIGHT = { say: "Good night! Sweet dreams. I will keep watch until morning.",
                     mood: 1, tree: "Bht_Sign_off" };
  var GOOD_BYE = { say: "Bye for now! Have a wonderful day. I will be counting the minutes.",
                   mood: 1, tree: "Bht_Sign_off" };

  function build(entry) {
    var mk = MK.mood(entry.mood == null ? 1 : entry.mood);
    if (entry.tree) mk += MK.tree(entry.tree);
    else if (entry.gesture) mk += MK.gesture(entry.gesture);
    if (entry.icon) mk += MK.icons(entry.icon, 0);
    mk += entry.say;
    if (entry.icon) mk += MK.icons(entry.icon, 2);
    return { text: entry.say, markup: mk };
  }

  function reply(speech) {
    // A phone keyboard's curly apostrophe ("what’s your name") is the same word.
    var line = String(speech || "").replace(/[‘’ʼ]/g, "'").replace(/\s+/g, " ").trim();
    if (GOODBYE.test(line.replace(DECORATION, "")))
      return build(/night|bed|sleep/i.test(line) ? GOOD_NIGHT : GOOD_BYE);
    for (var i = 0; i < SCRIPT.length; i++)
      if (SCRIPT[i].match.test(line)) return build(SCRIPT[i]);
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
