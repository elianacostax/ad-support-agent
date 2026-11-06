#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# Entrypoint del contenedor AD Support Agent (Linux).
# Arranca:
#   1. Migraciones Alembic (upgrade head).
#   2. Cron para la tarea diaria de desbloqueo AD.
#   3. Servicio FastAPI/Uvicorn en el puerto 3978.
# ---------------------------------------------------------------------------

APP_DIR="/app"
cd "$APP_DIR"

echo "[ENTRYPOINT] Esperando a que las variables de entorno estén listas..."

# ---------------------------------------------------------------------------
# Instalar CA interna de Active Directory para LDAPS (si se montó el certificado)
# El certificado se espera en /app/certs/ca-simm-pdc1-ca.crt
# ---------------------------------------------------------------------------
if [ -f /app/certs/ca-simm-pdc1-ca.crt ]; then
    echo "[ENTRYPOINT] Instalando CA interna para LDAPS..."
    cp /app/certs/ca-simm-pdc1-ca.crt /usr/local/share/ca-certificates/ca-simm-pdc1-ca.crt
    update-ca-certificates
fi

if [ -f .env ]; then
    set -a
    . .env
    set +a
fi

# Variables obligatorias
: "${API_KEY:?Falta API_KEY en .env}"
: "${TENANT_ID:?Falta TENANT_ID en .env}"
: "${CLIENT_ID:?Falta CLIENT_ID en .env}"
: "${CLIENT_SECRET:?Falta CLIENT_SECRET en .env}"
: "${AD_APPROVAL_GROUP_ID:?Falta AD_APPROVAL_GROUP_ID en .env}"
: "${AD_BIND_USER:?Falta AD_BIND_USER en .env (cuenta de servicio LDAP)}"
: "${AD_BIND_PASSWORD:?Falta AD_BIND_PASSWORD en .env (cuenta de servicio LDAP)}"

echo "[ENTRYPOINT] Aplicando migraciones Alembic..."
alembic upgrade head

# ---------------------------------------------------------------------------
# Cron: tarea diaria de desbloqueo AD (07:00 hora del contenedor / TZ)
# ---------------------------------------------------------------------------
CRON_FILE="/etc/cron.d/daily-ad-unlock"

cat > "$CRON_FILE" <<EOF
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
AD_SUPPORT_DIR=/app
0 7 * * * root cd /app && DRY_RUN="\${DAILY_UNLOCK_DRY_RUN:-true}" DAILY_UNLOCK_ENABLED="\${DAILY_UNLOCK_ENABLED:-false}" /opt/venv/bin/python /app/scripts/daily_ad_unlock.py >> /app/logs/cron.log 2>&1
EOF

chmod 0644 "$CRON_FILE"
crontab "$CRON_FILE"

echo "[ENTRYPOINT] Iniciando cron..."
service cron start || cron || true

echo "[ENTRYPOINT] Iniciando servicio FastAPI en puerto 3978..."
exec python -m app.main
