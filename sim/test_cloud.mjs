/* Cloud-console fixture + wiring test. The example parent console (cloud.html) is
 * a STATIC surface, but its data must stay faithful to the real Moxie contract so
 * the demo teaches the true shape and a live server can drop in unchanged:
 *   - JSON:API documents (server/moxie_server/serializers.py)
 *   - the MQTT content model: module_id/content_id, MentorBehavior, MissionConfig
 *     (docs/reverse-engineering/content-and-conversation.md)
 * This asserts the fixture carries those shapes and cloud.html actually consumes
 * them. Run: node sim/test_cloud.mjs
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const web = join(here, "web");
const fails = [];
const ok = (c, m) => { if (!c) fails.push(m); };

// ---- fixture is valid + faithfully shaped -----------------------------------
let d;
try { d = JSON.parse(readFileSync(join(web, "fixtures", "cloud.json"), "utf8")); }
catch (e) { console.log("❌ cloud tests FAILED:\n   - fixtures/cloud.json invalid: " + e.message); process.exit(1); }

// JSON:API users document with included children + robots (serializers.py shape)
const doc = d.user;
ok(doc && doc.data && doc.data.type === "users", "user.data must be a JSON:API users resource");
ok(Array.isArray(doc.included), "user.included must be an array (JSON:API)");
const inc = (t) => doc.included.filter((r) => r.type === t);
ok(inc("children").length >= 1, "fixture needs an included child resource");
ok(inc("robots").length >= 1, "fixture needs an included robot resource");
ok(inc("robot-setting").length >= 1, "fixture needs an included robot-setting resource");
const child = inc("children")[0].attributes, robot = inc("robots")[0].attributes;
for (const f of ["first-name", "pronoun", "content-day"])
  ok(f in child, `child attributes missing real field '${f}'`);
for (const f of ["software-version", "online", "battery-level", "endpoint"])
  ok(f in robot, `robot attributes missing real field '${f}'`);
ok(robot.endpoint === "OPEN_MOXIE" || robot.endpoint === "EMBODIED_LOCAL",
   "robot endpoint should be a self-hostable firmware enum");
ok(robot["software-version"] === "24.10.803", "fixture robot should be on the analyzed firmware");

// rewards: {badges, missions, rewards-choices} (child_rewards handler shape)
const rew = d.rewards && d.rewards.data;
ok(rew && Array.isArray(rew.badges) && Array.isArray(rew.missions) && Array.isArray(rew["rewards-choices"]),
   "rewards.data must have badges/missions/rewards-choices");
// missions carry the real content-module identity
for (const m of rew.missions || []) {
  ok("mission_id" in m, "mission missing mission_id (MissionConfig)");
  ok(m.module_id && m.content_id, "mission missing module_id/content_id");
  ok(["completed", "in_progress", "locked"].includes(m.status), `bad mission status ${m.status}`);
}

// MentorBehavior activity (content-and-conversation.md)
const mb = d.activity && d.activity.mentor_behaviors;
ok(Array.isArray(mb) && mb.length > 0, "activity.mentor_behaviors must be a non-empty array");
for (const a of mb || [])
  ok(a.module_id && "content_day" in a && a.action && a.ended_reason,
     "MentorBehavior needs module_id/content_day/action/ended_reason");

// conversation turns carry mood+gesture on Moxie turns (behavior markup source)
const turns = (d.conversation && d.conversation.turns) || [];
ok(turns.length > 0, "conversation.turns must be non-empty");
ok(turns.some((t) => t.speaker === "moxie" && t.mood && t.gesture),
   "at least one Moxie turn must carry mood + gesture");

// notifications JSON:API + unread meta (notifications handler shape)
ok(d.notifications && Array.isArray(d.notifications.data) &&
   d.notifications.meta && typeof d.notifications.meta.unread === "number",
   "notifications must be {data:[], meta:{unread:N}}");

// ---- cloud.html actually consumes the fixture -------------------------------
const html = readFileSync(join(web, "cloud.html"), "utf8");
ok(html.includes('fetch("fixtures/cloud.json")'), "cloud.html must fetch fixtures/cloud.json");
for (const panel of ["overview", "missions", "conversations", "robot", "notifications"])
  ok(html.includes(`data-panel="${panel}"`), `cloud.html missing #${panel} panel`);
ok(html.includes("mentor_behaviors") || html.includes("mentor_behavior"),
   "cloud.html should render the MentorBehavior activity log");
ok(html.includes("setup.html") && html.includes('href="./"'),
   "cloud.html should cross-link the setup page and simulator");

// ---- report -----------------------------------------------------------------
if (fails.length) {
  console.log("❌ cloud tests FAILED:");
  for (const f of fails) console.log("   -", f);
  process.exit(1);
}
console.log("✅ cloud tests OK — fixture matches the real JSON:API + MQTT content shapes, cloud.html wired");
