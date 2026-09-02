#!/bin/sh
# Generate the broker's per-appliance material into /certs (a docker volume) unless it is
# already there: the TLS CA + server cert, and the SUPERVISOR's broker credential
# (security-broker-auth.md §2.2). Both steps are independent and idempotent, so
# `docker compose up` can run this every time — including on a volume that predates the
# credential, which is how an existing appliance grows one without any owner action.
# Delete the volume (`docker compose down -v`, or `docker volume rm moxie_moxie-certs`)
# to regenerate everything — e.g. after your broker's LAN IP changes.
set -eu

CERT_DIR=/certs
HOST="${MOXIE_BROKER_HOST:-127.0.0.1}"

mkdir -p "$CERT_DIR"

# ── step 2 of 2 runs either way ─────────────────────────────────────────────────────
# The credential step is deliberately NOT behind the cert check: an appliance that was
# installed before broker auth existed already has certs, and must still get a password.
mint_credential() {
    /opt/moxie/gen-passwd.sh "$CERT_DIR" "${MOXIE_MQTT_USER:-supervisor}"
}

if [ -s "$CERT_DIR/ca.crt" ] && [ -s "$CERT_DIR/mosquitto.crt" ] && [ -s "$CERT_DIR/mosquitto.key" ]; then
    echo "[certs] broker certs already present in $CERT_DIR — keeping them"
    mint_credential
    exit 0
fi

# gen-certs.sh puts the host in the cert's subjectAltName as both IP: and DNS:, so a
# non-IP value makes openssl reject the extension. Fall back rather than fail the stack.
if ! echo "$HOST" | grep -Eq '^[0-9]{1,3}(\.[0-9]{1,3}){3}$'; then
    echo "[certs] MOXIE_BROKER_HOST='$HOST' is not an IPv4 address — using 127.0.0.1."
    echo "[certs] Set MOXIE_BROKER_HOST to your broker machine's LAN IP in .env so a"
    echo "[certs] real robot's TLS handshake sees a matching cert, then re-create the"
    echo "[certs] moxie-certs volume. (Firmware 24.10.803 also honours disable_verify.)"
    HOST=127.0.0.1
fi

echo "[certs] generating a self-signed CA + broker cert for $HOST"
/opt/moxie/gen-certs.sh "$HOST"
chmod 0644 "$CERT_DIR/ca.crt" "$CERT_DIR/mosquitto.crt" "$CERT_DIR/mosquitto.key"
mint_credential
echo "[certs] done — $CERT_DIR"
