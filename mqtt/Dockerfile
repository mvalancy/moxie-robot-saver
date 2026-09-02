# The Moxie robot-cloud supervisor (run.py) — the robot-facing half of the server app.
# Slim, pinned, non-root. Heavy optional voice/STT backends are NOT baked in; add them
# at build time (see docs/guides/one-command-stack.md):
#   --build-arg EXTRAS="piper-tts"              → offline Piper voice (also needs a model)
#   --build-arg EXTRAS="faster-whisper numpy"   → local speech-to-text
FROM python:3.12-slim-bookworm

ARG EXTRAS=""
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
 && if [ -n "$EXTRAS" ]; then pip install --no-cache-dir $EXTRAS; fi

COPY . .

# Non-root. /data is created in the image so a named volume mounted there inherits
# this ownership on first use (Docker seeds an empty named volume from the image path).
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin moxie \
 && mkdir -p /data/memory /models /cache \
 && chmod +x docker-entrypoint.sh \
 && chown -R moxie:moxie /data /cache /app
USER moxie

# Container-shaped defaults. Everything here is overridable from the root .env.
ENV MOXIE_MQTT_HOST=broker \
    MOXIE_MQTT_PORT=1883 \
    MOXIE_STATUS_PORT=8930 \
    MOXIE_DATA_DIR=/data \
    MOXIE_MEMORY_DIR=/data/memory

# The runtime's status server binds 127.0.0.1 on purpose (moxie_runtime.py). The
# console lives in another container, so the entrypoint can expose a read/write proxy
# on MOXIE_STATUS_PROXY_PORT — opt-in, set by the compose file, never on by default.
EXPOSE 8931

HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=6 \
  CMD python -c "import json,sys,urllib.request; sys.exit(0 if json.load(urllib.request.urlopen('http://127.0.0.1:'+__import__('os').environ.get('MOXIE_STATUS_PORT','8930')+'/status',timeout=4)).get('ok') else 1)"

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["python", "run.py"]
