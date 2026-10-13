from pathlib import Path

import msal

from app.config import settings

AUTHORITY = f"https://login.microsoftonline.com/{settings.tenant_id}"

SCOPES = [
    "User.Read",
    "ApprovalSolution.ReadWrite",
]

GRAPH_APP_SCOPES = [
    "https://graph.microsoft.com/.default",
]

BASE_DIR = Path(__file__).resolve().parent.parent.parent
CACHE_DIR = BASE_DIR / "data"
CACHE_FILE = CACHE_DIR / "msal_token_cache.bin"


def _create_cache() -> msal.SerializableTokenCache:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    cache = msal.SerializableTokenCache()

    if CACHE_FILE.exists():
        try:
            cache.deserialize(
                CACHE_FILE.read_text(encoding="utf-8")
            )
        except Exception:
            # Si la caché está corrupta, se inicia una nueva.
            pass

    return cache


def _save_cache(cache: msal.SerializableTokenCache) -> None:
    if not cache.has_state_changed:
        return

    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    CACHE_FILE.write_text(
        cache.serialize(),
        encoding="utf-8",
    )


def create_msal_app() -> msal.ConfidentialClientApplication:
    cache = _create_cache()

    return msal.ConfidentialClientApplication(
        client_id=settings.client_id,
        client_credential=settings.client_secret,
        authority=AUTHORITY,
        token_cache=cache,
    )


def acquire_token_silently(username: str | None = None) -> dict | None:
    app = create_msal_app()

    accounts = app.get_accounts()

    if not accounts:
        return None

    account = None

    if username:
        normalized_username = username.casefold()

        for candidate in accounts:
            candidate_username = (
                candidate.get("username") or ""
            ).casefold()

            if candidate_username == normalized_username:
                account = candidate
                break

    if account is None:
        account = accounts[0]

    result = app.acquire_token_silent(
        scopes=SCOPES,
        account=account,
    )

    if not result:
        return None

    _save_cache(app.token_cache)

    return result


def acquire_approval_token() -> dict | None:
    """
    Obtiene un token para Graph usando primero el caché
    persistente y, si falla, un recurso ROPC con la
    cuenta técnica configurada.
    """
    silent = acquire_token_silently(
        settings.approval_account or None
    )

    if silent and silent.get("access_token"):
        return silent

    if (
        not settings.approval_account_username
        or not settings.approval_account_password
    ):
        return None

    app = create_msal_app()

    ropc = app.acquire_token_by_username_password(
        username=settings.approval_account_username,
        password=settings.approval_account_password,
        scopes=SCOPES,
    )

    if "access_token" not in ropc:
        raise RuntimeError(
            ropc.get(
                "error_description",
                "No fue posible obtener el token de aprobaciones.",
            )
        )

    _save_cache(app.token_cache)

    return ropc


def acquire_app_token() -> dict:
    """
    Obtiene un token de Microsoft Graph utilizando
    Client Credentials (Application Permissions).

    Este flujo no depende de un usuario conectado
    ni de la sesión web de Mesa de Ayuda.
    """
    app = msal.ConfidentialClientApplication(
        client_id=settings.client_id,
        client_credential=settings.client_secret,
        authority=AUTHORITY,
    )

    result = app.acquire_token_for_client(
        scopes=GRAPH_APP_SCOPES,
    )

    if "access_token" not in result:
        raise RuntimeError(
            result.get(
                "error_description",
                "No fue posible obtener el token de aplicación de Microsoft Graph.",
            )
        )

    return result