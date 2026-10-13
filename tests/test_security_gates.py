"""Pruebas de las puertas de seguridad: API key y autorización de aprobadores."""
import pytest

from app.auth import verify_api_key
from app.config import settings
from app.services.authorization import is_ad_approver

# ============================================================
# verify_api_key
# ============================================================

def test_api_key_falta_devuelve_401():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        verify_api_key(x_api_key=None)

    assert exc.value.status_code == 401


def test_api_key_incorrecta_devuelve_403():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        verify_api_key(x_api_key="clave-invalida")

    assert exc.value.status_code == 403


def test_api_key_correcta_pasa():
    assert verify_api_key(x_api_key=settings.api_key) is None


def test_api_key_vacia_no_pasa():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        verify_api_key(x_api_key="")

    assert exc.value.status_code == 401


# ============================================================
# is_ad_approver
# ============================================================

def test_aprobador_en_lista(monkeypatch):
    monkeypatch.setattr(settings, "ad_approvers", "analista1, sacosta")

    assert is_ad_approver("analista1") is True
    assert is_ad_approver("sacosta") is True


def test_aprobador_mayusculas_espacios(monkeypatch):
    monkeypatch.setattr(settings, "ad_approvers", "analista1, sacosta")

    assert is_ad_approver("  SACOSTA  ") is True
    assert is_ad_approver("Analista1") is True


def test_aprobador_como_correo_entra(monkeypatch):
    """Entra puede entregar 'sacosta@sitti.com.co' aunque la lista tenga 'sacosta'."""
    monkeypatch.setattr(settings, "ad_approvers", "sacosta")

    assert is_ad_approver("sacosta@sitti.com.co") is True


def test_no_aprobador_rechazado(monkeypatch):
    monkeypatch.setattr(settings, "ad_approvers", "analista1")

    assert is_ad_approver("otro-usuario") is False
    assert is_ad_approver("otro@sitti.com.co") is False


def test_valores_vacios_rechazados(monkeypatch):
    monkeypatch.setattr(settings, "ad_approvers", "analista1")

    assert is_ad_approver("") is False
    assert is_ad_approver(None) is False
    assert is_ad_approver("   ") is False


def test_lista_vacia_nadie_aprueba(monkeypatch):
    monkeypatch.setattr(settings, "ad_approvers", "")

    assert is_ad_approver("sacosta") is False
