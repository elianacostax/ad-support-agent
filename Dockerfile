# syntax=docker/dockerfile:1
FROM python:3.14-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# Dependencias del sistema para ldap3 + TLS (LDAPS)
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        openssl \
        libldap-common \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install -r requirements.txt

# ---------------------------------------------------------------------------
# Imagen final (runtime)
# ---------------------------------------------------------------------------
FROM python:3.14-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# CA-certificates para validar el certificado del DC contra LDAPS
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        openssl \
        libldap-common \
        cron \
        tini \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv

# Código de la aplicación
COPY app ./app
COPY scripts ./scripts
COPY alembic ./alembic
COPY alembic.ini .
COPY README.md ./

# Directorio de datos y logs
RUN mkdir -p /app/data /app/logs

# Entrypoint (cron + servicio FastAPI)
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# Puerto expuesto por FastAPI/Uvicorn
EXPOSE 3978

# Health-check: el contenedor está vivo si /health responde
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:3978/health', timeout=3)" || exit 1

ENTRYPOINT ["tini", "--", "/usr/local/bin/docker-entrypoint.sh"]
