# Oshinei Dashboard — FastAPI + SQLite, deployed on Fly.io
FROM python:3.12-slim

# System libs needed by matplotlib/reportlab (fonts, freetype, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
        libfreetype6 libjpeg62-turbo libpng16-16 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (better layer caching)
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

# App code + frontend assets (db is NOT copied — it lives on the volume)
COPY backend/ /app/backend/
COPY index.html /app/index.html
COPY vendor/ /app/vendor/
COPY docs/ /app/docs/

# The SQLite DB lives on a persistent Fly volume mounted at /data
ENV OSHINEI_DB=/data/oshinei.db
ENV PORT=8080
EXPOSE 8080

CMD ["sh", "-c", "uvicorn backend.main:app --host 0.0.0.0 --port ${PORT}"]
