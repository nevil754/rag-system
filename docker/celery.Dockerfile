
FROM python:3.11-slim-bookworm AS builder
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gnupg2 \
    ca-certificates \
    unixodbc-dev \
    build-essential \
    && mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc \
       | gpg --dearmor -o /etc/apt/keyrings/microsoft.gpg \
    && echo "deb [signed-by=/etc/apt/keyrings/microsoft.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" \
       > /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir --no-deps -r requirements.txt


#carica models in fase di build di questa img, ma cosi i models finiscono nell'img invece io li voglio in volume docker apposito(cosi persistono anche agli shutdowns)! quindi ora verranno caricati al primo utilizzo di env var HF-HOME  
#ora uso intfloat/multilingual-e5-large invece di BAAI/bge-m3, xk questo fastembed (v0.8.0) non supporta BAAI/bg3-m3
# RUN python - <<'PY'
# from fastembed import TextEmbedding
# from fastembed import SparseTextEmbedding
# from fastembed.rerank.cross_encoder import TextCrossEncoder
# TextEmbedding("intfloat/multilingual-e5-large")  
# SparseTextEmbedding("prithivida/Splade_PP_en_v1")
# TextCrossEncoder("BAAI/bge-reranker-base")
# print("All AI models downloaded.")
# PY


FROM python:3.11-slim-bookworm AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
    unixodbc \
    curl \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /usr/lib/x86_64-linux-gnu/libodbc* /usr/lib/x86_64-linux-gnu/
COPY --from=builder /opt/microsoft /opt/microsoft
COPY --from=builder /etc/odbcinst.ini /etc/odbcinst.ini
COPY --from=builder /usr/local/lib/python3.11 /usr/local/lib/python3.11
COPY --from=builder /usr/local/bin /usr/local/bin
#COPY --from=builder /root/.cache /root/.cache  non piu necessatio ora ho il volume apposta x i models scaricati da huggingface

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    C_FORCE_ROOT=1

WORKDIR /app
COPY app/ ./app/
COPY config/ ./config/

CMD ["celery", "-A", "app.workers.celery_app.celery_app", "worker", \
     "--loglevel=info", "--concurrency=2"]

