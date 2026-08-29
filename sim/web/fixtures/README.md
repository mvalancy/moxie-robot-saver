# `sim/web/fixtures/` — demo data for the static surfaces

Canned JSON that lets the static site render a full experience with **no server**.

- **`cloud.json`** — the parent cloud console's data ([`../cloud.html`](../cloud.html)). Its shapes
  deliberately mirror the **real Moxie contract** so the demo teaches the true structure and a live
  server can drop in unchanged:
  - the **JSON:API** documents the app's DataManager expects (`data`/`included`/`attributes` for
    `users` · `children` · `robots` · `robot-setting`) — see [`../../../server/moxie_server/serializers.py`](../../../server/moxie_server/serializers.py);
  - the **MQTT content model** — `module_id`/`content_id`, `MentorBehavior`, `MissionConfig` (Daily
    Missions), rewards/badges — see [`content-and-conversation.md`](../../../docs/reverse-engineering/content-and-conversation.md).

  It's demo data (no real child). `node sim/test_cloud.mjs` asserts the fixture keeps these shapes and
  that `cloud.html` consumes them.

> A live deployment would serve the same shapes from `/api/users/me`, `/api/children/{id}/rewards`, and
> the activity log instead of this file — the page's `fetch` target is the only thing that changes.
