# 📡 Broker

Configuration for the local MQTT broker (eclipse-mosquitto) that replaces the robot's cloud IoT endpoint
on the LAN. TLS on **8883**, anonymous (the LAN-only trust model).

- [`mosquitto.conf`](mosquitto.conf) — broker config. The cert currently impersonates
  `mqtt.googleapis.com` to test faking Google IoT Core for older firmware.
- [`gen-certs.sh`](gen-certs.sh) — generate the CA + server keypair. Each appliance runs this to make its
  own keys; they are **not** committed.
- `keys/`, `log/` — runtime-only (gitignored): per-appliance private keys and broker logs.

---
📖 [Back to top](../../README.md)
