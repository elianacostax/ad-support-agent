"""
Autenticación única de la cuenta técnica de aprobaciones.

Abre el navegador, inicia sesión con la cuenta técnica y siembra
el caché MSAL (data/msal_token_cache.bin). Solo se ejecuta UNA vez;
después acquire_token_silently() renueva el token automáticamente.

Uso:
    python -m scripts.service_account_login
"""

import os
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import msal

from app.config import settings
from app.services.microsoft_auth import (
    AUTHORITY,
    SCOPES,
    _create_cache,
    _save_cache,
)


def _env(key: str, default: str = "") -> str:
    """Lee una variable del archivo .env del proyecto."""
    env_file = Path(__file__).resolve().parent.parent / ".env"

    if env_file.exists():
        for line in env_file.read_text(
            encoding="utf-8"
        ).splitlines():
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip()

    return os.environ.get(key, default)


# URI de redirección registrada en Entra para este script
# (antes settings.helpdesk_redirect_uri; la Mesa de Ayuda fue eliminada).
REDIRECT = _env(
    "HELPDESK_REDIRECT_URI",
    "http://localhost:3978/helpdesk/auth/callback",
)
PARSED = urlparse(REDIRECT)
PORT = PARSED.port or 3978

auth_code: dict[str, str | None] = {}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        params = parse_qs(urlparse(self.path).query)

        if "code" in params:
            auth_code["code"] = params["code"][0]

            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                b"<h1>Autenticacion completada</h1>"
                b"<p>Puedes cerrar esta ventana.</p>"
            )

        else:
            auth_code["error"] = params.get(
                "error_description", ["Error desconocido"]
            )[0]

            self.send_response(400)
            self.end_headers()

    def log_message(self, *args):
        pass


def main() -> None:
    cache = _create_cache()

    app = msal.ConfidentialClientApplication(
        client_id=settings.client_id,
        client_credential=settings.client_secret,
        authority=AUTHORITY,
        token_cache=cache,
    )

    state = "service-account-seed"

    url = app.get_authorization_request_url(
        scopes=SCOPES,
        state=state,
        redirect_uri=REDIRECT,
        login_hint=settings.approval_account or None,
    )

    server = HTTPServer(("", PORT), Handler)

    threading.Thread(target=server.serve_forever, daemon=True).start()

    print(
        f"Abriendo navegador. Si no abre, visita:\n{url}",
        flush=True,
    )

    webbrowser.open(url)

    import time

    while not auth_code:
        time.sleep(0.5)

    server.shutdown()

    if "error" in auth_code:
        raise SystemExit(f"Error: {auth_code['error']}")

    result = app.acquire_token_by_authorization_code(
        auth_code["code"],
        scopes=SCOPES,
        redirect_uri=REDIRECT,
    )

    if "access_token" not in result:
        raise SystemExit(
            "Error al obtener token: "
            f"{result.get('error_description')}"
        )

    _save_cache(app.token_cache)

    claims = result.get("id_token_claims", {})

    print(
        f"Token obtenido para: "
        f"{claims.get('preferred_username')}",
        flush=True,
    )

    print(
        "Caché MSAL actualizado. El worker puede operar "
        "ahora de forma autónoma.",
        flush=True,
    )


if __name__ == "__main__":
    main()
