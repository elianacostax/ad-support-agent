"""Constructores de mensajes de notificación al solicitante."""
from types import SimpleNamespace

from app.services.approval_worker import (
    build_completion_message,
    build_rejection_message,
)


def approval(**kwargs) -> SimpleNamespace:
    defaults = {
        "id": 42,
        "ad_username": "susuga",
        "action": "PASSWORD_RESET",
        "status": "COMPLETED",
        "result": "Se restableció la contraseña de 'susuga'.",
        "approval_comments": None,
        "temp_password": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


# ============================================================
# build_completion_message
# ============================================================

def test_reset_completado_incluye_temporal():
    msg = build_completion_message(
        approval(temp_password="Temp#Abc12345xyz")
    )

    assert 'Contraseña temporal: "Temp#Abc12345xyz"' in msg
    # La clave va entre comillas: el punto final pertenece a la frase,
    # no a la contraseña (importante con claves que terminan en '*').
    assert '"Temp#Abc12345xyz".' in msg
    assert "'susuga'" in msg
    assert "#42" in msg
    assert "cambiarla al iniciar sesión" in msg


def test_reset_sin_temporal_no_finge_exito_con_password():
    """Si no hay temporal en el objeto, el mensaje no debe inventar una."""
    msg = build_completion_message(approval(temp_password=None))

    assert "Contraseña temporal" not in msg
    assert "#42" in msg


def test_desbloqueo_completado_no_menciona_password():
    msg = build_completion_message(
        approval(
            action="UNLOCK_ACCOUNT",
            status="COMPLETED",
            temp_password="NO-DEBE-APARECER",
            result="Cuenta desbloqueada.",
        )
    )

    assert "Contraseña temporal" not in msg
    assert "NO-DEBE-APARECER" not in msg


def test_fallido_explica_el_error():
    msg = build_completion_message(
        approval(status="FAILED", result="AD no responde")
    )

    assert "no pudo completarse" in msg
    assert "AD no responde" in msg
    assert "#42" in msg


def test_rechazo_no_aparece_como_completado():
    msg = build_completion_message(approval(status="REJECTED"))
    assert "Contraseña temporal" not in msg


# ============================================================
# build_rejection_message
# ============================================================

def test_rechazo_reset_menciona_tipo_y_motivo():
    msg = build_rejection_message(
        approval(approval_comments="Política de seguridad")
    )

    assert "restablecimiento de contraseña" in msg
    assert "RECHAZADA" in msg
    assert "Política de seguridad" in msg
    assert "'susuga'" in msg


def test_rechazo_desbloqueo_menciona_tipo():
    msg = build_rejection_message(
        approval(action="UNLOCK_ACCOUNT", approval_comments="no aplica")
    )

    assert "desbloqueo" in msg
    assert "RECHAZADA" in msg


def test_rechazo_sin_comentarios_indica_sin_comentario():
    msg = build_rejection_message(approval(approval_comments=None))

    assert "sin comentario" in msg
