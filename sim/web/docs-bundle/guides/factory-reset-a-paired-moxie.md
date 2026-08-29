# Guide: factory-reset (or unpair) a paired Moxie

If a Moxie is still paired to the (now-dead) Embodied cloud, or paired to a different account, you
generally need to reset it before it will show the QR/pairing screen for a fresh setup.

> **Important:** In the original app, reset is **server-relayed** — the app tells the *cloud* to tell
> the *robot* to reset. There is **no button combo on the robot itself** (the decompiled app contains
> no on-device/hardware reset path). That means a clean reset needs a server the robot still trusts:
> either the original cloud (gone) or your own server once the robot is pointed at it. For a robot
> that has already been moved onto your server, the flow below is exactly what the app did.

## Unpair vs. factory reset
Both use the **same endpoint**, differing only by one flag:

| Action | Request | Effect |
|--------|---------|--------|
| **Unpair** | `DELETE /api/robots/{id}` | removes the robot from the account |
| **Factory reset** | `DELETE /api/robots/{id}?rfs=1` | unpair **+ restore factory settings** (`rfs` = restore-factory-settings) |

Both send `Authorization: Bearer <token>` and an empty body. In the app this is the
`BaseActivity.unpairMoxie()` bottom-sheet: a plain **Unpair** button vs. a red **Restore Factory
Settings** button. On success the app clears the robot, resets the crypto manager, re-fetches the
account, and the robot returns to `UNPAIRED` / the pairing screen.

## Doing it from our local server
Our server implements the same endpoint. Once you know the robot's id (from `GET /api/users/me`):

```bash
# unpair
curl -X DELETE "http://<server>:8080/api/robots/<robot_id>" \
     -H "Authorization: Bearer <token>"

# factory reset (unpair + wipe)
curl -X DELETE "http://<server>:8080/api/robots/<robot_id>?rfs=1" \
     -H "Authorization: Bearer <token>"
```

(A one-tap button for this will land in the web UI — see [`../../ROADMAP.md`](../../ROADMAP.md) Phase 1.)

## Restore from backup instead of wiping
If you want to move a child's data to a new/reset robot rather than start fresh, that's the **restore**
flow (`POST /api/robots/{id}/restores` with `{"restore":{"status":"initiated"}}`), gated by the
account's `has-backups` flag. It re-seals the child's encrypted keys to the new robot. See
[`../features/robot-lifecycle.md`](../features/robot-lifecycle.md).

## Reference
Full lifecycle detail (state model, enums, thresholds): [`../features/robot-lifecycle.md`](../features/robot-lifecycle.md).
