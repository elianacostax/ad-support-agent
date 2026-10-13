# 03 — Flujos de funcionamiento

Todos los diagramas son Mermaid (se renderizan en GitHub/VS Code).

## 1. Flujo de conversación en Teams

```mermaid
sequenceDiagram
    participant U as Usuario (Teams)
    participant B as Bot /api/messages
    participant A as ADSupportAgent
    participant AD as Active Directory
    participant S as ApprovalService
    participant G as Graph approvalItems

    U->>B: "cambiar la contraseña de sacosta"
    B->>A: process(mensaje)
    A->>A: detect_intent → password_reset<br/>extract_username → "sacosta"
    A->>AD: get_user("sacosta")  [PowerShell]
    AD-->>A: enabled / locked / password_expired
    A-->>U: "La cuenta 'sacosta' existe. ¿Deseas solicitar...? Responde sí o no."
    Note over A: pending_action=password_reset,<br/>pending_at=ahora (vence en 2 min)

    U->>B: "si"
    B->>A: process("si")
    A->>S: create_request(action=PASSWORD_RESET, conversation_id, token)
    S->>G: POST approvalItems (con reintentos)
    G-->>S: id de la aprobación
    S-->>A: approval (status=PENDING, graph_approval_id)
    A-->>U: "Solicitud de restablecimiento... Número de solicitud: #29.<br/>Quedará pendiente de aprobación. Te avisaré con la contraseña temporal..."
```

Si el usuario **no** envía el usuario en el mismo mensaje, el agente pregunta
`"¿Cuál es tu usuario de red?"` y espera ese dato (estado `pending_action` sin
`pending_username`). Ver [04-reglas-negocio.md](04-reglas-negocio.md).

## 2. Estados de la conversación (agente)

```mermaid
stateDiagram-v2
    [*] --> SinEstado
    SinEstado --> SinEstado : intent=other → mensaje de ayuda
    SinEstado --> ConsultaEstado : intent=status sin usuario<br/>("¿Cuál es tu usuario de red?")
    SinEstado --> ConsultaEstado : intent=status con usuario
    SinEstado --> EsperandoUsuario : intent=unlock/reset sin usuario
    SinEstado --> PideConfirmacion : intent=unlock/reset con usuario<br/>→ consulta AD → "¿Deseas...? sí/no"

    EsperandoUsuario --> PideConfirmacion : llega usuario válido
    EsperandoUsuario --> [*] : "no" → Solicitud cancelada
    EsperandoUsuario --> EsperandoUsuario : "si" → sigue preguntando

    PideConfirmacion --> [*] : "si" → crea solicitud (PENDING o COMPLETED fallback)
    PideConfirmacion --> [*] : "no" → Solicitud cancelada
    PideConfirmacion --> EsperandoUsuario : mensaje no reconocido (repite)

    ConsultaEstado --> [*] : devuelve estado de la cuenta → _reset()

    note right of PideConfirmacion
        Timeout 2 minutos (CONFIRMATION_TIMEOUT_MINUTES):
        si vence, se cancela con mensaje de seguridad
        y se audita result=EXPIRED
    end note
```

## 3. Aprobación y worker (cada 15 s)

```mermaid
flowchart TD
    W[Worker asyncio<br/>cada POLL_INTERVAL_SECONDS=15s] --> LP[list_pending<br/>status=PENDING por created_at]
    LP -->|0 filas| FIN[Revisión terminada]
    LP -->|filas| CHK{graph_approval_id?}
    CHK -->|no| OMIT[Omitir + warning log]
    CHK -->|sí| TOK{token Graph?}
    TOK -->|no| OMIT2[Omitir + warning log]
    TOK -->|sí| SYNC[sync_graph_approval<br/>GET approvalItems/id/responses]

    SYNC --> EST{estado Graph}
    EST -->|PENDING| WAIT[Sigue pendiente]
    EST -->|REJECTED| REJ[Audita + notifica rechazo<br/>build_rejection_message]
    EST -->|APPROVED| EXE[execute_approved_request]

    EXE --> Q{¿existe y habilitada?}
    Q -->|no| FAIL[FAILED + result explicativo]
    Q -->|sí| ACC{action}
    ACC -->|PASSWORD_RESET| RESET[reset_user_password<br/>PowerShell + temp fija]
    ACC -->|UNLOCK_ACCOUNT| UNLOCK{ya desbloqueada?}
    UNLOCK -->|sí| DONE[COMPLETED<br/>'ya estaba desbloqueada']
    UNLOCK -->|no| UNL[unlock_user + re-consulta AD<br/>verificación post-desbloqueo]

    RESET -->|OK| DONE2[COMPLETED + temp_password en memoria]
    RESET -->|fallo| FAIL2[FAILED + detalle del error]
    UNL -->|OK| DONE3[COMPLETED]
    UNL -->|fallo o sigue bloqueada| FAIL3[FAILED]

    DONE2 --> NOTIF[write_audit + notificar al solicitante<br/>build_completion_message]
    DONE3 --> NOTIF
    FAIL --> NOTIF2[write_audit + notificar<br/>mensaje 'no pudo completarse']
    FAIL2 --> NOTIF2
    FAIL3 --> NOTIF2
    REJ --> FIN
    NOTIF --> FIN
    NOTIF2 --> FIN
```

Puntos clave:

- El worker **solo** procesa `PENDING` con `graph_approval_id` y token Graph disponibles.
- `asyncio.to_thread` evita bloquear el loop mientras corre el proceso síncrono.
- Una excepción por solicitud **no detiene** el ciclo: se loguea y sigue con la siguiente.

## 4. Notificación al solicitante

```mermaid
flowchart LR
    MSG[build_completion_message<br/>o build_rejection_message] --> N[_notify_requester]
    N --> C1{conversation_id<br/>en la fila?}
    C1 -->|sí| SEND
    C1 -->|no| C2{conversation_store<br/>en memoria?}
    C2 -->|sí| SEND
    C2 -->|no| WARN[warning log<br/>no se notifica]
    SEND --> LOOP{loop principal<br/>_MAIN_LOOP abierto?}
    LOOP -->|sí| TS[run_coroutine_threadsafe<br/>timeout 30 s]
    LOOP -->|no| FB[asyncio.run<br/>último recurso + warning]
    TS --> OK["Notificación enviada"]
    FB --> OK
```

Detalles importantes (fuente: `app/services/approval_worker.py`):

- El worker y las APIs corren en el **mismo proceso** que el servidor; el loop real se
  resuelve desde `sys.modules["__main__"]` cuando el servicio arranca con
  `python -m app.main` (por eso **hay que usar `-m`**; un import normal crearia un segundo
  módulo con `_MAIN_LOOP=None` y caería al fallback `asyncio.run`).
- Las APIs envuelven la notificación en `try/except`: **un fallo de Teams nunca bloquea
  la API** (regresión cubierta por `tests/test_api_approvals.py`).
- Mensajes: `build_completion_message` incluye `Contraseña temporal: X. Debe cambiarla al
  iniciar sesión.` solo si `action=PASSWORD_RESET` **y** `status=COMPLETED` **y** hay
  `temp_password`; `build_rejection_message` indica tipo de solicitud y motivo.

## 5. Creación de la aprobación en Graph (resiliencia)

```mermaid
flowchart TD
    P[POST /solutions/approval/approvalItems<br/>approvalType=basic, approvers=grupo] --> R{_post_with_retry<br/>408/429/5xx → reintento}
    R -->|HTTP 200/201 con id| ID[ID devuelto directo]
    R -->|HTTP 202 + Location| OP[_resolve_operation<br/>polling con reintentos]
    R -->|error definitivo| FALLO[RuntimeError → fallback]
    OP -->|resuelve| ID
    OP -->|siempre 5xx| BUSQ[_find_approval_by_request_id<br/>busca 'Solicitud interna: #N']
    BUSQ -->|encuentra| ID
    BUSQ -->|no| FALLO
    ID --> OK[graph_approval_id guardado en BD]
    FALLO --> ACC{action}
    ACC -->|PASSWORD_RESET| FB1[fallback: reset directo en AD<br/>→ COMPLETED + temp]
    ACC -->|UNLOCK_ACCOUNT| FB2[FAILED<br/>NUNCA restablecer contraseña]
```

## 6. Flujo completo del reset de contraseña (resumen)

```
1. Mensaje → intención + usuario extraído
2. Consulta AD (PowerShell) → cuenta habilitada/estado
3. Confirmación "sí" (o timeout 2 min → cancela)
4. create_request → fila PENDING en BD
5. Aprobación en Teams/Graph (con reintentos) → graph_approval_id
6. (Si falla Teams) fallback: reset directo → COMPLETED + notificación inmediata
7. Aprobador acciona en Teams (tarjeta de aprobación de Graph)
8. Worker (≤15 s) detecta APPROVED → ejecuta reset en AD
      • contraseña temporal fija `Temporal$2026*`
      • ChangePasswordAtLogon = true (cambio obligatorio al primer inicio)
9. write_audit + mensaje en Teams: resultado + contraseña temporal + "debe cambiarla"
```
