# Guide: factory-reset (or unpair) a paired Moxie

If a Moxie is still paired to the (now-dead) Embodied cloud, or paired to a different account, you
generally need to reset it before it will show the QR/pairing screen for a fresh setup.

> **Important:** In the original app, reset is **server-relayed** — the app tells the *cloud* to tell
> the *robot* to reset. There is **no button combo on the robot itself** (the decompiled app contains
> no on-device/hardware reset path). That means a clean reset needs a server the robot still trusts:
> either the original cloud (gone) or your own server once the robot is pointed at it. For a robot
> that has already been moved onto your server, the flow below is exactly what the app did. The
> robot's setup app also takes a `restore_factory` code on its code screen ([below](#the-reset-code));
> this project has not yet seen that reset a robot.

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

In the web app's Moxie tab, the robot card has **Unpair this robot** and **Factory reset**, behind a
typed confirmation. Our server takes the robot off the account, stops serving it (its permit is
revoked and it is sent the not-paired settings) and voids the pairing codes you made but never used.
Your child's profile and what Moxie remembers are kept unless you tick the erase boxes. Details:
[`../features/robot-lifecycle.md`](../features/robot-lifecycle.md#built-here-unpair-and-factory-reset).

## The reset code
No cloud-to-robot reset command is known, so a factory reset from our server ends with a code for
Moxie's camera: `{"debug":{"command":"restore_factory"}}`, one of the setup app's four debug
commands ([QR commands](../reverse-engineering/protocol/qr-commands.md)). The web app shows it after a
reset, and under **No Moxie paired yet → Factory reset a robot** for a Moxie still paired somewhere
else (showing it changes nothing on the server). Moxie reads it on the screen where it asks for a
code. No physical robot has been reset this way by this project yet.

## Restore from backup instead of wiping
If you want to move a child's data to a new/reset robot rather than start fresh, that's the **restore**
flow (`POST /api/robots/{id}/restores` with `{"restore":{"status":"initiated"}}`), gated by the
account's `has-backups` flag. It re-seals the child's encrypted keys to the new robot. Our server
does not implement it yet (it answers `204` and does nothing). See
[`../features/robot-lifecycle.md`](../features/robot-lifecycle.md).

## Reference
Full lifecycle detail (state model, enums, thresholds): [`../features/robot-lifecycle.md`](../features/robot-lifecycle.md).
