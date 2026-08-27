#!/usr/bin/env bash
# Generate a self-signed CA + broker cert for the local Moxie MQTT broker.
# Works with firmware 24.10.803 (which honors disable_verify=true in the endpoint QR).
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)/keys"
HOST="${1:-$(hostname -I | awk '{print $1}')}"   # broker IP/hostname the robot will use
mkdir -p "$DIR"; cd "$DIR"
echo "[*] Generating CA + broker cert for host: $HOST"
openssl genrsa -out ca.key 2048 2>/dev/null
openssl req -new -x509 -days 3650 -key ca.key -out ca.crt -subj "/CN=MoxieSaver-CA" 2>/dev/null
openssl genrsa -out mosquitto.key 2048 2>/dev/null
openssl req -new -key mosquitto.key -out mosquitto.csr -subj "/CN=$HOST" 2>/dev/null
cat > ext.cnf <<EXT
subjectAltName = IP:$HOST,DNS:$HOST,DNS:localhost,IP:127.0.0.1
EXT
openssl x509 -req -in mosquitto.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
    -out mosquitto.crt -days 3650 -extfile ext.cnf 2>/dev/null
chmod 644 *.crt *.key
echo "[*] Done. Keys in $DIR (ca.crt, mosquitto.crt, mosquitto.key)"
