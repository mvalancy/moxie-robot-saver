# 🛡️ `safety-floor/` — the false-positive corpus

The safety floor (`functions/api/_lib/safety.js` + `safety.rules.js`) runs on both sides of a
hosted turn: the child's line before the gateway is called and Moxie's own reply after it,
before any voice ticket is minted (spec: `docs/architecture/backlog/live-sim-demo.md` §4.1 and
§4.12). On her side a false positive costs a child a good line, so **the gate for every
Moxie-side rule is this corpus**, and
[`edge/demo_proxy/13_safety_floor.mjs`](../../edge/demo_proxy/13_safety_floor.mjs) pins it on
every run.

| File | What it holds | What is pinned |
|---|---|---|
| [`moxie-replies.json`](moxie-replies.json) | `replies`: every distinct real Moxie reply on disk on 2026-10-08 (eval_live and model_bakeoff artifacts, review-lane transcripts and probes; the production pair and the models tried before it). `unsafe_replies`: the real endorsements of a weapon request served by models the bake-off retired. | 0 of `replies` swapped; every `unsafe_replies` swapped. |
| [`child-lines.json`](child-lines.json) | `harmless`: toys, stories, rainbows, elbows, fishing, games, play fights, accidents, sad stories. `sword_requests`: the weapon-noun gap. `hurt_disclosures`: a person hurting, frightening or endangering the child. | harmless lines neither block nor flag; sword requests block with zero upstream calls; hurt disclosures flag `hurt_disclosure`. |
| [`hurt-replays.json`](hurt-replays.json) | Every distinct (child line, real reply) pair on disk whose child line discloses hurt: persona v1's hurt replays from #315's bake-off on the production pair, and the review's probes of #315 on both arms. | Each reply without a trusted-grown-up referral gets exactly one appended; the rest are untouched; the counts. |

**Growing it.** A swap reported on a line a child should have heard goes into `replies`; a
request the floor let through goes into `sword_requests` or `hurt_disclosures`; a wrongly
flagged line goes into `harmless`. Then the rule changes until the section is green again. The
corpus is Moxie's words and short child lines only — never a key, a host or a transcript of a
real visitor.

---
📖 [Fixtures](../README.md) · [Tests](../../README.md) · [Back to top](../../../../README.md)
