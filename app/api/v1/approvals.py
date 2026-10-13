from fastapi import APIRouter, Depends, HTTPException, Path

from app.auth import verify_api_key
from app.db.database import SessionLocal
from app.schemas.approval import (
    ApprovalActionRequest,
    ApprovalRejectRequest,
    ApprovalResponse,
)
from app.services.approval_service import ApprovalService
from app.services.approval_worker import (
    notify_requester_rejection,
    notify_requester_result,
)
from app.services.authorization import is_ad_approver

router = APIRouter(
    prefix="/api/v1/approvals",
    tags=["approvals"],
    dependencies=[Depends(verify_api_key)],
)


@router.get(
    "/pending",
    response_model=list[ApprovalResponse],
)
def list_pending():
    db = SessionLocal()

    try:
        service = ApprovalService(db)
        return service.list_pending()
    finally:
        db.close()


@router.post("/{request_id}/approve", response_model=ApprovalResponse)
def approve(
    request: ApprovalActionRequest,
    request_id: int = Path(ge=1),
):
    if not is_ad_approver(request.analyst_id):
        raise HTTPException(
            status_code=403,
            detail="El analista no está autorizado para aprobar solicitudes de Active Directory.",
        )

    db = SessionLocal()

    try:
        service = ApprovalService(db)

        try:
            approval = service.approve_request(
                request_id=request_id,
                approved_by=request.analyst_id,
            )

            result = service.execute_approved_request(
                request_id=approval.id,
            )

            # Se informa al solicitante en Teams del resultado,
            # incluyendo la contraseña temporal cuando aplica.
            try:
                notify_requester_result(result)
            except Exception:
                pass  # La notificación no debe bloquear la API.

            return result

        except LookupError as exc:
            raise HTTPException(
                status_code=404,
                detail=str(exc),
            ) from exc

        except ValueError as exc:
            raise HTTPException(
                status_code=409,
                detail=str(exc),
            ) from exc

        except RuntimeError as exc:
            raise HTTPException(
                status_code=502,
                detail=str(exc),
            ) from exc

    finally:
        db.close()


@router.post(
    "/{request_id}/reject",
    response_model=ApprovalResponse,
)
def reject(
    request: ApprovalRejectRequest,
    request_id: int = Path(ge=1),
):

    if not is_ad_approver(request.analyst_id):
        raise HTTPException(
            status_code=403,
            detail="El analista no está autorizado para gestionar solicitudes de Active Directory.",
        )

    db = SessionLocal()

    try:
        service = ApprovalService(db)

        try:
            rejected = service.reject_request(
                request_id=request_id,
                rejected_by=request.analyst_id,
                reason=request.reason,
            )

            # Se informa al solicitante del rechazo.
            try:
                notify_requester_rejection(rejected)
            except Exception:
                pass  # La notificación no debe bloquear la API.

            return rejected

        except LookupError as exc:
            raise HTTPException(
                status_code=404,
                detail=str(exc),
            ) from exc

        except ValueError as exc:
            raise HTTPException(
                status_code=409,
                detail=str(exc),
            ) from exc

    finally:
        db.close()
