/* bridge/presence.js — the robot's own eyes.
 *
 * Vision runs on-device and sends only semantic events (vision.md §1.1), delivered as the
 * `speech` of an ordinary RemoteChatRequest, so the SIM emits them on the topic and
 * envelope a child's utterance uses. State lives in `presence` for tests to read.
 */
(function () {
  "use strict";
  const B = window.__moxieBridge;

  const FOUND = "eb-found-face", LOST = "eb-lost-target";
  const VISION_EVENTS = [FOUND, LOST, "eb-lost-face", "eb-qr-event", "eb-dr-event", "eb-br-event"];
  const presence = B.presence = {
    present: null,          // null = never told, true/false = told
    events: [],             // [{name, t}] bounded
    arrivals: 0, departures: 0,
    greetings: [],          // replies the server sent in answer to a face event
    lastEvent: "", lastEventId: "",
  };
  B.pendingFaceEvents = new Set();   // event_ids we are waiting on a reply for

  /* The badge reports what the ROBOT'S camera saw, so it stays HIDDEN until a face event
   * arrives: on the hosted site none ever does, and a permanent "PRESENCE UNKNOWN" reads
   * as broken. `data-presence` is written in every state (including `unknown`). The CSS
   * must not override `[hidden]` (css/hud.css); test_liveliness.mjs checks the rendered
   * result, not just the attribute. */
  B.presenceBadge = function presenceBadge() {
    const el = document.getElementById("presence-badge");
    const label = document.getElementById("presence-state");
    const state = presence.present === null ? "unknown" : (presence.present ? "here" : "away");
    if (el && el.setAttribute) el.setAttribute("data-presence", state);
    if (label) label.textContent = state.toUpperCase();
    if (el) el.hidden = (state === "unknown");
    const btn = document.getElementById("presence-toggle");
    if (btn) btn.textContent = presence.present ? "Walk away" : "Walk in";
    const st = document.getElementById("presence-status");
    if (st) st.textContent = presence.present === null ? "no face events yet"
      : `${presence.lastEvent} · ${presence.arrivals} in / ${presence.departures} out`;
  };

  /** A vision event in the `speech` slot? Record it and return true. */
  B.notePresence = function notePresence(name) {
    if (VISION_EVENTS.indexOf(name) < 0) return false;
    presence.events.push({ name: name, t: B.nowMs() });
    if (presence.events.length > 40) presence.events.shift();
    presence.lastEvent = name;
    if (name === FOUND) { presence.present = true; presence.arrivals++; }
    else if (name === LOST || name === "eb-lost-face") { presence.present = false; presence.departures++; }
    B.presenceBadge();
    B.status(`vision: ${name}`);
    return true;
  };

  /** The server answered a vision event: a hello has words, a NOREPLY_ACK has none. */
  B.noteFaceReply = function (eid, text) {
    if (!eid || !B.pendingFaceEvents.has(eid)) return;
    B.pendingFaceEvents.delete(eid);
    if (text) { presence.greetings.push({ text: text, event_id: eid, t: B.nowMs() });
                B.status(`greeting: "${text.slice(0, 40)}"`); }
  };

  // Publish one vision event the way the robot does, and remember its event_id so the
  // reply (a hello, or a silent NOREPLY_ACK) can be attributed to it. Returns the id.
  function faceEvent(kind) {
    const name = kind === "lost" ? LOST : (kind === "found" ? FOUND : kind);
    const eventId = "sim-face-" + Math.random().toString(36).slice(2, 10);
    const payload = JSON.stringify({
      event_id: eventId, command: "prompt", backend: "router",
      speech: name, module_name: "sim-web",
    });
    B.pendingFaceEvents.add(eventId);
    if (B.isLive()) B.client.publish(B.dev("events/remote-chat"), payload);
    B.route(B.dev("events/remote-chat"), payload);   // always record it locally
    presence.lastEventId = eventId;
    return eventId;
  }
  B.faceEvent = faceEvent;

  // "Someone walked in / walked away" — publish the recovered vision event.
  B.api.faceEvent = faceEvent;
  // Everything the page RECORDED about presence (tests read this, never a live sample).
  B.api.presenceStats = function () {
    return { present: presence.present, arrivals: presence.arrivals,
             departures: presence.departures, last_event: presence.lastEvent,
             events: presence.events.map((e) => e.name),
             greetings: presence.greetings.map((g) => g.text) };
  };
})();
