# 🎬 `sim/web/sessions/` — recorded bus sessions

- [`demo.json`](demo.json) — the scripted demo: a list of `{t, topic, payload}` MQTT messages (t in ms)
  that the rail's Demo button (`rec-demo`) replays through [`../bridge/index.js`](../bridge/index.js) with
  no broker. Timed so Moxie never talks over the child (`sim/tests/test_sil_child_voice.py` guards it);
  its lines are pre-rendered into [`../audio/`](../audio/README.md).

The rail's record/save buttons export sessions in the same shape.

📖 [sim/web](../README.md) · [Back to top](../../../README.md)
