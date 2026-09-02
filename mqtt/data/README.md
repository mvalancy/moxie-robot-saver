# 🗄️ `mqtt/data/` — the supervisor's durable per-robot store

Runtime state written by [`../moxie_sdk/store.py`](../moxie_sdk/store.py). **Nothing here
is source** — it is git-ignored (except this README) and safe to delete; the robot will
simply start over with an empty history.

## Layout

```
mqtt/data/
└── robots/
    └── d_<device-id>/
        └── mentor_behaviors.json     # what this child has already done
```

One JSON file per (robot, collection), written atomically (temp file + `os.replace`).

| Collection | Written by | Shape |
|---|---|---|
| `mentor_behaviors` | `MoxieRuntime.ingest_mentor_behavior` — a robot's `mentor_behavior` report on `client-service-activity-log` | a list of `embodied.robotbrain.MentorBehavior` records (`module_id`, `content_id`, `content_day`, `timestamp`, `action`, `instance_id`, `ended_reason`), newest kept, capped at 500 |

That history is what [`../moxie_sdk/schedule.py`](../moxie_sdk/schedule.py) reads so the
day plan skips activities the child already finished — and so first-time-user onboarding
ends instead of repeating forever.

## Configuration

`MOXIE_DATA_DIR` overrides the location (point it at a volume in the compose stack).
A missing directory is not an error: reads return empty, the first write creates it.

## Honest status

JSON files are a **stepping stone**, not a database — see the
[feature audit](../../docs/architecture/openmoxie-feature-audit.md) ADOPT #8 and the
"Known gaps" section of [`implementation-plan.md`](../../docs/architecture/implementation-plan.md).

---
📖 [mqtt/ overview](../README.md) · [SDK](../moxie_sdk/README.md)
