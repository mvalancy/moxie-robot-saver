# 📡 Broker

Configuration for the local MQTT broker (eclipse-mosquitto 2.0.20) that replaces the robot's cloud IoT
endpoint on the LAN. TLS on **8883** for the robot, plain **1883** for the supervisor and the tests,
websockets **9001** for the browser UI.

**P0 broker hardening shipped 2026-09-02** ([`security-broker-auth.md`](../../docs/architecture/backlog/security-broker-auth.md)
§2, reasoning in [`mqtt-and-conversation.md` §3.1](../../docs/architecture/mqtt-and-conversation.md)).
Say the limit before the feature list: **this is containment, not authentication.** A robot still
connects anonymously — that is the only thing a stock Moxie can do — so a device that copies a robot's
`d_<uuid>` is still served as that robot. What changed is how far any client can reach once it is on the
bus: every anonymous client is confined to `/devices/<its own client id>/…`, and `$SYS/broker/log` (the
fleet roster) is readable only by the supervisor, which now authenticates.

- [`mosquitto.conf`](mosquitto.conf) — broker config for a **bare-metal** appliance. The cert currently
  impersonates `mqtt.googleapis.com` to test faking Google IoT Core for older firmware.
- [`compose-mosquitto.conf`](compose-mosquitto.conf) — the same model for the repo-root
  [one-command stack](../../docs/guides/one-command-stack.md), with the plain listener on every
  *container* interface (in compose the supervisor is a different container). Inlined byte-for-byte into
  `docker-compose.images.yml`; `sim/tests/test_compose.py` and `sim/run_compose_smoke.sh` both fail on drift.
- [`acl`](acl) — the ACL for the **console-side** listeners (1883, 9001): the `%c` device floor, the
  browser SIM's read-only observer grant, and the authenticated `user supervisor` block.
- [`acl-robot`](acl-robot) — the ACL for the **robot** listener (8883): the `%c` floor and nothing else.
- [`gen-certs.sh`](gen-certs.sh) — generate the CA + server keypair. Each appliance runs this to make its
  own keys; they are **not** committed.
- [`gen-passwd.sh`](gen-passwd.sh) — mint this appliance's supervisor credential (`keys/passwd` +
  `keys/supervisor.pass`). Idempotent; also **not** committed.
- [`Dockerfile`](Dockerfile) + [`docker-certs-init.sh`](docker-certs-init.sh) — the one-shot init
  container that runs both scripts into the stack's `moxie-certs` volume on every `up`.
- `keys/`, `log/` — runtime-only (gitignored): per-appliance private keys, the password file, and logs.

## Why there are two ACL files

`mosquitto` substitutes `%c` (the client id) into a `pattern` line, and every client has a client id
whether or not it authenticated — which is what makes a per-device confinement available before device
auth is. Both files share the same four-line floor.

They differ because **the security settings are per listener, and have to be**:

| listener | who | `password_file` | `acl_file` |
|---|---|---|---|
| `8883` TLS | a real robot | ✗ — it presents an RS256 JWT as its password, and a password file would refuse it | `acl-robot` |
| `1883` plain | supervisor · SIM · tests | ✓ | `acl` |
| `9001` websockets | the browser UI | ✓ | `acl` |

On a listener with **no** password file, mosquitto accepts any username unchecked and then matches it
against the ACL's `user` blocks — so a `user supervisor` block on 8883 would hand the fleet to anyone who
typed the word. (Verified against `eclipse-mosquitto:2.0.20`; `sim/run_acl_proof.sh` re-proves it, and
`sim/tests/test_broker_acl.py::test_a_user_block_is_only_ever_reachable_behind_a_password_file` guards
it.) So the supervisor's identity lives only in `acl`, and `acl` is loaded only where `password_file` is.

## Running it bare metal

```sh
./gen-certs.sh 192.168.1.9        # your LAN IP -> keys/{ca,mosquitto}.{crt,key}
./gen-passwd.sh keys              # -> keys/passwd + keys/supervisor.pass (0600)
mosquitto -c mosquitto.conf
```

Then point the supervisor at the credential in `mqtt/.env`:

```sh
MOXIE_MQTT_USER=supervisor
MOXIE_MQTT_PASSWORD_FILE=/abs/path/to/mqtt/broker/keys/supervisor.pass
```

**`mosquitto.conf` will not start without `keys/passwd`** — that is mosquitto's behaviour for a missing
`password_file`, and it is the one manual step this slice adds to the bare-metal path. The compose path
has none: the `certs` one-shot mints it. Leaving both variables unset makes the supervisor an anonymous
client again, which is what the SIL harness (`sim/broker/ci-mosquitto.conf`, deliberately unhardened)
and a scratch dev broker run.

Two notes an owner hits:

- mosquitto 2.0.20 **warns** that the config/ACL files are not owned by `mosquitto` and are world
  readable. They are bind-mounted read-only from the repo, so it cannot chown them; the warnings are
  cosmetic today. The `passwd` file is `0644` on purpose (the broker reads it as uid 1883, and it holds
  PBKDF2-SHA512 hashes, not the secret); the **plaintext** is `supervisor.pass` at `0600`.
- the SIL-only motor path (`virtual_moxie.py --script` with `motors`) publishes to its own
  `commands/motor`, which the `%c` floor grants read but not write. It runs against the unhardened SIL
  broker, so nothing breaks — but a hardened broker would drop it.

---
📖 [Back to top](../../README.md)
