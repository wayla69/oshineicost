# Oshinei Dashboard + Reconciliation — FastAPI + SQLite, deployed on Fly.io
FROM python:3.12-slim

# System libs needed by matplotlib/reportlab, plus Tesseract (+ Thai language
# pack) for the OCR fallback that reads no-text-layer KTB statement PDFs.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libfreetype6 libjpeg62-turbo libpng16-16 \
        tesseract-ocr tesseract-ocr-tha \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install deps for both services (better layer caching)
COPY backend/requirements.txt /app/backend/requirements.txt
COPY recon/requirements.txt   /app/recon/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt \
 && pip install --no-cache-dir -r /app/recon/requirements.txt

# App code — db lives on the Fly volume, not in the image
COPY backend/ /app/backend/
COPY recon/   /app/recon/
COPY index.html /app/index.html
COPY js/        /app/js/
COPY tabs/      /app/tabs/
COPY vendor/    /app/vendor/
COPY docs/      /app/docs/
COPY start.sh   /app/start.sh
RUN chmod +x /app/start.sh

ENV OSHINEI_DB=/data/oshinei.db
ENV PORT=8080
EXPOSE 8080 8001

# One machine runs both apps (dashboard :8080, recon :8001) so they share the
# single oshinei_data volume. See start.sh.
CMD ["/app/start.sh"]
