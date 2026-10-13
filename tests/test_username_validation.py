"""Validación de usuario AD: la barrera anti-inyección de comandos PowerShell."""
import pytest

from app.services.active_directory import _validate_username

VALIDOS = [
    "sacosta",
    "susuga",
    "jareiza",
    "user-1",
    "user_name",
    "1234",
    "  sacosta  ",  # se recorta
]


INJECTIONES = [
    "user; rm -rf /",
    "user & whoami",
    "user | dir",
    "user`n whoami",
    "user$(calc)",
    "user`Get-Process",
    "user>out.txt",
    "user<in.txt",
    "user' OR '1'='1",
    'user"; Write-Host',
    "../etc/passwd",
    "user nombre",   # espacio
    "user\tname",    # tab
    "",              # vacío
    "   ",           # solo espacios
    "user/slash",
    "user*star",
    "user?query",
]


def test_usuarios_validos_pasan():
    for username in VALIDOS:
        assert _validate_username(username) == username.strip()


def test_inyecciones_rechazadas():
    for username in INJECTIONES:
        with pytest.raises(ValueError):
            _validate_username(username)
