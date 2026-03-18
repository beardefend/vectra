# Windows-based Dockerfile for Vectra - BearDefend

FROM python:3.12-slim-bookworm

LABEL org.opencontainers.image.title="Vectra"
LABEL org.opencontainers.image.description="Production-ready multi-tenant semantic search API"
LABEL org.opencontainers.image.vendor="BearDefend"

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py ./
COPY *.md ./
COPY static/ ./static/
COPY templates/ ./templates/

RUN mkdir -p /app/data/chroma /app/data/sqlite

ENV FRESHNESS_THRESHOLD_DAYS="7"
ENV SQLITE_DB_PATH="/app/data/sqlite/clients.db"
ENV CHROMADB_PATH="/app/data/chroma"
ENV PORT="8000"
ENV HOST="0.0.0.0"

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]