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
 * Read the expressive envelope `{say, mood, gesture, diagram}` out of a reply, or decide
 * there isn't one.
 *
 * NEVER THROWS AND NEVER LOSES THE REPLY: not JSON, an array, no `say`, an empty `say` —
 * all fall back to the raw line with `chosen: null`. A ```json fence is stripped first
 * because models add one even when told not to.
 *
 * @returns {{text: string, chosen: {mood?: string, gesture?: string}|null, diagram: string}}
 */
export function parseExpressive(raw) {
  const line = String(raw || "").trim();
  const plain = { text: line, chosen: null, diagram: "" };
  if (!line) return plain;

  let body = line;
  const fenced = /^```(?:json)?\s*([\s\S]*?)\s*```$/i.exec(body);
  if (fenced) body = fenced[1].trim();
  if (body.charAt(0) !== "{") return plain;

  let obj;
  try { obj = JSON.parse(body); } catch { return plain; }
  if (!obj || typeof obj !== "object" || Array.isArray(obj)) return plain;

  // Not flattened: `say` may carry a fenced diagram that `splitDiagram` removes first.
  const say = typeof obj.say === "string" ? obj.say.trim() : "";
  if (!say) return plain; // an envelope with no line in it is not an answer

  const chosen = {};
  if (typeof obj.mood === "string") chosen.mood = obj.mood;
  if (typeof obj.gesture === "string") chosen.gesture = obj.gesture;
  // A sibling `diagram` field (one level of escaping) is what models actually emit; the
  // ```mermaid fence in `splitDiagram` stays as the fallback for prose replies.
  const field = typeof obj.diagram === "string" ? obj.diagram.trim() : "";
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
 * EXACT (case- and whitespace-insensitive), NOT NEAR. A similarity threshold is a number
 * nobody can defend, and a measured lexical-overlap score already moved the right way while
 * the conversation still read as a loop — spending money on that signal would be wrong.
 * Compared against EVERY assistant turn in the signed history ("A, B, A" is the same loop),
 * which `hmac.js` already bounds.
 */
export function echoOf(reply, turns) {
  const norm = (v) => String(v == null ? "" : v).replace(/\s+/g, " ").trim().toLowerCase();
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
