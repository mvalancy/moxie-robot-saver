# The demo has exactly one brain, and today it was gone for half an hour

**Status (2026-09-08): OPEN — measured, not built. Needs an owner decision, because the fix costs a
second credential.**

## What happened

`gateway.graphlings.net` answered **503** to every request for at least 33 minutes of continuous
observation (21:25–21:58 UTC, two independent processes polling), on `/v1/models` and
`/v1/chat/completions` alike:

```json
{"error":{"message":"Service Unavailable, the authentication database is temporarily unreachable.
Please retry shortly.","type":"no_db_connection","param":"None","code":"503"}}
```

Nothing in this repo caused it and nothing in this repo can fix it. It is the gateway's own auth
database.

## What worked, and is worth saying plainly

**The degradation path did its job.** `/api/chat` answered `mode=degraded`, `reason=upstream_down`;
the page paints its badge, `ambient.js` speaks its single degraded line once and never repeats it, and
the visitor gets the scripted Moxie rather than a broken page. That is the fallback working exactly as
[the live-Sim spec](live-sim-demo.md) designed it, verified live during the outage rather than
reasoned about.

**One trap, recorded in [the deploy guide §6](../../guides/deploy-cloudflare.md):** `/api/health`
reported `mode=live` throughout. It derives mode from configuration and never calls the gateway — so
of all the `degraded` causes, an upstream outage is the one it structurally cannot see.

## The gap

`functions/api/_lib/env.js` takes **one** `DEMO_GATEWAY_BASE_URL` and one key. `chat.js` has no
second provider to try: every upstream failure path (`:855`, `:890`, `:904`, `:914`, `:922`) ends at
`upstream_down`. So a single vendor's database outage is a total brain outage for
`moxie.mattvalancy.com`, which is the one thing the live demo exists to show.

**The architecture is already ready for the fix and was designed to be.** `env.js:14` states the
constraint as C3 — *nothing hard-coded to our gateway or our domain* — and `baseUrl`/`apiKey` are
resolved in one place. A fallback is a second pair of variables and a retry at the call site, not a
redesign.

## Why it is not built here

It needs a second provider credential and therefore an owner decision about cost. Options, cheapest
first:

1. **Do nothing.** The scripted fallback already carries the page honestly. If gateway outages are
   rare, this is a defensible answer and costs nothing.
2. **A second gateway pair** (`DEMO_GATEWAY_BASE_URL_2` / `..._KEY_2`), tried once on
   `upstream_down` only — never on `too_long`, `blocked`, or a rate limit, or the fallback becomes a
   way to spend twice as much on refusals.
3. **A local engine as the last resort**, consistent with the standing rule that Piper and whisper
   stay first-class options. Costs no money and no second vendor, but the Cloudflare demo has no
   machine to run it on — this one only helps self-hosted deployments.

**Whichever is chosen, the retry must be bounded and must not extend the visitor's wait past the
existing timeout** — `env.js:608` already states that no configuration may make the wait rival the
upstream timeout, and a second attempt in series is exactly that risk.

---
📖 [Backlog index](README.md) · [Architecture index](../README.md) · [Live-Sim spec](live-sim-demo.md) · [Deploy guide](../../guides/deploy-cloudflare.md)
