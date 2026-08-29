# Piper TTS service for the Moxie SIL. Voices are NOT baked in (63MB each) —
# mount sim/tts/voices/ at runtime (see sim/README.md for the download command).
FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir piper-tts
COPY server.py .
ENV MOXIE_PIPER_PY=/usr/local/bin/python
EXPOSE 8081
CMD ["python", "server.py", "8081"]
