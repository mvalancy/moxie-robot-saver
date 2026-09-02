# One-shot init container for the one-command stack: generates the broker's self-signed
# CA + server cert into the shared `moxie-certs` volume on first run, then exits. Uses
# the SAME gen-certs.sh a bare-metal appliance runs, so there is one source of truth.
FROM alpine:3.20

RUN apk add --no-cache bash openssl

COPY gen-certs.sh /opt/moxie/gen-certs.sh
COPY docker-certs-init.sh /usr/local/bin/certs-init
# gen-certs.sh writes to <its dir>/keys — point that at the mounted volume.
RUN chmod +x /opt/moxie/gen-certs.sh /usr/local/bin/certs-init \
 && ln -s /certs /opt/moxie/keys

ENTRYPOINT ["/usr/local/bin/certs-init"]
