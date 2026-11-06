# 07 — Despliegue en Linux con Docker

**Objetivo:** desplegar `ad-support-agent` en el servidor Ubuntu que ya tiene 2 aplicaciones con Docker, sin romperlas y reutilizando la red de contenedores existente.

> Estado: listo para desplegar. `active_directory.py` ya usa `ldap3` (sin PowerShell/RSAT), por lo que corre nativamente en Linux.

---

## 1. Opciones evaluadas

| Opción | Ventajas | Desventajas | Veredicto |
|--------|----------|-------------|-----------|
| **A) Dockerizar este proyecto** (recomendado) | Mismo patrón que las 2 apps existentes; aislamiento total; fácil de parar/borrar/actualizar; redes y volumes compartibles con Docker Compose. | Imagen extra (~150 MB). | ✅ **Elegida** |
| B) Instalar nativamente (venv + systemd + cron) | Sin overhead de contenedor. | Requiere instalar Python 3.14, ldap3, CA del DC, cron, gestionar dependencias a mano; no encaja con el estándar del servidor. | ❌ Descarte |

---

## 2. Requisitos previos en el servidor

1. **Docker y Docker Compose instalados** (ya lo están por las 2 apps existentes).
2. **Red Docker** compartida: si las otras apps usan una red personalizada (p. ej. `app-net`), agrégala a `docker-compose.yml` bajo `networks` o conecta el contenedor a ella con `docker network connect`.
3. **Puerto 3978 libre** (o cambiar el mapeo `3978:3978` en `docker-compose.yml`).
4. **Cuenta de servicio en AD** ya creada:
   - Usuario: `sittiad`
   - Contraseña: `S1tt1M3d3ll1n2025++`
   - Debe tener permisos de **lectura** y **desbloqueo** (`Reset password` delegado) sobre la OU de usuarios.
   - En `.env` se configura como `AD_BIND_USER=sittiad@unetrans.com.co`.
5. **CA del DC instalada** en el servidor (ver `docs/06-migracion-a-linux.md` §3.2), de lo contrario LDAPS falla con `Verify return code: 21`.

---

## 3. Pasos de despliegue

Desde el servidor Ubuntu, en la carpeta del proyecto:

```bash
# 1. Copiar el ejemplo de variables de entorno
cp .env.example .env

# 2. Editar .env con los valores reales (API_KEY, TENANT_ID, CLIENT_ID, CLIENT_SECRET, AD_APPROVAL_GROUP_ID)
nano .env

# 3. Asegurar que AD_BIND_USER y AD_BIND_PASSWORD usen la cuenta de servicio:
#    AD_BIND_USER=sittiad@unetrans.com.co
#    AD_BIND_PASSWORD=S1tt1M3d3ll1n2025++

# 4. (Opcional) Activar desbloqueo real solo cuando estés listo:
#    DAILY_UNLOCK_DRY_RUN=false
#    DAILY_UNLOCK_ENABLED=false   # se activa solo tras validar el dry-run

# 5. Construir y levantar
docker compose up -d --build

# 6. Verificar
docker compose ps
docker compose logs -f ad-support-agent
curl http://localhost:3978/health   # debe devolver {"status":"ok","service":"ad-support-agent"}
```

### Validar el desbloqueo programado (dry-run)

```bash
# Ejecutar la tarea manualmente en modo dry-run (no modifica AD)
docker compose exec ad-support-agent \
  DRY_RUN=true DAILY_UNLOCK_ENABLED=false \
  python /app/scripts/daily_ad_unlock.py

# Revisar el log
cat /app/logs/daily_unlock.log
```

### Activar desbloqueo real

Cuando hayas validado que el dry-run lista correctamente las cuentas bloqueadas:

```bash
# Editar .env
DAILY_UNLOCK_DRY_RUN=false
DAILY_UNLOCK_ENABLED=false   # mantener false hasta que estés 100% seguro

# Reiniciar para que cron tome los nuevos valores
docker compose restart ad-support-agent
```

> ⚠️ **Regla de seguridad:** mantén `DAILY_UNLOCK_ENABLED=false` hasta que hayas revisado manualmente el reporte del dry-run durante al menos 1 semana. Nunca pongas `true` sin haber validado primero.

---

## 4. Estructura de archivos generados para Docker

```
ad-support-agent/
├── Dockerfile                   # Imagen multi-stage (builder + runtime)
├── .dockerignore               # Qué excluir de la imagen
├── docker-compose.yml          # Orquestación (puerto 3978, volumes, red)
├── docker-entrypoint.sh        # Migraciones + cron + FastAPI
├── .env.example                # Plantilla de variables de entorno
├── scripts/daily_ad_unlock.py  # Tarea diaria de desbloqueo (dry-run por defecto)
├── app/services/active_directory.py  # list_locked_enabled_users() (LDAP masivo)
└── tests/test_active_directory_ldap.py  # pruebas de la nueva función
```

### `docker-entrypoint.sh`
1. Carga `.env`.
2. Valida variables obligatorias (`API_KEY`, `AD_BIND_USER`, etc.).
3. Ejecuta `alembic upgrade head`.
4. Configura cron para las 07:00 (hora del contenedor, `TZ=America/Bogota`).
5. Arranca `python -m app.main` bajo `tini` (init proces adecuado para contenedores).

### `scripts/daily_ad_unlock.py`
- Modo **dry-run por defecto**: solo lista cuentas bloqueadas, no toca AD.
- Para activar: `DRY_RUN=false DAILY_UNLOCK_ENABLED=false` (primero validar), luego `DAILY_UNLOCK_ENABLED=true`.
- Todo queda auditado en `logs/audit.jsonl` (acción `AUTO_UNLOCK`) y en `logs/daily_unlock.log`.

---

## 5. Integración con las 2 apps ya desplegadas

Si las otras aplicaciones usan una red Docker personalizada, agrega al final de `docker-compose.yml`:

```yaml
networks:
  ad-support-net:
    external:
      name: <nombre-de-la-red-existente>
```

O conecta el contenedor después de levantarlo:

```bash
docker network connect <red-existente> ad-support-agent
```

Si necesitas que las otras apps llamen a los endpoints de este servicio (p. ej. `GET /api/v1/users/{username}`), el host dentro de la red Docker es `ad-support-agent` y el puerto interno `3978`.

---

## 6. Logs y auditoría

| Ruta | Contenido |
|------|-----------|
| `/app/logs/audit.jsonl` | Eventos de auditoría (incluye `AUTO_UNLOCK` del desbloqueo programado). |
| `/app/logs/daily_unlock.log` | Log propio de la tarea diaria (inicio, fin, cuentas encontradas, desbloqueadas, fallos). |
| `/app/logs/cron.log` | Salida de cron (stdout/stderr del script diario). |
| `stdout` del contenedor | Logs de FastAPI y del worker de aprobaciones (cada 15 s). |

Rotación de logs: el contenedor usa `cron` y escribe a archivos; en producción se recomienda montar un volumen o usar `docker logs` con driver `json-file` y opciones `max-size`/`max-file`.

---

## 7. Migración de `.env` desde Windows

Si llevas el `.env` de Windows, ten en cuenta:

| Variable | Acción |
|----------|--------|
| `AD_BIND_USER` | Poner `sittiad@unetrans.com.co` |
| `AD_BIND_PASSWORD` | Poner `S1tt1M3d3ll1n2025++` |
| `SESSION_SECRET` | Eliminar (ya no existe; se retiró con la Mesa de Ayuda). |
| `HELPDESK_REDIRECT_URI` | Eliminar (solo para scripts antiguos de OAuth). |

---

## 8. Troubleshooting rápido

| Síntoma | Causa probable | Acción |
|---------|----------------|--------|
| `Falta AD_BIND_USER` | `.env` no cargado o variable vacía | Revisar `.env` y `docker compose config` |
| LDAPS `Verify return code: 21` | CA del DC no instalada en Ubuntu | Instalar CA (ver docs/06 §3.2) |
| `0 bloqueadas` en dry-run | Filtro LDAP no coincide o cuenta de servicio sin permisos de lectura | Validar `Get-ADUser` con `sittiad` y revisar OU base (`_base_dn()`) |
| Cron no ejecuta | `cron` no arrancó en el contenedor | Revisar `docker compose logs ad-support-agent` y asegurar que `service cron start` corra |
| Puerto 3978 ocupado | Otra app ya usa ese puerto | Cambiar mapeo en `docker-compose.yml` a `3979:3978` |

---

## 9. Próximos pasos después del despliegue

1. Ejecutar `docker compose up -d --build` y validar `/health`.
2. Correr `daily_ad_unlock.py` en dry-run durante 1 semana y revisar `daily_unlock.log`.
3. Cuando estés seguro, poner `DAILY_UNLOCK_ENABLED=false` + `DAILY_UNLOCK_DRY_RUN=false` y reiniciar.
4. Activar `DAILY_UNLOCK_ENABLED=true` solo cuando quieras desbloqueo automático real.
