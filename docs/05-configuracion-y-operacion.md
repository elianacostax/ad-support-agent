# 05 — Configuración y operación

## 1. Variables de entorno (`.env`)

La app carga la configuración con `pydantic-settings` desde el archivo **`.env`** en la
raíz del proyecto (`app/config.py`, `extra="ignore"`).

### Obligatorias (sin valor por defecto → la app no arranca sin ellas)

| Variable | Propósito |
|---|---|
| `API_KEY` | Llave del header `X-API-Key` para `/api/v1/approvals/*`, `/api/v1/chat`, `/api/v1/users` |
| `TENANT_ID` | Tenant de Microsoft Entra ID |
| `CLIENT_ID` | Client id de la app Entra (MSAL) |
| `CLIENT_SECRET` | Secreto de la app Entra (client credentials / ROPC) |
| `AD_APPROVAL_GROUP_ID` | OID del **grupo de aprobadores** al que va la aprobación en Graph |

### Opcionales (con valores por defecto)

| Variable | Default | Propósito |
|---|---|---|
| `AD_DOMAIN` | `unetrans.com.co` | Dominio AD para `Get-ADUser` |
| `AD_SERVER` | `SIMM-PDC1.unetrans.com.co` | Controlador de dominio usado por PowerShell |
| `AD_APPROVERS` | `""` (**nadie aprueba**) | Lista separada por comas de usuarios aprobadores; ver reglas en [04-reglas-negocio.md](04-reglas-negocio.md#6-roles-y-autorización) |
| `APPROVAL_ACCOUNT` | `""` | Username de la cuenta técnica (caché silenciosa MSAL) |
| `APPROVAL_ACCOUNT_USERNAME` | `""` | Cuenta técnica para ROPC (fallback de token Graph) |
| `APPROVAL_ACCOUNT_PASSWORD` | `""` | Contraseña de la cuenta técnica |
| `HELPDESK_REDIRECT_URI` | `http://localhost:3978/helpdesk/auth/callback` | Solo la usa `scripts/service_account_login.py` (debe estar registrada en Entra) |

> **Observado en `.env` actual:** las claves del entorno de pruebas están definidas
> (`API_KEY`, `TENANT_ID`, `CLIENT_ID`, `CLIENT_SECRET`, `AD_APPROVERS`,
> `AD_APPROVAL_GROUP_ID`, `APPROVAL_ACCOUNT*`, `HELPDESK_REDIRECT_URI`);
> `AD_DOMAIN`/`AD_SERVER` usan el default. **`.env` contiene secretos reales: nunca
> subirlo a git ni compartirlo.** (La variable `SESSION_SECRET` fue retirada junto con
> la Mesa de Ayuda.)

### Previstas (migración a Linux, aún sin crear)

| Variable | Propósito |
|---|---|
| `AD_BIND_USER` | UPN de la cuenta de servicio LDAP (`svc_adsupport@unetrans.com.co`) |
| `AD_BIND_PASSWORD` | Contraseña de dicha cuenta |

Solo aplican tras migrar `active_directory.py` a `ldap3`. Ver
[06-migracion-a-linux.md](06-migracion-a-linux.md).

### Plantilla mínima

```dotenv
API_KEY=cambia-esta-llave
TENANT_ID=<tenant-guid>
CLIENT_ID=<client-guid>
CLIENT_SECRET=<secreto>
AD_APPROVERS=sacosta,susuga
AD_APPROVAL_GROUP_ID=<oid-del-grupo>
# Solo para scripts/service_account_login.py (URI registrada en Entra):
HELPDESK_REDIRECT_URI=http://localhost:3978/helpdesk/auth/callback
# AD_DOMAIN=unetrans.com.co
# AD_SERVER=SIMM-PDC1.unetrans.com.co
# APPROVAL_ACCOUNT=mesa.ayuda@unetrans.com.co
# APPROVAL_ACCOUNT_USERNAME=mesa.ayuda@unetrans.com.co
# APPROVAL_ACCOUNT_PASSWORD=***
```

## 2. Comandos principales

Todos desde la raíz del proyecto, en PowerShell:

```powershell
# --- Instalación (máquina nueva) ---
.venv\Scripts\python.exe -m pip install -r requirements.txt

# --- Base de datos ---
.venv\Scripts\python.exe -m alembic upgrade head        # aplicar migraciones
.venv\Scripts\python.exe -m alembic current             # revisión actual (fab551fff4d9)
.venv\Scripts\python.exe -m alembic history             # cadena completa

# --- Ejecutar el servicio (PUERTO 3978) ---
.venv\Scripts\python.exe -m app.main
```

> ⚠️ **Obligatorio arrancar con `-m app.main`**, no `python app/main.py`: las notificaciones
> del worker resuelven el loop real desde `sys.modules["__main__"]`; con un import normal
> se crearía un segundo módulo y se caería al fallback `asyncio.run` (ver
> [03-flujos.md](03-flujos.md#4-notificación-al-solicitante)).

### Pruebas

```powershell
# 32 pruebas unitarias/de API (tests/)
.venv\Scripts\python.exe -m pytest tests\ -q

# 52 checks de flujo integral (mockea AD/Graph)
.venv\Scripts\python.exe test_full_flow.py

# Cobertura combinada (branch; 43% tras la auditoría + eliminación de la Mesa de Ayuda)
.venv\Scripts\python.exe -m coverage run --branch --source=app -m pytest tests\
.venv\Scripts\python.exe -m coverage run --branch --source=app test_full_flow.py --append
.venv\Scripts\python.exe -m coverage report
```

### Calidad de código

```powershell
# Lint (0 hallazgos tras la auditoría); --fix aplica correcciones automáticas
& "$env:LOCALAPPDATA\Temp\opencode\secscan-venv\Scripts\python.exe" -m ruff check app tests --fix
```

> El entorno temporal de herramientas de seguridad (bandit, pip-audit, ruff) vive en
> `C:\Users\sacosta\AppData\Local\Temp\opencode\secscan-venv`.

## 3. Gestión del servicio

| Tarea | Comando |
|---|---|
| Detener | `powershell -ExecutionPolicy Bypass -File scripts\stop_service.ps1` |
| Verificar (PID, puerto 3978, `/health`) | `powershell -ExecutionPolicy Bypass -File scripts\check_service.ps1` |
| Arrancar de nuevo | `.venv\Scripts\python.exe -m app.main` (preferible en background) |

- El servicio escucha en **`0.0.0.0:3978`**; health check en `GET /health` (sin auth).
- Si la sesión donde corre el proceso termina, el servicio se cae: relanzarlo con el
  comando de arranque.
- El worker de aprobaciones corre dentro del mismo proceso (`asyncio.Task` iniciado en el
  lifespan de FastAPI) — no necesita proceso aparte.

## 4. Estructura de logs y artefactos

| Ruta | Contenido | Notas |
|---|---|---|
| `logs/audit.jsonl` | Eventos de auditoría (JSON Lines) | Append-only; **sin rotación** (pendiente FIND-007); nunca contiene contraseñas |
| stdout del proceso | Logs `[APPROVAL WORKER]`, warnings y errores | En terminal/background shell |
| `ad_support.db` | SQLite con `approval_requests` | Copiar para backup (ver [02-base-de-datos.md](02-base-de-datos.md#5-copias-de-seguridad)) |
| `data/msal_token_cache.bin` | Caché de tokens MSAL | Gitineado; no contiene secretos de usuario en claro |
| `.env` | Secretos | **No versionar** |

## 5. Runbook de incidentes

### 5.1 Servicio no responde

1. `check_service.ps1` → ¿proceso vivo? ¿`/health` responde?
2. Si no: `stop_service.ps1` (limpiar restos) → relanzar `.venv\Scripts\python.exe -m app.main`.
3. Revisar stdout: errores de import (`ImportError`) = código desplegado incompleto.

### 5.2 Solicitud `PENDING` sin procesar

Síntoma: el usuario no recibe notificación y el worker loguea
`no tiene graph_approval_id. Se omite.`

1. Buscar la fila: `SELECT id, status, graph_approval_id, action FROM approval_requests WHERE status='PENDING';`
2. **Sin `graph_approval_id`** (falló Graph): para `PASSWORD_RESET` se puede ejecutar un
   reset manual con `scripts/debug_execute_request.py`; para `UNLOCK_ACCOUNT`, desbloquear
   manualmente con la cuenta de servicio y cerrar la fila.
3. **Con `graph_approval_id` pero sin token**: verificar `APPROVAL_ACCOUNT*` en `.env` y
   probar el login con `scripts/service_account_login.py`.
4. Con token OK, el worker la tomará solo en el próximo ciclo (≤ 15 s).

### 5.3 Graph/Teams devuelve errores

- `429/5xx` en creación: los reintentos y la búsqueda por `Solicitud interna: #N` ya cubren
  la mayoría; si persiste, revisar cuota/cuenta técnica.
- Aprobación creada pero sin ID: `_find_approval_by_request_id` la recupera por descripción.

### 5.4 Reset falla en AD

- Causa típica: controlador AD inaccesible o cuenta deshabilitada → status `FAILED` con el
  detalle en `result`. La fila es la fuente de verdad; verificar con
  `scripts/debug_graph_approvals.py` (consulta Graph) o PowerShell `Get-ADUser`.

### 5.5 Auditoría / cumplimiento

- Historial de acciones: `logs/audit.jsonl` (filtros por `session_id`, `username`, `result`).
- Historial de solicitudes: tabla `approval_requests` (comentarios del aprobador incluidos).

## 6. Endpoints disponibles (tras auditoría 2026-10-09 y eliminación de la Mesa de Ayuda)

| Método y ruta | Auth | Descripción |
|---|---|---|
| `GET /health` | — | Salud del servicio |
| `POST /api/messages` | JWT Bot Framework (SDK) | Canal de Teams |
| `GET /api/v1/users/{username}` | `X-API-Key` | Consulta de usuario en AD |
| `POST /api/v1/chat` | `X-API-Key` | Conversación vía API |
| `GET /api/v1/approvals/pending` | `X-API-Key` | Listado PENDING |
| `POST /api/v1/approvals/{id}/approve` | `X-API-Key` + aprobador | Aprobar |
| `POST /api/v1/approvals/{id}/reject` | `X-API-Key` + aprobador | Rechazar |

> **Eliminados (2026-10-09):** los 6 endpoints `/api/v1/debug/*` de la auditoría y toda la
> **Mesa de Ayuda** (2026-10-09): `/helpdesk/auth/*`, `/api/v1/helpdesk/approvals/*` y los
> estáticos `/helpdesk/` — hoy responden **404**. Las operaciones de recuperación se hacen
> con los scripts de `scripts/`. Pendientes restantes: ver [AUDITORIA.md](../AUDITORIA.md).
