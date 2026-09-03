# One-shot init container for the one-command stack: generates this appliance's
# self-signed CA + server cert AND the supervisor's broker credential into the shared
# `moxie-certs` volume on first run, then exits. Uses the SAME gen-certs.sh /
# gen-passwd.sh a bare-metal appliance runs, so there is one source of truth.
#
# `mosquitto` is here for exactly one binary — mosquitto_passwd, which writes the
# PBKDF2-SHA512 hashes the broker's password_file needs (security-broker-auth.md §2.2).
# Hand-rolling that hash format is the kind of cleverness that silently locks an owner
# out of their own appliance.
FROM alpine:3.20

RUN apk add --no-cache bash openssl mosquitto

COPY gen-certs.sh /opt/moxie/gen-certs.sh
COPY gen-passwd.sh /opt/moxie/gen-passwd.sh
COPY docker-certs-init.sh /usr/local/bin/certs-init
# gen-certs.sh writes to <its dir>/keys — point that at the mounted volume.
RUN chmod +x /opt/moxie/gen-certs.sh /opt/moxie/gen-passwd.sh /usr/local/bin/certs-init \
 && ln -s /certs /opt/moxie/keys

ENTRYPOINT ["/usr/local/bin/certs-init"]
