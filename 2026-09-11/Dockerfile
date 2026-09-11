# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies in their own layer so `docker build` only reinstalls them
# when requirements.txt actually changes, not on every code edit.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY solution.py .

# Runs as a non-root user -- a container escape shouldn't hand over root.
RUN useradd --create-home --uid 1000 appuser
USER appuser

EXPOSE 8000

# OPENAI_API_KEY is intentionally NOT set here -- it's injected at
# `docker run` time (or by the orchestrator/Secrets Manager in
# terraform/), never baked into the image.

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz', timeout=2)" || exit 1

CMD ["uvicorn", "solution:app", "--host", "0.0.0.0", "--port", "8000"]
