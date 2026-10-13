# Auditoría ad-support-agent — Informe consolidado

**Fecha:** 2026-10-09
**Alcance:** validación + limpieza completa (seguridad, código, pruebas)
**Herramientas:** bandit 1.9.4, pip-audit 2.10.1, ruff, coverage.py, pytest, revisión manual

---

## 1. Resumen ejecutivo

| Área | Hallazgos | Estado |
|---|---|---|
| 🔒 Seguridad | 1 HIGH · 2 MEDIUM · 4 LOW · 4 INFO | HIGH y MEDIUM críticos **cerrados** |
| 🧹 Código | 1 HIGH · 3 MEDIO · 4 BAJO | HIGH + limpieza **cerrados** |
| 🧪 Pruebas | Cobertura 35% → 51% (auditoría) → **43%** actual | 32 tests, todos OK |
| 🧩 Alcance | **Mesa de Ayuda eliminada** (2026-10-09, decisión del usuario) | OAuth + frontend + 2 routers fuera; CR-002 y FIND-002 cerrados |

**Verificaciones finales (post-eliminación de la Mesa de Ayuda):** ruff: solo FIND-005
(15) · 32/32 pytest · 52/52 suite integral · servicio activo (puerto 3978) · endpoints
debug **y helpdesk** → 404 · API protegida → 401 · dependencias sin CVEs conocidos.

---

## 2. Seguridad

### Cerrados en esta auditoría
- **FIND-001 (HIGH)** — `POST /api/v1/debug/execute-approval/{id}` no tenía autenticación
  y ejecutaba acciones en AD accesible desde toda la red. **Eliminado el endpoint** (junto
  con los otros 5 debug: 298 líneas). Verificado: ahora 404.
- **FIND-003 (MEDIUM)** — los 6 endpoints debug solo exigían sesión de cualquier usuario
  (incluía crear aprobaciones reales en Teams). **Eliminados.**
- **FIND-002 (MEDIUM)** — cookie de sesión con `https_only=False`: **cerrado por
  eliminación** — la Mesa de Ayuda y su `SessionMiddleware` se retiraron el 2026-10-09,
  ya no existe cookie de sesión en la app.

### Pendientes (no críticos)
| ID | Sev. | Hallazgo | Recomendación |
|---|---|---|---|
| FIND-004 | LOW | API key comparada con `!=` (`auth.py:15`) | `secrets.compare_digest` |
| FIND-005 | LOW | `except Exception` sin log ×15 según ruff (BLE001+S110): notificaciones APIs, caché MSAL, handler Teams, AD, worker | agregar `logger.warning` antes del `pass` |
| FIND-006 | LOW | Subprocess AD hereda **todo** el entorno (`CLIENT_SECRET`, etc.) | env mínimo: solo `AD_TARGET_USER`, `AD_SERVER`, `AD_TEMP_PASSWORD` |
| FIND-007 | LOW | `logs/audit.jsonl` sin rotación | rotación por tamaño |
| INFO | — | caché MSAL con refresh token en claro (`data/`); servidor en `0.0.0.0`; auth del bot delegada al SDK; `.env` con secretos (ACL restringida ✓) | rotación de secretos / Key Vault; firewall interno |
| INFO | — | **Riesgo aceptado (2026-10-09):** contraseña temporal **fija** `Temporal$2026*` compartida entre todas las cuentas reseteadas — decisión del usuario para simplificar su comunicación (constante `TEMP_PASSWORD` en `active_directory.py`) | mitigación activa: `ChangePasswordAtLogon` obligatorio; para revertir, restaurar generación con `secrets` |

### Validado como BIEN ✅
- Sin inyección PowerShell: username validado `^[a-zA-Z0-9_-]+$` y pasado por **variable de entorno**
- Contraseña temporal: **fija `Temporal$2026*`** (decisión del usuario 2026-10-09;
  antes se generaba con `secrets`), pasada por env var, **cero apariciones en logs/audit**
- pip-audit: sin vulnerabilidades; ORM sin SQL crudo (el frontend con `escapeHtml()`/`textContent`
  fue retirado con la Mesa de Ayuda)
- `.env` en `.gitignore` con ACL restringida (SYSTEM/Admin/sacosta)

---

## 3. Código

### Cerrados en esta auditoría
- **CR-001 (HIGH)** — sin manifiesto de dependencias → creado **`requirements.txt`** (50 paquetes).
- **CR-008** — ruff: 9 hallazgos → **0** (imports duplicados/sin usar, variables muertas, sets duplicados).
- **Limpieza** — eliminados: 5 backups (`main.py.backup`, `main.py.before-approvals`,
  `agent.py.before-approval`, `approval_service.py.backup`, `approvals.py.backup`),
  `services/llm.py` (muerto), `db/models.py` (huérfano; `alembic/env.py` redirigido al modelo real),
  `services/microsoft_graph.py` (quedó huérfano al eliminar los debug), `.ruff_cache/`.
  Movidos a `scripts/`: `test_graph_app.py` → `debug_graph_approvals.py`,
  `test_execute.py` → `debug_execute_request.py` (renombrados para que pytest no los recolecte
  — `test_execute.py` ejecutaba acciones al importarse).
- **CR-002 (MEDIO)** — **cerrado por eliminación de la Mesa de Ayuda** (2026-10-09):
  desapareció `helpdesk_approvals.py` y con ella las ~80 líneas duplicadas con `approvals.py`.
- **.gitignore** ampliado: `.ruff_cache/`, `.coverage`.

### Pendientes
| ID | Sev. | Hallazgo | Recomendación |
|---|---|---|---|
| CR-003 | MEDIO | `approvals.py` sin `response_model` → expone `temp_password`, `conversation_id`, etc. (probado con `jsonable_encoder`; la mitad helpdesk cerró con la eliminación) | `response_model=ApprovalResponse` (+campo explícito si se desea) |
| CR-004 | MEDIO | 20 funciones ≥50 líneas: `_handle_confirmation` (251), `process_pending_approvals` (213), `process` (144) | dividir en funciones pequeñas |
| CR-005 | BAJO | `SessionManager.remove_session` nunca se llama → sesiones sin TTL | TTL o limpieza al cerrar conversación |
| CR-006 | BAJO | `@app.on_event` deprecado (`main.py:140,157`) | migrar a `lifespan` |
| CR-007 | BAJO | `datetime.utcnow` deprecado (`models/approval_request.py`) | `datetime.now(timezone.utc)` |

---

## 4. Pruebas

### Ejecutado
- **Cobertura (branch, combinado pytest + suite integral): 35% → 51% → 43% actual**
  (la baja es recomposición: los módulos helpdesk eliminados estaban muy cubiertos)
- Suite `tests/` — **32 tests pytest, todos pasan** (eran 41; se retiraron los 9 del helpdesk):
  - `test_security_gates.py` — API key (401/403) y `is_ad_approver` → 100% esas unidades
  - `test_username_validation.py` — 18 intentos de inyección PowerShell rechazados
  - `test_message_builders.py` — completion incluye temporal + "cambiarla al iniciar sesión";
    desbloqueo no menciona password; FAILED explica error; rechazo por tipo
  - `test_api_approvals.py` — **regresión**: aprobar/rechazar por API notifica al solicitante;
    fallo de notificación no rompe la API; 401/403/404/409/502
- Suite integral `test_full_flow.py`: **52/52 OK**

### Cobertura por módulo (combinada, después)
100%: `auth`, `authorization`, `config`, `schemas`, `models` · 85% `approvals` ·
56% `teams_approval_service` · 35% `main` · 22% `active_directory` · 17% `microsoft_auth` ·
**baja:** `agent` 6%, `approval_service` 7%, `intent` 16%

### Pendientes
| ID | Sev. | Hallazgo | Recomendación |
|---|---|---|---|
| T-001 | MEDIO | Los tests comparten `ad_support.db` con el **servicio vivo** (worker cada 15s podría procesar filas `test-flow-*`) | BD de tests separada o worker excluye `test-flow-%` |
| T-002 | MEDIO | `handle_teams_message` sin cubrir (el callback OAuth fue eliminado con la Mesa de Ayuda) | test con `activity` simulada |
| T-003 | MEDIO | Subprocess AD sin test (solo `_validate_username`) | inyectar runner de PowerShell y mockearlo |
| T-004 | BAJO | `test_full_flow.py` es script plano, no pytest | migrar a pytest |
| T-005 | BAJO | Sin CI | pipeline (pytest + ruff) |
| T-006 | — | ~~Frontend sin E2E~~ | **N/A**: frontend eliminado con la Mesa de Ayuda (2026-10-09) |

---

## 5. Acciones ejecutadas (checklist)

- [x] Eliminados los 6 endpoints `/api/v1/debug/*` (298 líneas) + `microsoft_graph.py` huérfano
- [x] Eliminados 5 archivos backup, `llm.py`, `db/models.py`, `.ruff_cache/`
- [x] `alembic/env.py` redirigido al modelo real
- [x] ruff `--fix`: 10 correcciones → 0 hallazgos (F/E9/B/C4)
- [x] `requirements.txt` generado (`pip freeze`)
- [x] `.gitignore` ampliado (`.ruff_cache/`, `.coverage`)
- [x] 41 tests nuevos en `tests/` (puertas de seguridad, inyección, notificación, builders)
  — hoy **32** tras retirar los 9 del helpdesk
- [x] Cobertura medida: 35% → 51% → 43% actual (recomposición tras eliminaciones)
- [x] Servicio reiniciado con el código nuevo (puerto 3978, worker 15s)
- [x] Verificación post-cambio: `/health` 200 · debug 404 · `/approvals/pending` sin key 401
- [x] **Mesa de Ayuda + Entra eliminada** (2026-10-09): routers `helpdesk_auth` y
  `helpdesk_approvals`, servicio `helpdesk_auth`, `graph_token_store` (código muerto —
  `get_token` sin consumidores), `frontend/`, `SessionMiddleware`, funciones
  `get_authorization_url`/`acquire_token_by_code`, campos `session_secret` y
  `helpdesk_redirect_uri` de `config.py`, `SESSION_SECRET` de `.env` y 9 tests.
  Respaldo en `%TEMP%\opencode\removed-helpdesk\`. Cierra **CR-002** y **FIND-002**.
- [x] ruff ampliado (BLE/S110/I001/ISC/SIM): 19 auto + 6 manuales corregidos →
  solo queda **FIND-005** (15 hallazgos de except sin log)
- [x] Verificación post-eliminación: `/health` 200 · `/helpdesk/*` 404 ·
  `/api/v1/helpdesk/*` 404 · `approvals` sin key 401 · 32/32 pytest · 52/52 integral

## 6. Estado del servicio

- Corriendo en segundo plano de esta sesión:
  `.venv\Scripts\python.exe -m app.main` (desde la carpeta del proyecto)
- Helpers: `scripts\stop_service.ps1`, `scripts\check_service.ps1`
- Si la sesión termina, relanzar desde la carpeta del proyecto con
  `.venv\Scripts\python.exe -m app.main`
- **Importante:** arrancar siempre con `-m app.main` (el worker resuelve el loop real
  desde `__main__`; un import normal rompe las notificaciones)
