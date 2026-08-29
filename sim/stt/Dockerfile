# faster-whisper STT service for the Moxie SIL. The model downloads on first
# request and is cached in /root/.cache (mount a volume to persist it).
FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir faster-whisper
COPY server.py .
ENV MOXIE_STT_MODEL=base.en
EXPOSE 8082
CMD ["python", "server.py", "8082"]
