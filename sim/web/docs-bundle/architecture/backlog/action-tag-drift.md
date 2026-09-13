# Historical `<exit>` absence and the prompt-block hypothesis

**Status (2026-09-13): OPEN — one historical measurement, one candidate cause, and no current
re-measurement. A bounded, counts-only runner is under review; gateway availability and model adherence
are still unmeasured.** Do not read the second half of this brief as a diagnosis of the first.

## The measurement

`test_live_action_tags` fails on **non-empty** model replies: *"only 0/3 goodbye turns emitted
`<exit>`"*. The model answers, warmly and correctly as speech, and simply omits the tag. Without it
the runtime never ends the module (`moxie_sdk/app.py:95`), so a child who says goodbye is not let go.

This is **not** the gateway outage. It was reported against real replies, and it is a separate slice
from the five other `test_live_*` reds, which are confounded by the 503s and remain undiagnosed. An
earlier report that these tests *"fail instead of skipping without credentials"* was **wrong about the
mechanism and is withdrawn**: all six carry correct `skipif` guards, but `load_repo_dotenv()` has
already put credentials in the environment, so they never skip — they run, and they fail for reasons.

## The wiring is intact — checked, not assumed

The obvious hypothesis is that the instruction stopped reaching the model. It did not.
`_LAST_CHECK` (`apps/llm_app.py:79`) exists **precisely for this failure** — its own comment reads
*"the single most load-bearing line of the tag prompt: graphling-medium writes a warm goodbye and
simply stops"* — and it is still the tail of both `_TAG_EXAMPLES` and `_TAG_EXAMPLES_PLAIN`
(`:248`, `:256`), while `_system()` returns `persona + who + fmt + tags` (`:340`), so it remains the
last thing the model reads. **The mitigation for this exact drift was present and correctly placed
when the historical 0/3 sample was observed.**

## The old assertion did not reliably tell completion from adherence

Before blaming the tests, they were run **against the live outage** — the rare case where the
condition you want to handle is happening while you look at it. They report it unmistakably:

```
E  openai.InternalServerError: Error code: 503 - {'message': 'Service Unavailable, the
   authentication database is temporarily unreachable...', 'type': 'no_db_connection'}
[gateway] busy — InternalServerError; slowing down 0.8s (retry 1) … 5.2s (retry 4)
3 failed, 3 passed, 1 skipped in 408.66s
```

That historical output distinguished those particular runs, but it did not prove the harness always
could. `LLMApp.respond()` converts exhausted server and budget errors into a friendly fallback, while
the old assertion accepted any two tagged replies in its three returned `Reply` objects. A hermetic
negative control reproduced a false green: two tagged completions, then four 503 attempts and refusal
before a seventh request still satisfied the 2/3 assertion because the third trial returned fallback
speech. Non-empty speech is therefore not evidence of a completed model response.

The repaired campaign wraps the existing client boundary, counts attempts and structurally valid,
non-empty responses independently of `Reply`, and stops scoring at the first incomplete trial. Three
eligible completions are required before adherence is evaluated. Provider replies, action identifiers,
and exception text are discarded; the supervisor emits only allow-listed aggregate counts and fixed
termination categories. Timeout, missing prerequisites, child failure, and counter disagreement cannot
be reported as a passing measurement.

## The candidate cause: the block tells the model its own examples are invalid

Inside the same block, `fmt` advertises the mood enum and closes with:

> *"Your face has these eleven expressions and no others; anything else is ignored."*

Three of the four examples immediately following it — **including all three `<exit>` examples** — use
`"mood": "positive"`, which is **not one of those eleven**.

**No runtime defect results from this.** `positive` is an accepted alias in `vocab.py:83`, mapping to
`1`, identical to canonical `happy`; the table exists for exactly this reason and says so
(*"our own older LLM prompt menu … pre-floor"*). The mood arrives correctly. That was checked before
this brief was written, because the first guess — that goodbyes were losing their expression — was
wrong.

What remains is a **contradiction the model reads**: the paragraph declaring a closed set, followed by
worked examples that violate it, inside the block labelled *"most important rule"*. A model shown that
its exemplars are out-of-schema has been given a reason to treat the whole block as approximate — and
the one rule in it that has decayed is the one those same examples demonstrate.

## The proposed change, and why it is not made here

Three edits in `apps/llm_app.py:241,243,245`: `"mood": "positive"` → `"mood": "happy"`. Canonical,
same mood id, no downstream difference, and the examples stop contradicting the sentence above them.

**It is not applied, because it cannot be tested.** The claim is about model behaviour, and
`gateway.graphlings.net` has been 503 for over half an hour
(see [`one-brain-no-failover.md`](one-brain-no-failover.md)). Shipping an untested prompt change to the
thing that drives the live demo's personality, during an outage, to fix a drift it has not been shown
to cause, would be the same substitution this repo keeps catching. **The test is cheap and exists**:
apply the three edits and run `test_live_action_tags`; the honest baseline to beat is 0/3.

If it still reads 0/3, the contradiction was a red herring and the cause is upstream of the prompt —
which is worth knowing, and is why the number goes in this file either way.

## The next measurement is mechanically bounded

Do not run the whole live test file for this question. It contains a third wire test, and each
`LLMApp.respond()` used to hide up to four retries from the shared model-call counter. The targeted
runner is now:

```sh
sim/tools/run_live_action_tags.sh
```

It selects only the three-trial goodbye acceptance check, sets a process-wide six-attempt ceiling that
is checked immediately before every request including retries, and gives the entire process a 360-second
deadline with a five-second termination grace. A normal run therefore spends three attempts, not the
ceiling; DRAW adherence remains a separate future measurement. The direct `LLMApp` request path
now participates in the same counter as the other chat seams. Hermetic tests prove attempts one
through six pass, attempt seven is refused before the client, transient retries cannot cross the
limit, invalid limits fail closed, and a normal direct reply is counted. Campaign controls additionally
prove that a completed 2/3 passes, a completed 1/3 fails, two successes plus exhaustion is inconclusive,
retry recovery counts every attempt, invalid/empty responses do not become completions, missing
prerequisites are visible, a hanging child reports timeout with its flushed attempt count, inconsistent
instruments fail closed, and secret-like model/exception text never enters the aggregate result.

This makes the test safe to schedule under an explicit six-attempt budget; it does not spend that
budget, establish current gateway availability, or update the historical 0/3 and 0/2 observations.

---
📖 [Backlog index](README.md) · [Architecture index](../README.md) · [One brain, no failover](one-brain-no-failover.md)
