# VaultX backend (FastAPI). Build vanuit de repo-root:
#   docker build -f docker/backend.Dockerfile -t vaultx-backend .
FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN groupadd --system --gid 10001 vaultx \
 && useradd --system --uid 10001 --gid vaultx --home-dir /app --shell /usr/sbin/nologin vaultx

WORKDIR /app
COPY backend/requirements.txt .
RUN pip install -r requirements.txt

COPY backend/alembic.ini ./
COPY backend/alembic ./alembic
COPY backend/app ./app

USER vaultx
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status == 200 else 1)"

# Proxy-headers enkel vertrouwen van de interne nginx (zet VAULTX_FORWARDED_ALLOW_IPS).
ENV VAULTX_FORWARDED_ALLOW_IPS=127.0.0.1
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips \"$VAULTX_FORWARDED_ALLOW_IPS\" --no-server-header"]
