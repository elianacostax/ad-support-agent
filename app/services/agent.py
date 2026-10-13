from datetime import datetime, timedelta, timezone

from app.db.database import SessionLocal
from app.services.active_directory import get_user
from app.services.approval_service import ApprovalService
from app.services.audit import write_audit
from app.services.intent import detect_intent, extract_username, is_stopword
from app.services.microsoft_auth import acquire_approval_token

CONFIRMATION_TIMEOUT_MINUTES = 2


class ADSupportAgent:
    def __init__(
        self,
        session_id: str,
        requester_id: str | None = None,
        requester_name: str | None = None,
    ):
        self.session_id = session_id
        self.requester_id = requester_id
        self.requester_name = requester_name

        self.pending_action: str | None = None
        self.pending_username: str | None = None
        self.pending_at: datetime | None = None

    def process(self, message: str) -> str:
        message = message.strip()

        if not message:
            return "¿En qué puedo ayudarte?"

        if self.pending_action in {"unlock", "password_reset", "status_username"} and self._is_pending_expired():
            username = self.pending_username

            was_password_reset = self.pending_action == "password_reset"

            write_audit(
                session_id=self.session_id,
                requester_id=self.requester_id,
                action=(
                    "UNLOCK"
                    if self.pending_action == "unlock"
                    else (
                        "PASSWORD_RESET"
                        if was_password_reset
                        else "STATUS"
                    )
                ),
                username=username,
                result="EXPIRED",
                reason="CONFIRMATION_TIMEOUT",
            )

            self._reset()

            if was_password_reset:
                return (
                    "La solicitud de restablecimiento de contraseña "
                    "ha vencido por tiempo de espera. Por seguridad, "
                    "debes iniciar nuevamente la solicitud."
                )

            return (
                "La solicitud de desbloqueo ha vencido por tiempo de espera. "
                "Por seguridad, debes iniciar nuevamente la solicitud."
            )

        if self.pending_action in {"unlock", "password_reset"}:
            if not self.pending_username:
                username = extract_username(message)

                if username:
                    return self._check_account(
                        username,
                        for_password_reset=(
                            self.pending_action == "password_reset"
                        ),
                    )

                text = message.casefold().strip()

                # Respuestas de confirmación antes de tener usuario:
                # no deben tomarse como nombre de usuario.
                if text in {"no", "cancelar", "cancela", "cancelado"}:
                    was_password_reset = (
                        self.pending_action == "password_reset"
                    )

                    write_audit(
                        session_id=self.session_id,
                        requester_id=self.requester_id,
                        action=(
                            "PASSWORD_RESET_REQUEST"
                            if was_password_reset
                            else "UNLOCK_REQUEST"
                        ),
                        username=None,
                        result="CANCELLED",
                        reason="USER_DECLINED",
                    )

                    self._reset()

                    return "Solicitud cancelada."

                if text in {"si", "sí", "confirmo", "confirmar"}:
                    return "¿Cuál es tu usuario de red?"

                if self._looks_like_username(message):
                    return self._check_account(
                        message,
                        for_password_reset=(
                            self.pending_action == "password_reset"
                        ),
                    )

                return "¿Cuál es tu usuario de red?"

            return self._handle_confirmation(message)

        if self.pending_action == "status_username":
            username = extract_username(message) or message.strip()

            if not self._looks_like_username(username):
                return "¿Cuál es tu usuario de red?"

            return self._return_status(username)

        intent = detect_intent(message)

        if intent == "unlock":
            username = extract_username(message)

            if not username:
                self.pending_action = "unlock"
                self.pending_at = datetime.now(timezone.utc)

                return "Claro. ¿Cuál es tu usuario de red?"

            return self._check_account(username)


        if intent == "password_reset":
            username = extract_username(message)

            if not username:
                self.pending_action = "password_reset"
                self.pending_at = datetime.now(timezone.utc)

                return "Claro. ¿Cuál es tu usuario de red?"

            return self._check_account(username, for_password_reset=True)

        if intent == "status":
            username = extract_username(message)

            if username:
                return self._return_status(username)

            self.pending_action = "status_username"
            self.pending_at = datetime.now(timezone.utc)

            return "¿Cuál es tu usuario de red?"
        return (
            "Puedo ayudarte con el desbloqueo de cuentas, el "
            "restablecimiento de contraseña o la consulta de estado. "
            "Indícame qué necesitas y, si corresponde, tu usuario "
            "de red."
        )

    def _check_account(self, username: str, for_password_reset: bool = False) -> str:
        audit_action = "PASSWORD_RESET" if for_password_reset else "UNLOCK"

        try:
            user = get_user(username)

        except ValueError:
            write_audit(
                session_id=self.session_id,
                requester_id=self.requester_id,
                action=audit_action,
                username=username,
                result="REJECTED",
                reason="INVALID_USERNAME",
            )

            return (
                "El usuario de red indicado no tiene "
                "un formato válido."
            )

        except Exception:
            write_audit(
                session_id=self.session_id,
                requester_id=self.requester_id,
                action=audit_action,
                username=username,
                result="ERROR",
                reason="AD_QUERY_ERROR",
            )

            return (
                f"No pude consultar el usuario '{username}' "
                "en Active Directory."
            )

        if user is None:
            write_audit(
                session_id=self.session_id,
                requester_id=self.requester_id,
                action=audit_action,
                username=username,
                result="NOT_FOUND",
                reason="USER_NOT_FOUND",
            )

            self._reset()

            return (
                f"No encontré el usuario '{username}' "
                "en Active Directory."
            )

        if for_password_reset:
            if not user.get("enabled"):
                self._reset()
                return (
                    f"La cuenta '{username}' está deshabilitada. "
                    "No puedo restablecer contraseña de una cuenta deshabilitada."
                )

            self.pending_action = "password_reset"
            self.pending_username = user["username"]
            self.pending_at = datetime.now(timezone.utc)

            return (
                f"La cuenta '{user['username']}' existe. "
                "¿Deseas solicitar el restablecimiento de contraseña? Responde sí o no."
            )

        if not user.get("enabled"):
            write_audit(
                session_id=self.session_id,
                requester_id=self.requester_id,
                action="UNLOCK",
                username=username,
                result="REJECTED",
                reason="ACCOUNT_DISABLED",
            )

            self._reset()

            return (
                f"La cuenta '{username}' está deshabilitada. "
                "No puedo desbloquear una cuenta deshabilitada."
            )

        if not user.get("locked_out"):
            write_audit(
                session_id=self.session_id,
                requester_id=self.requester_id,
                action="UNLOCK",
                username=username,
                result="NOT_REQUIRED",
                reason="ACCOUNT_NOT_LOCKED",
            )

            self._reset()

            return (
                f"La cuenta '{username}' no está bloqueada actualmente."
            )

        self.pending_action = "unlock"
        self.pending_username = user["username"]
        self.pending_at = datetime.now(timezone.utc)

        return (
            f"La cuenta '{user['username']}' está bloqueada. "
            "¿Deseas solicitar el desbloqueo? Responde sí o no."
        )

    def _handle_confirmation(self, message: str) -> str:
        text = message.casefold().strip()

        yes = {
            "si",
            "sí",
            "confirmo",
            "confirmar",
            "desbloqueala",
            "desbloquéala",
            "desbloquearla",
            "hazlo",
            "adelante",
        }

        no = {
            "no",
            "cancelar",
            "cancela",
            "cancelado",
            "déjalo",
            "dejalo",
        }

        if text in yes:
            username = self.pending_username

            if not username:
                write_audit(
                    session_id=self.session_id,
                    requester_id=self.requester_id,
                    action="UNLOCK_REQUEST",
                    username=None,
                    result="ERROR",
                    reason="NO_PENDING_REQUEST",
                )

                self._reset()

                return (
                    "No tengo una solicitud de desbloqueo pendiente."
                )

            db = SessionLocal()

            try:
                service = ApprovalService(db)

                requester_id = self.requester_id or "UNKNOWN"

                graph_access_token = None

                token_result = acquire_approval_token()

                if token_result:
                    graph_access_token = token_result.get(
                        "access_token"
                    )

                conversation_id = (
                    self.session_id.split("teams:", 1)[1]
                    if self.session_id.startswith("teams:")
                    else None
                )

                conversation_meta = None

                if self.requester_id:
                    from app.services.conversation_store import (
                        get_conversation,
                    )

                    conversation_meta = get_conversation(
                        self.requester_id
                    )

                approval = service.create_request(
                    requester_id=requester_id,
                    action=(
                        "UNLOCK_ACCOUNT"
                        if self.pending_action == "unlock"
                        else "PASSWORD_RESET"
                    ),
                    requester_name=self.requester_name,
                    conversation_id=conversation_id,
                    conversation_service_url=(
                        conversation_meta.get("service_url")
                        if conversation_meta
                        else None
                    ),
                    ad_username=username,
                    reason=(
                        "Solicitud de desbloqueo de cuenta bloqueada."
                        if self.pending_action == "unlock"
                        else "Solicitud de restablecimiento de contraseña."
                    ),
                    graph_access_token=graph_access_token,
                )

            except RuntimeError:
                was_unlock = self.pending_action == "unlock"

                write_audit(
                    session_id=self.session_id,
                    requester_id=self.requester_id,
                    action=(
                        "UNLOCK_REQUEST"
                        if was_unlock
                        else "PASSWORD_RESET_REQUEST"
                    ),
                    username=username,
                    result="ERROR",
                    reason=(
                        "GRAPH_APPROVAL_CREATION_ERROR"
                    ),
                )

                self._reset()

                if was_unlock:
                    return (
                        "La solicitud fue registrada, pero no fue "
                        "posible crear la aprobación en Microsoft "
                        "Teams. Intenta nuevamente más tarde."
                    )

                return (
                    "No fue posible crear la aprobación en Microsoft "
                    "Teams ni restablecer la contraseña en este "
                    "momento. Intenta nuevamente más tarde."
                )

            except Exception:
                was_unlock = self.pending_action == "unlock"

                write_audit(
                    session_id=self.session_id,
                    requester_id=self.requester_id,
                    action=(
                        "UNLOCK_REQUEST"
                        if was_unlock
                        else "PASSWORD_RESET_REQUEST"
                    ),
                    username=username,
                    result="ERROR",
                    reason="APPROVAL_REQUEST_CREATION_ERROR",
                )

                self._reset()

                if was_unlock:
                    return (
                        "No fue posible registrar la solicitud de "
                        "desbloqueo. Intenta nuevamente."
                    )

                return (
                    "No fue posible registrar la solicitud de "
                    "restablecimiento de contraseña. "
                    "Intenta nuevamente."
                )

            finally:
                db.close()

            write_audit(
                session_id=self.session_id,
                requester_id=self.requester_id,
                action=(
                    "UNLOCK_REQUEST"
                    if self.pending_action == "unlock"
                    else "PASSWORD_RESET_REQUEST"
                ),
                username=username,
                result=approval.status,
                reason=f"APPROVAL_REQUEST_{approval.id}",
            )

            was_unlock = self.pending_action == "unlock"

            self._reset()

            # Restablecimiento inmediato (fallback cuando no fue
            # posible crear la aprobación en Teams): se confirma
            # a la usuario con la contraseña temporal.
            if approval.status == "COMPLETED" and not was_unlock:
                temp_password = approval.__dict__.get(
                    "temp_password"
                )

                message = (
                    f"La contraseña de '{username}' fue "
                    f"restablecida correctamente "
                    f"(solicitud #{approval.id})."
                )

                if temp_password:
                    message += (
                        f" Contraseña temporal: \"{temp_password}\". "
                        "Debe cambiarla al iniciar sesión."
                    )

                return message

            if was_unlock:
                return (
                    f"La solicitud de desbloqueo de la cuenta "
                    f"'{username}' fue enviada a Mesa de Ayuda. "
                    f"Número de solicitud: {approval.id}. "
                    "Quedará pendiente de aprobación. "
                    "Te avisaré cuando sea resuelta."
                )

            return (
                f"La solicitud de restablecimiento de contraseña de la cuenta "
                f"'{username}' fue enviada a Mesa de Ayuda. "
                f"Número de solicitud: {approval.id}. "
                "Quedará pendiente de aprobación. "
                "Te avisaré con la contraseña temporal cuando "
                "sea aprobada."
            )

        if text in no:
            username = self.pending_username

            write_audit(
                session_id=self.session_id,
                requester_id=self.requester_id,
                action=(
                    "UNLOCK_REQUEST"
                    if self.pending_action == "unlock"
                    else "PASSWORD_RESET_REQUEST"
                ),
                username=username,
                result="CANCELLED",
                reason="USER_DECLINED",
            )

            self._reset()

            return "Solicitud cancelada."

        if self.pending_action == "password_reset":
            return (
                "¿Deseas solicitar el restablecimiento de contraseña? Responde sí o no."
            )

        return (
            "La cuenta está bloqueada. "
            "¿Deseas solicitar el desbloqueo? Responde sí o no."
        )

    def _return_status(self, username: str) -> str:
        try:
            user = get_user(username)

        except ValueError:
            self._reset()
            return "El usuario de red indicado no tiene un formato válido."

        except Exception:
            self._reset()
            return f"No pude consultar el usuario '{username}' en Active Directory."

        if user is None:
            self._reset()
            return f"No encontré el usuario '{username}' en Active Directory."

        self._reset()

        estado = "habilitada" if user.get("enabled") else "deshabilitada"
        bloqueada = "sí" if user.get("locked_out") else "no"
        expirada = "sí" if user.get("password_expired") else "no"

        return (
            f"Estado de la cuenta '{user['username']}':\n"
            f"- Habilitada: {estado}\n"
            f"- Bloqueada: {bloqueada}\n"
            f"- Contraseña expirada: {expirada}\n"
            f"- Último cambio de contraseña: {user.get('password_last_set') or 'desconocido'}"
        )

    def _is_pending_expired(self) -> bool:
        if self.pending_at is None:
            return True

        expiration = self.pending_at + timedelta(
            minutes=CONFIRMATION_TIMEOUT_MINUTES
        )

        return datetime.now(timezone.utc) >= expiration

    @staticmethod
    def _looks_like_username(message: str) -> bool:
        message = message.strip()

        if not message or " " in message:
            return False

        # Palabras vacías ("si", "no", "cuenta"...) jamás son usuario.
        if is_stopword(message):
            return False

        if len(message) < 3 or len(message) > 64:
            return False

        return (
            message.replace("-", "")
            .replace("_", "")
            .isalnum()
        )

    def _reset(self):
        self.pending_action = None
        self.pending_username = None
        self.pending_at = None


