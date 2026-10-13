"""Pruebas de la API de aprobaciones (API key): autorización y notificación."""
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import app.api.v1.approvals as approvals_module
from app.api.v1.approvals import approve, reject
from app.config import settings
from app.schemas.approval import ApprovalActionRequest, ApprovalRejectRequest


def executed_approval() -> SimpleNamespace:
    return SimpleNamespace(
        id=42,
        requester_id="req-1",
        ad_username="susuga",
        action="PASSWORD_RESET",
        status="COMPLETED",
        reason="Cambio solicitado",
        approved_by="analista1",
        approved_at=None,
        rejected_by=None,
        rejected_at=None,
        completed_at=None,
        result="Restablecida",
        created_at=None,
        updated_at=None,
    )


# ============================================================
# API key a nivel de router (HTTP)
# ============================================================

def test_pending_sin_api_key_devuelve_401():
    # Sin context manager: no se dispara el lifespan ni el worker.
    from app.main import app as fastapi_app

    client = TestClient(fastapi_app)

    response = client.get("/api/v1/approvals/pending")

    assert response.status_code == 401


def test_pending_con_api_key_invalida_devuelve_403():
    from app.main import app as fastapi_app

    client = TestClient(fastapi_app)

    response = client.get(
        "/api/v1/approvals/pending",
        headers={"X-API-Key": "incorrecta"},
    )

    assert response.status_code == 403


# ============================================================
# approve: autorización
# ============================================================

def test_approve_no_aprobador_recibe_403(monkeypatch):
    monkeypatch.setattr(settings, "ad_approvers", "analista1")

    with (
        patch.object(approvals_module, "ApprovalService") as MockSvc,
        pytest.raises(HTTPException) as exc,
    ):
        approve(
            ApprovalActionRequest(analyst_id="nadie"),
            request_id=42,
        )

    assert exc.value.status_code == 403
    MockSvc.return_value.approve_request.assert_not_called()


def test_approve_sin_analista_devuelve_403(monkeypatch):
    monkeypatch.setattr(settings, "ad_approvers", "analista1")

    with pytest.raises(HTTPException) as exc:
        approve(ApprovalActionRequest(analyst_id=""), request_id=42)

    assert exc.value.status_code == 403


# ============================================================
# approve: flujo + notificación al solicitante (regresión)
# ============================================================

def test_approve_ejecuta_y_notifica(monkeypatch):
    monkeypatch.setattr(settings, "ad_approvers", "analista1")

    executed = executed_approval()

    with patch.object(approvals_module, "ApprovalService") as MockSvc, \
         patch.object(approvals_module, "notify_requester_result") as MockNotify:
        MockSvc.return_value.approve_request.return_value = SimpleNamespace(id=42)
        MockSvc.return_value.execute_approved_request.return_value = executed

        result = approve(
            ApprovalActionRequest(analyst_id="analista1"),
            request_id=42,
        )

    assert result is executed
    MockSvc.return_value.approve_request.assert_called_once_with(
        request_id=42,
        approved_by="analista1",
    )
    MockSvc.return_value.execute_approved_request.assert_called_once_with(
        request_id=42,
    )
    MockNotify.assert_called_once_with(executed)


def test_approve_notificacion_fallida_no_rompe_la_api(monkeypatch):
    """Regresión: si Teams falla, la API debe devolver igual el resultado."""
    monkeypatch.setattr(settings, "ad_approvers", "analista1")

    executed = executed_approval()

    with patch.object(approvals_module, "ApprovalService") as MockSvc, \
         patch.object(
             approvals_module,
             "notify_requester_result",
             side_effect=RuntimeError("Graph caído"),
         ):
        MockSvc.return_value.approve_request.return_value = SimpleNamespace(id=42)
        MockSvc.return_value.execute_approved_request.return_value = executed

        result = approve(
            ApprovalActionRequest(analyst_id="analista1"),
            request_id=42,
        )

    assert result is executed


def test_approve_solicitud_inexistente_devuelve_404(monkeypatch):
    monkeypatch.setattr(settings, "ad_approvers", "analista1")

    with patch.object(approvals_module, "ApprovalService") as MockSvc:
        MockSvc.return_value.approve_request.side_effect = LookupError(
            "Solicitud 42 no existe."
        )

        with pytest.raises(HTTPException) as exc:
            approve(
                ApprovalActionRequest(analyst_id="analista1"),
                request_id=42,
            )

    assert exc.value.status_code == 404


def test_approve_estado_invalido_devuelve_409(monkeypatch):
    monkeypatch.setattr(settings, "ad_approvers", "analista1")

    with patch.object(approvals_module, "ApprovalService") as MockSvc:
        MockSvc.return_value.approve_request.side_effect = ValueError(
            "La solicitud no está pendiente."
        )

        with pytest.raises(HTTPException) as exc:
            approve(
                ApprovalActionRequest(analyst_id="analista1"),
                request_id=42,
            )

    assert exc.value.status_code == 409


def test_approve_fallo_ad_devuelve_502(monkeypatch):
    monkeypatch.setattr(settings, "ad_approvers", "analista1")

    with patch.object(approvals_module, "ApprovalService") as MockSvc:
        MockSvc.return_value.approve_request.side_effect = RuntimeError(
            "AD no responde"
        )

        with pytest.raises(HTTPException) as exc:
            approve(
                ApprovalActionRequest(analyst_id="analista1"),
                request_id=42,
            )

    assert exc.value.status_code == 502


# ============================================================
# reject: notificación de rechazo (regresión)
# ============================================================

def test_reject_notifica_al_solicitante(monkeypatch):
    monkeypatch.setattr(settings, "ad_approvers", "analista1")

    rejected = executed_approval()
    rejected.status = "REJECTED"

    with patch.object(approvals_module, "ApprovalService") as MockSvc, \
         patch.object(
             approvals_module,
             "notify_requester_rejection",
         ) as MockNotify:
        MockSvc.return_value.reject_request.return_value = rejected

        result = reject(
            ApprovalRejectRequest(
                analyst_id="analista1",
                reason="No autorizado",
            ),
            request_id=42,
        )

    assert result is rejected
    MockSvc.return_value.reject_request.assert_called_once_with(
        request_id=42,
        rejected_by="analista1",
        reason="No autorizado",
    )
    MockNotify.assert_called_once_with(rejected)


def test_reject_notificacion_fallida_no_rompe_la_api(monkeypatch):
    monkeypatch.setattr(settings, "ad_approvers", "analista1")

    rejected = executed_approval()
    rejected.status = "REJECTED"

    with patch.object(approvals_module, "ApprovalService") as MockSvc, \
         patch.object(
             approvals_module,
             "notify_requester_rejection",
             side_effect=RuntimeError("Graph caído"),
         ):
        MockSvc.return_value.reject_request.return_value = rejected

        result = reject(
            ApprovalRejectRequest(analyst_id="analista1"),
            request_id=42,
        )

    assert result is rejected


def test_reject_no_aprobador_recibe_403(monkeypatch):
    monkeypatch.setattr(settings, "ad_approvers", "analista1")

    with (
        patch.object(approvals_module, "ApprovalService") as MockSvc,
        pytest.raises(HTTPException) as exc,
    ):
        reject(
            ApprovalRejectRequest(analyst_id="nadie"),
            request_id=42,
        )

    assert exc.value.status_code == 403
    MockSvc.return_value.reject_request.assert_not_called()
