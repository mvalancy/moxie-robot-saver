# sim/scenarios — scripted SIL conversations

Each file is a JSON `{ "name", "turns": [ {"say": str, "expect_contains"?: str}, ... ] }`.
The virtual robot sends each `say` as a `remote-chat` prompt and asserts a non-empty
reply; `expect_contains` (optional, case-insensitive) checks the reply text.

Run one against a live broker + supervisor:
```sh
python3 sim/virtual_moxie.py --scenario sim/scenarios/basic.json --port 1883
```
`expect_contains` assumes the **echo** MoxieApp (reply mirrors the input). For the LLM
app the reply is model-generated, so omit `expect_contains` and rely on the non-empty check.

`sim/run_scenarios.sh` boots a broker + echo supervisor and runs every scenario here. Add
`--notify` (or `--notify chunk`) and the robot reports what it said after each answer, as a real
Moxie does ([mqtt-and-conversation.md §4.2](../../docs/architecture/mqtt-and-conversation.md)).

Bridge markup→avatar logic is unit-tested (no browser) by `node sim/test_bridge.mjs`.
