# 📡 Broker

Configuration for the local MQTT broker (eclipse-mosquitto) that replaces the robot's cloud IoT endpoint
on the LAN. TLS on **8883**, anonymous (the LAN-only trust model).

- [`mosquitto.conf`](mosquitto.conf) — broker config. The cert currently impersonates
  `mqtt.googleapis.com` to test faking Google IoT Core for older firmware.
- [`gen-certs.sh`](gen-certs.sh) — generate the CA + server keypair. Each appliance runs this to make its
  own keys; they are **not** committed.
- [`compose-mosquitto.conf`](compose-mosquitto.conf) — the same config for the repo-root
  [one-command stack](../../docs/guides/one-command-stack.md), with the plain listener on every
  *container* interface (in compose the supervisor is a different container).
- [`Dockerfile`](Dockerfile) + [`docker-certs-init.sh`](docker-certs-init.sh) — the one-shot init
  container that runs `gen-certs.sh` into the stack's `moxie-certs` volume on first `up`.
- `keys/`, `log/` — runtime-only (gitignored): per-appliance private keys and broker logs.

---
📖 [Back to top](../../README.md)
