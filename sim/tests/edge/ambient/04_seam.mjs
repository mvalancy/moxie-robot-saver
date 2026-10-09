/* §4 THE SIGN-OFF SEAM, through the real bridge/: which replies say `moxie-signoff`, and the
 * aside at the end of a real goodbye turn, bridge and ambient.js in one page.
 *
 * The hosted goodbye is built with the functions `functions/api/chat.js` builds it with
 * (`markupFloor(reply, chosen, SIGN_OFF)`, `endTurn: closing`; demo_proxy §20 pins that chat.js
 * does so on a goodbye and only then). It carries NO `response_actions`: the bridge's
 * `exit_module` case never sees it, so the seam reads `end_turn` too. The robot path's
 * goodbye is an `exit_module` action and no `end_turn` (`mqtt/moxie_sdk/wire.py`).
 */
import { api } from "../common.mjs";
import { BEAT, MIN, ambientPage, eq, ok } from "./harness.mjs";

const wire = await api("_lib", "wire.js");
const asides = BEAT("signoff");
const GAP = 3500;
const CHAT = "/devices/d_sim/commands/remote_chat", USER = "/devices/d_sim/events/remote-chat";

/** A hosted `/api/chat` message: `wire.chatMessage` of `wire.buildChatResponse`, as chat.js. */
function hosted(text, { goodbye = false, chosen = null } = {}) {
  return wire.chatMessage("d_sim", wire.buildChatResponse({
    eventId: "sim-0123456789ab", text, endTurn: goodbye,
    markup: wire.markupFloor(text, chosen, goodbye ? wire.SIGN_OFF : ""),
  }));
}
/** A robot-path reply (wire.py's shape) carrying these `response_actions`. */
const robot = (text, actions) => ({ topic: CHAT, payload: JSON.stringify({
  command: "remote_chat", result: 0, event_id: "rb-1", output: { text, markup: text },
  response_actions: actions.map((a) => ({ output_type: "GLOBAL_RESPONSE", action: a })) }) });

/* 4a. Which replies sign off: the hosted goodbye and the robot's exit; nothing else, not even
 *     the same wave without end_turn. */
{
  const counts = {};
  await ambientPage({ bridge: true }, async (t) => {
    const signoffs = () => t.events.filter((e) => e.type === "moxie-signoff").length;
    for (const [label, msg] of [
      ["a hosted ordinary reply", hosted("I like penguins. They waddle!")],
      ["a hosted reply where the model chose `wave` (no end_turn)", hosted("Hi Sam!", { chosen: { mood: "happy", gesture: "wave" } })],
      ["the hosted goodbye (end_turn, the sign-off wave)", hosted("Bye Sam! See you tomorrow.", { goodbye: true })],
      ["the robot's exit_module", robot("Bye Sam!", ["exit_module"])],
      ["the robot's older `exit`", robot("Okay, all done.", ["exit"])],
      ["the robot's sleep", robot("Good night.", ["sleep"])],
      ["the robot's launch", robot("Let's draw!", ["launch"])],
    ]) {
      const n = signoffs();
      t.bridge().route(msg.topic, msg.payload);
      counts[label] = signoffs() - n;
    }
  });
  const hostedGoodbye = JSON.parse(hosted("Bye!", { goodbye: true }).payload);
  ok(hostedGoodbye.end_turn === true && !("response_actions" in hostedGoodbye) &&
     hostedGoodbye.output.markup.includes("+behaviour+:+" + wire.SIGN_OFF + "+"),
     "precondition: the hosted goodbye is end_turn + the sign-off wave, with no actions at all");
  for (const [label, want] of [
    ["a hosted ordinary reply", 0], ["a hosted reply where the model chose `wave` (no end_turn)", 0],
    ["the hosted goodbye (end_turn, the sign-off wave)", 1], ["the robot's exit_module", 1],
    ["the robot's older `exit`", 1], ["the robot's sleep", 0], ["the robot's launch", 0],
  ]) eq(counts[label], want, `${label}: ${want ? "one sign-off" : "no sign-off"}`);
}

/* 4b. The seam never throws: on a window with no dispatchEvent (as bridge_harness's node
 *     window has none) the exit is still recorded and nothing counts as a failed action. */
{
  let st = null;
  await ambientPage({ bridge: true }, async (t) => {
    delete globalThis.window.dispatchEvent;
    for (const msg of [robot("Bye Sam!", ["exit_module"]), hosted("Bye Sam!", { goodbye: true })])
      t.bridge().route(msg.topic, msg.payload);
    st = t.bridge().actionStats();
  });
  ok(st && st.exits === 1 && st.unknown === 0,
     `with no dispatchEvent the exit is still recorded and nothing fails (${JSON.stringify(st && { exits: st.exits, unknown: st.unknown })})`);
}

/* 4c. End to end: the visitor's goodbye and the hosted reply through the real bridge, which
 *     speaks it and waves, then the real ambient.js's aside a few seconds after. */
for (const [label, reply] of [
  ["hosted", hosted("Bye Sam! I hope tomorrow is full of pumpkins.", { goodbye: true })],
  ["robot", robot("Bye Sam! I hope tomorrow is full of pumpkins.", ["exit_module"])],
]) {
  const t = await ambientPage({ bridge: true }, async (t) => {
    await t.advance(2 * MIN);
    for (let i = 0; i < 200 && t.busy(GAP); i++) await t.advance(100);
    t.bridge().route(USER, JSON.stringify({ command: "prompt", backend: "router", speech: "okay bye moxie!" }));
    t.visitorLine();                                 // the observer would see the "turn user" row
    await t.advance(1500);
    t.R = t.now;
    t.bridge().route(reply.topic, reply.payload);
    t.reply();                                       // …and her "turn moxie" row
    await t.advance(MIN);
  });
  const spoken = t.said.find((s) => s.at >= t.R && s.text.startsWith("Bye Sam!"));
  const a = t.of(asides);
  ok(!!spoken, `[${label}] precondition: the bridge spoke her goodbye`);
  eq(a.length, 1, `[${label}] after a real goodbye turn she says exactly one aside`);
  const end = spoken ? spoken.at + 600 + 60 * spoken.text.length : Infinity;   // the stub voice's length
  ok(a.length === 1 && a[0].at >= end + GAP, `[${label}] …only once her goodbye has been said, and a beat more`);
}
