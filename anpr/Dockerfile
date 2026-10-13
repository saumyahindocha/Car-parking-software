# ANPR edge service image.
#
# GPU (default, edge server with NVIDIA driver + nvidia-container-toolkit):
#   docker build -t anpr-service anpr/
#   docker run --gpus all -e BACKEND_URL=http://backend:8000 -e ANPR_API_KEY=... \
#     -v /srv/anpr/site.yaml:/config/site.yaml:ro -v /srv/images:/data/images -v /srv/anpr:/data/anpr \
#     -v /srv/models:/models:ro anpr-service
#
# CPU-only variant (laptop demo / CI; classical or commercial recogniser, or ONNX on CPU):
#   docker build -t anpr-service:cpu --build-arg BASE_IMAGE=ubuntu:24.04 --build-arg ONNX_PACKAGE=onnxruntime anpr/
#
# The OpenCV wheel bundles an LGPL FFmpeg build for RTSP/H.264/H.265 decoding.
# GStreamer / NVDEC pipelines need an OpenCV built from source with
# -DWITH_GSTREAMER=ON (see docs/ANPR.md, licence table).
ARG BASE_IMAGE=nvidia/cuda:12.6.3-cudnn-runtime-ubuntu24.04
FROM ${BASE_IMAGE}

ARG ONNX_PACKAGE=onnxruntime-gpu
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Kolkata \
    IMAGE_ROOT=/data/images \
    ANPR_OUTBOX=/data/anpr/outbox.sqlite \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH

RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-venv python3-pip tzdata ca-certificates \
      libglib2.0-0 libgomp1 \
 && ln -fs /usr/share/zoneinfo/${TZ} /etc/localtime \
 && rm -rf /var/lib/apt/lists/* \
 && python3 -m venv ${VIRTUAL_ENV}

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt "${ONNX_PACKAGE}>=1.17" scipy

COPY anpr_service ./anpr_service
COPY config ./config
COPY models ./models
COPY tools ./tools

RUN useradd --system --uid 10001 --home /app anpr \
 && mkdir -p /data/images /data/anpr \
 && chown -R anpr /data
USER anpr

VOLUME ["/data/images", "/data/anpr"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD python3 -c "import sqlite3,os; sqlite3.connect(os.environ['ANPR_OUTBOX']).execute('select count(*) from outbox')" || exit 1

ENTRYPOINT ["python3", "-m", "anpr_service"]
CMD ["run", "--config", "/config/site.yaml"]
