/* bridge/actions.js — `response_actions`: the cloud drives navigation, not just speech.
 *
 * Each entry is `{output_type, action, module_id, content_id}` (+ `function_id` /
 * `function_args` / `action_args` on an `execute`); the FIRST may also/instead carry
 * `event_subscription:{active[], clear}`, so an action-less entry is legal. The legacy
 * singular `response_action` mirrors `[0]` and is read only when the plural is absent.
 * NOTHING here throws: an unknown action type is counted and skipped, so a newer server
 * cannot break an older client's turn.
 */
(function () {
  "use strict";
  const B = window.__moxieBridge;
  const status = (t) => B.status(t);

  const ACTION_KINDS = ["launch", "exit", "sleep", "enable_qr", "execute"];
  const actionState = B.actionState = {
    applied: [],            // [{action, module_id, content_id, function, args, t}] bounded
    unknown: 0,             // action types this client does not implement (skipped safely)
    module_id: "", content_id: "",   // the module the cloud last put us in
    launches: 0, exits: 0,
    asleep: false, qr_enabled: false,
    subscribed: [],         // event_subscription.active, as the brain last asked for it
    last: "",
  };

  // `action_args` (proto field 10, `repeated ActionArgsEntry{key, value}`) as `{key: value}`,
  // or `null` when absent/unreadable so the caller falls through to the next spelling.
  // Mirrors `virtual_moxie.py::VirtualMoxie._action_args` exactly (entries without `key`
  // dropped, missing `value` -> null), so both clients' args round-trip as one document.
  function actionArgs(entries) {
    if (!Array.isArray(entries)) return null;
    const out = {};
    let n = 0;
    for (const e of entries) {
      if (!e || typeof e !== "object" || Array.isArray(e)) continue;
      if (e.key === undefined || e.key === null) continue;
      out[String(e.key)] = e.value === undefined ? null : e.value;
      n += 1;
    }
    return n ? out : null;
  }

  function applyAction(entry) {
    const m = window.moxie;
    const kind = String(entry.action || "").toLowerCase();
    const moduleId = entry.module_id || "", contentId = entry.content_id || "";
    // An `execute`'s name and args: `function_id` (7), `function_args` (8, list) and
    // `action_args` (10, mapping), read in the SAME order as
    // `virtual_moxie.py::_apply_action` so the two clients never prefer different spellings.
    // Only `undefined`/`null` falls through — `[]` is a legitimate value, not absence.
    const fn = entry.function_id || entry.function || "";
    let args = entry.function_args;
    if (args === undefined || args === null) args = actionArgs(entry.action_args);
    if (args === undefined || args === null) args = entry.args;
    const recordedArgs = (args === undefined || args === null) ? [] : args;
    if (ACTION_KINDS.indexOf(kind) < 0) {
      actionState.unknown += 1;
      status(`action: ignored unknown ${JSON.stringify(entry.action)}`);
      return false;
    }
    switch (kind) {
      case "launch":
        // The module's badge goes up and Moxie greets it: the closest the avatar has to
        // "a module started".
        actionState.module_id = moduleId; actionState.content_id = contentId;
        actionState.asleep = false; actionState.launches += 1;
        if (m && m.showIcons && moduleId) m.showIcons([moduleId]);
        B.behaviourTree("Bht_Gesture_Greet");
        status(`action: launch ${moduleId}${contentId ? ":" + contentId : ""}`);
        break;
      case "exit":
        actionState.module_id = ""; actionState.content_id = ""; actionState.exits += 1;
        if (m && m.clearIcons) m.clearIcons();
        B.behaviourTree("Bht_Sign_off");
        status("action: exit");
        break;
      case "sleep":
        actionState.asleep = true;
        B.behaviourTree("Bht_Sleeping_Anim");
        status("action: sleep");
        break;
      case "enable_qr":
        // The launch-card path (docs/reverse-engineering/qr-codes.md). A browser SIM has no
        // scanner, so the camera badge is the only honest render.
        actionState.qr_enabled = true;
        if (m && m.showIcons) m.showIcons(["QR"]);
        if (m && m.setFace) m.setFace("curious");
        status("action: QR scanning on");
        break;
      case "execute":
        // A named on-robot function: RECORDED and shown, never guessed at — nothing is
        // called and no `execute_returns[]` is invented.
        status(`action: execute ${fn || "(unnamed)"}`);
        break;
      default: break;
    }
    actionState.last = kind;
    actionState.applied.push({ action: kind, module_id: moduleId, content_id: contentId,
                               function: fn, args: recordedArgs, t: B.nowMs() });
    if (actionState.applied.length > 40) actionState.applied.shift();
    return true;
  }

  function noteSubscription(sub) {
    if (!sub || typeof sub !== "object") return;
    if (sub.clear) actionState.subscribed = [];
    for (const name of sub.active || [])
      if (actionState.subscribed.indexOf(name) < 0) actionState.subscribed.push(name);
    status(`action: event subscription: ${actionState.subscribed.join(", ") || "(none)"}`);
  }

  B.handleActions = function handleActions(msg) {
    let list = [];
    if (Array.isArray(msg.response_actions)) list = msg.response_actions;
    else if (msg.response_action) list = [msg.response_action];   // legacy singular only
    for (const entry of list) {
      if (!entry || typeof entry !== "object") { actionState.unknown += 1; continue; }
      noteSubscription(entry.event_subscription);
      if (entry.action === undefined || entry.action === null || entry.action === "")
        continue;                     // an action-less entry carries the subscription only
      try { applyAction(entry); }
      catch (e) { actionState.unknown += 1; status(`action failed: ${e && e.message}`); }
    }
  };

  /* What `response_actions` actually DID, recorded as it happened. `applied` keys are
   * exactly `cloud_to_robot_actions.json`'s `applied_keys` (= `virtual_moxie.py::
   * action_stats()`). Tests assert this, never a live sample. */
  B.api.actionStats = function () {
    return { applied: actionState.applied.map((a) => ({ action: a.action,
               module_id: a.module_id, content_id: a.content_id, function: a.function,
               args: a.args })),
             unknown: actionState.unknown, module_id: actionState.module_id,
             content_id: actionState.content_id, launches: actionState.launches,
             exits: actionState.exits, asleep: actionState.asleep,
             qr_enabled: actionState.qr_enabled,
             subscribed: actionState.subscribed.slice(), last: actionState.last };
  };
})();
