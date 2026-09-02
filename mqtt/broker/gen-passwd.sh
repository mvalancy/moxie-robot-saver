#!/usr/bin/env bash
# Mint this appliance's SUPERVISOR broker credential — one identity, one machine.
# security-broker-auth.md §2.2. Idempotent: it does nothing if the files already exist,
# so `docker compose up` can run it on every boot.
#
#   gen-passwd.sh <dir> [username]
#
# Writes two files into <dir> (the `moxie-certs` volume in compose, `keys/` bare metal):
#
#   passwd          mosquitto's password file — PBKDF2-SHA512 hashes, never the secret.
#                   0644 because the broker reads it as uid 1883 (`mosquitto`) and the
#                   volume, not the file mode, is the boundary — the same posture
#                   gen-certs.sh already takes with the broker's private key.
#   supervisor.pass the PLAINTEXT, 0600, owned by the supervisor's uid. This is the ONLY
#                   place the secret exists in the clear. It is never echoed, never a
#                   compose `environment:` literal (which `docker inspect` would show),
#                   and never leaves the volume: the supervisor reads it through
#                   MOXIE_MQTT_PASSWORD_FILE.
#
# Delete both (or `docker compose down -v`) to roll the credential; the supervisor picks
# the new one up on its next start.
set -euo pipefail

DIR="${1:?usage: gen-passwd.sh <dir> [username]}"
USERNAME="${2:-supervisor}"
# The uid the supervisor image runs as (mqtt/Dockerfile: `useradd --uid 10001 moxie`).
SUPERVISOR_UID="${MOXIE_SUPERVISOR_UID:-10001}"

mkdir -p "$DIR"

if [ -s "$DIR/passwd" ] && [ -s "$DIR/supervisor.pass" ]; then
    echo "[passwd] broker credential already present in $DIR — keeping it"
    exit 0
fi

command -v mosquitto_passwd >/dev/null 2>&1 || {
    echo "[passwd] mosquitto_passwd not found — install the 'mosquitto' package." >&2
    exit 1; }

# 32 bytes of kernel randomness, base64 -> 43 URL-safe characters. Generated here and
# never printed: the only readers are this script and the two files it writes.
SECRET="$(head -c 32 /dev/urandom | base64 | tr -d '\n=' | tr '+/' '-_')"

umask 077
printf '%s' "$SECRET" > "$DIR/supervisor.pass"
mosquitto_passwd -b -c "$DIR/passwd" "$USERNAME" "$SECRET"
unset SECRET

chmod 0644 "$DIR/passwd"
chmod 0600 "$DIR/supervisor.pass"
# Best-effort: bare metal usually runs the supervisor as the same user that ran this.
chown "$SUPERVISOR_UID" "$DIR/supervisor.pass" 2>/dev/null || true

echo "[passwd] minted the '$USERNAME' credential in $DIR (passwd + supervisor.pass)"
