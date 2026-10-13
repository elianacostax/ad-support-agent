import time

import requests

from app.config import settings

GRAPH_BETA_URL = "https://graph.microsoft.com/beta"

# Errores transitorios que justifican reintentar.
RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}


class TeamsApprovalService:
    def __init__(self, access_token: str):
        if not access_token:
            raise ValueError(
                "El access token de Microsoft Graph es obligatorio."
            )

        self.access_token = access_token

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def create_unlock_approval(
        self,
        *,
        ad_username: str,
        requester_id: str,
        request_id: int,
        requester_name: str | None = None,
        action: str = "UNLOCK_ACCOUNT",
    ) -> str:
        TITLES = {
            "UNLOCK_ACCOUNT": "Solicitud de desbloqueo",
            "PASSWORD_RESET": (
                "Solicitud de restablecimiento de contraseña"
            ),
        }

        action_title = TITLES.get(
            action,
            "Solicitud de desbloqueo",
        )

        payload = {
            "displayName": (
                f"{action_title}: {ad_username}"
            ),
            "description": (
                f"Se solicita {action_title.lower()} para la cuenta "
                f"de red: {ad_username}\n"
                f"Solicitante: {requester_name or requester_id}\n"
                f"Solicitud interna: #{request_id}"
            ),
            "approvalType": "basic",
            "approvers": [
                {
                    "group": {
                        "id": settings.ad_approval_group_id,
                    }
                }
            ],
        }

        response = self._post_with_retry(
            f"{GRAPH_BETA_URL}/solutions/approval/approvalItems",
            payload,
        )

        if response.status_code not in (200, 201, 202):
            raise RuntimeError(
                "No fue posible crear la aprobación en Microsoft "
                f"Graph. HTTP {response.status_code}: "
                f"{response.text}"
            )

        # Algunas respuestas pueden traer directamente el ID.
        if response.content:
            try:
                data = response.json()
            except ValueError:
                data = {}

            approval_id = data.get("id")

            if approval_id:
                return approval_id

        # En el flujo 202 Accepted, Graph devuelve Location.
        location = response.headers.get("Location")

        if not location:
            # Sin Location no hay cómo resolver la operación;
            # se intenta recuperar el ID por búsqueda.
            recovered = self._find_approval_by_request_id(request_id)

            if recovered:
                return recovered

            raise RuntimeError(
                "Microsoft Graph aceptó la solicitud, pero no "
                "devolvió el ID ni la ubicación de la operación."
            )

        try:
            return self._resolve_operation(location)
        except RuntimeError:
            # La aprobación pudo haberse creado igual.
            # Se intenta recuperar el ID mediante búsqueda.
            recovered = self._find_approval_by_request_id(request_id)

            if recovered:
                return recovered

            raise

    def _post_with_retry(
        self,
        url: str,
        payload: dict,
        *,
        max_attempts: int = 3,
        delay_seconds: float = 2.0,
    ):
        """
        Realiza el POST reintentando ante errores transitorios
        de Microsoft Graph (429/5xx) o de red.
        """
        last_response = None
        last_error: Exception | None = None

        for attempt in range(max_attempts):
            if attempt:
                time.sleep(delay_seconds * attempt)

            try:
                response = requests.post(
                    url,
                    headers=self._headers(),
                    json=payload,
                    timeout=30,
                )
            except requests.RequestException as exc:
                last_error = exc
                last_response = None
                continue

            if response.status_code in RETRYABLE_STATUS_CODES:
                last_response = response
                last_error = None
                continue

            return response

        if last_response is not None:
            return last_response

        raise RuntimeError(
            "No fue posible contactar Microsoft Graph para crear "
            f"la aprobación tras {max_attempts} intentos: {last_error}"
        )

    def _find_approval_by_request_id(self, request_id: int) -> str | None:
        """
        Busca en Graph una aprobación reciente cuya descripción
        contenga el número de solicitud interno. Sirve para
        recuperar el ID cuando la resolución de la operación falla.
        """
        try:
            response = requests.get(
                f"{GRAPH_BETA_URL}/solutions/approval/approvalItems",
                headers=self._headers(),
                params={"$top": 100},
                timeout=30,
            )
        except requests.RequestException:
            return None

        if not response.ok:
            return None

        try:
            data = response.json()
        except ValueError:
            return None

        marker = f"Solicitud interna: #{request_id}"

        for item in data.get("value", []):
            description = item.get("description") or ""

            if marker in description:
                item_id = item.get("id")

                if item_id:
                    return item_id

        return None

    def _resolve_operation(
        self,
        location: str,
        *,
        max_attempts: int = 10,
        delay_seconds: float = 1.5,
    ) -> str:
        last_transient_status: int | None = None

        for _ in range(max_attempts):
            try:
                response = requests.get(
                    location,
                    headers=self._headers(),
                    timeout=30,
                )
            except requests.RequestException:
                # Error de red: se reintenta.
                time.sleep(delay_seconds)
                continue

            if response.status_code in RETRYABLE_STATUS_CODES:
                # Error transitorio (p. ej. HTTP 500): se reintenta
                # en lugar de fallar de inmediato.
                last_transient_status = response.status_code
                time.sleep(delay_seconds)
                continue

            if not response.ok:
                raise RuntimeError(
                    "No fue posible consultar la operación de "
                    f"Microsoft Graph. HTTP {response.status_code}: "
                    f"{response.text}"
                )

            try:
                data = response.json()
            except ValueError:
                data = {}

            resource_location = data.get("resourceLocation")

            if resource_location:
                approval_id = (
                    resource_location.rstrip("/")
                    .split("/")[-1]
                )

                if approval_id:
                    return approval_id

            status = str(
                data.get("status", "")
            ).casefold()

            if status in {
                "failed",
                "canceled",
                "cancelled",
            }:
                raise RuntimeError(
                    "Microsoft Graph no pudo completar la "
                    f"creación de la aprobación. Estado: {status}"
                )

            time.sleep(delay_seconds)

        if last_transient_status is not None:
            raise RuntimeError(
                "No fue posible consultar la operación de "
                f"Microsoft Graph. HTTP {last_transient_status} "
                "(error transitorio persistente)."
            )

        raise RuntimeError(
            "Microsoft Graph no devolvió el recurso de aprobación "
            "dentro del tiempo esperado."
        )

    def get_approval_responses(
        self,
        approval_id: str,
    ) -> dict:
        response = requests.get(
            f"{GRAPH_BETA_URL}/solutions/approval/"
            f"approvalItems/{approval_id}/responses",
            headers=self._headers(),
            timeout=30,
        )

        if not response.ok:
            raise RuntimeError(
                "No fue posible consultar las respuestas de "
                f"la aprobación. HTTP {response.status_code}: "
                f"{response.text}"
            )

        return response.json()

    def get_approval_result(
        self,
        approval_id: str,
    ) -> dict:
        data = self.get_approval_responses(
            approval_id
        )

        responses = data.get("value", [])

        if not responses:
            return {
                "status": "PENDING",
                "response": None,
                "comments": None,
                "approved_by": None,
                "created_at": None,
            }

        latest = responses[-1]

        response_value = latest.get("response")

        if response_value == "Approve":
            status = "APPROVED"
        elif response_value == "Reject":
            status = "REJECTED"
        else:
            status = "PENDING"

        created_by = latest.get("createdBy") or {}
        user = created_by.get("user") or {}

        return {
            "status": status,
            "response": response_value,
            "comments": latest.get("comments"),
            "approved_by": user.get("id"),
            "created_at": latest.get("createdDateTime"),
        }