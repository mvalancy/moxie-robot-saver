---
name: factory-reset-moxie
description: Unpair or factory-reset a paired Moxie robot. Use when a Moxie is stuck paired to the old Embodied cloud or a different account and needs to be reset before fresh setup.
---

# Factory-reset (or unpair) a paired Moxie

Reset is **server-relayed** — there is NO button combo on the robot itself. The app tells the cloud,
which tells the robot. So you need a server the robot still trusts (the original cloud is gone; use
your own server once the robot is pointed at it).

## The two operations (same endpoint, one flag)
```bash
# Unpair only — remove the robot from the account
curl -X DELETE "http://<server>:8080/api/robots/<robot_id>" \
     -H "Authorization: Bearer <token>"

# Factory reset — unpair AND wipe (rfs = restore-factory-settings)
curl -X DELETE "http://<server>:8080/api/robots/<robot_id>?rfs=1" \
     -H "Authorization: Bearer <token>"
```
Get `<robot_id>` from `GET /api/users/me` (the `robots` relationship). Get `<token>` from the login
flow (or `/local/quicklogin` on our server).

On success the robot returns to the UNPAIRED state / pairing screen.

## Restore instead of wipe
To move a child's data to a reset/new robot rather than start fresh, use the restore flow:
`POST /api/robots/{id}/restores` with `{"restore":{"status":"initiated"}}` (needs the account's
`has-backups`). It re-seals the child's encrypted keys to the new robot.

## Reference
- `docs/guides/factory-reset-a-paired-moxie.md`
- `docs/features/robot-lifecycle.md`
