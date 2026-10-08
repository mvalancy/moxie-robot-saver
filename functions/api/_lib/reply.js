/* functions/api/_lib/reply.js — reading a chat completion, and the re-roll decision.
 *
 * Everything here is pure. The envelope is unwrapped and the diagram split off at the
 * gateway boundary, so nothing downstream (safety sweep, TTS ticket, transcript, response
 * body) ever sees JSON braces or mermaid syntax — a child must never be read either aloud.
 */

/** The OpenAI chat-completions reply text, defensively. Trimmed, NOT flattened: a mermaid
 *  diagram is newline-delimited, so flattening happens once, in `splitDiagram`. */
export function completionText(json) {
  const choice = json && Array.isArray(json.choices) ? json.choices[0] : null;
  const msg = choice && choice.message;
  const raw = msg && typeof msg.content === "string" ? msg.content : "";
  return raw.trim();
}

/**
 * Every balanced `{…}` at the top level of `body`, string-aware, plus an unterminated one
 * (`open`) when the completion was cut off by `max_tokens` inside the envelope. Models
 * split the envelope (`{"say":…} {"mood":…}`), wrap it in prose ("Sure! {…}", "{…} Hope
 * that helps!"), or break it (`&` for `,`, an unescaped quote, single quotes) — all shapes
 * served live or offline — so the objects are collected here and READ tolerantly below.
 */
function scanObjects(body) {
  const objects = [];
  let depth = 0, start = -1, inStr = false, esc = false;
  for (let i = 0; i < body.length; i++) {
    const c = body[i];
    if (inStr) {
      if (esc) esc = false;
      else if (c === "\\") esc = true;
      else if (c === '"') inStr = false;
    } else if (depth === 0 && body.slice(i, i + 10).toLowerCase() === "```mermaid") {
      // A fenced diagram's braces are drawing (`B{Cold?}` is a decision node), not an
      // envelope: skip to its closing fence. An unclosed fence runs to the end.
      const end = body.indexOf("```", i + 10);
      i = end < 0 ? body.length : end + 2;
    } else if (c === '"' && depth > 0) {
      inStr = true;
    } else if (c === "{") {
      if (depth++ === 0) start = i;
    } else if (c === "}" && depth > 0) {
      if (--depth === 0) { objects.push(body.slice(start, i + 1)); start = -1; }
    }
  }
  return { objects, open: start >= 0 ? body.slice(start) : "" };
}

/** The pieces of `text` around and including its ```mermaid fences: prose at the even
 *  indexes, a fence at every odd one. Diagrams are set aside this way wherever braces are
 *  read or removed, because inside a fence a brace is syntax and a newline is a line. */
const FENCE_SPLIT = /(```mermaid[\s\S]*?```)/i;
const proseAndFences = (text) => String(text).split(FENCE_SPLIT);

/** `text` with every brace outside a fence removed. Not flattened. */
function braceless(text) {
  return proseAndFences(text).map((part, i) => (i % 2 ? part : part.replace(/[{}]/g, ""))).join("");
}

/** A JSON string's escapes undone; the raw text when it was not valid JSON after all. */
function unescapeJson(raw) {
  try { return JSON.parse('"' + raw.replace(/(?<!\\)"/g, '\\"') + '"'); } catch { return raw; }
}

/**
 * One string field read out of BROKEN JSON. The value runs to the quote that is followed by
 * a comma, a closing brace, a stray `&`, a colon (a dropped key: `"say": "Bye!":"wave"` was
 * served live) or the end — so an unescaped quote INSIDE `say` ("My friend said "hi" to
 * me!") survives. Single quotes are accepted as the delimiter.
 */
function fieldOf(text, name) {
  const re = new RegExp("[\"']" + name + "[\"']\\s*:\\s*([\"'])([\\s\\S]*?)\\1\\s*(?=,|\\}|&|:|$)");
  const m = re.exec(text);
  return m ? unescapeJson(m[2]) : undefined;
}

/** `say` from an envelope the model never finished: whatever of the line arrived. */
function openSayOf(text) {
  const m = /["']say["']\s*:\s*"([^"]*)$/.exec(text);
  return m ? unescapeJson(m[1]) : undefined;
}

/** The fields of one object: parsed as JSON, or read by regex when the JSON is broken. */
function readEnvelope(text, unfinished) {
  try {
    const one = JSON.parse(text);
    if (one && typeof one === "object" && !Array.isArray(one)) return one;
  } catch { /* broken JSON: read the fields instead */ }
  const out = {};
  for (const k of ["say", "mood", "gesture", "diagram"]) {
    const v = fieldOf(text, k);
    if (v !== undefined) out[k] = v;
  }
  if (unfinished && out.say === undefined) {
    const partial = openSayOf(text);
    if (partial !== undefined) out.say = partial;
  }
  return out;
}

/**
 * Read the expressive envelope `{say, mood, gesture, diagram}` out of a reply, or decide
 * there isn't one.
 *
 * NEVER THROWS, NEVER LOSES THE REPLY, AND NEVER HANDS A BRACE TO THE VOICE. A reply with no
 * `{` in it is prose and is returned as is. One with an envelope anywhere in it — alone,
 * split in two, fenced, wrapped in prose, after a `<think>` block, cut off by `max_tokens`,
 * or with its JSON broken — yields its `say` (the prose around it is the model talking to
 * itself, not to the child). An object with no `say` in it is not an answer: what is spoken
 * is whatever prose surrounds it, with every brace removed; an empty result is the route's
 * `upstream_down`, like any empty completion. Measured offline before this: 9 of 11 served
 * shapes reached the TTS ticket with the braces in them.
 *
 * The brace claim is made true by a LAST PASS over `say` itself, not by the shapes above
 * being exhaustive: a `say` that is itself an envelope (a model quoting its own format) is
 * unwrapped once, and any brace still standing is dropped. The one place a brace survives
 * is inside a ```mermaid fence, where it is a decision node and `splitDiagram` takes the
 * whole fence out of the spoken text before anything is said.
 *
 * @returns {{text: string, chosen: {mood?: string, gesture?: string}|null, diagram: string}}
 */
export function parseExpressive(raw) {
  const line = String(raw || "").trim();
  const plain = { text: line, chosen: null, diagram: "" };
  if (!line) return plain;

  // A reasoning model's <think> block is never spoken; an unclosed one runs to the end, and
  // a block the chat template opened arrives with only its closing tag — everything before
  // that tag is the model talking to itself.
  let body = line.replace(/<think>[\s\S]*?<\/think>/gi, "").replace(/<think>[\s\S]*$/i, "");
  body = body.replace(/^[\s\S]*<\/think>/i, "").trim();
  // A ```json (or bare) fence around the envelope goes; a ```mermaid fence is a diagram and stays.
  body = body.replace(/```(?:json)?\s*(\{[\s\S]*?\})\s*```/gi, "$1").trim();
  if (!body.includes("{")) return body === line ? plain : { text: body, chosen: null, diagram: "" };

  const { objects, open } = scanObjects(body);
  const merged = {};
  for (const o of objects) Object.assign(merged, readEnvelope(o, false));
  if (open) Object.assign(merged, readEnvelope(open, true));

  // Not flattened: `say` may carry a fenced diagram that `splitDiagram` removes first.
  let say = merged.say;
  if (Array.isArray(say)) say = say.filter((s) => typeof s === "string").join(" ");
  say = typeof say === "string" ? say.trim() : "";
  if (!say) {
    let prose = body;
    for (const o of objects) prose = prose.replace(o, " ");
    if (open) prose = prose.replace(open, " ");
    // The words lose their braces and are flattened; a diagram among them keeps both.
    const text = proseAndFences(prose)
      .map((part, i) => (i % 2 ? part : part.replace(/[{}]/g, "").replace(/\s+/g, " ")))
      .join("").trim();
    return { text, chosen: null, diagram: "" };
  }
  if (say.includes("{")) {
    // The last pass. One level of nesting is read (the inner fields fill gaps, never
    // overrule); whatever brace is left outside a fence is not for a child to hear.
    const inner = scanObjects(say);
    const nested = {};
    for (const o of inner.objects) Object.assign(nested, readEnvelope(o, false));
    if (inner.open) Object.assign(nested, readEnvelope(inner.open, true));
    if (typeof nested.say === "string" && nested.say.trim()) {
      say = nested.say.trim();
      for (const k of ["mood", "gesture", "diagram"]) if (merged[k] === undefined && nested[k] !== undefined) merged[k] = nested[k];
    }
    say = braceless(say).trim();
    if (!say) return { text: "", chosen: null, diagram: "" };
  }

  const chosen = {};
  if (typeof merged.mood === "string") chosen.mood = merged.mood;
  if (typeof merged.gesture === "string") chosen.gesture = merged.gesture;
  // A sibling `diagram` field (one level of escaping) is what models actually emit; the
  // ```mermaid fence in `splitDiagram` stays as the fallback for prose replies.
  const field = typeof merged.diagram === "string" ? merged.diagram.trim() : "";
  return {
    text: say,
    chosen: Object.keys(chosen).length ? chosen : null,
    diagram: field,
  };
}

/**
 * Pull a ```mermaid diagram out of a reply and give back the words WITHOUT it.
 *
 * The stripping is the point: a fence left in `say` would be synthesised verbatim, charged
 * for, and written into the transcript. Bare ``` is not a diagram (a model fencing a word
 * for emphasis must not lose its sentence). The body is returned unvalidated — mermaid's
 * parser lives in the browser (`sim/web/diagram.js`) — and size-capped.
 */
export const MAX_DIAGRAM_CHARS = 1200;
const MERMAID_FENCE = /```mermaid\s*([\s\S]*?)```/i;

export function splitDiagram(text) {
  const s = String(text || "");
  // The one place prose is flattened, after the diagram is out.
  const flat = (x) => String(x).replace(/\s+/g, " ").trim();
  const m = MERMAID_FENCE.exec(s);
  if (!m) return { spoken: flat(s), diagram: "" };
  const body = String(m[1] || "").trim();
  const spoken = flat(s.slice(0, m.index) + " " + s.slice(m.index + m[0].length));
  if (!body || body.length > MAX_DIAGRAM_CHARS) return { spoken, diagram: "" };
  return { spoken, diagram: body };
}

/**
 * The earlier assistant line `reply` repeats, or "" when it repeats nothing.
 *
 * EXACT (case-, whitespace- and punctuation-insensitive), NOT NEAR. A similarity threshold
 * is a number nobody can defend, and a measured lexical-overlap score already moved the
 * right way while the conversation still read as a loop — spending money on that signal
 * would be wrong. Punctuation is folded because "That's okay." and "That's okay!" were
 * served live as two turns of one conversation, and a child hears the same line twice.
 * Compared against EVERY assistant turn in the signed history ("A, B, A" is the same loop),
 * which `hmac.js` already bounds.
 */
export function echoOf(reply, turns) {
  const norm = (v) => String(v == null ? "" : v).toLowerCase().replace(/[^\p{L}\p{N}]+/gu, " ").trim();
  const key = norm(reply);
  if (!key) return "";
  for (const t of turns || []) {
    if (!t || t.role !== "assistant") continue;
    if (norm(t.content) === key) return String(t.content);
  }
  return "";
}

/**
 * How many ms a re-roll may use, or 0 for "not now".
 *
 * The bound is the route's own `DEMO_CHAT_TIMEOUT_MS`, not a new number: the second call
 * gets what is LEFT of it, and only if that is at least what the first call took. So a
 * re-rolled turn never outlasts the promised timeout, and a gateway that is already slow
 * gets its duplicate served now rather than a fresh answer that may never arrive.
 */
export function rerollBudgetMs(cfg, elapsedMs) {
  const spent = Number.isFinite(elapsedMs) && elapsedMs > 0 ? elapsedMs : 0;
  const left = (cfg.chatTimeoutMs || 0) - spent;
  return left >= spent ? left : 0;
}
