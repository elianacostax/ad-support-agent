from datetime import datetime, timezone

from ldap3 import ALL, MODIFY_REPLACE, Connection, Server, Tls
from ldap3.core.exceptions import LDAPBindError, LDAPException

from app.config import settings

# Flags de userAccountControl (MS-ADA1)
_UAC_ACCOUNTDISABLE = 0x0002
_UAC_PASSWORD_EXPIRED = 0x800000

# lockoutTime usa la época FileTime de Windows: 1601-01-01. Cuando la
# cuenta NO está bloqueada el atributo viene como este instante, no como
# None, por eso se compara contra la época y no se usa truthiness.
_FILETIME_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)

_LDAPS_PORT = 636
_TIMEOUT_SECONDS = 20


def _validate_username(username: str) -> str:
    username = username.strip()

    if not username:
        raise ValueError("El usuario de red es obligatorio.")

    if not username.replace("-", "").replace("_", "").isalnum():
        raise ValueError("El formato del usuario no es válido.")

    return username


# Contraseña temporal fija (decisión operativa 2026-10-09).
# Sustituyó a la generación aleatoria de 16 caracteres con `secrets`
# para simplificar su comunicación al usuario. El riesgo de un valor
# compartido se mitiga con el cambio obligatorio al iniciar sesión
# (ChangePasswordAtLogon) — ver AUDITORIA.md.
TEMP_PASSWORD = "Temporal$2026*"


def _base_dn() -> str:
    """Convierte el dominio AD (unetrans.com.co) en su DN (DC=unetrans,DC=com,DC=co)."""
    return ",".join(f"DC={part}" for part in settings.ad_domain.split("."))


def _connect() -> Connection:
    """Abre una sesión LDAPS con validación de certificado."""
    if not settings.ad_bind_user or not settings.ad_bind_password:
        raise RuntimeError(
            "Falta la cuenta de servicio de AD "
            "(AD_BIND_USER / AD_BIND_PASSWORD)."
        )

    tls = Tls(validate=True)
    server = Server(
        settings.ad_server,
        port=_LDAPS_PORT,
        use_ssl=True,
        tls=tls,
        get_info=ALL,
    )

    try:
        return Connection(
            server,
            user=settings.ad_bind_user,
            password=settings.ad_bind_password,
            auto_bind=True,
            receive_timeout=_TIMEOUT_SECONDS,
        )
    except LDAPBindError as exc:
        raise RuntimeError(
            "No fue posible autenticar la cuenta de servicio en Active Directory."
        ) from exc


def _find_user(conn: Connection, username: str):
    """Busca el usuario por sAMAccountName. Devuelve la entrada o None."""
    conn.search(
        search_base=_base_dn(),
        search_filter=f"(sAMAccountName={username})",
        attributes=[
            "sAMAccountName",
            "userAccountControl",
            "lockoutTime",
            "pwdLastSet",
        ],
    )

    return conn.entries[0] if conn.entries else None


def _as_datetime(value) -> datetime | None:
    """Normaliza los atributos de tiempo de AD a datetime UTC."""
    if value is None:
        return None

    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

    return None


def _locked_out(entry) -> bool:
    lockout_time = _as_datetime(entry.lockoutTime.value)

    if lockout_time is None:
        return False

    # Igualar o superar la época FileTime = sin bloqueo.
    return lockout_time > _FILETIME_EPOCH


def _uac(entry) -> int:
    value = entry.userAccountControl.value
    return int(value) if value is not None else 0


def get_user(username: str) -> dict | None:
    username = _validate_username(username)

    try:
        conn = _connect()

        try:
            entry = _find_user(conn, username)
        finally:
            conn.unbind()

    except LDAPException as exc:
        raise RuntimeError(
            "No fue posible consultar Active Directory."
        ) from exc

    if entry is None:
        return None

    pwd_last_set = _as_datetime(entry.pwdLastSet.value)

    return {
        "username": str(entry.sAMAccountName.value),
        "enabled": not bool(_uac(entry) & _UAC_ACCOUNTDISABLE),
        "locked_out": _locked_out(entry),
        "password_expired": bool(_uac(entry) & _UAC_PASSWORD_EXPIRED),
        "password_last_set": (
            pwd_last_set.isoformat() if pwd_last_set else None
        ),
    }


def _modify(conn: Connection, dn: str, changes: dict, accion: str) -> None:
    """Aplica cambios al usuario dado su DN. Lanza RuntimeError si AD lo rechaza."""
    if not conn.modify(dn, changes):
        detalle = conn.result.get("message") or conn.result.get("description", "")
        raise RuntimeError(
            f"No fue posible {accion} en Active Directory: {detalle}"
        )


def reset_user_password(username: str) -> dict:
    username = _validate_username(username)

    conn = _connect()

    try:
        entry = _find_user(conn, username)

        if entry is None:
            raise LookupError("Usuario no encontrado.")

        if not bool(_uac(entry) & _UAC_ACCOUNTDISABLE):
            # unicodePwd exige el valor entre comillas dobles en UTF-16-LE
            # y solo se acepta sobre una conexión cifrada (LDAPS).
            encoded = f'"{TEMP_PASSWORD}"'.encode("utf-16-le")

            _modify(
                conn,
                str(entry.entry_dn),
                {
                    "unicodePwd": [(MODIFY_REPLACE, [encoded])],
                    # pwdLastSet=0 equivale a ChangePasswordAtLogon $true.
                    "pwdLastSet": [(MODIFY_REPLACE, ["0"])],
                },
                "restablecer la contraseña",
            )

            return {
                "username": str(entry.sAMAccountName.value),
                "reset": True,
                "reason": "PASSWORD_RESET",
                "temp_password": TEMP_PASSWORD,
            }

        return {
            "username": str(entry.sAMAccountName.value),
            "reset": False,
            "reason": "ACCOUNT_DISABLED",
        }

    except (LookupError, RuntimeError):
        raise

    except LDAPException as exc:
        raise RuntimeError(
            "No fue posible restablecer la contraseña en Active Directory."
        ) from exc

    finally:
        conn.unbind()


def list_locked_enabled_users() -> list[dict]:
    """
    Enumera cuentas de AD que estén HABILITADAS pero BLOQUEADAS por lockout.

    Filtro LDAP:
      - !(userAccountControl:1.2.840.113556.1.4.803:=2)  -> cuenta habilitada (bit ACCOUNTDISABLE desactivado)
      - lockoutTime > 1601-01-01                          -> bloqueada por lockout

    Devuelve una lista de dicts con: username, sam, lockout_time (ISO), dn.
    """
    conn = _connect()

    try:
        # 1.2.840.113556.1.4.803 = reglas LDAP_MATCHING_RULE_BIT_AND (bitwise AND).
        # userAccountControl & ACCOUNTDISABLE == 0  =>  cuenta no deshabilitada.
        search_filter = (
            "(&"
            "(!(userAccountControl:1.2.840.113556.1.4.803:=2))"
            "(lockoutTime>=1)"
            ")"
        )

        conn.search(
            search_base=_base_dn(),
            search_filter=search_filter,
            attributes=[
                "sAMAccountName",
                "userPrincipalName",
                "displayName",
                "mail",
                "userAccountControl",
                "lockoutTime",
                "distinguishedName",
            ],
            size_limit=0,  # sin paginación artificial; ldap3 maneja referrals si aplica
        )

        results: list[dict] = []

        for entry in conn.entries:
            sam = str(entry.sAMAccountName.value) if entry.sAMAccountName.value else ""
            lockout_time = _as_datetime(entry.lockoutTime.value)
            dn = str(entry.distinguishedName.value) if entry.distinguishedName.value else ""

            results.append(
                {
                    "username": sam,
                    "sam": sam,
                    "user_principal_name": (
                        str(entry.userPrincipalName.value)
                        if entry.userPrincipalName.value
                        else None
                    ),
                    "display_name": (
                        str(entry.displayName.value)
                        if entry.displayName.value
                        else None
                    ),
                    "mail": (
                        str(entry.mail.value) if entry.mail.value else None
                    ),
                    "dn": dn,
                    "locked_out": True,
                    "lockout_time": lockout_time.isoformat() if lockout_time else None,
                }
            )

        return results

    except LDAPException as exc:
        raise RuntimeError(
            "No fue posible enumerar cuentas bloqueadas en Active Directory."
        ) from exc

    finally:
        conn.unbind()


def unlock_user(username: str) -> dict:
    username = _validate_username(username)

    conn = _connect()

    try:
        entry = _find_user(conn, username)

        if entry is None:
            raise LookupError("Usuario no encontrado.")

        sam = str(entry.sAMAccountName.value)

        if not bool(_uac(entry) & _UAC_ACCOUNTDISABLE):
            if not _locked_out(entry):
                return {
                    "username": sam,
                    "unlocked": False,
                    "reason": "ACCOUNT_NOT_LOCKED",
                }

            _modify(
                conn,
                str(entry.entry_dn),
                {"lockoutTime": [(MODIFY_REPLACE, ["0"])]},
                "desbloquear la cuenta",
            )

            return {
                "username": sam,
                "unlocked": True,
                "reason": "UNLOCKED",
            }

        return {
            "username": sam,
            "unlocked": False,
            "reason": "ACCOUNT_DISABLED",
        }

    except (LookupError, RuntimeError):
        raise

    except LDAPException as exc:
        raise RuntimeError(
            "No fue posible desbloquear la cuenta en Active Directory."
        ) from exc

    finally:
        conn.unbind()
