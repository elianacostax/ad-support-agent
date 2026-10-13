from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.approval_request import ApprovalRequest
from app.services.active_directory import get_user, reset_user_password, unlock_user
from app.services.teams_approval_service import TeamsApprovalService


class ApprovalService:
    def __init__(self, db: Session):
        self.db = db

    def create_request(
        self,
        *,
        requester_id: str,
        ad_username: str,
        reason: str | None = None,
        graph_access_token: str | None = None,
        requester_name: str | None = None,
        conversation_id: str | None = None,
        conversation_service_url: str | None = None,
        action: str = "UNLOCK_ACCOUNT",
    ) -> ApprovalRequest:
        approval = ApprovalRequest(
            requester_id=requester_id,
            conversation_id=conversation_id,
            conversation_service_url=conversation_service_url,
            ad_username=ad_username,
            action=action,
            status="PENDING",
            reason=reason,
        )

        self.db.add(approval)
        self.db.commit()
        self.db.refresh(approval)

        if not graph_access_token:
            return self._handle_creation_failure(
                approval=approval,
                action=action,
                ad_username=ad_username,
                error=RuntimeError(
                    "No hay un token Graph disponible para crear "
                    "la aprobación."
                ),
            )

        try:
            teams_approval = TeamsApprovalService(graph_access_token)

            graph_approval_id = teams_approval.create_unlock_approval(
                ad_username=ad_username,
                requester_id=requester_id,
                requester_name=requester_name,
                request_id=approval.id,
                action=action,
            )

            approval.graph_approval_id = graph_approval_id

            self.db.commit()
            self.db.refresh(approval)

        except Exception as exc:
            return self._handle_creation_failure(
                approval=approval,
                action=action,
                ad_username=ad_username,
                error=exc,
            )

        return approval

    def _handle_creation_failure(
        self,
        *,
        approval: ApprovalRequest,
        action: str,
        ad_username: str,
        error: Exception,
    ) -> ApprovalRequest:
        """
        Maneja el fallo al crear la aprobación en Teams.

        - Para PASSWORD_RESET se intenta, como último recurso,
          restablecer la contraseña directamente en AD y se
          devuelve la solicitud COMPLETED con la contraseña
          temporal.
        - Para UNLOCK_ACCOUNT NO se ejecuta ninguna acción sobre
          AD: la solicitud queda FAILED.
        """
        fallback_error: Exception | None = None

        if action == "PASSWORD_RESET":
            try:
                reset_result = reset_user_password(ad_username)

                if reset_result.get("reset"):
                    approval.status = "COMPLETED"
                    approval.result = (
                        f"Se restableció la contraseña de "
                        f"'{ad_username}'. Debe cambiarla al "
                        f"iniciar sesión."
                    )
                    approval.completed_at = datetime.now(
                        timezone.utc
                    )

                    self.db.commit()
                    self.db.refresh(approval)

                    approval.__dict__["temp_password"] = (
                        reset_result.get("temp_password")
                    )

                    return approval

                fallback_error = RuntimeError(
                    reset_result.get(
                        "reason",
                        "No fue posible restablecer la contraseña.",
                    )
                )

            except Exception as exc:
                fallback_error = exc

        approval.status = "FAILED"

        if fallback_error is not None:
            approval.result = (
                f"No fue posible crear la aprobación en Microsoft "
                f"Teams: {error}. El restablecimiento directo "
                f"también falló: {fallback_error}"
            )

            self.db.commit()
            self.db.refresh(approval)

            raise RuntimeError(
                "No fue posible crear la aprobación en Microsoft "
                "Teams ni restablecer la contraseña directamente."
            ) from error

        approval.result = (
            f"No fue posible crear la aprobación en Microsoft "
            f"Teams: {error}"
        )

        self.db.commit()
        self.db.refresh(approval)

        raise RuntimeError(
            "No fue posible crear la aprobación en Microsoft Teams."
        ) from error

        

    def get_request(
        self,
        request_id: int,
    ) -> ApprovalRequest:
        approval = self.db.get(
            ApprovalRequest,
            request_id,
        )

        if not approval:
            raise LookupError(
                f"No existe la solicitud #{request_id}."
            )

        return approval

    def list_pending(self) -> list[ApprovalRequest]:
        statement = (
            select(ApprovalRequest)
            .where(
                ApprovalRequest.status == "PENDING"
            )
            .order_by(
                ApprovalRequest.created_at.asc()
            )
        )

        return list(
            self.db.scalars(statement).all()
        )

    def approve_request(
        self,
        *,
        request_id: int,
        approved_by: str,
    ) -> ApprovalRequest:
        approval = self.get_request(request_id)

        if approval.status != "PENDING":
            raise ValueError(
                f"La solicitud #{request_id} no está pendiente."
            )

        approval.status = "APPROVED"
        approval.approved_by = approved_by
        approval.approved_at = datetime.now(
            timezone.utc
        )

        self.db.commit()
        self.db.refresh(approval)

        return approval

    def reject_request(
        self,
        *,
        request_id: int,
        rejected_by: str,
        reason: str | None = None,
    ) -> ApprovalRequest:
        approval = self.get_request(request_id)

        if approval.status != "PENDING":
            raise ValueError(
                f"La solicitud #{request_id} no está pendiente."
            )

        approval.status = "REJECTED"
        approval.rejected_by = rejected_by
        approval.rejected_at = datetime.now(
            timezone.utc
        )

        if reason:
            approval.approval_comments = reason

        self.db.commit()
        self.db.refresh(approval)

        return approval

    def sync_graph_approval(
        self,
        *,
        request_id: int,
        graph_access_token: str,
    ) -> ApprovalRequest:
        approval = self.get_request(request_id)

        if not approval.graph_approval_id:
            raise ValueError(
                f"La solicitud #{request_id} no tiene "
                "un graph_approval_id."
            )

        if approval.status not in {
            "PENDING",
            "APPROVED",
            "REJECTED",
        }:
            return approval

        teams_approval = TeamsApprovalService(
            graph_access_token
        )

        result = teams_approval.get_approval_result(
            approval.graph_approval_id
        )

        graph_status = result["status"]

        if graph_status == "PENDING":
            return approval

        if graph_status == "APPROVED":
            approval.status = "APPROVED"
            approval.approved_by = (
                result.get("approved_by")
            )
            approval.approval_comments = (
                result.get("comments")
            )

            created_at = result.get("created_at")

            if created_at:
                try:
                    approval.approved_at = (
                        datetime.fromisoformat(
                            created_at.replace(
                                "Z",
                                "+00:00",
                            )
                        )
                    )
                except ValueError:
                    approval.approved_at = (
                        datetime.now(timezone.utc)
                    )

        elif graph_status == "REJECTED":
            approval.status = "REJECTED"
            approval.rejected_by = (
                result.get("approved_by")
            )
            approval.approval_comments = (
                result.get("comments")
            )

            created_at = result.get("created_at")

            if created_at:
                try:
                    approval.rejected_at = (
                        datetime.fromisoformat(
                            created_at.replace(
                                "Z",
                                "+00:00",
                            )
                        )
                    )
                except ValueError:
                    approval.rejected_at = (
                        datetime.now(timezone.utc)
                    )

        self.db.commit()
        self.db.refresh(approval)

        return approval

    def execute_approved_request(
        self,
        *,
        request_id: int,
    ) -> ApprovalRequest:
        approval = self.get_request(request_id)

        if approval.status != "APPROVED":
            raise ValueError(
                f"La solicitud #{request_id} no está aprobada."
            )

        username = approval.ad_username

        user = get_user(username)

        if not user:
            approval.status = "FAILED"
            approval.result = (
                f"No existe el usuario de AD '{username}'."
            )
            self.db.commit()
            self.db.refresh(approval)
            return approval

        if not user.get("enabled", False):
            approval.status = "FAILED"
            approval.result = (
                f"La cuenta '{username}' está deshabilitada."
            )
            self.db.commit()
            self.db.refresh(approval)
            return approval

        if approval.action == "PASSWORD_RESET":
            try:
                reset_result = reset_user_password(username)

                if not reset_result.get("reset"):
                    raise RuntimeError(
                        reset_result.get(
                            "reason",
                            "No fue posible restablecer la contraseña.",
                        )
                    )

                approval.status = "COMPLETED"
                approval.result = (
                    f"Se restableció la contraseña de "
                    f"'{username}'. Debe cambiarla al iniciar sesión."
                )
                approval.completed_at = datetime.now(timezone.utc)

                self.db.commit()
                self.db.refresh(approval)

                approval.__dict__["temp_password"] = (
                    reset_result.get("temp_password")
                )

                return approval

            except Exception as exc:
                approval.status = "FAILED"
                approval.result = (
                    f"No fue posible restablecer la contraseña de "
                    f"'{username}': {exc}"
                )

                self.db.commit()
                self.db.refresh(approval)

                return approval

        if not user.get("locked_out", False):
            approval.status = "COMPLETED"
            approval.result = (
                f"La cuenta '{username}' ya estaba "
                "desbloqueada."
            )
            approval.completed_at = datetime.now(
                timezone.utc
            )

            self.db.commit()
            self.db.refresh(approval)

            return approval

        try:
            unlock_result = unlock_user(username)

            if not unlock_result:
                raise RuntimeError(
                    "Active Directory no confirmó "
                    "el desbloqueo."
                )

            updated_user = get_user(username)

            if not updated_user:
                raise RuntimeError(
                    "No fue posible verificar el usuario "
                    "después del desbloqueo."
                )

            if updated_user.get("locked_out", False):
                raise RuntimeError(
                    "La cuenta continúa bloqueada después "
                    "del intento de desbloqueo."
                )

            approval.status = "COMPLETED"
            approval.result = (
                f"La cuenta '{username}' fue "
                "desbloqueada correctamente."
            )
            approval.completed_at = datetime.now(
                timezone.utc
            )

        except Exception as exc:
            approval.status = "FAILED"
            approval.result = (
                f"No fue posible desbloquear "
                f"'{username}': {exc}"
            )

        self.db.commit()
        self.db.refresh(approval)

        return approval