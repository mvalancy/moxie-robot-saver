#!/bin/sh
# Generate the broker's TLS material into /certs (a docker volume) unless it is already
# there. Idempotent: `docker compose up` can run this every time. Delete the volume
# (`docker compose down -v`, or `docker volume rm moxie_moxie-certs`) to regenerate —
# e.g. after your broker's LAN IP changes.
set -eu

CERT_DIR=/certs
HOST="${MOXIE_BROKER_HOST:-127.0.0.1}"

mkdir -p "$CERT_DIR"

if [ -s "$CERT_DIR/ca.crt" ] && [ -s "$CERT_DIR/mosquitto.crt" ] && [ -s "$CERT_DIR/mosquitto.key" ]; then
    echo "[certs] broker certs already present in $CERT_DIR — keeping them"
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
echo "[certs] done — $CERT_DIR"
