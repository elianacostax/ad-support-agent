import asyncio
import logging

from app.db.database import SessionLocal
from app.services.approval_service import ApprovalService
from app.services.audit import write_audit
from app.services.conversation_store import get_conversation
from app.services.microsoft_auth import acquire_approval_token

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 15


def _notify_requester(
    requester_id: str | None,
    message: str,
    conversation_id: str | None = None,
    conversation_service_url: str | None = None,
) -> None:
    if not requester_id:
        return

    if not conversation_id:
        meta = get_conversation(requester_id)

        if meta:
            conversation_id = meta.get("conversation_id")
            conversation_service_url = (
                meta.get("service_url")
            )

    if not conversation_id:
        logger.warning(
            "No hay conversación Teams registrada para %s.",
            requester_id,
        )

        print(
            f"[APPROVAL WORKER] No hay conversación registrada "
            f"para {requester_id}.",
            flush=True,
        )

        return

    try:
        import sys

        from microsoft_teams.api import (
            Account,
            ConversationAccount,
            ConversationReference,
            MessageActivityInput,
        )
        from microsoft_teams.apps.activity_send import (
            send_or_update_activity,
        )

        # Cuando el proceso arranca con `python -m app.main`, el código
        # real (teams_app y _MAIN_LOOP) se ejecuta en `__main__`.
        # Importar `app.main` crearía una SEGUNDA instancia del módulo
        # con _MAIN_LOOP=None, lo que obliga al fallback asyncio.run
        # que rompe las conexiones httpx al cerrar el loop.
        main_module = sys.modules.get("__main__")

        if main_module is None or not hasattr(
            main_module, "teams_app"
        ):
            import app.main as main_module

        teams_app = main_module.teams_app
        main_loop = getattr(main_module, "_MAIN_LOOP", None)

        activity = MessageActivityInput(text=message)

        async def _send():
            conversation_ref = ConversationReference(
                channel_id="msteams",
                service_url=(
                    conversation_service_url
                    or teams_app.api.service_url
                ),
                bot=Account(id=teams_app.id),
                conversation=ConversationAccount(
                    id=conversation_id
                ),
            )

            await send_or_update_activity(
                teams_app.api,
                activity,
                conversation_ref,
            )

        if main_loop is not None and not main_loop.is_closed():
            future = asyncio.run_coroutine_threadsafe(
                _send(),
                main_loop,
            )

            future.result(timeout=30)

        else:
            logger.warning(
                "Loop principal no disponible; se usa asyncio.run "
                "como último recurso para notificar."
            )

            asyncio.run(_send())

        print(
            f"[APPROVAL WORKER] Notificación enviada a "
            f"{conversation_id}.",
            flush=True,
        )

    except Exception:
        logger.exception(
            "No fue posible notificar al solicitante %s.",
            requester_id,
        )

        print(
            "[APPROVAL WORKER] ERROR enviando notificación "
            "al solicitante.",
            flush=True,
        )


def build_completion_message(approval) -> str:
    """
    Construye el mensaje final que recibe el solicitante,
    incluyendo la contraseña temporal cuando aplica.
    """
    temp_password = getattr(approval, "temp_password", None)

    if (
        approval.action == "PASSWORD_RESET"
        and approval.status == "COMPLETED"
        and temp_password
    ):
        return (
            f"Contraseña restablecida para "
            f"'{approval.ad_username}' "
            f"(solicitud #{approval.id}). "
            f"Contraseña temporal: \"{temp_password}\". "
            "Debe cambiarla al iniciar sesión."
        )

    if approval.status == "FAILED":
        return (
            f"La solicitud #{approval.id} para "
            f"'{approval.ad_username}' no pudo completarse: "
            f"{approval.result}"
        )

    return f"Solicitud #{approval.id}: {approval.result}"


def build_rejection_message(approval) -> str:
    """Construye el mensaje de rechazo según el tipo de solicitud."""
    motivo = approval.approval_comments or "sin comentario"

    if approval.action == "PASSWORD_RESET":
        return (
            f"Tu solicitud de restablecimiento de contraseña "
            f"para '{approval.ad_username}' fue RECHAZADA. "
            f"Motivo: {motivo}."
        )

    return (
        f"Tu solicitud de desbloqueo "
        f"para '{approval.ad_username}' fue RECHAZADA. "
        f"Motivo: {motivo}."
    )


def notify_requester_result(approval) -> None:
    """
    Notifica al solicitante el resultado final de su solicitud
    (ejecución o fallo), incluyendo la contraseña temporal.
    """
    _notify_requester(
        approval.requester_id,
        build_completion_message(approval),
        conversation_id=approval.conversation_id,
        conversation_service_url=(
            approval.conversation_service_url
        ),
    )


def notify_requester_rejection(approval) -> None:
    """Notifica al solicitante que su solicitud fue rechazada."""
    _notify_requester(
        approval.requester_id,
        build_rejection_message(approval),
        conversation_id=approval.conversation_id,
        conversation_service_url=(
            approval.conversation_service_url
        ),
    )


def process_pending_approvals() -> None:
    db = SessionLocal()

    try:
        service = ApprovalService(db)
        approvals = service.list_pending()

        print(
            f"[APPROVAL WORKER] Solicitudes PENDING encontradas: {len(approvals)}",
            flush=True,
        )

        for approval in approvals:
            print(
                f"[APPROVAL WORKER] "
                f"Solicitud #{approval.id} | "
                f"usuario={approval.ad_username} | "
                f"graph_id={approval.graph_approval_id} | "
                f"requester={approval.requester_id}",
                flush=True,
            )

            if not approval.graph_approval_id:
                logger.warning(
                    "Solicitud #%s no tiene graph_approval_id. Se omite.",
                    approval.id,
                )

                print(
                    f"[APPROVAL WORKER] "
                    f"Solicitud #{approval.id} omitida: "
                    f"no tiene graph_approval_id.",
                    flush=True,
                )

                continue

            token_result = acquire_approval_token()

            access_token = (
                token_result.get("access_token")
                if token_result
                else None
            )

            print(
                f"[APPROVAL WORKER] "
                f"Solicitud #{approval.id} | "
                f"token={'SI' if access_token else 'NO'}",
                flush=True,
            )

            if not access_token:
                logger.warning(
                    "No hay token Graph disponible para la solicitud #%s.",
                    approval.id,
                )

                print(
                    f"[APPROVAL WORKER] "
                    f"Solicitud #{approval.id} omitida: "
                    f"no hay token Graph.",
                    flush=True,
                )

                continue

            try:
                previous_status = approval.status

                print(
                    f"[APPROVAL WORKER] "
                    f"Consultando estado de Teams para solicitud #{approval.id}...",
                    flush=True,
                )

                approval = service.sync_graph_approval(
                    request_id=approval.id,
                    graph_access_token=access_token,
                )

                print(
                    f"[APPROVAL WORKER] "
                    f"Solicitud #{approval.id} | "
                    f"estado después de sincronizar={approval.status}",
                    flush=True,
                )

                if approval.status == "REJECTED":
                    if previous_status != "REJECTED":
                        is_password_reset = (
                            approval.action == "PASSWORD_RESET"
                        )

                        write_audit(
                            session_id=f"approval:{approval.id}",
                            requester_id=approval.requester_id,
                            action=(
                                "PASSWORD_RESET_REQUEST"
                                if is_password_reset
                                else "UNLOCK_REQUEST"
                            ),
                            username=approval.ad_username,
                            result="REJECTED",
                            reason=approval.approval_comments,
                        )

                        _notify_requester(
                            approval.requester_id,
                            build_rejection_message(approval),
                            conversation_id=approval.conversation_id,
                            conversation_service_url=(
                                approval.conversation_service_url
                            ),
                        )

                    logger.info(
                        "Solicitud #%s rechazada.",
                        approval.id,
                    )

                    print(
                        f"[APPROVAL WORKER] "
                        f"Solicitud #{approval.id} rechazada.",
                        flush=True,
                    )

                    continue

                if approval.status != "APPROVED":
                    print(
                        f"[APPROVAL WORKER] "
                        f"Solicitud #{approval.id} todavía pendiente.",
                        flush=True,
                    )

                    continue

                is_password_reset = (
                    approval.action == "PASSWORD_RESET"
                )

                logger.info(
                    "Solicitud #%s aprobada. "
                    "Ejecutando %s de %s.",
                    approval.id,
                    (
                        "restablecimiento de contraseña"
                        if is_password_reset
                        else "desbloqueo"
                    ),
                    approval.ad_username,
                )

                print(
                    f"[APPROVAL WORKER] "
                    f"Solicitud #{approval.id} aprobada. "
                    f"Ejecutando "
                    f"{'restablecimiento de contraseña' if is_password_reset else 'desbloqueo'} "
                    f"de {approval.ad_username}...",
                    flush=True,
                )

                approval = service.execute_approved_request(
                    request_id=approval.id,
                )

                write_audit(
                    session_id=f"approval:{approval.id}",
                    requester_id=approval.requester_id,
                    action=approval.action,
                    username=approval.ad_username,
                    result=approval.status,
                    reason=approval.result,
                )

                _notify_requester(
                    approval.requester_id,
                    build_completion_message(approval),
                    conversation_id=approval.conversation_id,
                    conversation_service_url=(
                        approval.conversation_service_url
                    ),
                )

                logger.info(
                    "Solicitud #%s finalizada con estado %s.",
                    approval.id,
                    approval.status,
                )

                print(
                    f"[APPROVAL WORKER] "
                    f"Solicitud #{approval.id} finalizada | "
                    f"estado={approval.status} | "
                    f"resultado={approval.result}",
                    flush=True,
                )

            except Exception as exc:
                logger.exception(
                    "Error procesando la solicitud #%s.",
                    approval.id,
                )

                print(
                    f"[APPROVAL WORKER] "
                    f"ERROR procesando solicitud #{approval.id}: {exc}",
                    flush=True,
                )

    finally:
        db.close()


async def approval_worker() -> None:
    print(
        f"[APPROVAL WORKER] Worker iniciado. "
        f"Intervalo: {POLL_INTERVAL_SECONDS} segundos.",
        flush=True,
    )

    while True:
        try:
            print(
                "[APPROVAL WORKER] Revisando solicitudes pendientes...",
                flush=True,
            )

            await asyncio.to_thread(
                process_pending_approvals
            )

            print(
                "[APPROVAL WORKER] Revisión terminada.",
                flush=True,
            )

        except asyncio.CancelledError:
            print(
                "[APPROVAL WORKER] Worker detenido.",
                flush=True,
            )
            raise

        except Exception as exc:
            logger.exception(
                "Error general en approval worker."
            )

            print(
                f"[APPROVAL WORKER] ERROR GENERAL: {exc}",
                flush=True,
            )

        await asyncio.sleep(
            POLL_INTERVAL_SECONDS
        )