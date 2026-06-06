# Oshinei Reconciliation App — FastAPI + SQLite, deployed on Fly.io
FROM python:3.12-slim

WORKDIR /app

# Install Python deps
COPY recon/requirements.txt /app/recon/requirements.txt
RUN pip install --no-cache-dir -r /app/recon/requirements.txt

# App code + frontend
COPY recon/ /app/recon/

# recon.db lives on the Fly volume, not in the image
ENV RECON_DB=/data/recon.db
ENV MAIN_API_URL=http://oshinei-dashboard.internal:8080
ENV PORT=8080
EXPOSE 8080

CMD ["sh", "-c", "uvicorn recon.main:app --host 0.0.0.0 --port ${PORT}"]
