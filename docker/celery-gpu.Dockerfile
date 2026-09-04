# Variante GPU di docker/celery.Dockerfile per il server2 (con NVIDIA GPU).
# Richiede sull'host: driver NVIDIA + NVIDIA Container Toolkit (vedi README3.2.md §6).
# Stesse note del fastapi-gpu.Dockerfile: singolo stage sulla stessa immagine NVIDIA/Ubuntu
# (niente builder Debian + runtime Ubuntu, rischio ABI/glibc su torch/onnxruntime-gpu compilati).
FROM nvidia/cuda:12.8.1-cudnn-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    C_FORCE_ROOT=1

# Python 3.11 (deadsnakes) + driver ODBC per SQL Server + build-essential (compilazione dipendenze
# native) + librerie runtime per parsing documenti (docling/unstructured), stessi pacchetti apt del
# Dockerfile CPU (docker/celery.Dockerfile).
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
# Rimuove l'eventuale onnxruntime CPU che unstructured/unstructured-inference potrebbero tirare come
# dipendenza transitiva se requirements-gpu.txt viene rigenerato in futuro: le due distribuzioni
# installerebbero entrambe in site-packages/onnxruntime in modo order-dipendente (README3.2.md §1.1).
# Tenere solo onnxruntime-gpu.
RUN sed -i '/^onnxruntime==/d' requirements-gpu.txt \
    && echo "onnxruntime pins rimasti:" \
    && grep -i "^onnxruntime" requirements-gpu.txt

RUN python3.11 -m pip install --no-cache-dir --upgrade pip \
    && python3.11 -m pip install --no-cache-dir --no-deps -r requirements-gpu.txt

COPY app/ ./app/
COPY config/ ./config/

CMD ["python3.11", "-m", "celery", "-A", "app.workers.celery_app.celery_app", "worker", \
     "--loglevel=info", "--concurrency=2"]
