# Cloud relay + customer self-service pages. Build from the repo root or this folder:
#   docker build -t parking-relay customer_web
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    RELAY_DATABASE_URL=sqlite:////data/relay.db

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt && useradd --system --uid 10001 --home /app relay && mkdir /data && chown relay /data
COPY relay ./relay

USER relay
VOLUME ["/data"]
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=4).status == 200 else 1)"
# RELAY_WORKERS=1 for demo mode (the mock gateway keeps its state in memory); 2-4 with Razorpay
ENV RELAY_WORKERS=1
CMD exec uvicorn relay.main:app --host 0.0.0.0 --port 8080 --proxy-headers --forwarded-allow-ips '*' \
    --workers "$RELAY_WORKERS"
