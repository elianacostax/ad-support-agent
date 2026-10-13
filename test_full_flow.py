"""
Prueba integral del flujo completo de cambio de contraseña.
Simula la conversación con el agente, la creación de la aprobación,
la aprobación en Teams y la notificación final con la contraseña
temporal. No toca Active Directory ni Microsoft Graph reales.
"""
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, r"C:\Users\sacosta\Desktop\ad-support-agent")

from app.db.database import SessionLocal
from app.models.approval_request import ApprovalRequest

RESULTS = []


def check(name, condition, detail=""):
    RESULTS.append((name, bool(condition), detail))
    status = "OK " if condition else "FAIL"
    print(f"[{status}] {name}" + (f" -> {detail}" if detail and not condition else ""))


ENABLED_USER = {
    "username": "susuga",
    "enabled": True,
    "locked_out": False,
    "password_expired": False,
    "password_last_set": "2026-09-29T09:50:30",
}

LOCKED_USER = {
    "username": "jareiza",
    "enabled": True,
    "locked_out": True,
    "password_expired": False,
    "password_last_set": "2026-09-01T08:00:00",
}


def cleanup(requester_prefix="test-flow-"):
    db = SessionLocal()
    try:
        rows = (
            db.query(ApprovalRequest)
            .filter(ApprovalRequest.requester_id.like(f"{requester_prefix}%"))
            .all()
        )
        for row in rows:
            db.delete(row)
        db.commit()
        return len(rows)
    finally:
        db.close()


def get_last_approval(requester_id):
    db = SessionLocal()
    try:
        return (
            db.query(ApprovalRequest)
            .filter(ApprovalRequest.requester_id == requester_id)
            .order_by(ApprovalRequest.id.desc())
            .first()
        )
    finally:
        db.close()


# ============================================================
# 1) Extracción de usuario en el mismo mensaje
# ============================================================
print("\n=== 1) Extracción de usuario en el mismo mensaje ===")
from app.services.intent import extract_username

cases = [
    ("ayudame a cambiar la contraseña del usuario sacosta", "sacosta"),
    ("en que estado esta la cuenta de susuga", "susuga"),
    ("en que estado esta la cuenta susuga", "susuga"),
    ("cambiar la contraseña de sacosta por favor", "sacosta"),
    ("restablecer contraseña vgiraldoq", "vgiraldoq"),
    ("desbloquear la cuenta Jareiza", "Jareiza"),
    ("estado de la cuenta de Jareiza", "Jareiza"),
    ("dime en que estado esta el usuario sacosta", "sacosta"),
    ("necesito cambiar password de agomeza", "agomeza"),
    ("cuenta de dvargasr", "dvargasr"),
]

for message, expected in cases:
    got = extract_username(message)
    check(f"extract '{message[:45]}...'", got == expected, f"esperado={expected} obtenido={got}")

# Mensajes sin usuario no deben devolver basura
for message in ["hola", "cual fue el ganador del mundia?", "gracias"]:
    got = extract_username(message)
    check(f"sin usuario '{message}'", got is None, f"obtenido={got}")


# ============================================================
# 2) Flujo completo: aprobación creada en Teams (PENDING)
# ============================================================
print("\n=== 2) Flujo: aprobación creada correctamente en Teams ===")
from app.services.agent import ADSupportAgent

cleanup()

with patch("app.services.agent.get_user", return_value=ENABLED_USER), \
     patch("app.services.agent.acquire_approval_token",
           return_value={"access_token": "tok-123"}), \
     patch("app.services.approval_service.TeamsApprovalService") as MockTeams, \
     patch("app.services.approval_service.get_user", return_value=ENABLED_USER):

    MockTeams.return_value.create_unlock_approval.return_value = "graph-id-001"

    agent = ADSupportAgent(
        session_id="teams:conv-test-1",
        requester_id="test-flow-req1",
        requester_name="Sergio Test",
    )

    r1 = agent.process("Ayudame a cambiar la contraseña")
    r2 = agent.process("susuga")
    r3 = agent.process("si")

    check("paso 1: pide usuario", "usuario de red" in r1, r1)
    check("paso 2: confirma cuenta y pregunta", "¿Deseas solicitar" in r2, r2)
    check("paso 3: informa solicitud enviada",
          "solicitud" in r3.lower() and "pendiente de aprobación" in r3,
          r3)
    check("paso 3: incluye número de solicitud", "Número de solicitud" in r3, r3)

    approval = get_last_approval("test-flow-req1")
    check("BD: solicitud creada", approval is not None)
    if approval:
        check("BD: status PENDING", approval.status == "PENDING", approval.status)
        check("BD: graph_approval_id guardado",
              approval.graph_approval_id == "graph-id-001",
              str(approval.graph_approval_id))
        check("BD: action PASSWORD_RESET",
              approval.action == "PASSWORD_RESET", approval.action)
        check("BD: usuario correcto", approval.ad_username == "susuga", approval.ad_username)


# ============================================================
# 3) Flujo: sin token Graph -> restablecimiento directo (fallback)
# ============================================================
print("\n=== 3) Flujo: fallo Teams -> restablecimiento directo con temporal ===")

with patch("app.services.agent.get_user", return_value=ENABLED_USER), \
     patch("app.services.agent.acquire_approval_token", return_value=None), \
     patch("app.services.approval_service.reset_user_password",
           return_value={"reset": True, "username": "susuga",
                         "reason": "PASSWORD_RESET",
                         "temp_password": "Temp#123abcXYZ"}) as MockReset:

    agent = ADSupportAgent(
        session_id="teams:conv-test-2",
        requester_id="test-flow-req2",
        requester_name="Sergio Test",
    )

    r1 = agent.process("cambiar la contraseña de susuga por favor")
    r2 = agent.process("si")

    check("paso 1: usuario extraído del mismo mensaje",
          "¿Deseas solicitar" in r1, r1)
    check("paso 2: confirma cambio con contraseña temporal",
          "Contraseña temporal: \"Temp#123abcXYZ\"" in r2, r2)
    check("paso 2: indica que debe cambiarla",
          "cambiarla al iniciar sesión" in r2, r2)
    check("paso 2: confirma que fue restablecida",
          "fue restablecida" in r2, r2)
    check("fallback: se llamó reset_user_password", MockReset.called)

    approval = get_last_approval("test-flow-req2")
    check("BD: solicitud COMPLETED", approval and approval.status == "COMPLETED",
          approval.status if approval else "sin fila")
    check("BD: acción PASSWORD_RESET",
          approval and approval.action == "PASSWORD_RESET",
          approval.action if approval else "")


# ============================================================
# 4) Flujo desbloqueo: NUNCA debe restablecer contraseña
# ============================================================
print("\n=== 4) Flujo desbloqueo: no debe restablecer contraseña ===")

with patch("app.services.agent.get_user", return_value=LOCKED_USER), \
     patch("app.services.agent.acquire_approval_token", return_value=None), \
     patch("app.services.approval_service.reset_user_password",
           return_value={"reset": True, "temp_password": "NO-DEBE-USARSE"}) as MockReset:

    agent = ADSupportAgent(
        session_id="teams:conv-test-3",
        requester_id="test-flow-req3",
        requester_name="Sergio Test",
    )

    r1 = agent.process("desbloquear la cuenta Jareiza")
    r2 = agent.process("si")

    check("paso 1: pregunta desbloqueo", "¿Deseas solicitar el desbloqueo" in r1, r1)
    check("paso 2: informa fallo de aprobación (sin reset)",
          "no fue posible crear la aprobación" in r2.lower(), r2)
    check("NO se restableció contraseña en desbloqueo",
          not MockReset.called,
          "reset_user_password fue llamado incorrectamente")

    approval = get_last_approval("test-flow-req3")
    check("BD: solicitud FAILED", approval and approval.status == "FAILED",
          approval.status if approval else "sin fila")
    check("BD: action UNLOCK_ACCOUNT",
          approval and approval.action == "UNLOCK_ACCOUNT",
          approval.action if approval else "")


# ============================================================
# 5) Worker: aprueba, ejecuta y notifica con la contraseña temporal
# ============================================================
print("\n=== 5) Worker: aprueba -> ejecuta -> notifica con temporal ===")

# Limpiar filas de pruebas anteriores para que el worker solo
# procese la solicitud de esta prueba.
cleanup()

# Crear solicitud PENDING como si la aprobación en Teams se hubiera creado
db = SessionLocal()
try:
    row = ApprovalRequest(
        requester_id="test-flow-req4",
        conversation_id="conv-test-4",
        conversation_service_url="https://smba.trafficmanager.net/x",
        ad_username="susuga",
        action="PASSWORD_RESET",
        status="PENDING",
        graph_approval_id="graph-id-004",
        reason="Solicitud de restablecimiento de contraseña.",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    request_id = row.id
finally:
    db.close()

with patch("app.services.approval_service.TeamsApprovalService") as MockTeams, \
     patch("app.services.approval_worker.acquire_approval_token",
           return_value={"access_token": "tok-worker"}), \
     patch("app.services.approval_service.reset_user_password",
           return_value={"reset": True, "username": "susuga",
                         "reason": "PASSWORD_RESET",
                         "temp_password": "Worker#Pass999"}), \
     patch("app.services.approval_worker._notify_requester") as MockNotify, \
     patch("app.services.approval_service.get_user", return_value=ENABLED_USER):

    MockTeams.return_value.get_approval_result.return_value = {
        "status": "APPROVED",
        "response": "Approve",
        "comments": "aprobado por prueba",
        "approved_by": "approver-oid",
        "created_at": None,
    }

    from app.services.approval_worker import process_pending_approvals
    process_pending_approvals()

    approval = get_last_approval("test-flow-req4")
    check("worker: solicitud COMPLETED", approval and approval.status == "COMPLETED",
          approval.status if approval else "sin fila")
    check("worker: notificación enviada", MockNotify.called)

    if MockNotify.called:
        args = MockNotify.call_args
        message = args[0][1] if len(args[0]) > 1 else ""
        check("worker: mensaje contiene la contraseña temporal",
              "Worker#Pass999" in message, message)
        check("worker: mensaje indica que debe cambiarla",
              "cambiarla al iniciar sesión" in message, message)
        check("worker: menciona al usuario",
              "susuga" in message, message)
    else:
        check("worker: mensaje contiene la contraseña temporal", False, "sin notificación")
        check("worker: mensaje indica que debe cambiarla", False, "sin notificación")
        check("worker: menciona al usuario", False, "sin notificación")


# ============================================================
# 6) Worker: rechazo notifica correctamente
# ============================================================
print("\n=== 6) Worker: rechazo notifica al solicitante ===")

cleanup()

db = SessionLocal()
try:
    row = ApprovalRequest(
        requester_id="test-flow-req5",
        conversation_id="conv-test-5",
        ad_username="susuga",
        action="PASSWORD_RESET",
        status="PENDING",
        graph_approval_id="graph-id-005",
    )
    db.add(row)
    db.commit()
finally:
    db.close()

with patch("app.services.approval_service.TeamsApprovalService") as MockTeams, \
     patch("app.services.approval_worker.acquire_approval_token",
           return_value={"access_token": "tok-worker"}), \
     patch("app.services.approval_worker._notify_requester") as MockNotify:

    MockTeams.return_value.get_approval_result.return_value = {
        "status": "REJECTED",
        "response": "Reject",
        "comments": "no aprobado",
        "approved_by": "approver-oid",
        "created_at": None,
    }

    from app.services.approval_worker import process_pending_approvals
    process_pending_approvals()

    approval = get_last_approval("test-flow-req5")
    check("rechazo: solicitud REJECTED", approval and approval.status == "REJECTED",
          approval.status if approval else "sin fila")

    if MockNotify.called:
        message = MockNotify.call_args[0][1]
        check("rechazo: mensaje correcto para password reset",
              "restablecimiento de contraseña" in message and "RECHAZADA" in message,
              message)
    else:
        check("rechazo: mensaje correcto para password reset", False, "sin notificación")


# ============================================================
# 7) Reintentos en la creación de la aprobación (Graph)
# ============================================================
print("\n=== 7) Reintentos de Microsoft Graph ===")

import app.services.teams_approval_service as tas
from app.services.teams_approval_service import TeamsApprovalService

svc = TeamsApprovalService.__new__(TeamsApprovalService)
svc.access_token = "tok-test"

# 7a) POST falla 2 veces con HTTP 500 y a la tercera funciona
with patch.object(tas.requests, "post") as MockPost:
    ok_response = MagicMock()
    ok_response.status_code = 201
    ok_response.content = b'{"id": "approval-retry-1"}'
    ok_response.json.return_value = {"id": "approval-retry-1"}

    error_response = MagicMock()
    error_response.status_code = 500

    MockPost.side_effect = [error_response, error_response, ok_response]

    with patch.object(tas.time, "sleep"):
        approval_id = svc.create_unlock_approval(
            ad_username="susuga",
            requester_id="req",
            request_id=900001,
            action="PASSWORD_RESET",
        )

    check("POST reintenta ante HTTP 500", MockPost.call_count == 3,
          f"intentos={MockPost.call_count}")
    check("POST: obtiene el ID en el intento 3",
          approval_id == "approval-retry-1", str(approval_id))

# 7b) La resolución de la operación reintenta ante HTTP 500
with patch.object(tas.requests, "get") as MockGet:
    error_response = MagicMock()
    error_response.status_code = 500

    ok_response = MagicMock()
    ok_response.status_code = 200
    ok_response.ok = True
    ok_response.json.return_value = {
        "resourceLocation": "https://graph.microsoft.com/beta/xxx/approvals/approval-op-77",
    }

    MockGet.side_effect = [error_response, ok_response]

    with patch.object(tas.time, "sleep"):
        resolved = svc._resolve_operation("https://graph.microsoft.com/operations/1")

    check("resolve reintenta ante HTTP 500", MockGet.call_count == 2,
          f"intentos={MockGet.call_count}")
    check("resolve obtiene el ID después del reintento",
          resolved == "approval-op-77", str(resolved))

# 7c) Si la resolución falla siempre, se recupera el ID por búsqueda
search_response = MagicMock()
search_response.status_code = 200
search_response.ok = True
search_response.json.return_value = {
    "value": [
        {"id": "otra", "description": "otro asunto"},
        {"id": "approval-recuperada",
         "description": "Solicitud interna: #900002"},
    ],
}

with patch.object(tas.requests, "get") as MockFind:
    MockFind.return_value = search_response
    found = svc._find_approval_by_request_id(900002)

check("búsqueda recupera el ID por número de solicitud",
      found == "approval-recuperada", str(found))

with patch.object(tas.requests, "get") as MockFind:
    MockFind.return_value = search_response
    not_found = svc._find_approval_by_request_id(12345)

check("búsqueda no devuelve IDs ajenos", not_found is None, str(not_found))

# 7d) Flujo completo: POST 202 + Location, la resolución falla
# siempre con 500 y el ID se recupera mediante búsqueda.
ok_post = MagicMock()
ok_post.status_code = 202
ok_post.headers = {"Location": "https://graph.microsoft.com/beta/operations/op-9"}
ok_post.content = b""

search_ok = MagicMock()
search_ok.status_code = 200
search_ok.ok = True
search_ok.json.return_value = {
    "value": [
        {"id": "approval-final-9",
         "description": "Solicitud interna: #900003"},
    ],
}

error_500 = MagicMock()
error_500.status_code = 500

with patch.object(tas.requests, "post", return_value=ok_post), \
     patch.object(tas.requests, "get") as MockGet, \
     patch.object(tas.time, "sleep"):
    MockGet.side_effect = [error_500] * 10 + [search_ok]

    aid = svc.create_unlock_approval(
        ad_username="susuga",
        requester_id="req",
        request_id=900003,
        action="PASSWORD_RESET",
    )

check("creación recupera el ID aunque la resolución falle",
      aid == "approval-final-9", str(aid))


# ============================================================
# 8) Casos borde de conversación
# ============================================================
print("\n=== 8) Casos borde de conversación ===")

with patch("app.services.agent.get_user", return_value=ENABLED_USER) as MockGetUser:
    agent = ADSupportAgent(
        session_id="teams:conv-test-8",
        requester_id="test-flow-req8",
        requester_name="Sergio Test",
    )

    r1 = agent.process("Ayudame a cambiar la contraseña")
    r2 = agent.process("si")
    check("sin usuario: 'si' no se toma como usuario",
          "¿Cuál es tu usuario de red?" in r2, r2)
    check("sin usuario: no consultó AD con 'si'",
          not MockGetUser.called, f"get_user llamado con {MockGetUser.call_args}")

    r3 = agent.process("no")
    check("sin usuario: 'no' cancela la solicitud",
          r3 == "Solicitud cancelada.", r3)

# Palabras comunes no deben tomarse como usuario en consulta de estado
with patch("app.services.agent.get_user", return_value=ENABLED_USER) as MockGetUser:
    agent = ADSupportAgent(
        session_id="teams:conv-test-8b",
        requester_id="test-flow-req8b",
        requester_name="Sergio Test",
    )

    r = agent.process("dime el estado de la cuenta de susuga")
    check("estado en un solo mensaje funciona",
          "Estado de la cuenta 'susuga'" in r, r)


# ============================================================
# Resumen y limpieza
# ============================================================
removed = cleanup()
print(f"\n=== LIMPIEZA: {removed} filas de prueba eliminadas ===")

passed = sum(1 for _, ok, _ in RESULTS if ok)
failed = [(n, d) for n, ok, d in RESULTS if not ok]

print(f"\n=== RESUMEN: {passed}/{len(RESULTS)} pruebas OK ===")
for name, detail in failed:
    print(f"  FALLA: {name} -> {detail}")

sys.exit(1 if failed else 0)
