# The Moxie parent console (server/run.py) — the parent-app-facing half of the server app:
# the account-free REST API plus the mobile web client. Slim, pinned, non-root.
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Built from the REPO ROOT (see docker-compose.yml / .dockerignore): the console
# imports the clean-room pairing-QR codec from tools/pairing/, which sits outside
# server/.  docker build -f server/Dockerfile .
WORKDIR /app
COPY server/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY server/ /app/
COPY tools/pairing/ /tools/pairing/

# Non-root. /data holds the SQLite DB; a named volume mounted there inherits this
# ownership on first use (Docker seeds an empty named volume from the image path).
RUN useradd --uid 10002 --create-home --shell /usr/sbin/nologin moxie \
 && mkdir -p /data \
 && chown -R moxie:moxie /data /app
USER moxie

ENV HOST=0.0.0.0 \
    PORT=8080 \
    MOXIE_DB=/data/moxie.db

EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=5s --start-period=15s --retries=6 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/local/broker/status',timeout=4).status==200 else 1)"

CMD ["python", "run.py"]
