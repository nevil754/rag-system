# Variante GPU di docker/fastapi.Dockerfile per il server2 (con NVIDIA GPU).
# Richiede sull'host: driver NVIDIA + NVIDIA Container Toolkit (vedi README3.2.md §6).
#
# Nota (README3.2.md §5): niente multi-stage builder(Debian)/runtime(Ubuntu) come nel Dockerfile
# CPU, perche' copiare site-packages compilati (torch, onnxruntime-gpu) tra due distro diverse e'
# a rischio ABI/glibc. Qui si builda e si gira sulla STESSA immagine NVIDIA/Ubuntu, in singolo stage.
#
# La coppia CUDA/cuDNN dell'immagine base deve combaciare con: il +cu128 di torch/torchvision e la
# versione di onnxruntime-gpu in requirements-gpu.txt (oggi: torch+cu128, onnxruntime-gpu==1.29.0,
# nvidia-cudnn-cu12==9.19.0.56). Verifica il tag esatto disponibile su hub.docker.com/r/nvidia/cuda/tags
# prima del build: se "12.8.1-cudnn-runtime-ubuntu22.04" non esiste, usa il tag "*-cudnn-runtime-ubuntu22.04"
# piu' vicino alla 12.8 pubblicato da NVIDIA.
FROM nvidia/cuda:12.8.1-cudnn-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONFAULTHANDLER=1 \
    PIP_NO_CACHE_DIR=1

# Python 3.11 (non nei repo standard di Ubuntu 22.04 -> deadsnakes) + driver ODBC per SQL Server,
# stessi pacchetti apt del Dockerfile CPU (docker/fastapi.Dockerfile).
RUN apt-get update && apt-get install -y --no-install-recommends \
        software-properties-common \
        curl \
        gnupg2 \
        ca-certificates \
        unixodbc-dev \
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

COPY config/ ./config/
COPY main.py .
COPY app/ ./app/

RUN addgroup --system appgroup \
    && adduser --system --ingroup appgroup appuser \
    && mkdir -p /app/.cache/embeddings \
    && chown -R appuser:appgroup /app

USER appuser

EXPOSE 8000

CMD ["python3.11", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "1", "--loop", "uvloop", "--http", "httptools"]
