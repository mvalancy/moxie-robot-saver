# 🗂️ `sim/web/fixtures/` — demo data for the static cloud console

Canned JSON that lets the static site render the parent console with no server.

- [`cloud.json`](cloud.json) — data for [`../cloud.html`](../cloud.html), shaped like the real contract so a live
  server can drop in unchanged: JSON:API documents for `users` / `children` / `robots` / `robot-setting`
  (see [`serializers.py`](../../../server/moxie_server/serializers.py)) and the MQTT content model
  (`module_id` / `content_id`, `MentorBehavior`, `MissionConfig`, rewards; see
  [`content-and-conversation.md`](../../../docs/reverse-engineering/runtime/content-and-conversation.md)).
  Demo data, no real child. `node sim/test_cloud.mjs` pins the shapes.

A live deployment serves the same shapes from `/api/users/me`, `/api/children/{id}/rewards` and the
activity log; only the page's `fetch` target changes.

📖 [sim/web](../README.md) · [Back to top](../../../README.md)
