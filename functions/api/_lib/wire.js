/* functions/api/_lib/wire.js — the two payloads `bridge.js` already knows how to render.
 *
 * Spec: docs/architecture/backlog/live-sim-demo.md §2.2 (the cloud/turn contract), §3.2
 * (both route response shapes), §2.6 (the "minimal markup floor" that stands in for
 * `automarkup.annotate`).
 *
 * The SIM front end is a PROTOCOL client: `bridge.js::route(topic, payloadString)` parses
 * the JSON itself and `audio.js` decodes `CloudTTSResponse` itself. So the hosted brain
 * must produce the same JSON strings the supervisor does. This is a cited transcription
 * of `mqtt/moxie_sdk/wire.py::build_chat_response` and
 * `mqtt/moxie_sdk/tts.py::build_cloud_tts_response` (a Function cannot import Python).
 *
 * Field-set rules (spec §10; asserted by `sim/test_demo_proxy.mjs`):
 *   1. Chat fields are EXACTLY `command`, `result` (enum NAME), `backend`, `event_id`,
 *      `output.{text, markup}`, `end_turn`.
 *   2. `chunk_num`/`consistency_control` are OMITTED on a single-chunk turn, keeping it
 *      byte-identical to the pre-streaming wire.
 *   3. NO `emotion` field — no real server emits one; the mood MARK carries the face.
 *   4. `payload` is a STRING, because `route()` calls `JSON.parse` itself.
 *
 * Nothing here is configurable by a request: the device id is `DEMO_DEVICE_ID` and the
 * rest are constants.
 */

/* ---------------------------------------------------------------------------- *
 * The gateway URL
 * ---------------------------------------------------------------------------- */

/** `base` + `/path`, tolerant of a trailing slash on the base. `base` is the secret
 *  `DEMO_GATEWAY_BASE_URL` (§4.2): the result goes into `fetch()` and nowhere else. */
export function joinUrl(base, path) {
  return String(base).replace(/\/+$/, "") + "/" + String(path).replace(/^\/+/, "");
}

/* ---------------------------------------------------------------------------- *
 * Topics and ids
 * ---------------------------------------------------------------------------- */

/** `/devices/<id>/<suffix>` — `bridge.js::route()` dispatches on the suffix only. */
export function topic(deviceId, suffix) {
  return "/devices/" + String(deviceId || "d_sim") + "/" + String(suffix || "");
}

/** A per-turn `event_id`, `sim-` prefixed like the SIM's own ids so it never collides
 *  with a robot's. */
export function eventId() {
  const b = new Uint8Array(6);
  crypto.getRandomValues(b);
  let s = "";
  for (const v of b) s += v.toString(16).padStart(2, "0");
  return "sim-" + s;
}

/* ---------------------------------------------------------------------------- *
 * The chat response
 * ---------------------------------------------------------------------------- */

/** The `ResultCode` NAMES the contract uses (a name, never a number). The SIM ignores
 *  it; the honest one is sent anyway for other clients. */
export const RESULT = Object.freeze({ SUCCESS: "SUCCESS", ERROR_OFFLINE: "ERROR_OFFLINE" });

/**
 * `wire.build_chat_response`'s output, field for field.
 *
 * `end_turn` defaults to FALSE like `wire.py`: `true` means "stop listening after this".
 *
 * @param {{result?:string, backend?:string, eventId:string, text:string, markup?:string, endTurn?:boolean}} o
 */
export function buildChatResponse(o) {
  const text = String((o && o.text) || "");
  const markup = o && o.markup ? String(o.markup) : text; // wire.py: `markup or text`
  return {
    command: "remote_chat",
    result: o && o.result === RESULT.ERROR_OFFLINE ? RESULT.ERROR_OFFLINE : RESULT.SUCCESS,
    backend: String((o && o.backend) || "router"),
    event_id: String((o && o.eventId) || ""),
    output: { text, markup },
    end_turn: !!(o && o.endTurn),
  };
}

/** One `{topic, payload}` pair, ready for `route()`. `payload` is a string on purpose. */
export function chatMessage(deviceId, response) {
  return { topic: topic(deviceId, "commands/remote_chat"), payload: JSON.stringify(response) };
}

/* ---------------------------------------------------------------------------- *
 * The CloudTTSResponse
 * ---------------------------------------------------------------------------- */

/**
 * `tts.build_cloud_tts_response`'s output — the inverse of `audio.js::decodeCloudTTS`.
 * `buffer` is base64 of RAW little-endian 16-bit PCM, not a container. `marks` is `[]`:
 * with no marks the mouth follows the audio envelope, so lip-sync still happens.
 */
export function buildCloudTtsResponse(o) {
  return {
    request_source: "ROBOT_TTS_REQUEST",
    audio: {
      buffer: String((o && o.buffer) || ""),
      channels: Number((o && o.channels) || 1),
      sample_rate: Number((o && o.sampleRate) || 0),
    },
    marks: [],
    event_id: String((o && o.eventId) || ""),
    chunk_num: Number((o && o.chunkNum) || 0),
  };
}

export function ttsMessage(deviceId, response) {
  return { topic: topic(deviceId, "commands/tts"), payload: JSON.stringify(response) };
}

/* ---------------------------------------------------------------------------- *
 * The minimal markup floor
 * ---------------------------------------------------------------------------- */

/** The three mark templates, byte-for-byte the ones `sim/web/stub.js` emits and
 *  `bridge.js::applyMarkup` parses (the only three families it knows). */
export const MK = Object.freeze({
  mood(m) {
    return '<mark name="cmd:playback-mood,data:{+mood+:' + Number(m) + ',+intensity+:1}"/>';
  },
  gesture(g) {
    return (
      '<mark name="cmd:behaviour-tree,data:{+transition+:0.5,+duration+:1.0,+repeat+:1,' +
      "+blocking+:false,+action+:0,+eventName+:+" +
      String(g) +
      "+,+category+:+BehaviourTree+," +
      '+behaviour+:++,+Track+:++}"/>'
    );
  },
  icons(name, cmd) {
    return (
      '<mark name="cmd:icons-v2,data:{+command+:' +
      Number(cmd) +
      ",+index+:0,+transition+:0," +
      "+volume+:0.5,+icon0+:{+iconType+:1,+value+:+" +
      String(name) +
      "+,+background+:+Null+}," +
      '+highlight+:0}"/>'
    );
  },
  /** A whole-body behaviour tree (`Bht_*`), byte-for-byte `vocab.tree_mark("Gesture_None",
   *  name)`: the tree rides `behaviour` and the gesture slot holds the null gesture, which is
   *  what `bridge.js::applyMarkup` reads it from. */
  tree(name) {
    return (
      '<mark name="cmd:behaviour-tree,data:{+transition+:0.5,+duration+:1.0,+repeat+:1,' +
      "+blocking+:false,+action+:0,+eventName+:+Gesture_None+,+category+:+BehaviourTree+," +
      "+behaviour+:+" +
      String(name) +
      '+,+Track+:++}"/>'
    );
  },
});

/** `ePlaybackMood`, the authoritative eleven (recovered from Assembly-CSharp;
 *  `docs/reverse-engineering/runtime/behavior-markup.md`), mapped 1:1 to SIL faces by
 *  `bridge.js`. The regex floor picks five; the MODEL may name any of the eleven. */
export const MOOD = Object.freeze({
  NEUTRAL: 0, HAPPY: 1, SAD: 2, ANGRY: 3, SHY: 4, SURPRISED: 5,
  AFRAID: 6, CONCERNED: 7, CONFUSED: 8, CURIOUS: 9, EMBARRASSED: 10,
});

/** Name -> number for a mood the MODEL wrote. Closed set: anything else is ignored. */
const MOOD_BY_NAME = Object.freeze(
  Object.fromEntries(Object.entries(MOOD).map(([k, v]) => [k.toLowerCase(), v])));

/** The gestures `bridge.js::gesture()` implements, by the short name the model writes.
 *  An unknown name is dropped, never passed through (it would silently do nothing). */
const GESTURE_BY_NAME = Object.freeze({
  none: "Gesture_None", talk: "Gesture_Talk", think: "Gesture_Think",
  question: "Gesture_Question", point: "Gesture_Point", self: "Gesture_Self",
  big: "Gesture_Large", large: "Gesture_Large", up: "Gesture_Higher",
  down: "Gesture_Lower", celebrate: "Gesture_Celebrate",
});

/** The goodbye wave: `bridge/body.js::behaviourTree` plays it, `actions.js` plays it on an
 *  `exit`, and the Python performance layer names it for a closing beat. */
export const SIGN_OFF = "Bht_Sign_off";

/** Whole-body trees the MODEL may name, by the short name it writes. She asked for `wave`
 *  unprompted on 3 of 6 goodbyes before it was offered (measured), and the table above
 *  dropped it. A closed set like the gestures. */
const TREE_BY_NAME = Object.freeze({ wave: SIGN_OFF });

/** The vocabulary `chat.js` hands the model, built from the tables above. */
export function expressiveVocab() {
  return {
    moods: Object.keys(MOOD_BY_NAME),
    gestures: ["none", "talk", "think", "question", "point", "self", "big", "up", "down", "celebrate",
               ...Object.keys(TREE_BY_NAME)],
  };
}

/**
 * The floor's rule set, in evaluation order. Deliberately tiny and deterministic: a
 * faithful port of `automarkup.annotate` (golden-tested Python) is P2 (§9), and a guessed
 * port would be a second, subtly different behaviour language.
 */
const FLOOR = [
  { re: /\b(sorry|sad|miss|lonely|hurt|cry|crying|upset)\b/i, mood: MOOD.SAD, gesture: "Gesture_Self" },
  { re: /\b(wow|amazing|whoa|incredible|awesome)\b/i, mood: MOOD.SURPRISED, gesture: "Gesture_Large" },
  { re: /\b(hooray|yay|congratulations|happy birthday|well done)\b/i, mood: MOOD.HAPPY, gesture: "Gesture_Celebrate" },
  { re: /\?\s*$/, mood: MOOD.CURIOUS, gesture: "Gesture_Question" },
  { re: /\b(hmm+|maybe|i think|let me think|i wonder)\b/i, mood: MOOD.CURIOUS, gesture: "Gesture_Think" },
  { re: /!\s*$/, mood: MOOD.HAPPY, gesture: "Gesture_Celebrate" },
];

/** The default: warm, talking. `Gesture_Talk` is what a line with no other signal gets. */
const FLOOR_DEFAULT = { mood: MOOD.HAPPY, gesture: "Gesture_Talk" };

/** A handful of on-face badges, matched on the reply's own words (not exhaustive). */
const ICONS = [
  { re: /\bbirthday\b/i, icon: "Birthday" },
  { re: /\bschool\b/i, icon: "School" },
  { re: /\b(star|stars|space|planet|moon)\b/i, icon: "Star" },
  { re: /\b(music|song|sing|dance)\b/i, icon: "Music" },
];

/**
 * Text -> markup: mood + one gesture + an optional icon pair around the text (as
 * `stub.js::build` does), the floor §2.6 specifies. PURE, so tests assert exact markup.
 *
 * @param {string} [tree] a whole-body tree the ROUTE asks for (the sign-off wave on a
 *   goodbye turn); one of `TREE_BY_NAME`'s values or it is ignored.
 */
export function markupFloor(text, chosen, tree) {
  const s = String(text || "");
  if (!s) return "";
  /* The model's own choice (`chosen = {mood, gesture}`, short names; absent for plain
   * prose or when the envelope is off) wins over the floor, field by field, each checked
   * against the closed tables. The floor's default is HAPPY + talk, which is why the site
   * once wore one fixed grin; the model has the actual intent. */
  const pick = chosen && typeof chosen === "object" ? chosen : null;
  const moodName = pick && typeof pick.mood === "string" ? pick.mood.trim().toLowerCase() : "";
  const gestName = pick && typeof pick.gesture === "string" ? pick.gesture.trim().toLowerCase() : "";
  const moodNum = Object.prototype.hasOwnProperty.call(MOOD_BY_NAME, moodName)
    ? MOOD_BY_NAME[moodName] : null;
  const gestWire = Object.prototype.hasOwnProperty.call(GESTURE_BY_NAME, gestName)
    ? GESTURE_BY_NAME[gestName] : null;
  /* A whole-body tree outranks a gesture: the route's (`tree`) or the model's (`wave`). It
   * takes the gesture's slot, as the Python performance layer emits it, so the arms are not
   * asked for two things at once. */
  const treeName = Object.values(TREE_BY_NAME).includes(tree) ? tree
    : (Object.prototype.hasOwnProperty.call(TREE_BY_NAME, gestName) ? TREE_BY_NAME[gestName] : "");

  const matched = FLOOR.find((r) => r.re.test(s));
  const floor = matched || FLOOR_DEFAULT;

  /* The one overrule: this model collapses onto `happy` as a null answer (measured — it
   * answered "I'm sorry you felt left out" happily). So when it says HAPPY and a floor
   * rule ACTUALLY matched (never FLOOR_DEFAULT) with a different mood, the floor wins.
   * Any non-happy choice is taken as written. */
  const overrule = moodNum === MOOD.HAPPY && matched && matched.mood !== MOOD.HAPPY;
  const rule = {
    mood: overrule ? matched.mood : (moodNum === null ? floor.mood : moodNum),
    gesture: gestWire === null ? floor.gesture : gestWire,
  };
  const hit = ICONS.find((r) => r.re.test(s));
  let mk = MK.mood(rule.mood) + (treeName ? MK.tree(treeName) : MK.gesture(rule.gesture));
  if (hit) mk += MK.icons(hit.icon, 0);
  mk += s;
  if (hit) mk += MK.icons(hit.icon, 2);
  return mk;
}
