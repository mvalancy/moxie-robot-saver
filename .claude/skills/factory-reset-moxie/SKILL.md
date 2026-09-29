---
name: factory-reset-moxie
description: Unpair or factory-reset a paired Moxie robot. Use when a Moxie is stuck paired to the old Embodied cloud or a different account and needs to be reset before fresh setup.
---

# Factory-reset (or unpair) a paired Moxie

There is **no button combo** on the robot. In the original app, reset was **server-relayed**: the app told
the cloud, which told the robot. The robot's setup app also accepts a **`restore_factory` debug QR**.

## The app's REST operations (same endpoint, one flag)
```bash
# Unpair only — remove the robot from the account
curl -X DELETE "http://<server>:8080/api/robots/<robot_id>" \
     -H "Authorization: Bearer <token>"

# Factory reset — unpair AND wipe (rfs = restore-factory-settings)
curl -X DELETE "http://<server>:8080/api/robots/<robot_id>?rfs=1" \
     -H "Authorization: Bearer <token>"
```
Get `<robot_id>` from `GET /api/users/me` (the `robots` relationship) and `<token>` from the login flow
(or `POST /local/quicklogin` on our server).

**Our server today:** `DELETE /api/robots/{id}` removes the robot record (with or without `rfs`); it
does not relay a wipe to the robot. `POST /api/robots/{id}/restores` is acknowledged (204) but not
implemented. Check `server/moxie_server/routes/robots.py` for the current behavior.

## On the robot: the debug QR
From `tools/robot-toolkit/`:
```bash
python -m moxie_toolkit.cli debug restore_factory --png reset.png   # enters the factory-restore flow
python -m moxie_toolkit.cli debug reset_network --png net.png       # forget all Wi-Fi only
```
Show the code to the robot while it is on its QR-reading screen. Semantics:
`docs/reverse-engineering/protocol/qr-commands.md`.

## Restore instead of wipe
The original app moved a child's data to a reset/new robot with
`POST /api/robots/{id}/restores` and `{"restore":{"status":"initiated"}}` (needs the account's
`has-backups`); it re-seals the child's encrypted keys to the new robot.

## Reference
- `docs/guides/factory-reset-a-paired-moxie.md`
- `docs/features/robot-lifecycle.md`
