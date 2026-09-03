/* functions/api/_lib/wire.js — the two payloads `bridge.js` already knows how to render.
 *
 * Spec: docs/architecture/backlog/live-sim-demo.md §2.2 (the cloud/turn contract), §3.2
 * (both route response shapes), §2.6 (the "minimal markup floor" that stands in for
 * `automarkup.annotate`).
 *
 * WHY A WHOLE MODULE FOR THIS. The SIM front end is a PROTOCOL client, not an SDK client:
 * `bridge.js`'s `route(topic, payloadString)` dispatches on the topic suffix and parses
 * the JSON itself, and `audio.js` decodes the `CloudTTSResponse` wire itself "exactly like
 * robot firmware, never importing the server SDK". So the entire job of the hosted demo's
 * brain is to produce the same two JSON strings the supervisor produces — and this file is
 * the only place that shape is written down on the edge. It is a deliberate, cited
 * transcription of `mqtt/moxie_sdk/wire.py::build_chat_response` (:56-62) and
 * `mqtt/moxie_sdk/tts.py::build_cloud_tts_response` (:369-382), NOT an import: a Pages
 * Function cannot import Python, and a SIM client that decoded its own wire deserves a
 * server that builds its own wire.
 *
 * The four field-set rules, each one proven in the spec's §10 assumption ledger and
 * asserted by `sim/test_demo_proxy.mjs`:
 *
 *   1. The chat field set is EXACTLY `command`, `result` (the enum NAME), `backend`,
 *      `event_id`, `output.{text, markup}`, `end_turn`. Nothing else.
 *   2. `chunk_num` and `consistency_control` are OMITTED ENTIRELY on a single-chunk turn
 *      (`wire.py`:78-81; the runtime's `solo = final and n == 0` rule at
 *      `moxie_runtime.py`:1846-1855). That is what makes a non-streaming reply
 *      byte-identical to the pre-streaming wire, and P0 sends single-chunk turns only.
 *   3. NO `emotion` field. `bridge.js`:224 reads one, but `build_chat_response` never
 *      emits one (§10 assumption 20), so emitting it here would put a field on the wire
 *      that no real server sends. The mood MARK carries the face instead.
 *   4. `payload` is a STRING, because `route()` calls `JSON.parse` itself.
 *
 * Nothing in this file is configurable by a request. The device id comes from
 * `DEMO_DEVICE_ID` and everything else is a constant, so a visitor cannot influence the
 * topic, the result code or the shape of what their own browser is handed.
 */

/* ---------------------------------------------------------------------------- *
 * The gateway URL
 * ---------------------------------------------------------------------------- */

/** `base` + `/path`, tolerant of a trailing slash on the base.
 *
 *  It lives here rather than in a route so that BOTH routes build the upstream URL the
 *  same way and neither has to import the other. `base` is `DEMO_GATEWAY_BASE_URL`, which
 *  §4.2 forbids the browser from ever seeing: this function's output goes into `fetch()`
 *  and nowhere else — never into a response body, a header or an error string. */
export function joinUrl(base, path) {
  return String(base).replace(/\/+$/, "") + "/" + String(path).replace(/^\/+/, "");
}

/* ---------------------------------------------------------------------------- *
 * Topics and ids
 * ---------------------------------------------------------------------------- */

/** `/devices/<id>/<suffix>` — the topic layout `bridge.js`:53 builds and :599 matches.
 *  `route()` dispatches on the SUFFIX only, so the prefix is identity, not routing. */
export function topic(deviceId, suffix) {
  return "/devices/" + String(deviceId || "d_sim") + "/" + String(suffix || "");
}

/** A per-turn `event_id`. `sim-` prefixed like the SIM's own ids (`bridge.js`:436) so a
 *  recorded hosted turn is visibly a SIM turn and never collides with a robot's. */
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

/** The `ResultCode` NAMES the contract uses. `result` is a name, never a number
 *  (`wire.py`:56). The SIM ignores it entirely (§10 assumption 5) — we send the honest
 *  one anyway, because the next client to read this wire might not. */
export const RESULT = Object.freeze({ SUCCESS: "SUCCESS", ERROR_OFFLINE: "ERROR_OFFLINE" });

/**
 * `wire.build_chat_response`'s output, field for field.
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
    end_turn: o && o.endTurn === false ? false : true,
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
 * `tts.build_cloud_tts_response`'s output (:369-382), which is the exact inverse of
 * `audio.js`'s `decodeCloudTTS` (:260-282). `buffer` is base64 of RAW little-endian
 * signed 16-bit PCM — NOT a container: `audio.js`:610-614 says so in its own comment, and
 * `decodeAudioData()` could not read it. `audio.js` ignores `request_source`; it is sent
 * because a real server sends it.
 *
 * `marks` is `[]` in P0. That is not a lost feature: with no marks the mouth follows the
 * audio ENVELOPE (`audio.js`:666-681 and `sim/web/README.md`:58-62), so lip-sync still
 * happens — it is driven by amplitude instead of by visemes.
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

/**
 * The three mark templates, byte-for-byte the ones `sim/web/stub.js`:17-31 already emits —
 * which is exactly why the avatar is guaranteed to render them: `sim/test_bridge.mjs`
 * asserts against this markup today, and `applyMarkup` (`bridge.js`:131-160) parses these
 * three families and no others.
 */
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
});

/** `ePlaybackMood` values `bridge.js`:39-42 maps to a face. Only these five are used by
 *  the floor; the map has eleven, and a mood the floor never picks is not a mood the
 *  avatar cannot show. */
export const MOOD = Object.freeze({ NEUTRAL: 0, HAPPY: 1, SAD: 2, SURPRISED: 5, CURIOUS: 9 });

/**
 * The floor's whole rule set, in evaluation order. DELIBERATELY TINY and deliberately
 * deterministic: `automarkup.annotate` is a pure, golden-tested Python function whose
 * determinism rests on a `blake2b` digest (`automarkup.py`:29-31, :60-70), and a faithful
 * JS port with the Python goldens as its oracle is P2 (§9). Guessing at a port would give
 * the demo a second, subtly different behaviour language; this gives it a small, honest
 * one. Every gesture name here is one `bridge.js`'s `gesture()` switch actually implements.
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

/** A handful of on-face badges, matched on the reply's own words. Icon names are free
 *  text as far as the avatar is concerned (`moxie.js::showIcons` renders the label), so
 *  this table exists to make the face do something recognisable, not to be exhaustive. */
const ICONS = [
  { re: /\bbirthday\b/i, icon: "Birthday" },
  { re: /\bschool\b/i, icon: "School" },
  { re: /\b(star|stars|space|planet|moon)\b/i, icon: "Star" },
  { re: /\b(music|song|sing|dance)\b/i, icon: "Music" },
];

/**
 * Text -> markup, deterministically. Mood + one gesture + an optional icon pair, which is
 * precisely the floor §2.6 specifies. The text itself is embedded between the show and
 * clear icon marks the way `stub.js::build` does it, so a turn shows a badge at the start
 * and clears it at the end.
 *
 * PURE: same input, same output, no clock, no randomness. That is what lets
 * `sim/test_demo_proxy.mjs` assert the markup a given reply produces instead of merely
 * asserting that some markup came out.
 */
export function markupFloor(text) {
  const s = String(text || "");
  if (!s) return "";
  const rule = FLOOR.find((r) => r.re.test(s)) || FLOOR_DEFAULT;
  const hit = ICONS.find((r) => r.re.test(s));
  let mk = MK.mood(rule.mood) + MK.gesture(rule.gesture);
  if (hit) mk += MK.icons(hit.icon, 0);
  mk += s;
  if (hit) mk += MK.icons(hit.icon, 2);
  return mk;
}
