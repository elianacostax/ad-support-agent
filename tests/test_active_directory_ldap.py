"""Mapeo de atributos LDAP de app/services/active_directory.py.

Cubre el puente entre los atributos crudos de AD y el diccionario que
consumen agent.py y approval_service.py, sin red: la conexión se mockea.

El punto más delicado es lockoutTime: AD lo devuelve como la época
FileTime (1601-01-01) cuando la cuenta NO está bloqueada, y ldap3 puede
entregarlo con o sin zona horaria. Un truthiness ingenuo marcaría toda
cuenta sana como bloqueada.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.services import active_directory as ad

_UAC_NORMAL = 512                     # cuenta normal, habilitada
_UAC_DISABLED = 514                   # 512 | ACCOUNTDISABLE
_UAC_PWD_EXPIRED = _UAC_NORMAL | 0x800000

_BASE_DN = "DC=unetrans,DC=com,DC=co"
_FILETIME_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)


class _Attr:
    def __init__(self, value):
        self.value = value


class _Entry:
    def __init__(self, sam="sacosta", uac=_UAC_NORMAL, lockout=None, pwd=None):
        self.sAMAccountName = _Attr(sam)
        self.userAccountControl = _Attr(uac)
        self.lockoutTime = _Attr(lockout)
        self.pwdLastSet = _Attr(pwd)
        self.entry_dn = f"CN={sam},OU=T,{_BASE_DN}"


class _FakeConn:
    """Connection de ldap3 mínima: search devuelve las entradas prefijadas."""

    def __init__(self, entries=None, modify_ok=True, modify_error=""):
        self.entries = entries or []
        self._modify_ok = modify_ok
        self._modify_error = modify_error
        self.modify_calls = []
        self.search_calls = []
        self.bound = True
        self.result = {"description": "success"}

    def search(self, *, search_base, search_filter, attributes):
        self.search_calls.append((search_base, search_filter))
        return True

    def modify(self, dn, changes):
        self.modify_calls.append((dn, changes))
        if not self._modify_ok:
            self.result = {"description": "insufficientAccessRights",
                           "message": self._modify_error}
        return self._modify_ok

    def unbind(self):
        self.bound = False


@pytest.fixture
def patch_conn(monkeypatch):
    """Devuelve una función que hace que _connect() entregue el fake dado."""
    def _apply(conn):
        monkeypatch.setattr(ad, "_connect", lambda: conn)
        return conn
    return _apply


# ------------------------------------------------------------------
# _base_dn
# ------------------------------------------------------------------

def test_base_dn_deriva_del_dominio():
    assert ad._base_dn() == _BASE_DN


# ------------------------------------------------------------------
# get_user: mapeo de atributos
# ------------------------------------------------------------------

def test_get_user_mapea_atributos_basicos(patch_conn):
    pwd = datetime(2026, 10, 9, 16, 6, 44, tzinfo=timezone.utc)
    conn = patch_conn(_FakeConn([_Entry(uac=_UAC_NORMAL, pwd=pwd)]))

    result = ad.get_user("sacosta")

    assert result == {
        "username": "sacosta",
        "enabled": True,
        "locked_out": False,
        "password_expired": False,
        "password_last_set": pwd.isoformat(),
    }
    # Busca en el DN del dominio, por sAMAccountName.
    assert conn.search_calls[0][0] == _BASE_DN
    assert conn.search_calls[0][1] == "(sAMAccountName=sacosta)"
    assert conn.bound is False  # la sesión siempre se libera


def test_get_user_cuenta_deshabilitada(patch_conn):
    patch_conn(_FakeConn([_Entry(uac=_UAC_DISABLED)]))

    assert ad.get_user("sacosta")["enabled"] is False


def test_get_user_password_expirada(patch_conn):
    patch_conn(_FakeConn([_Entry(uac=_UAC_PWD_EXPIRED)]))

    assert ad.get_user("sacosta")["password_expired"] is True


def test_get_user_sin_password_seteada_da_none(patch_conn):
    patch_conn(_FakeConn([_Entry(pwd=None)]))

    assert ad.get_user("sacosta")["password_last_set"] is None


def test_get_user_devuelve_none_si_no_existe(patch_conn):
    patch_conn(_FakeConn([]))

    assert ad.get_user("noexiste") is None


def test_get_user_alfa_numero_con_guion(patch_conn):
    """El filtro LDAP debe usar el username ya validado, sin tocarlo."""
    conn = patch_conn(_FakeConn([_Entry(sam="user-1")]))

    assert ad.get_user("user-1")["username"] == "user-1"
    assert conn.search_calls[0][1] == "(sAMAccountName=user-1)"


# ------------------------------------------------------------------
# get_user: lockoutTime — el gotcha
# ------------------------------------------------------------------

@pytest.mark.parametrize("lockout", [
    _FILETIME_EPOCH,                                  # aware (lo que devolvió la prueba real)
    _FILETIME_EPOCH.replace(tzinfo=None),             # naive
])
def test_lockouttime_en_epoca_significa_sin_bloqueo(patch_conn, lockout):
    """Sin bloqueo AD devuelve 1601-01-01, NO None. No es truthy-correcto."""
    patch_conn(_FakeConn([_Entry(lockout=lockout)]))

    assert ad.get_user("sacosta")["locked_out"] is False


def test_lockouttime_ausente_significa_sin_bloqueo(patch_conn):
    patch_conn(_FakeConn([_Entry(lockout=None)]))

    assert ad.get_user("sacosta")["locked_out"] is False


def test_lockouttime_posterior_a_epoca_significa_bloqueada(patch_conn):
    locked = _FILETIME_EPOCH + timedelta(days=40000)
    patch_conn(_FakeConn([_Entry(lockout=locked)]))

    assert ad.get_user("sacosta")["locked_out"] is True


def test_lockouttime_naive_posterior_a_epoca_significa_bloqueada(patch_conn):
    locked = (_FILETIME_EPOCH + timedelta(days=40000)).replace(tzinfo=None)
    patch_conn(_FakeConn([_Entry(lockout=locked)]))

    assert ad.get_user("sacosta")["locked_out"] is True


def test_lockouttime_naive_sin_zona_se_trata_como_utc(patch_conn):
    """_as_datetime normaliza los naive a UTC antes de comparar."""
    # datetime naive a propósito: es exactamente el caso que debe normalizarse.
    naive = datetime(2026, 1, 1, 12, 0, 0)  # noqa: DTZ001

    assert ad._as_datetime(naive) == naive.replace(tzinfo=timezone.utc)


def test_as_datetime_con_none_y_tipo_invalido():
    assert ad._as_datetime(None) is None
    assert ad._as_datetime("no-es-fecha") is None


# ------------------------------------------------------------------
# _validate_username: sigue siendo la barrera (ahora también LDAP)
# ------------------------------------------------------------------

@pytest.mark.parametrize("inyeccion", [
    "*)(uid=*",
    "sacosta)(objectClass=*",
    "a)(&(b=c",
    "user)(|(uid=*",
    "sacosta\\00",
    "user; rm -rf /",
    "user$(calc)",
    "../etc/passwd",
    "user nombre",
    "",
])
def test_inyeccion_ldap_y_shell_rechazada(inyeccion):
    with pytest.raises(ValueError):
        ad._validate_username(inyeccion)


@pytest.mark.parametrize("valido", ["sacosta", "user-1", "user_name", "  sacosta  "])
def test_usuarios_validos_pasan(valido):
    assert ad._validate_username(valido) == valido.strip()


# ------------------------------------------------------------------
# reset_user_password
# ------------------------------------------------------------------

def test_reset_en_cuenta_habilitada_aplica_unicodepwd(patch_conn):
    conn = patch_conn(_FakeConn([_Entry(uac=_UAC_NORMAL)]))

    result = ad.reset_user_password("sacosta")

    assert result["reset"] is True
    assert result["reason"] == "PASSWORD_RESET"
    assert result["username"] == "sacosta"
    assert result["temp_password"] == ad.TEMP_PASSWORD

    assert len(conn.modify_calls) == 1
    _dn, changes = conn.modify_calls[0]

    # unicodePwd: comillas dobles + UTF-16-LE, tal como exige AD.
    encoded = changes["unicodePwd"][0][1][0]
    assert encoded == f'"{ad.TEMP_PASSWORD}"'.encode("utf-16-le")
    assert encoded.decode("utf-16-le") == f'"{ad.TEMP_PASSWORD}"'

    # pwdLastSet=0 fuerza el cambio al iniciar sesión.
    assert changes["pwdLastSet"][0][1] == ["0"]


def test_reset_en_cuenta_deshabilitada_no_modifica(patch_conn):
    conn = patch_conn(_FakeConn([_Entry(uac=_UAC_DISABLED)]))

    result = ad.reset_user_password("sacosta")

    assert result == {
        "username": "sacosta",
        "reset": False,
        "reason": "ACCOUNT_DISABLED",
    }
    assert conn.modify_calls == []  # nada se escribió


def test_reset_usuario_inexistente_lanza_lookuperror(patch_conn):
    patch_conn(_FakeConn([]))

    with pytest.raises(LookupError):
        ad.reset_user_password("noexiste")


def test_reset_rechazado_por_permisos_lanza_runtimeerror(patch_conn):
    patch_conn(_FakeConn([_Entry()], modify_ok=False,
                         modify_error="insufficient access rights"))

    with pytest.raises(RuntimeError) as exc:
        ad.reset_user_password("sacosta")

    assert "restablecer" in str(exc.value)


def test_reset_libera_laconexion_incluso_si_falla(patch_conn):
    conn = patch_conn(_FakeConn([_Entry()], modify_ok=False))

    with pytest.raises(RuntimeError):
        ad.reset_user_password("sacosta")

    assert conn.bound is False


# ------------------------------------------------------------------
# unlock_user
# ------------------------------------------------------------------

def test_unlock_en_cuenta_bloqueada_escribe_cero(patch_conn):
    locked = _FILETIME_EPOCH + timedelta(days=40000)
    conn = patch_conn(_FakeConn([_Entry(lockout=locked)]))

    result = ad.unlock_user("sacosta")

    assert result == {"username": "sacosta", "unlocked": True, "reason": "UNLOCKED"}
    assert conn.modify_calls[0][1] == {"lockoutTime": [(ad.MODIFY_REPLACE, ["0"])]}


def test_unlock_en_cuenta_no_bloqueada_no_modifica(patch_conn):
    conn = patch_conn(_FakeConn([_Entry(lockout=_FILETIME_EPOCH)]))

    result = ad.unlock_user("sacosta")

    assert result == {
        "username": "sacosta",
        "unlocked": False,
        "reason": "ACCOUNT_NOT_LOCKED",
    }
    assert conn.modify_calls == []


def test_unlock_en_cuenta_deshabilitada_no_modifica(patch_conn):
    conn = patch_conn(_FakeConn([_Entry(uac=_UAC_DISABLED)]))

    result = ad.unlock_user("sacosta")

    assert result == {
        "username": "sacosta",
        "unlocked": False,
        "reason": "ACCOUNT_DISABLED",
    }
    assert conn.modify_calls == []


def test_unlock_usuario_inexistente_lanza_lookuperror(patch_conn):
    patch_conn(_FakeConn([]))

    with pytest.raises(LookupError):
        ad.unlock_user("noexiste")


def test_unlock_rechazado_por_permisos_lanza_runtimeerror(patch_conn):
    patch_conn(_FakeConn([_Entry(lockout=_FILETIME_EPOCH + timedelta(days=1))],
                         modify_ok=False))

    with pytest.raises(RuntimeError) as exc:
        ad.unlock_user("sacosta")

    assert "desbloquear" in str(exc.value)


# ------------------------------------------------------------------
# _connect
# ------------------------------------------------------------------

def test_connect_sin_cuenta_de_servicio_falla_explicito(monkeypatch):
    """Sin AD_BIND_USER la operación falla con error claro, sin red."""
    monkeypatch.setattr(ad.settings, "ad_bind_user", "")
    monkeypatch.setattr(ad.settings, "ad_bind_password", "")

    with pytest.raises(RuntimeError) as exc:
        ad._connect()

    assert "AD_BIND_USER" in str(exc.value)


# ------------------------------------------------------------------
# list_locked_enabled_users
# ------------------------------------------------------------------

def test_list_locked_devuelve_solo_bloqueadas_habilitadas(patch_conn):
    """Debe filtrar: habilitada+bloqueada sí; deshabilitada no; habilitada sin bloqueo no."""
    ahora = _FILETIME_EPOCH
    bloqueada = _FILETIME_EPOCH.replace(year=2026)

    entradas = [
        _Entry(sam="sacosta", uac=_UAC_NORMAL, lockout=bloqueada),   # HABILITADA + BLOQUEADA -> incluir
        _Entry(sam="inactivo", uac=_UAC_DISABLED, lockout=bloqueada), # DESHABILITADA + BLOQUEADA -> excluir
        _Entry(sam="sana", uac=_UAC_NORMAL, lockout=ahora),           # HABILITADA + SIN BLOQUEO -> excluir
    ]
    conn = patch_conn(_FakeConn(entradas))

    resultado = ad.list_locked_enabled_users()

    usernames = {u["username"] for u in resultado}
    assert usernames == {"sacosta"}

    # El filtro LDAP debe contener la regla de bit AND y lockoutTime>=1
    filtro = conn.search_calls[0][1]
    assert "1.2.840.113556.1.4.803" in filtro
    assert "lockoutTime>=1" in filtro
    assert conn.bound is False


def test_list_locked_devuelve_lista_vacia_sin_coincidencias(patch_conn):
    """Si no hay cuentas bloqueadas y habilitadas, devuelve [] sin fallar."""
    conn = patch_conn(_FakeConn([
        _Entry(sam="sana", uac=_UAC_NORMAL, lockout=_FILETIME_EPOCH),
    ]))

    assert ad.list_locked_enabled_users() == []
    assert conn.bound is False


def test_list_locked_campos_de_cada_usuario(patch_conn):
    """Cada entrada debe traer username, dn, lockout_time y opcionalmente mail/upn."""
    bloqueada = _FILETIME_EPOCH.replace(year=2026)
    pwd = datetime(2026, 10, 1, tzinfo=timezone.utc)
    entrada = _Entry(
        sam="sacosta",
        uac=_UAC_NORMAL,
        lockout=bloqueada,
        pwd=pwd,
    )
    entrada.userPrincipalName = type("A", (), {"value": "sacosta@unetrans.com.co"})()
    entrada.displayName = type("A", (), {"value": "Sergio Acosta"})()
    entrada.mail = type("A", (), {"value": "sacosta@unetrans.com.co"})()
    entrada.distinguishedName = type("A", (), {"value": "CN=Sergio,OU=Usuarios,DC=unetrans,DC=com,DC=co"})()

    conn = patch_conn(_FakeConn([entrada]))

    resultado = ad.list_locked_enabled_users()

    assert len(resultado) == 1
    u = resultado[0]
    assert u["username"] == "sacosta"
    assert u["locked_out"] is True
    assert u["dn"] == "CN=Sergio,OU=Usuarios,DC=unetrans,DC=com,DC=co"
    assert u["mail"] == "sacosta@unetrans.com.co"
    assert u["user_principal_name"] == "sacosta@unetrans.com.co"
    assert u["lockout_time"] is not None
    assert conn.bound is False


def test_list_locked_falla_si_no_hay_cuenta_de_servicio(monkeypatch):
    """Sin AD_BIND_USER debe lanzar RuntimeError claro."""
    monkeypatch.setattr(ad.settings, "ad_bind_user", "")
    monkeypatch.setattr(ad.settings, "ad_bind_password", "")

    with pytest.raises(RuntimeError) as exc:
        ad.list_locked_enabled_users()

    assert "AD_BIND_USER" in str(exc.value)
