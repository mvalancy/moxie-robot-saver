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

`sim/run_scenarios.sh` boots a broker + echo supervisor and runs every scenario here.
