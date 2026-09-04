
FROM nvidia/cuda:12.8.1-cudnn-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    C_FORCE_ROOT=1


RUN apt-get update && apt-get install -y --no-install-recommends \
        software-properties-common \
        curl \
        gnupg2 \
        ca-certificates \
        unixodbc-dev \
        build-essential \
        libgl1 \
        libglib2.0-0 \
        libmagic1 \
        poppler-utils \
    && add-apt-repository -y ppa:deadsnakes/ppa \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        python3.11 \
        python3.11-venv \
        python3.11-dev \
    && python3.11 -m ensurepip --upgrade \
    && mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc \
       | gpg --dearmor -o /etc/apt/keyrings/microsoft.gpg \
    && echo "deb [signed-by=/etc/apt/keyrings/microsoft.gpg] https://packages.microsoft.com/ubuntu/22.04/prod jammy main" \
       > /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements-gpu.txt .

RUN sed -i '/^onnxruntime==/d' requirements-gpu.txt \
    && echo "onnxruntime pins rimasti:" \
    && grep -i "^onnxruntime" requirements-gpu.txt

RUN python3.11 -m pip install --no-cache-dir --upgrade pip \
    && python3.11 -m pip install --no-cache-dir --no-deps -r requirements-gpu.txt

COPY app/ ./app/
COPY config/ ./config/

CMD ["python3.11", "-m", "celery", "-A", "app.workers.celery_app.celery_app", "worker", \
     "--loglevel=info", "--concurrency=2"]


     