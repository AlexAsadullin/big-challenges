# 3.11: faiss-cpu 1.10 needs numpy.distutils on ARM64, which is gone in Python 3.12
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
# CPU-only PyTorch first: the default Linux wheel pulls in several GB of CUDA libraries
RUN pip install torch==2.5.1 --index-url https://download.pytorch.org/whl/cpu \
    && pip install -r requirements.txt

COPY src ./src
COPY web ./web
COPY scripts ./scripts
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh

# All state lives in /data: SQLite database, books.csv, FAISS index, downloaded models
RUN useradd --create-home --uid 1000 app \
    && mkdir /data && chown app:app /data \
    && chmod +x /usr/local/bin/entrypoint.sh
USER app
ENV DB_PATH=/data/books.db \
    CSV_PATH=/data/books.csv \
    INDEX_PATH=/data/faiss_index.index \
    HF_HOME=/data/huggingface \
    PYTHONPATH=/app
# rag.py caches models in ./models, so run from the data volume
WORKDIR /data
VOLUME ["/data"]
EXPOSE 8000

ENTRYPOINT ["entrypoint.sh"]
# Hosts like Render pass the port to listen on in PORT
CMD ["sh", "-c", "exec uvicorn web.app:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers"]
