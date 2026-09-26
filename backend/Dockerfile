# Edge backend + built admin dashboard. Build from the repo root:
#   docker build -f backend/Dockerfile -t parking-backend .
FROM node:22-slim AS dashboard
WORKDIR /dashboard
COPY dashboard/package*.json ./
RUN npm ci --no-audit --no-fund
COPY dashboard/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PARK_DASHBOARD_DIST=/app/dashboard/dist PARK_IMAGE_ROOT=/data/images PARK_UPLOAD_ROOT=/data/uploads
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-client curl tini \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app/backend
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ ./
COPY --from=dashboard /dashboard/dist /app/dashboard/dist
RUN useradd -r -u 1001 parking && mkdir -p /data/images /data/uploads && chown -R parking /data
USER parking
EXPOSE 8000
HEALTHCHECK --interval=20s --timeout=5s --retries=3 CMD curl -fs http://localhost:8000/api/health || exit 1
ENTRYPOINT ["/usr/bin/tini", "--"]
# single process on purpose: the WebSocket hub and the job scheduler live in this process
CMD ["sh", "-c", "alembic upgrade head && python -m app.seed && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --timeout-keep-alive 30"]
