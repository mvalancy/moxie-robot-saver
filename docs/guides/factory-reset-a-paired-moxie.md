# Factory-reset (or unpair) a paired Moxie

For owners with a Moxie that is paired to an account it should leave: one on this server, someone
else's, or the shut-down Embodied cloud. **Unpair** takes the robot off your account on this server.
A **factory reset** does that too, then gives you a code that wipes the robot itself when Moxie
scans it.

## Unpair or reset from the web app

In the console's **🤖 Moxie** tab, the robot card has **Unpair this robot** and **Factory reset**.
Each asks you to confirm by typing: `UNPAIR` or the robot's name to unpair, or `RESET` and a tick
beside "cannot be undone" to reset.

Our server then takes the robot off the account and stops serving it: its permit is revoked and it
is sent the not-paired settings. It also voids the pairing codes you made but never used. Your
child's profile, what Moxie remembers and the activity history are kept unless you tick the erase
boxes. After a reset, the app shows the reset code (next section). Details:
[`../features/robot-lifecycle.md`](../features/robot-lifecycle.md#built-here-unpair-and-factory-reset).

## The reset code
Our server sends the robot no wipe command, because no cloud-to-robot reset command is known. A
factory reset from our server ends with a code for Moxie's camera:
`{"debug":{"command":"restore_factory"}}`, one of the setup app's four debug commands
([QR commands](../reverse-engineering/protocol/qr-commands.md)). The robot is wiped only when it
scans that code.

The web app shows it after a reset. For a Moxie still paired somewhere else, it is under **No Moxie
paired yet → Factory reset a robot**, and showing it there changes nothing on the server. Moxie
reads it on the screen where it asks for a code. No physical robot has been reset this way by this
project yet.

## The same calls from a terminal
Our server implements the original app's endpoint, so a script can do the same. Find the robot's id
with `GET /api/users/me`:

```bash
# unpair
curl -X DELETE "http://<server>:8080/api/robots/<robot_id>" \
     -H "Authorization: Bearer <token>"

# factory reset (unpair, then show Moxie the reset code)
curl -X DELETE "http://<server>:8080/api/robots/<robot_id>?rfs=1" \
     -H "Authorization: Bearer <token>"
```

Both use the **same endpoint**, differing only by one flag:

| Action | Request | Effect |
|--------|---------|--------|
| **Unpair** | `DELETE /api/robots/{id}` | removes the robot from the account |
| **Factory reset** | `DELETE /api/robots/{id}?rfs=1` | unpair **+ restore factory settings** (`rfs` = restore-factory-settings) |

Both send `Authorization: Bearer <token>` and an empty body. In the original app this is the
`BaseActivity.unpairMoxie()` bottom-sheet: a plain **Unpair** button vs. a red **Restore Factory
Settings** button. On success the app clears the robot, resets the crypto manager, re-fetches the
account, and the robot returns to `UNPAIRED` / the pairing screen.

## Why there is no reset button on the robot
In the original app, reset is **server-relayed**: the app tells the *cloud* to tell the *robot* to
reset. There is **no button combo on the robot itself**; the decompiled app contains no on-device or
hardware reset path ([robot lifecycle §1.4](../features/robot-lifecycle.md#14-on-device-hardware-reset)).
How Embodied's cloud told the robot is not in anything this project has studied, which is why our
server uses the reset code instead.

Whether a robot still paired to the dead Embodied cloud must be reset before it shows its code screen
is not verified. Moxie's software returns to that screen by itself when it wakes and finds no
internet ([boot and launcher](../reverse-engineering/firmware/boot-and-launcher.md#two-facts-that-matter-for-revival)).

## Restore from backup instead of wiping
If you want to move a child's data to a new/reset robot rather than start fresh, that's the **restore**
flow (`POST /api/robots/{id}/restores` with `{"restore":{"status":"initiated"}}`), gated by the
account's `has-backups` flag. It re-seals the child's encrypted keys to the new robot. Our server
does not implement it yet (it answers `204` and does nothing). See
[`../features/robot-lifecycle.md`](../features/robot-lifecycle.md).

## Reference
Full lifecycle detail (state model, enums, thresholds): [`../features/robot-lifecycle.md`](../features/robot-lifecycle.md).

---
📖 [Guides index](README.md) · [Permitting a robot](permitting-a-robot.md) · [Back to top](../../README.md)
