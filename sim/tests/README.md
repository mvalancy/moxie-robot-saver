# SIL + static-site pytest automation

A comprehensive [Playwright](https://playwright.dev/python/) suite that drives the
whole static site in a real browser — every page at every standard resolution, and
the simulator through **all of its modes and controls**:

- **All pages × all resolutions** (`phone-portrait` → `ultrawide`): load clean, no
  console errors, no horizontal page scroll.
- **SIL**: click every expression chip (the 11 Eyeseme moods + sleep/thinking/blink);
  drag every motor slider; toggle **ALIVE**/liveness, axes, sound, heart-LED; run the
  speech path (pre-cached chip + free text); center-pose.
- **ALIVE loop**: on by default, stops on toggle-off, mirrors the panel checkbox, and
  a user-held joint is left alone.
- **Docs explorer**: full-text search filters, opening a hit, Mermaid renders.

## The non-browser tests in here

The directory has grown a second, larger family: plain pytest files that need no
browser at all and carry the hermetic suite CI actually runs.

```bash
python3 -m venv .venv && .venv/bin/pip install -q -r sim/tests/requirements.txt
.venv/bin/python -m pytest sim/tests -q -k "not test_sil and not test_docs" \
  --ignore=sim/tests/test_live_gateway.py      # the hermetic suite
```

## The two requirements files — and why there are exactly two

**`requirements-hermetic.txt` is the ONE declaration** of what the suite needs, and every CI
job that runs `pytest sim/tests` installs that exact file. **`requirements.txt` is that file
plus `playwright`**, and nothing else: it is what `run.sh`, an agent's venv and the fast
tier's whole-suite step use. The two `-k "not test_sil and not test_docs"` runs deselect
every browser-driving test, so they should not pay for a ~35 MB wheel — that single
difference is the only reason the split exists, and `test_ci_workflows.py` fails if the
files ever differ by anything else.

Neither file names `paho-mqtt`, `openai`, `jinja2`, `fastapi`, `pynacl` or `segno` itself:
it pulls in `mqtt/requirements.txt` and `server/requirements.txt`, because the suite imports
the supervisor and the console, so what they need it needs — and each list stays owned by
the thing that actually runs it. Never hand-write a package list in a workflow step or an
agent brief again; four separate defects came out of doing that, all in the same shape
(a missing package makes the tests that need it `importorskip` themselves away, which is a
skip that reads as a pass). Read either file's header for the whole post-mortem.

- **`helpers_runtime.py`** — the shared harness for anything that drives a turn
  through the real `MoxieRuntime`: a `FakeClient` that records publishes,
  `make_runtime` / `drive_turn` / `drive_once`, and `assert_spec_response` (the
  RemoteChatResponse conformance check). Import this rather than growing a fifth
  private copy of it. It also owns `LatchClient` (a fake transport a test can *wait on*
  instead of sleeping) and `CountingSynth`.

  Four more pieces landed with the v0.7.0 integration pass, each replacing a hand-rolled
  copy: **`free_port()`** (bind `:0` — never a hard-coded 8930/1883, which a lab machine
  and sibling agents already hold), **`status_server(rt)`** which starts the runtime's
  *real* `_start_status_server` on one and hands back its base URL, **`http_json(url, …)`**
  for talking to it, and **`loopback(rt, vm)`** — the two-subscriber in-process broker that
  lets a `sim/virtual_moxie.py` robot and a real runtime exchange bytes on the real topics
  with no mosquitto and no sleeps. `make_runtime` also takes `store=` now, so a test that
  writes durable state points it at a `tmp_path` instead of the developer's data dir.
- **`test_segment.py`** — the pure sentence segmenter (`moxie_sdk/segment.py`) a streaming
  brain talks through: boundaries, the same split whatever size the network cuts the
  stream into, decimals, abbreviations and initials, ellipses, the minimum chunk length,
  and the flush contract (the LAST sentence must still be buffered when the stream ends,
  so there is always a chunk left to close the turn with).
- **`test_streaming.py`** — streamed replies end to end: chunk numbering under one
  `event_id`, a one-sentence stream staying wire-identical to a plain reply, an action
  riding out on chunk 0, a late first token → filler, a mid-answer stall → a second filler
  and provably never a third, a newer turn cancelling a stream, a stream failing before a
  word (falls back to `respond`) or mid-answer (closes the sequence), `MOXIE_STREAMING=0`,
  LLMApp's own streaming path, and the SIL client joining the chunks of one turn.
- **`test_brain_latency.py`** — the background-inference + filler behavior: a fast brain
  still answers in exactly one `SUCCESS` chunk, a slow one speaks a filler
  (`REPLY_PENDING`, chunk 0) inside the budget and delivers the real line as chunk 1, a
  superseded turn's answer is dropped, fillers never repeat back-to-back, and both chunks
  get their own `CloudTTSResponse`. No sleeps: the fake brain blocks on an `Event` the
  test releases and the fake transport is a `Condition` the test waits on.
- **`test_fleet_config.py`** — the fleet-level default config (audit ADOPT #6): the pure
  precedence + deep-merge rule of `cloud_config.merge_config_layers`, the store's
  `fleet/config.json` record (and that a robot literally named `config` cannot collide
  with it), and the runtime seam — one fleet edit re-pushes **every** connected robot, a
  per-robot override still wins, and the status snapshot stays JSON-safe.
- **`test_console_roundtrip.py`** — the parent console ⇄ supervisor contract, driven
  in-process against a status-server double whose payload keys are diffed against the
  real runtime. Needs `fastapi` + `httpx`; skips cleanly without them (CI has neither).
- **`test_memory_view.py`** — the pure transform behind the console's 🧠 What Moxie
  remembers card (`moxie_server/fleet.py::normalize_memory`): the runtime's namespaced
  `/memory` payload flattened into dated rows per activity, newest first, with counts —
  plus the tolerance that matters on a parent's screen (a partial namespace, a list a
  module invented, a raw `memory.json` off disk, and a supervisor that is down).
- **`test_broker_acl.py`** — broker hardening P0
  ([`security-broker-auth.md`](../../docs/architecture/backlog/security-broker-auth.md) §2), pure: no
  broker, no Docker. Half is `mqtt/moxie_sdk/broker_acl.py::render_acl` — the permit-derived ACL that is
  generated now and **inert until P1** — asserted for a byte-stable golden shape, one `user d_<uuid>`
  block per permitted device, and the property the whole floor rests on: **no bare `topic` grant before
  the first `user` block**, so an anonymous client's only reach is its own `%c` subtree (plus the
  injection case: a device id carrying a newline cannot forge an ACL line). Half reads the **shipped**
  `mqtt/broker/` files, and catches the two ways this slice can silently become a no-op — an ACL that
  stops being loaded, and a `user` block on a listener with no `password_file`, where mosquitto matches a
  username nobody verified. The rest is the supervisor credential: `config.broker_credentials()`
  precedence (literal > file > anonymous, and a username without a password is *not* credentials) and
  `MoxieRuntime._build_client` actually calling `username_pw_set`. The end-to-end proof against a real
  broker is [`../run_acl_proof.sh`](../run_acl_proof.sh).
- **`test_compose.py` + `helpers_compose.py`** — the one-command stack, asserted with
  PyYAML and never Docker. Half is shape (`docker-compose.yml` declares the three
  services plus the cert one-shot, healthchecks, restart policies, named volumes,
  strictly opt-in profiles, a documented and secret-free `.env.example`); half is
  **parity** between `docker-compose.yml` and the self-contained
  `docker-compose.images.yml`. The images file cannot reference this repo — an owner
  downloads it alone — so it repeats the supervisor's `environment:` block and inlines
  `mqtt/broker/compose-mosquitto.conf`, and both copies have to be re-proven copies:
  identical `MOXIE_*` env for `supervisor`/`console`/`certs` (same keys, same
  `${VAR:-default}`), the inlined broker config diffed against the file *and* checked for
  the un-escaped `$` that a pure diff is blind to, and the same services, healthchecks,
  `depends_on` conditions, published-port defaults and volume paths. This is a regression
  suite: v0.6.0's promotion stalled because the pairing-gate knob
  `MOXIE_ALLOW_UNVERIFIED_BOTS` reached only one of the two files, and until now only the
  deep tier's PR-to-main docker smokes could see that. Every guard is paired with
  **negative tests** — tiny in-memory compose pairs carrying exactly one injected drift —
  so the suite proves the guards still bite, not merely that they pass.
- **`test_schedule_sil_e2e.py`** — the adaptive day plan end to end: the parent writes a
  bedtime and a `ParentRequest` over the **real** `POST /config?scope=fleet`, a SIL robot
  pulls its day over the **real** `CloudQuery` round trip, and the parent reads the "why
  this activity today" lines back over the **real** `GET /schedule`. The claim that binds
  them is the one nothing else asserted: *the ids the robot was served are exactly the ids
  the parent is shown an explanation for* — an audit trail describing a different day than
  the robot got would be worse than none. Also: nothing is planned inside bedtime (and the
  day really is truncated by it), the parent's request is pinned and says so, finished FTUE
  never comes back, a module quit an hour ago is not re-offered, and a reported
  `mentor_behavior` reaches the store and changes the next plan. Hermetic — the loopback
  above stands in for the broker, the store is a `tmp_path`, and every clock window is
  computed relative to *now*. It does not depend on the hour it runs at, and that is
  asserted rather than asserted-in-prose: the scenario's parent request is built to land
  on today's calendar day at all 1440 minutes (two slots ahead, or two behind in the tail
  of a day), a sibling test walks every one of those minutes to prove it, and the
  "belongs to tomorrow" contract has its own test built from a constructed instant instead
  of a branch that only ever ran in CI at 23:4x. The hour-dependence this suite actually
  caught was in the **planner**, not here — see `test_schedule_planner.py`'s
  `test_a_parent_request_survives_every_hour_the_planner_could_run_at`.
- **`test_package_contents.py`** — what `pip install moxie-cloud-sdk` actually gets, which
  no other test can see because every other test runs against the source tree: every
  package on disk is in `[tool.setuptools] packages` (add `moxie_sdk/telehealth/`, forget
  the line, and it ships absent), every non-code file inside a package is covered by a
  `[tool.setuptools.package-data]` glob (`moxie_sdk = ["*.json"]` covers exactly one
  package — a JSON under `apps/` or `content/` matches nothing and is dropped silently),
  the version has one source, and **no module needs an optional backend to import** — that
  last one asserted in a subprocess where `openai`/`numpy`/`jinja2`/`piper`/`faster_whisper`
  are made unimportable, so the full-fat venv catches what playbook rule 9 says the fast
  tier otherwise discovers as a red push.
- **`test_live_gateway_turn_e2e.py` + `helpers_stack.py`** — ONE live turn on the
  *assembled appliance*, which is the combination nothing else covered:
  `test_live_gateway.py` proves the brain, `test_live_gateway_tts.py` proves the voice but
  builds its synthesizer in-process, and `sim/run_smoke.sh` puts a real robot on a real
  broker with the echo app and the tone beep. `helpers_stack.py` boots the real thing —
  mosquitto on a free port (binary, else docker) and `mqtt/run.py` in a subprocess with its
  own scratch `MOXIE_DATA_DIR` — and the SIL robot takes one turn with `MOXIE_APP=llm` +
  `MOXIE_VOICE_BASE_URL`. Budget: **1 completion + 1 `/audio/speech`**, one module-scoped
  fixture every test reads (the filler timer is put out of reach so a slow brain cannot
  buy a second voice call). The audio assertion is spectral flatness, not sample rate:
  `ToneSynthesizer` also emits 22050 Hz mono, and the creds-free guard in the same file
  proves the tone fails the check, so a green cannot mean "the standby spoke".
- **`test_e2e_actions_to_robot.py`** — the last hop of the action contract, which nothing
  had asserted: `test_action_tags.py` reads `response_actions` back off the `FakeClient`
  that recorded the *publish*, so it proves the server's half. Here the real
  `MoxieRuntime` and the real `sim/virtual_moxie.py` are wired through
  `helpers_runtime.loopback()`, the ROBOT starts the turn, and the assertions are on what
  the robot's own `_on_message` decoded — a `launch` with `output_type`/`module_id`/
  `content_id`, an `exit`, no stray action on an ordinary answer, and the same through
  `WebhookApp` (whose bogus action type is dropped, not forwarded). Hermetic and instant:
  `LLMApp`'s `client=` seam takes a canned completion, so there is no broker, no network
  and no `openai` import. It is the **delivery** half only: that a SIM client then *acts* on
  what it was handed is asserted in `test_actions_reach_the_robot.py` (SIL, against
  `VirtualMoxie.action_stats()`) and `sim/test_bridge.mjs` (browser, against
  `bridge.js::actionStats()`) — both clients have read `response_actions` since PR #52 /
  PR #116, closing the DoD criterion-4 gap this entry used to describe.
- **`test_launch_cards_sil.py`** — 🎴 T10, the launch-card round trip on a wire. The other two
  card suites are units: `test_launch_cards.py` is the decoder, `test_launch_cards_runtime.py`
  is `_on_vision_turn` read back off a `FakeClient`. Here a real `sim/virtual_moxie.py`
  publishes an `eb-qr-event` carrying `GO<launch:DM>` through `helpers_runtime.loopback()`
  and the assertions are on the **robot's own** `action_stats()` — written by
  `_apply_action`, which only runs because a payload arrived on `commands/remote_chat`. The
  refusals travel the same wire and are the point: `launch_if_confirmed`, `sleep`, `exit`, an
  id outside the catalog, a lowercased `go` marker, an over-long value and two smuggling
  shapes each leave the robot holding **nothing** *and* answer `NOREPLY_ACK` on the scan's own
  `event_id`, so a refused card is silence rather than a stall. Held in both directions by
  `sim/tools/launch_card_mutation_check.py`, whose M15–M19 mutate the **client** — 18 of its
  19 rows redden this file on its own. Honest ceiling in its docstring: no physical Moxie has
  ever sent us an `eb-qr-event`, and a SIL robot is not a robot.
- **`test_ci_workflows.py`** — the CI harness, guarded as code. Written after four PRs
  (#43–#46) were merged on checks that had not concluded, leaving `dev` red for hours while
  the PR-side runs — which had failed identically — were believed green. It asserts that
  `sim/ci/*.yml` is byte-identical to `.github/workflows/*.yml` in both directions, that
  the fast tier's push and pull_request cover the same branches and carry **no** `if:`, no
  `paths:` filter and no cancelling `concurrency` (so a green PR and a red push can never
  be legitimate), that at least one fast-tier pytest runs the WHOLE suite (playbook rule
  9), that the hermetic suite runs *before* the browser install so a failure beats a
  two-minute merge gate, and — since 2026-09-05 — that **every test dependency is declared
  exactly once**. That last family replaced a parity check between hand-written lists,
  because parity between copies was never the property we wanted: the list lived in five
  workflow steps, no two the same, and each difference cost something (`server/requirements.txt`
  missing, so all 55 `test_console_roundtrip.py` tests silently `importorskip`ed; `pyyaml`
  missing from the deep tier; `numpy` missing from BOTH hermetic tiers, which deleted the
  creds-free tone/speech guard from every push). Now `sim/tests/requirements-hermetic.txt`
  is the one declaration, every job that runs `pytest sim/tests` installs it, no job may
  re-declare a package it owns, every third-party module the suite imports **anywhere** —
  including inside a helper *function*, which is how numpy hid — must be in it, and the
  agent-brief recipe in `docs/architecture/orchestration-plan.md` must point at it rather
  than hand-list. Nine mutants, 9/9 caught.
- **`test_speech_guard.py`** — the tone/speech predicate, guarded, and the rule that keeps a
  numpy-free suite numpy-free. `ToneSynthesizer` emits 22050 Hz mono PCM16 exactly like a
  real voice, so only the spectrum separates them, and `helpers_audio.is_real_speech` is
  what every live audio assertion leans on. It needed numpy — while two of its callers
  (`test_live_gateway_stt.py`, `test_live_hosted_ears.py`) exist to prove the CLOUD ears
  work on a box that installed nothing but `openai`. The result was measured on 2026-09-05:
  a complete healthy live turn (overlap 1.00, 203 612 B @ 22050 Hz) that then died with
  `ModuleNotFoundError` on its last line, four gateway calls in, while its siblings skipped.
  The fix was a stdlib twin (`spectral_flatness_stdlib`, a 20-line radix-2 FFT) rather than
  a third `importorskip`, which would have deleted the proof on exactly the machine shape it
  is about. This file asserts the twin rejects a real tone (8.968e-10), accepts synthetic
  broadband audio (1.177e-01) and a **real recorded voice** (3.073e-02 on the stdlib path,
  3.200e-03 on the numpy one — `goldens/real_voice_22050_mono.wav`, read with `wave` so it
  needs no decoder), agrees with the numpy implementation in both directions, computes with
  numpy forcibly unimportable in a subprocess, and — the class guards — that no numpy-free
  suite calls a helper which reaches numpy (the numpy-only set is derived from
  `helpers_audio.py`'s own call graph, so a new helper joins it automatically) and that **no
  test in this directory shells out to an undeclared external binary**. That last one exists
  because the first version of the recorded-voice test called `ffmpeg` and reddened CI, in
  the very change that made `requirements-hermetic.txt` the single source of truth — a
  binary is invisible to a guard that reads `pip install` lines, so the five programs the
  suite may spawn (`mosquitto`, `docker`, `node`, `git`, `bash`) are declared in
  `DECLARED_BINARIES` with their reason and their provider. Eleven mutants, 11/11 caught.
- **`test_ext_escapes.py`** — X1–X12, the escape suite for [sandboxed content
  extensions](../../docs/architecture/backlog/sandboxed-extensions.md) (BEYOND #6). Its own file,
  apart from the behaviour tests, because a reviewer asking *"can a stranger's content pack hurt
  this appliance?"* should be able to read one file and get an answer. No op or path can name an
  import or a dunder and the op/statement/fact-root sets are **frozen literals** (so a new operator
  takes a test edit and a reviewer); the fact base the host builds is plain JSON all the way down,
  so an attribute walk has nothing to walk to; no loop, recursion or function construct exists in
  the grammar at all; the step, wall-clock, per-value and total-allocation budgets each fail their
  op and return; 10 000-deep nesting is a **load refusal**, never a `RecursionError`; the evaluator
  imports no clock and no entropy (parsed with `ast`) and the same seed replays byte for byte; ten
  Unicode homoglyph tricks are refused as capabilities **and** as operators — normalize-and-*check*,
  never normalize-and-use, because `ｍemory.write` folds *to* `memory.write`; no other namespace and
  no other child; capability mismatch is a load refusal in both directions; effects are
  all-or-nothing; and extensions build no regex, so `MAX_PATTERN_CHARS` still governs (the residual
  risk is named rather than claimed away). X3 fences the appliance's *other* execution surface —
  six jinja2 template-injection probes, run **both** as shipped and with jinja2 forced absent, so
  neither renderer is left unfenced and neither shape skips.
  Its companion is [`../tools/ext_mutation_check.py`](../tools/README.md), which deletes each of 28
  guards in turn and requires the matching test to go red — 28/28 caught. Two were **not** caught on
  the first run, and both were real: the dunder probes were being refused for an undeclared
  capability rather than by the guard under test, and the total-allocation cap was shadowed by the
  per-value cap. A test that passes for the wrong reason is a test that will pass after the guard is
  deleted.
- **`test_ext.py`** — T1–T18, the other two questions: *does the language express what content
  authors actually wrote?* and *does a broken one leave the child with a working robot?* All six
  OpenMoxie hooks reproduce their goldens from
  [`data/ext_conformance.json`](data/README.md) byte for byte (four `xfail(strict)` until the
  `RemoteChatAction` wire lands, with their grammar asserted valid **today**); 100 runs at a fixed
  injected clock and seed give one answer, and moving the clock moves it. A poisoned `on: global`
  falls through to the conversation and a poisoned `turn.before` lets the model answer — the child
  hears no error text — nothing is half-written, and three breaches quarantine with **one**
  `ext_events` entry rather than four. A pack round-trips with an extension inside; a
  capability escalation defaults un-ticked across the whole five-row matrix; flipping one operator
  breaks the digest so the review ticks nothing. `explain()` leaks no JSON and **no capability
  identifier** (checked on word boundaries, so an author's "that card says" is not mistaken for the
  `say` capability), and every capability has parent-facing words. Under `NO_DATA` the write is
  dropped at the store and the extension still speaks. And the shipped `What Time Is It` activity
  answers end to end **with no model call** — while an imported look-alike carrying a *different*
  program gets only the four default grants and falls through, which is the point of anchoring the
  shipped grant to the program's bytes rather than to its key.
- **`test_render_sandbox_parity.py`** — the *other* half of the content-renderer security
  fix. `test_render_sandbox.py` proves eight escape probes come back inert; that says
  nothing about whether `SandboxedEnvironment` broke a legitimate prompt, and the failure
  would be silent by design (an unsafe attribute substitutes an undefined, which renders
  as `""`). So the oracle here is the **pre-sandbox renderer itself**, transcribed from
  `git show c584d3e^`: every Jinja-bearing string in `mqtt/content_modules/*.json` is
  globbed at test time and must render byte-identically through both, under a populated
  and an empty memory context, plus 27 documented constructs (dict-key access that only
  resolves through `__getitem__`, `.items()`/`.get()` on known mutables, iteration over
  the `FactList` list subclass, a `@property`, filters, `{% set %}`, whitespace control).
  Output parity alone is not proof, so `render.BLOCKED` is asserted flat — a legitimate
  template must never trip the counter. It also pins the renderer the **shipped
  appliance** actually uses: `mqtt/requirements.txt` has no jinja2, so the container and a
  bare wheel install take the `ImportError` branch, and one test records the known hole
  there (`{% if %}` passes through verbatim) rather than letting it look like it works.
- **`test_sil_durable_telemetry.py`** — the two claims about durable telemetry that no
  fixture can establish. A second `MoxieRuntime` in the same interpreter proves the
  hydration code path and nothing about durability, so this boots the real appliance
  (`helpers_stack.Stack`), sends telemetry from a real paho robot, **kills the supervisor
  process** (`Stack.restart_supervisor`), starts a new one over the same `MOXIE_DATA_DIR`
  and reads the history back with the robot deliberately not reconnected — a history that
  only reappears when the device re-announces itself is a cache. The `LoggingPolicy` gate
  runs all three values against that *running* supervisor with the verdict read off
  **disk**, not off an API that might be describing its intentions. The same stack then
  drives the console's three formerly-lying endpoints: `wakeup` is asserted by a real MQTT
  subscriber, `reboot` is a 501 that publishes nothing, `ota_status` returns the firmware
  the robot itself sent. Named `test_sil_*` so a broker boot stays out of the tiers that
  promise to report in seconds.
- **`test_sil_child_voice.py`** — the child's voice, in a real Chromium. `speakClipOnly`
  shipped with 770 hermetic assertions behind it, and every one of them reads a *file*:
  the clips exist, the manifest lists them, the session leaves room. None of them ever
  loaded the page, and Web Audio is stubbed in the node tests, so a `decodeAudioData` that
  rejects or a source node wired to nothing would have passed all 770 with the demo as
  silent as before. This replays `sessions/demo.json` in the browser and asserts what the
  audio graph DID: both MP3s fetched 200 at their on-disk size, decoded to real PCM (peak
  and RMS above a silence floor, so a valid-but-empty buffer fails), `start()`ed on a node
  whose connect-graph **reaches `ctx.destination`**, and held for their full length with
  `stop()` never called. That last one found the live bug — the shipped demo still cut her
  off mid-word, and its second line cleared being dropped entirely by ~700 ms of load
  latency. It is not proof by ear and says so: a headless browser has no speaker. Rule 11
  throughout — one module-scoped replay, six assertions over the record it left.
- **`test_ci_test_coverage.py`** — the ratchet that stops "a green test nobody runs".
  `test_ci_workflows.py` guards what the tiers *say*; this guards what they *cover*: every
  `sim/test_*.mjs` and `sim/run_*.sh` must be named by some `sim/ci/*.yml` step, and every
  flag a `run_*.sh` declares in its own `case` arms must be too — a referenced file is not
  a covered harness. `KNOWN_UNRUN` / `KNOWN_UNRUN_MODES` carry the current gaps
  (`test_ambient.mjs`, `test_presence_bridge.mjs`, `run_acl_proof.sh`, and
  `run_smoke.sh --telehealth`), each with a date, and are asserted from **both** sides so
  the lists can only shrink: an unlisted unreferenced file fails, and so does a listed one
  that has since been wired in or deleted.
- **`test_clock_dependence.py`** — the ratchet that stops "a test that is red for twenty
  minutes a day". Three flakes in two days came from the same disease (PR #60's
  20-minutes-before-midnight schedule request, PR #63's `["00:00", "23:59"]` window that
  is false for exactly 23:59, and a `TODAY = int(time.time())` that filed packets under
  yesterday's roll-up row for ~30 s after midnight). The shape is never "a test used the
  clock" — plenty must, and the runtime reads its own clock so pinning the test's would
  prove nothing. It is *a test that reads the clock and nobody wrote down why that is
  safe*. So `REVIEWED` lists every wall-clock read in the tree (`time.time`,
  `strftime`/`localtime`, `datetime.now`, `date.today`, and the node `Date.now` family —
  monotonic clocks are excluded by construction) keyed by `file::scope`, each with one of
  three verdicts (`DETERMINISTIC` / `RELATIVE` / `BOTH BRANCHES`) and the reason. Asserted
  from both sides plus a third: an unlisted read fails, a listed read that no longer
  exists fails, and a listed row whose *constructs* changed fails — so adding a
  `datetime.now()` to an already-reviewed deadline loop is still caught. Its own scanner
  is proven against a planted file.
- **`test_sil_handshake.py`** — 🤝 *a robot must not announce itself before the broker can
  answer it.* The SIL job's two intermittent reds of 2026-09-04 included twelve setup
  errors reading `no paired config pushed within timeout` (a 60 s wait) in runs where the
  supervisor had logged `→ pushed config to d_… (pairing_status=paired)`. Both halves were
  telling the truth: `connect()` does not wait for CONNACK, the SUBSCRIBE goes out from
  `on_connect` on paho's network thread, and the caller's next line published `/state` —
  so the config answering it, which is **QoS 0 and not retained**
  (`moxie_runtime._publish`; QoS 1 is refused by §4.3), could be delivered to a
  subscription that did not exist yet and is never replayed. A lost race deletes the
  message rather than delaying it, which is why **no timeout is long enough** and why the
  fix is to wait for the SUBACK (`VirtualMoxie.announce`). Beware the experiment that
  clears it: `_device_connect` pushes on a **1.0 s settle timer**, so an injected delay
  under a second is absorbed — measured 0/4 lost at 500 ms and 1100 ms, 1/4 at 1500 ms,
  always at 3000 ms. Five cases: the shipped client survives a 1.5 s-late SUBSCRIBE, the
  announcement provably waited for the SUBACK, **teeth** that run the pre-change call site
  against a cloud with no settle timer and require the config to be LOST, and a sweep of
  every `.py` under `sim/` that drives a real broker and publishes a `/state` — which is
  discovered, not listed, and whose own teeth name the four clients it must be seeing.
- **`test_sil_supervisor_readiness.py`** — 🔌 *the same rule, the other end of the wire.*
  The promotion PR's HIL job went red as `❌ scenario 'basic-conversation': 0/4 turns OK —
  no config pushed within timeout` while `motion-demo`, the **second** scenario in the same
  job, passed 4/4: first-fails-second-passes is a startup race. `subscribe()` does not
  subscribe — it queues a packet — so `[runtime] broker connected`, printed straight after
  it, meant *"we asked"*, and until 2026-09-05 the supervisor had no `on_subscribe` at all.
  A robot booted on that line announces into a broker holding no matching subscription and
  its QoS-0 config answer is never generated. **The robot-side trick does not reproduce
  this one**: a sleep before the subscribe also delays the readiness line, so the gap never
  opens — the supervisor's gap is on the wire, *after* the callback returns. So this file
  puts a TCP relay in front of the broker and holds the SUBSCRIBE packet for 3 s with
  nothing in the appliance patched. Two cases: the shipped supervisor booted on the SUBACK
  line serves the robot through that hold, and the **teeth** — the identical run booted on
  `[runtime] broker connected` loses the config outright, which is the HIL red on demand.
- **`test_sil_performance_e2e.py`** — the behavior planner with a **broker** between it
  and the client. #92 proved its criterion (c) "through the real runtime", which is an
  in-process runtime with a fake MQTT client; a scored field dropped by `json.dumps`, by
  `build_chat_response`'s omit-when-empty rules or by the chunk path's argument list would
  pass all 124 of those cases and reach no robot. So: real mosquitto, `mqtt/run.py` as its
  own process, robots reading `commands/remote_chat` off the wire. Nineteen cases —
  the five scored fields on the single-reply path and on **every** streamed chunk
  (`signals` **plural**, renamed across the `_publish_chat` seam), one face per answer,
  `vocab.validate_markup` clean over everything a robot was handed, the 🎬 rehearsal
  through the supervisor's real status HTTP **and** the real console app's
  `POST /local/robots/{id}/preview` with the captured payloads replayed through
  `../test_preview_render.mjs`, three robots on three brains at once (the clock extension
  under a per-robot brain beside `echo` and a streaming model), and `MOXIE_EXPRESSIVE=floor`
  proven a rollback rather than a downgrade. It also **pins the gap it found**: on the
  `llm` brain the markup a robot performs is the floor's `annotate` byte for byte, because
  `LLMApp` authors markup and `_stage` honours authored markup verbatim — so C6 is unmet
  on the model path and `test_the_model_path_performs_the_floors_markup` makes closing it
  a red test rather than a silent change. The brain is the local streaming stub in
  `sim/tools/first_audio_ab.py` (imported, not re-written); nothing here needs credentials.
  Note the file name: `-k "not test_sil"` excludes it from the hermetic command, and the
  fast tier's `pytest sim/tests -q` step is what runs it.
- **`test_live_telehealth_voice.py`** — 🎭 the operator's line in Moxie's *real* mouth.
  `run_smoke.sh --telehealth` proves the recovered wire but speaks with the zero-dep tone,
  so what the robot played was a beep. This boots the same appliance `helpers_stack.py`
  builds with the gateway voice, runs one session through the supervisor's status HTTP (the
  seam the console proxies), and asserts the `CloudTTSResponse` the robot received is real
  22050 Hz speech — `helpers_audio.SPEECH_FLATNESS_FLOOR`, the shared tone/speech line —
  and that the session spent **exactly one** gateway call (no brain, no ears, and
  `INTERRUPT` must not re-synthesize).
- **Live tests** (`test_live_gateway.py`, `test_live_action_tags.py`,
  `test_live_content_e2e.py`) — real completions through the LLM gateway. They run
  only when `MOXIE_LLM_API_KEY` (or `LITELLM_MASTER_KEY`) is present, e.g. from the
  git-ignored `mqtt/.env`, and **skip** otherwise, so the hermetic run stays fast and
  CI stays green with no key. They find that file through
  `helpers_runtime.load_repo_dotenv()`, which looks in this tree and then in the **main
  checkout**, so the live tier runs from a `git worktree` too (before that it silently
  skipped there). To force the creds-free behavior locally — exactly what CI does — run
  the suite with `MOXIE_LLM_API_KEY= `. `test_live_action_tags.py` asserts a *rate* (2 of 3
  sampled turns) rather than a single sample, because the brain runs at temperature
  0.8 — see its docstring for the measured numbers.
  **In CI** all three run together in the deep tier's dispatch-only step
  (`gh workflow run ci-deep.yml --ref dev`) as one `pytest -q -ra` invocation against the
  repo secrets — see [`../ci/README.md`](../ci/README.md). That step *fails* on an empty
  `MOXIE_LLM_API_KEY` rather than skipping, because a green live run that tested nothing is
  the exact gap it exists to close. It is manual because it spends ≈12–13 real completions.
- **`test_live_gateway_tts.py`** — the *gateway voice* live tests (live since 2026-09-02).
  `MOXIE_VOICE_BASE_URL` + `MOXIE_VOICE_MODEL` is the whole switch, so the file builds its
  synthesizer with `config.build_synthesizer()` and then makes the gateway prove it is
  speaking: four `/audio/speech` calls — `piper-amy` as WAV (unwrapped to the header's own
  22050 Hz and transcribed back at word overlap ≥ 0.7), `piper-ryan` to show the model *is*
  the voice switch, one `pcm` call, and one deliberately-unknown model to prove the turn
  downgrades to the standby voice instead of going silent. The tier-1 anti-tone guard is
  the same one `test_live_talk_e2e.py` uses, so this suite cannot pass on the placeholder
  beep either. Needs `faster-whisper` + `numpy` (not `piper-tts`, and no 63 MB voice file —
  the gateway does the speaking); skips cleanly without them or without a voice URL. **In
  CI** it rides in the same creds-only dispatch step as the three suites above, which also
  fails on an empty `MOXIE_VOICE_BASE_URL`.
- **`test_live_hosted_ears.py` + `helpers_route.mjs`** — the only test that puts **real
  spoken words through `functions/api/transcribe.js`**, the route the hosted page's
  microphone posts to. Everything else about that route is proven with a stubbed `fetch`
  and a 440 Hz tone ([`../test_demo_ears.mjs`](../test_demo_ears.mjs),
  [`../test_mic_spend.mjs`](../test_mic_spend.mjs)), and `test_live_gateway_stt.py`'s
  overlap-1.00 proof goes through the *Python* seam and never touches the route. Two
  tiers: **A** runs the route MODULE against the real gateway (`helpers_route.mjs` is the
  Python↔JS bridge — it builds the `Request`, calls `onRequestPost`, and reports
  `_lib/limits.js`'s own upstream-call counter so the spend is measured, not assumed);
  **B** POSTs the same WAV at a real deployment named by `MOXIE_DEMO_ORIGIN` (**no host is
  hard-coded** — C3 — so it skips loudly when unset). 3 gateway calls for a full run;
  `MOXIE_EARS_WAV=<file>` reuses a previous run's audio and makes it 2. The floor is 0.7,
  the same number `test_live_gateway_stt.py` uses, and a **decoy sentence scored against
  the same transcript must stay under 0.35** — that control is what stops the floor being
  a test that passes on any transcript at all. **It proves the route, not a person:** no
  human has ever spoken into the hosted microphone, and this file does not change that.
- **`test_live_talk_e2e.py` + `helpers_audio.py`** — the *voice* live tests: real Piper
  speech in, real Piper speech out, read back by real faster-whisper. Tier 1 round-trips
  Moxie's own voice through Whisper (and proves the built-in `ToneSynthesizer` would
  fail the same check, so the suite can never pass on the placeholder); tier 2 plays a
  child with a second voice, frames the audio as `zmqSTTRequest` protobuf and drives the
  whole loop through the real `MoxieRuntime` — `events/zmq` → transcript →
  `RemoteChatResponse` → `CloudTTSResponse` → transcribed back. `helpers_audio.py` holds
  the PCM maths (resample, spectral flatness, ZCR), the word-overlap scorer and the
  frame encoder. **Deliberately not in `requirements.txt`**: `piper-tts` and
  `faster-whisper` are ~2 GB of local model wheels, so the file skips at collection when
  they (or the git-ignored `sim/tts/voices/*.onnx`, or a gateway key) are absent — they are
  the only two entries on `test_ci_workflows.py`'s `DELIBERATELY_OPTIONAL` list, which is
  what stops the coverage guard treating them as an omission. `numpy` is no longer among
  them: it is a declared test dep, because the creds-free speech guard needs it. To run it:
  `pip install -r sim/tests/requirements.txt piper-tts faster-whisper`, then
  `python -m pytest sim/tests/test_live_talk_e2e.py -q -s` — add `MOXIE_VOICES_DIR=…`
  if the voices live outside this checkout (a git worktree starts without them).
  **In CI** it is opt-in on the same dispatch: `gh workflow run ci-deep.yml --ref dev -f
  voice=true` installs those three packages and fetches the voices with
  [`../ci/fetch_piper_voices.py`](../ci/fetch_piper_voices.py) (pinned `rhasspy/piper-voices`
  `v1.0.0` URLs, sha256-verified, cached, idempotent). That step fails unless ≥3 of its 4
  tests really passed — only the live-brain one may legitimately skip, when the gateway
  degrades to its canned fallback.

## Two rules that keep this suite hermetic and green

Both were learned from a red `dev` push (CI run 33624718428), and both are properties of
the *code under test*, not of the assertions:

- **Never assert on a live animation; assert on what the page recorded.** The mouth is
  driven by the audio envelope for the ~1 s an utterance lasts, so a test that samples
  `getMouthOpen()` has to catch it mid-open and loses that race on a loaded runner.
  `audio.js` therefore remembers the loudest frame of each cloud-TTS utterance and keeps
  it after playback ends — `moxieAudio.lastMouthPeak()` — so the assertion happens once
  the utterance is *over*. It is 0 when no PCM rendered and ~1.0 when it did, so it still
  fails loudly if the Web Audio graph breaks. Same idea as reading the whole speaking
  state from one atomic `wait_for_function` snapshot instead of several round-trips.

  The **queue** is the same kind of live thing, and cost a second flake (run
  `33629395950`, ~50% on `dev`): `test_cloud_tts_chunks_play_in_order_then_stop` sampled
  `moxieAudio.ttsPending()` at the instant `isSpeaking()` first went true, and on a fast
  runner four tenths of a second of audio had already drained into the gap, so the chunks
  that demonstrably *had* queued read as `pending: 0`. The cure is the same shape:
  `moxieAudio.lastPlaybackStats()` → `{event_id, chunks_played, order, max_pending}`,
  recorded as playback happens and frozen when it ends, so the test waits for the
  utterance to finish and then asserts three stronger facts (all three chunks played,
  in `chunk_num` order, having queued at least one deep). The test proves the assertion
  no longer depends on timing by repeating it with 10 ms chunks injected in one
  synchronous round trip — they drain faster than any observer could look, and the
  recorded stats still report the same thing.

- **A test that drives a brain with a fake must not need the real SDK.** `LLMApp` (like
  `OpenAIVoiceSynthesizer`) takes the OpenAI-compatible `client=` seam and only imports
  `openai` when it has to build one itself, so the tag/streaming tests construct it with
  a fake and run on a bare interpreter. Reserve `pytest.importorskip("openai")` for tests
  that genuinely talk to a gateway (`test_live_*.py`).

## Run


```bash
sim/tests/run.sh              # sets up the venv on first run, then runs everything
sim/tests/run.sh -q -k alive  # pass any pytest args through
```

It reuses the locally-cached Chrome (`~/.cache/puppeteer/...`, the same binary the
node/puppeteer tests use), so nothing needs downloading. If no Chromium/Chrome is
available, the suite **skips cleanly** (exit 0) — like the node browser tests — so CI
stays green without a browser.
