# `content_modules/` — authored Moxie activities

Data-driven content modules loaded by the [content engine](../moxie_sdk/content/)
(`ContentApp`). A module is JSON with `conversations[]` / `globals[]` / `schedules[]`
— see [the content-module contract](../../docs/architecture/content-module-contract.md).

- [`starter.json`](starter.json) — a friendly free-chat conversation (`FREE_CHAT`) +
  a timer global. Run it: `MOXIE_APP=content MOXIE_CONTENT_MODULE=content_modules/starter.json python run.py`.

Add an activity by dropping another `.json` here and pointing `MOXIE_CONTENT_MODULE` at it
(or a directory to merge — a future loader slice).
