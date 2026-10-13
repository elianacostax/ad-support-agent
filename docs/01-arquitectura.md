# 01 — Arquitectura

## 1. Visión general

```
                 ┌────────────────────┐
  Usuario ──────▶│  Microsoft Teams   │
  (mensaje)      │  (bot Framework)   │
                 └─────────┬──────────┘
                           │ POST /api/messages (JWT del SDK)
                 ┌─────────▼──────────────────────────────────┐
                 │  FastAPI (app/main.py, puerto 3978)        │
                 │                                            │
                 │  • handle_teams_message → ADSupportAgent   │
                 │  • API /api/v1/approvals  (X-API-Key)      │
                 │  • Worker asyncio (cada 15 s)               │
                 └───────┬──────────────────────┬──────────────┘
                         │                      │
        ┌────────────────▼───────┐   ┌──────────▼───────────────┐
        │ SQLite ad_support.db   │   │ Microsoft Graph (beta)   │
        │ (approval_requests)    │   │ solutions/approval       │
        └────────────────────────┘   │ approvalItems            │
                                     └──────────┬───────────────┘
                                                │ aprobación en Teams
                                     ┌──────────▼───────────────┐
                                     │ Grupo de aprobadores     │
                                     │ (AD_APPROVAL_GROUP_ID)   │
                                     └──────────────────────────┘

        Cualquier ejecución sobre AD:
        Python → subprocess PowerShell (módulo ActiveDirectory)
                 variables de entorno AD_TARGET_USER/AD_SERVER/AD_TEMP_PASSWORD
```

**Ciclo de vida de una solicitud:** el usuario escribe en Teams → el agente detecta la
intención, extrae el usuario y consulta AD → pide confirmación → crea la solicitud en BD
(**PENDING**) y la aprobación en Graph → el **worker** (cada 15 s) sincroniza el estado →
al aprobarse ejecuta en AD → **notifica al solicitante en Teams** con el resultado (y la
contraseña temporal, si aplica).

## 2. Stack tecnológico

| Capa | Tecnología | Versión instalada |
|---|---|---|
| Lenguaje | Python | 3.14.7 |
| Web/API | FastAPI + Uvicorn | 0.142.2 / 0.54.0 |
| ORM | SQLAlchemy (estilo 2.x `Mapped[]`) | 2.1.3 |
| Base de datos | SQLite (`ad_support.db`) | nativa |
| Migraciones | Alembic | 1.20.0 |
| Bot de Teams | SDK `microsoft_teams` (`App`, `FastAPIAdapter`) | 2.1.0 |
| Identidad Microsoft | MSAL (silent + ROPC + client credentials; auth code solo en `scripts/`) | 1.39.0 |
| API Microsoft | Graph **beta** `solutions/approval/approvalItems` | — |
| Active Directory | PowerShell + módulo `ActiveDirectory` vía `subprocess` | — |
| Validación/config | Pydantic v2 + `pydantic-settings` (lee `.env`) | 2.13.5 |
| Pruebas | pytest + coverage (+ suite script `test_full_flow.py`) | 9.1.1 / 7.16.2 |

> **Única dependencia de Windows:** `app/services/active_directory.py` (3 funciones →
> `subprocess.run(["powershell.exe", ...])`). Todo lo demás es Python puro multiplataforma.
> La migración a Ubuntu está en validación y se documenta en
> [06-migracion-a-linux.md](06-migracion-a-linux.md).

## 3. Estructura del proyecto

```
ad-support-agent/
├── app/
│   ├── main.py                 # App FastAPI, rutas Teams, arranque del worker
│   ├── auth.py                 # verify_api_key (header X-API-Key)
│   ├── config.py               # Settings → variables de entorno/.env
│   ├── db/
│   │   ├── database.py         # engine SQLite, Base, SessionLocal
│   │   └── models.py           # (eliminado en la auditoría; ver alembic/env.py)
│   ├── models/
│   │   └── approval_request.py # Modelo ORM ApprovalRequest
│   ├── schemas/
│   │   └── approval.py         # ApprovalResponse, ApprovalActionRequest, ApprovalRejectRequest
│   ├── api/v1/
│   │   └── approvals.py        # API con X-API-Key: pending / approve / reject
│   └── services/
│       ├── intent.py           # detect_intent + extract_username + stopwords
│       ├── agent.py            # ADSupportAgent: máquina de estados de la conversación
│       ├── approval_service.py # CRUD + fallback + sync Graph + ejecución
│       ├── approval_worker.py  # Worker 15 s + constructores de mensajes + notificación
│       ├── teams_approval_service.py # Cliente Graph approvalItems (con reintentos)
│       ├── active_directory.py # get_user / reset_user_password / unlock_user (PowerShell)
│       ├── microsoft_auth.py   # Tokens: silent, ROPC, client credentials
│       ├── authorization.py    # is_ad_approver (lista AD_APPROVERS)
│       ├── audit.py            # write_audit → logs/audit.jsonl
│       ├── conversation_store.py # Caché en memoria requester→conversación Teams
│       ├── session_manager.py  # Agentes por sesión de conversación
│       └── teams_cards.py      # AdaptiveCard de prueba
├── tests/                      # 71 pruebas pytest (puertas de seguridad, APIs, builders, LDAP)
├── test_full_flow.py           # 52 checks de flujo integral (mockea AD/Graph)
├── alembic/                    # 4 migraciones (head: fab551fff4d9)
├── scripts/                    # stop/check service, login de cuenta técnica, debug_*
├── logs/audit.jsonl            # Eventos de auditoría (JSON Lines)
├── data/msal_token_cache.bin   # Caché de tokens MSAL (en memoria de proceso/archivo)
├── ad_support.db               # Base de datos SQLite
├── requirements.txt            # 50 paquetes congelados
├── AUDITORIA.md                # Informe de auditoría
└── README.md
```

## 4. Mapa de módulos (quién llama a quién)

```
main.handle_teams_message
  └─▶ SessionManager.get_agent ─▶ ADSupportAgent.process
        ├─▶ intent.detect_intent / extract_username
        ├─▶ active_directory.get_user                 (consulta AD)
        ├─▶ microsoft_auth.acquire_approval_token     (token Graph)
        └─▶ ApprovalService.create_request
              ├─▶ TeamsApprovalService.create_unlock_approval  (Graph beta, con reintentos)
              └─▶ [fallback] active_directory.reset_user_password (solo PASSWORD_RESET)

approval_worker (cada 15 s, asyncio.to_thread)
  └─▶ ApprovalService.list_pending
        └─▶ sync_graph_approval → TeamsApprovalService.get_approval_result
              ├─▶ REJECTED → notificar rechazo
              └─▶ APPROVED → execute_approved_request
                    ├─▶ reset_user_password / unlock_user (PowerShell)
                    └─▶ notificar resultado (contraseña temporal)

API approve/reject (X-API-Key)
  └─▶ ApprovalService.approve/reject ─▶ execute ─▶ notify_requester_*
```

## 5. Modelo de seguridad

| Superficie | Mecanismo |
|---|---|
| API `/api/v1/approvals/*`, `/api/v1/chat`, `/api/v1/users` | Header **`X-API-Key`** contra `API_KEY` |
| Acción de aprobar/rechazar | Además de la auth: `is_ad_approver()` (lista `AD_APPROVERS`) |
| Bot `/api/messages` | Validación JWT del SDK Bot Framework (`FastAPIAdapter`) |
| Ejecución en AD | Username validado `^[a-zA-Z0-9_-]+$`; valores pasados por **variable de entorno** (nunca interpolados en el script PowerShell) → sin inyección de comandos |
| Contraseña temporal | Valor fijo `Temporal$2026*` (decisión operativa 2026-10-09, ver AUDITORIA.md); pasa por env var; **nunca se escribe en logs ni en `audit.jsonl`**; cambio obligatorio al iniciar |
| Tokens Graph | Caché MSAL en `data/` (gitineado); el relay `graph_token_store` fue eliminado con la Mesa de Ayuda |

> **Mesa de Ayuda eliminada (2026-10-09):** se retiraron el frontend `frontend/`, el OAuth
> de Entra (`/helpdesk/auth/*`), las APIs con sesión, `SessionMiddleware` y el relay de
> tokens — uso orgánico cero y dos canales de aprobación divergentes. Cerró **CR-002** y
> **FIND-002**. Ver [AUDITORIA.md](../AUDITORIA.md).
>
> Los endpoints `/api/v1/debug/*` fueron **eliminados** en la auditoría del 2026-10-09
> (incluido uno sin autenticación). La recuperación de incidentes se hace con los scripts
> locales de `scripts/`.
