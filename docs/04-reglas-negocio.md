# 04 — Reglas de negocio

## 1. Intenciones (`app/services/intent.py`)

`detect_intent(message)` evalúa en **orden de prioridad** (primera coincidencia gana):

| Prioridad | Intención | Palabras clave (casefold, subcadena) |
|---|---|---|
| 1 | `unlock` | `desbloquear`, `desbloquea`, `desbloqueo`, `cuenta bloqueada`, `cuenta bloqueado`, `me bloquearon` |
| 2 | `password_reset` | `contraseña`, `contrasena`, `password`, `reset`, `restablecer`, `cambiar contraseña`, `cambiar contrasena` |
| 3 | `status` | `estado`, `consultar`, `verificar usuario`, `estado de la cuenta` |
| 4 | `other` | → mensaje de ayuda general |

**Regla:** como `unlock` se evalúa primero, "desbloquear la cuenta y cambiar la
contraseña" se interpreta como **desbloqueo**.

## 2. Extracción de usuario (`extract_username`)

Orden de aplicación:

1. **Patrones específicos** (de mayor a menor precisión), todos con
   captura `([a-zA-Z0-9_-]+)`:
   - `del|de la|de|al|a|para` + `usuario|cuenta|user` → "del usuario sacosta"
   - `usuario|cuenta|user` (+ `de red`) → "cuenta de susuga"
   - verbo (`desbloquear|restablecer|cambiar|...`) + `cuenta|contraseña|...` → "cambiar la contraseña de sacosta"
   - `cuenta|contraseña|password|clave` → "contraseña vgiraldoq"
2. **Respaldo por intención:** si `detect_intent != "other"`, toma el **último token**
   válido del mensaje ("en que estado esta la cuenta de susuga" → `susuga`).
3. Si nada aplica → `None` (el agente pregunta `¿Cuál es tu usuario de red?`).

### Validación de candidato (`_valid_candidate`)

Un candidato es usuario solo si:

- no es **stopword** (lista `_STOPWORDS`: artículos/preposiciones, dominio
  `cuenta|usuario|contraseña|estado|...`, verbos de petición, saludos, `si`, `no`, `tarjeta`, `prueba`...),
- tiene entre **3 y 64** caracteres,
- cumple `^[a-zA-Z0-9_-]+$`.

`_looks_like_username()` aplica reglas equivalentes a mensajes sueltos: sin espacios,
no-stopword, 3–64, alfanumérico con `-`/`_`. **Por eso "si"/"no" nunca se toman como usuario.**

## 3. Validación para Active Directory (`_validate_username`)

Puerta anti-inyección usada por `get_user`, `reset_user_password` y `unlock_user`:

- `strip()`; vacío → `ValueError`;
- si tras quitar `-` y `_` **no** es alfanumérico → `ValueError`
  (rechaza `;`, `&`, `|`, backtick, `$()`, espacios, rutas... — 18 intentos de inyección
  cubiertos en `tests/test_username_validation.py`);
- los valores viajan a PowerShell por **variables de entorno**
  (`AD_TARGET_USER`, `AD_SERVER`, `AD_TEMP_PASSWORD`), nunca interpolados en el script.

## 4. Reglas por acción

### Consulta de estado (`status`)
Devuelve: habilitada sí/no, bloqueada sí/no, contraseña expirada sí/no, último cambio.
No modifica nada ni requiere aprobación.

### Desbloqueo (`UNLOCK_ACCOUNT`)
| Condición | Resultado |
|---|---|
| Usuario no existe | "No encontré el usuario..." (audit `NOT_FOUND`) |
| Cuenta deshabilitada | rechaza (audit `ACCOUNT_DISABLED`) |
| Cuenta **no** bloqueada | "no está bloqueada actualmente" (audit `NOT_REQUIRED`) |
| Cuenta bloqueada | pide confirmación → solicitud |
| Ejecución: ya desbloqueada | `COMPLETED` "ya estaba desbloqueada" |
| Ejecución | `unlock_user` + **re-consulta** para verificar que quedó desbloqueada |
| Fallback sin Teams | **`FAILED` — NUNCA restablece contraseñas** |

### Restablecimiento de contraseña (`PASSWORD_RESET`)
| Condición | Resultado |
|---|---|
| Cuenta deshabilitada | rechaza (no se restablece en cuentas deshabilitadas) |
| Cualquier otro caso | pide confirmación → solicitud |
| Fallback sin Teams/Graph | **reset directo en AD** → `COMPLETED` + contraseña temporal entregada en el mismo mensaje |
| Ejecución | genera contraseña, la aplica y fuerza cambio en el inicio de sesión |

## 5. Confirmación y timeouts

- **Ventana de confirmación: 2 minutos** (`CONFIRMATION_TIMEOUT_MINUTES = 2`).
  Al vencer: se limpia el estado, se audita `result=EXPIRED / reason=CONFIRMATION_TIMEOUT`
  y se responde que la solicitud venció por seguridad.
- Afirmativos: `si`, `sí`, `confirmo`, `confirmar`, `desbloqueala`, `desbloquéala`,
  `desbloquearla`, `hazlo`, `adelante`.
- Negativos: `no`, `cancelar`, `cancela`, `cancelado`, `déjalo`, `dejalo` →
  "Solicitud cancelada." (audit `CANCELLED / USER_DECLINED`).
- Texto no reconocido en confirmación → repite la pregunta (no crea la solicitud).

## 6. Roles y autorización

| Rol | Cómo se identifica | Puede |
|---|---|---|
| **Cualquier usuario de Teams** | aad_object_id del mensaje | solicitar desbloqueo/reset, consultar estado |
| **Aprobador** | `is_ad_approver(username)` | aprobar/rechazar por API (X-API-Key) |
| **Llamador de API** | header `X-API-Key == API_KEY` | `GET /api/v1/users`, `POST /api/v1/chat`, listar/aprobar/rechazar `/api/v1/approvals` |
| **Cuenta técnica Graph** | `APPROVAL_ACCOUNT*` (ROPC + caché) | crear/consultar aprobaciones en Graph |

Regla `is_ad_approver` (`app/services/authorization.py`):

1. Lista `AD_APPROVERS` = separada por comas, valores con `strip().casefold()`;
2. coincidencia exacta con el `username` (case-insensitive y con espacios tolerados);
3. si el valor trae `@` (Entra entrega `user@dominio.com`), se compara también la
   **parte local** (`sacosta@sitti.com.co` → `sacosta`);
4. lista vacía o usuario sin match → **nadie aprueba**.

## 7. Aprobación en Graph

Payload (`TeamsApprovalService.create_unlock_approval`):

- `displayName`: `"Solicitud de desbloqueo: <usuario>"` o `"Solicitud de restablecimiento de contraseña: <usuario>"`;
- `description`: acción + cuenta + solicitante + **`Solicitud interna: #<id>`**
  (ese texto es el ancla para recuperar el ID si Graph falla);
- `approvalType`: `basic`;
- `appvers`: grupo `AD_APPROVAL_GROUP_ID`.

Interpretación de respuestas (`get_approval_result`): última respuesta del `approvalItem`;
`Approve` → `APPROVED`, `Reject` → `REJECTED`, vacío/otro → `PENDING`; `approved_by` = OID
del aprobador; `comments` → `approval_comments`.

Reintentos: `408/429/5xx` en el POST, polling de la operación con reintentos, y
recuperación del ID por búsqueda de `Solicitud interna: #N`.

## 8. Contraseña temporal

- **Valor fijo: `Temporal$2026*`** (decisión operativa 2026-10-09; sustituyó a la
  generación aleatoria de 16 caracteres con `secrets` — constante `TEMP_PASSWORD` en
  `app/services/active_directory.py`).
- **Riesgo aceptado:** al ser el mismo valor para todos los resets, cualquiera que la
  conozca podría iniciar sesión en una cuenta recién reseteada antes que su dueño.
  Mitigación: `Set-ADUser -ChangePasswordAtLogon $true` → el **primer inicio de sesión
  obliga a cambiarla**; el usuario debe usarla de inmediato tras recibirla.
- Se pasa a PowerShell por env var `AD_TEMP_PASSWORD` (invisible en línea de comandos);
- `Set-ADAccountPassword -Reset` + `Set-ADUser -ChangePasswordAtLogon $true`
  → **debe cambiarla al iniciar sesión**;
- Se entrega solo por Teams (agente o worker) y **nunca** se escribe en logs,
  `audit.jsonl` ni columnas de la BD;
- El mensaje estandarizado — la clave va **entre comillas** para que el punto final de la
  frase no se confunda con parte de la contraseña (importante porque `Temporal$2026*`
  termina en `*`):
  `Contraseña temporal: "Temporal$2026*". Debe cambiarla al iniciar sesión.`

## 9. Auditoría (`logs/audit.jsonl`)

Cada evento es una línea JSON:
`timestamp` (UTC ISO), `session_id`, `requester_id`, `action`, `username`, `result`, `reason`.

Valores observados:

| action | result | reason típico |
|---|---|---|
| `UNLOCK` / `PASSWORD_RESET` | `REJECTED` | `INVALID_USERNAME`, `ACCOUNT_DISABLED` |
| `UNLOCK` | `NOT_REQUIRED` | `ACCOUNT_NOT_LOCKED` |
| `UNLOCK` / `PASSWORD_RESET` | `NOT_FOUND` | `USER_NOT_FOUND` |
| `UNLOCK` / `PASSWORD_RESET` / `STATUS` | `EXPIRED` | `CONFIRMATION_TIMEOUT` |
| `*_REQUEST` | `CANCELLED` | `USER_DECLINED` |
| `*_REQUEST` | `PENDING` | `APPROVAL_REQUEST_<id>` |
| `*_REQUEST` | `ERROR` | `GRAPH_APPROVAL_CREATION_ERROR`, `APPROVAL_REQUEST_CREATION_ERROR` |
| `PASSWORD_RESET` / `UNLOCK_ACCOUNT` (worker) | `COMPLETED`/`FAILED`/`REJECTED` | texto de `result` o comentario del aprobador |

## 10. Catálogo de mensajes al usuario (principales)

| Situación | Mensaje |
|---|---|
| Sin intención reconocida | "Puedo ayudarte con el desbloqueo de cuentas, el restablecimiento de contraseña o la consulta de estado..." |
| Falta usuario | "¿Cuál es tu usuario de red?" / "Claro. ¿Cuál es tu usuario de red?" |
| Usuario inválido | "El usuario de red indicado no tiene un formato válido." |
| No existe | "No encontré el usuario '<u>' en Active Directory." |
| Deshabilitada | "La cuenta '<u>' está deshabilitada. No puedo [desbloquear/restablecer]..." |
| No bloqueada | "La cuenta '<u>' no está bloqueada actualmente." |
| Pide confirmación | "La cuenta '<u>' está bloqueada. ¿Deseas solicitar el desbloqueo? Responde sí o no." |
| Creada (unlock) | "...fue enviada a Mesa de Ayuda. Número de solicitud: #N. Quedará pendiente de aprobación. Te avisaré cuando sea resuelta." |
| Creada (reset) | "...Te avisaré con la contraseña temporal cuando sea aprobada." |
| Fallback reset inmediato | "La contraseña de '<u>' fue restablecida correctamente (solicitud #N). Contraseña temporal: \"X\". Debe cambiarla al iniciar sesión." |
| Fallback sin Teams (unlock) | "La solicitud fue registrada, pero no fue posible crear la aprobación en Microsoft Teams..." |
| Cancelada | "Solicitud cancelada." |
| Expirada (2 min) | "La solicitud ... ha vencido por tiempo de espera. Por seguridad, debes iniciar nuevamente la solicitud." |
| Rechazo | "Tu solicitud de [restablecimiento de contraseña/desbloqueo] para '<u>' fue RECHAZADA. Motivo: <comentario>." |
| Éxito worker | "Contraseña restablecida para '<u>' (solicitud #N). Contraseña temporal: \"X\". Debe cambiarla al iniciar sesión." |
| Fallo worker | "La solicitud #N para '<u>' no pudo completarse: <detalle>." |

> Estos textos están **cubiertos por pruebas** en `tests/test_message_builders.py`,
> `tests/test_api_approvals.py` y `test_full_flow.py` — si se cambian, actualizar los tests.
