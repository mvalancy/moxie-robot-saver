/* §1–2b: the clip manifest is whole, every recorded session line (BOTH speakers) is
 * parsed, and the script leaves the child room to speak and to finish.
 */
import {
  FLOORS, audioDir, eq, existsSync, join, manifest, notes, ok, readFileSync, readdirSync,
  sessionsDir, statSync,
} from "./harness.mjs";

/* --------------------------------------------------------------------------- *
 * 1. The manifest
 * --------------------------------------------------------------------------- */
ok(manifest && typeof manifest === "object", "audio/index.json must be an object");

/* Every group PRESENT and NON-EMPTY: a renderer run that drops a group orphans every clip in
 * it with no error. §9 guards the tool; this guards the artefact that ships. */
for (const group of ["moxie", "child", "ambient"]) {
  ok(manifest[group] && typeof manifest[group] === "object",
     `audio/index.json must have a ${group} group — a run of prerender_audio.py that drops one orphans every clip in it`);
  const n = Object.keys(manifest[group] || {}).length;
  ok(n >= FLOORS[group],
     `audio/index.json's ${group} group has ${n} entries, floor is ${FLOORS[group]} — clips were removed, or a manifest write dropped them`);
}

/** Every clip the manifest claims is on disk and non-empty (a dangling entry costs the visitor
 *  a 404's latency before any fallback). */
export let clipCount = 0;
export let clipBytes = 0;
for (const [group, entries] of Object.entries(manifest)) {
  if (!entries || typeof entries !== "object") continue;
  for (const [phrase, rel] of Object.entries(entries)) {
    clipCount++;
    const path = join(audioDir, rel);
    ok(typeof rel === "string" && rel.length > 0, `${group}: empty path for ${JSON.stringify(phrase.slice(0, 40))}`);
    ok(existsSync(path), `${group}: clip file missing on disk: ${rel} (for ${JSON.stringify(phrase.slice(0, 40))})`);
    if (existsSync(path)) {
      const size = statSync(path).size;
      clipBytes += size;
      // 2 KB: far under the shortest real clip (~14 KB), far over a truncated write.
      ok(size > 2048, `${group}: clip file is implausibly small (${size} B) — truncated or silent: ${rel}`);
    }
    // The key is the EXACT string `voice/speak` looks up; surrounding space orphans it.
    eq(phrase, phrase.trim(), `${group}: a manifest key has surrounding whitespace: ${JSON.stringify(phrase)}`);
  }
}

/* --------------------------------------------------------------------------- *
 * 2. Every line in every recorded session has a clip — BOTH speakers
 *
 * Child lines are inventoried too, under a STRICTER rule (`strict` in §5).
 * --------------------------------------------------------------------------- */
const sessionFiles = readdirSync(sessionsDir).filter((f) => f.endsWith(".json"));
ok(sessionFiles.length > 0, "there must be at least one recorded session");

/* A child utterance rides `/events/remote-chat`, but a perception event (same `speech` slot)
 * and a `notify` echo are not speech; the handler skips both, and so does this. */
const PERCEPTION = /^(eb-)?(found|lost)[-_]?(face|target|person)?$/i;
const childSpeech = (msg) => {
  if (!msg || msg.command === "notify") return "";
  let speech = msg.speech || "";
  for (const ln of msg.extra_lines || [])
    if (ln.context_type === "input" && ln.text) speech = ln.text;
  speech = String(speech).trim();
  return PERCEPTION.test(speech) ? "" : speech;
};

export const sessionLines = [];
export const childSessionLines = [];
export const sessions = [];
for (const file of sessionFiles) {
  const events = JSON.parse(readFileSync(join(sessionsDir, file), "utf8"));
  ok(Array.isArray(events), `${file} must be an array of {t, topic, payload} events`);
  if (!Array.isArray(events)) continue;
  sessions.push({ file, events });

  for (const [i, ev] of events.entries()) {
    ok(ev && typeof ev === "object", `${file}[${i}] must be an object`);
    ok(typeof ev.topic === "string" && ev.topic.length > 0, `${file}[${i}] must carry a topic`);
    // `route()` JSON.parses `payload` itself, so a non-string payload renders nothing.
    eq(typeof ev.payload, "string", `${file}[${i}] payload must be a STRING (route() parses it)`);
    ok(Number.isFinite(Number(ev.t)), `${file}[${i}] must carry a numeric t`);

    if (ev.topic.endsWith("/events/remote-chat")) {
      let msg = null;
      try { msg = JSON.parse(ev.payload); } catch {}
      ok(msg !== null, `${file}[${i}] remote-chat payload must be valid JSON`);
      const text = childSpeech(msg);
      if (text) childSessionLines.push({ text, where: `${file}[${i}]`, t: Number(ev.t), i });
      continue;
    }

    if (!ev.topic.endsWith("/commands/remote_chat")) continue;
    let msg = null;
    try { msg = JSON.parse(ev.payload); } catch {}
    ok(msg !== null, `${file}[${i}] remote_chat payload must be valid JSON`);
    const text = ((msg && msg.output && msg.output.text) || "").trim();
    if (text) sessionLines.push({ text, where: `${file}[${i}]` });
  }
}
ok(sessionLines.length > 0, "the sessions must contain at least one spoken Moxie line");
ok(childSessionLines.length > 0,
   "the sessions must contain at least one CHILD line — a demo conversation with only one " +
   "voice in it is the thing this section exists to stop shipping again");

/* --------------------------------------------------------------------------- *
 * 2b. The script leaves the child room to SPEAK, and room to FINISH
 *
 * Two failure modes (`sim/tests/test_sil_child_voice.py` sees both in a real Chromium):
 *   · CUT — Moxie's turn lands while the child's clip plays; `speak()` calls stop().
 *   · DROPPED — the child's turn lands while MOXIE plays; `speakClipOnly` refuses to start
 *     over the robot, so the line is silent while its transcript row still appears.
 * SPEECH_MARGIN_MS covers the clip's load latency (fetch + decode, ~900 ms measured) that
 * `stop()` does not pay. Duration is estimated from file size (mono ~64 kbit MP3; /8000
 * rounds UP, the safe direction).
 * --------------------------------------------------------------------------- */
const MP3_BYTES_PER_SEC = 8000;
//: Load latency + a conversational beat (~4x the deficit measured in a real browser).
const SPEECH_MARGIN_MS = 1000;
// Only these topics make MOXIE speak, and only speaking calls stop().
const MOXIE_SPEAKS = ["/commands/remote_chat", "/commands/tts", "/commands/telehealth"];
//: How long a clip takes, estimated from its file size — "" when the line is silent.
const clipMs = (group, text) => {
  const rel = (manifest[group] || {})[text];
  if (!rel || !existsSync(join(audioDir, rel))) return 0;
  return (statSync(join(audioDir, rel)).size / MP3_BYTES_PER_SEC) * 1000;
};
//: The text MOXIE speaks for one event, or "" when the event is not her speaking.
const moxieSpeech = (ev) => {
  if (!MOXIE_SPEAKS.some((t) => String(ev.topic).endsWith(t))) return "";
  try { return ((JSON.parse(ev.payload).output || {}).text || "").trim(); } catch { return ""; }
};
{
  let cutChecks = 0, dropChecks = 0;
  for (const { file, events } of sessions) {
    for (const ln of childSessionLines.filter((c) => c.where.startsWith(file + "["))) {
      const needMs = clipMs("child", ln.text);
      if (!needMs) continue;                  // silent line: nothing to cut, nothing to drop

      // ── CUT: the next thing that makes Moxie speak must not land before she is done.
      const next = events.find((e, j) => j > ln.i && Number(e.t) > ln.t &&
                                         MOXIE_SPEAKS.some((t) => String(e.topic).endsWith(t)));
      if (next) {
        const gapMs = Number(next.t) - ln.t;
        cutChecks++;
        ok(gapMs >= needMs + SPEECH_MARGIN_MS,
           `${ln.where}: Moxie speaks ${Math.round(gapMs)} ms after the child starts, but the child's ` +
           `clip needs about ${Math.round(needMs)} ms plus ${SPEECH_MARGIN_MS} ms of load margin — ` +
           `speak() calls stop(), so the shipped demo would cut her off mid-word on ` +
           `${JSON.stringify(ln.text.slice(0, 40))}. Move the reply later.`);
      }

      // ── DROPPED: the LAST thing Moxie said before this line must have finished.
      let prev = null;
      for (const [j, e] of events.entries())
        if (j < ln.i && Number(e.t) <= ln.t && moxieSpeech(e)) prev = e;
      if (prev) {
        const heldMs = clipMs("moxie", moxieSpeech(prev));
        if (heldMs) {
          const gapMs = ln.t - Number(prev.t);
          dropChecks++;
          ok(gapMs >= heldMs + SPEECH_MARGIN_MS,
             `${ln.where}: the child speaks ${Math.round(gapMs)} ms after Moxie starts, but Moxie's ` +
             `clip runs about ${Math.round(heldMs)} ms — speakClipOnly REFUSES to play over the ` +
             `robot, so ${JSON.stringify(ln.text.slice(0, 40))} would make no sound at all while the ` +
             `transcript still showed it. Move the child's line later.`);
        }
      }
    }
  }
  ok(cutChecks > 0, "no scripted child line was timing-checked — the extractor above stopped finding them");
  notes.push(`session timing: ${cutChecks} child line(s) have room to finish before Moxie answers, ` +
             `${dropChecks} have room to be heard after she stops (${SPEECH_MARGIN_MS} ms margin)`);
}

notes.push(`manifest: ${clipCount} clips, ${(clipBytes / 1024 / 1024).toFixed(2)} MiB on disk ` +
           `(${Object.keys(manifest.moxie || {}).length} moxie / ${Object.keys(manifest.child || {}).length} child / ` +
           `${Object.keys(manifest.ambient || {}).length} ambient)`);
