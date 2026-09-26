/* bridge/activity.js — robot → cloud: the activity log.
 *
 * `/devices/{id}/events/client-service-activity-log`, multiplexed by `subtopic`
 * (mqtt-and-conversation.md §3.3):
 *   subtopic:"query"        pull the day plan / history / a license key (start of every
 *                           session); answered by a CloudQueryResponse on commands/query_result
 *   (no subtopic)           a `mentor_behavior` REPORT — what the child just finished
 *   subtopic:"telehealth"   a `TelehealthRobotEvent`: the robot's own session state
 * Same topic, envelopes, subtopics and key order as `sim/virtual_moxie.py`; only identity
 * fields differ. Parity is pinned by `sim/tests/goldens/robot_to_cloud_activity.json`.
 */
(function () {
  "use strict";
  const B = window.__moxieBridge;
  const dev = B.dev, status = (t) => B.status(t);
  const DEVICE_ID = B.DEVICE_ID, FIRMWARE = B.FIRMWARE, MODULE_NAME = B.MODULE_NAME;
  const ACTIVITY_TOPIC = dev("events/client-service-activity-log");

  // The CloudQueryResponse field each answer is keyed under (Cloud.proto:310-352), the same
  // table as `virtual_moxie.py::QUERY_FIELD` — duplicated on purpose: a client decodes the
  // wire itself and never imports the SDK it exists to test.
  const QUERY_FIELD = {
    idf: "idf_values", license: "license_values", schedule: "schedule",
    contexts: "contexts", context_store: "versioned_contexts",
    mentor_behaviors: "mentor_behaviors", remote_lines: "remote_lines",
  };

  const activity = {
    published: [],          // every envelope this robot put upstream, in order (bounded)
    results: {},            // query name → {request_id, field, value}
    pending: {},            // request_id → query name, until the answer lands
    telehealth_state: "",   // what we last told the cloud we are doing
    last_query: "",
  };

  function publishActivity(envelope) {
    const s = JSON.stringify(envelope);
    activity.published.push(envelope);
    if (activity.published.length > 60) activity.published.shift();
    B.record(ACTIVITY_TOPIC, s);
    if (B.isLive()) B.client.publish(ACTIVITY_TOPIC, s);
    return envelope;
  }

  // A CloudQueryRequest (Cloud.proto:292-305).
  function sendQuery(query) {
    const requestId = "sim-q-" + Math.random().toString(36).slice(2, 10);
    activity.pending[requestId] = query;
    activity.last_query = query;
    publishActivity({ timestamp: Date.now(), subtopic: "query", query: query,
                      request_id: requestId, auid: DEVICE_ID,
                      software_version: FIRMWARE, module_name: MODULE_NAME });
    status(`→ activity-log query ${query}`);
    return requestId;
  }

  // An ActivityUpdate whose `mentor_behavior` (Cloud.proto:241) carries the finished
  // activity (MentorBehavior.proto:26-36) — the history that stops the robot repeating
  // the same missions forever.
  function reportMentorBehavior(mbh) {
    const rec = publishActivity({ timestamp: Date.now(), mentor_behavior: mbh || {},
                                  software_version: FIRMWARE, module_name: MODULE_NAME });
    status(`→ mentor_behavior ${(mbh || {}).module_id || "?"}`);
    return rec;
  }

  // A TelehealthRobotEvent (docs/reverse-engineering/protocol/telehealth.md:88-91).
  function reportTelehealthState(state, sessionId) {
    activity.telehealth_state = state;
    return publishActivity({ subtopic: "telehealth",
      message: { timestamp: Date.now(), state: state, session_id: sessionId || "",
                 action: "UPDATE_STATE", software_version: FIRMWARE,
                 module_name: MODULE_NAME } });
  }

  // The cloud's answer: a CloudQueryResponse keyed by the query's own proto field.
  B.handleQueryResult = function handleQueryResult(payload) {
    const msg = B.parse(payload); if (!msg) return;
    const query = msg.query || activity.pending[msg.request_id] || "";
    const field = QUERY_FIELD[query] || "";
    const value = field ? msg[field] : undefined;
    delete activity.pending[msg.request_id];
    activity.results[query] = { request_id: msg.request_id || "", field: field, value: value };
    const size = Array.isArray(value) ? value.length : (value == null ? "MISSING" : "ok");
    status(`← query_result ${query}: ${field}=${size}`);
  };

  B.sendQuery = sendQuery;
  B.reportTelehealthState = reportTelehealthState;
  Object.assign(B.api, {
    // Every envelope this client put upstream, in order, plus the answers that came back.
    activityStats: function () {
      return { topic: ACTIVITY_TOPIC, published: activity.published.slice(),
               results: JSON.parse(JSON.stringify(activity.results)),
               telehealth_state: activity.telehealth_state,
               last_query: activity.last_query };
    },
    sendQuery: sendQuery,
    reportMentorBehavior: reportMentorBehavior,
    reportTelehealthState: reportTelehealthState,
  });
})();
