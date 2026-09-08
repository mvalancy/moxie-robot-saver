# `<exit>` stopped being written, and the prompt block that carries it contradicts itself

**Status (2026-09-08): OPEN — one measurement, one candidate cause, and the candidate is UNTESTED
because the gateway is down.** Do not read the second half of this brief as a diagnosis of the first.

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
last thing the model reads. **The mitigation for this exact drift is present and correctly placed,
and the drift is happening anyway.**

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

---
📖 [Backlog index](README.md) · [Architecture index](../README.md) · [One brain, no failover](one-brain-no-failover.md)
