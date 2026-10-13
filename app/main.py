import asyncio

from fastapi import Depends, FastAPI, HTTPException, Path
from microsoft_teams.api import (
    AdaptiveCardActionMessageResponse,
    AdaptiveCardInvokeActivity,
    AdaptiveCardInvokeResponse,
)
from microsoft_teams.apps import ActivityContext, App
from microsoft_teams.apps.http import FastAPIAdapter
from pydantic import BaseModel, Field

from app.api.v1.approvals import router as approvals_router
from app.auth import verify_api_key
from app.config import settings
from app.services.active_directory import get_user
from app.services.approval_worker import approval_worker
from app.services.conversation_store import save_conversation
from app.services.session_manager import SessionManager

_MAIN_LOOP: asyncio.AbstractEventLoop | None = None
from app.services.teams_cards import build_test_approval_card

# ============================================================
# FastAPI
# ============================================================

app = FastAPI(
    title="AD Support Agent",
    version="0.5.0",
    description=(
        "Agente conversacional interno de soporte "
        "de Active Directory"
    ),
)

approval_worker_task: asyncio.Task | None = None


# ============================================================
# Routers
# ============================================================

app.include_router(approvals_router)


# ============================================================
# Session Manager
# ============================================================

session_manager = SessionManager()


# ============================================================
# Modelos API
# ============================================================

class ChatRequest(BaseModel):
    session_id: str = Field(
        min_length=1,
        max_length=100,
    )

    requester_id: str = Field(
        min_length=1,
        max_length=200,
    )

    message: str = Field(
        min_length=1,
        max_length=1000,
    )


class ChatResponse(BaseModel):
    response: str


# ============================================================
# Health
# ============================================================

@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "ad-support-agent",
    }


# ============================================================
# Worker
# ============================================================


@app.on_event("startup")
async def startup_event():
    global approval_worker_task, _MAIN_LOOP

    # El loop principal queda registrado desde el arranque para que
    # el worker y las APIs puedan notificar al solicitante sin caer
    # en el fallback asyncio.run (que rompe las conexiones httpx
    # al cerrar el loop tras cada llamada).
    _MAIN_LOOP = asyncio.get_running_loop()

    approval_worker_task = asyncio.create_task(
        approval_worker()
    )

    print("[APPROVAL WORKER] Worker iniciado.")


@app.on_event("shutdown")
async def shutdown_event():
    global approval_worker_task

    if approval_worker_task:
        approval_worker_task.cancel()

        try:
            await approval_worker_task
        except asyncio.CancelledError:
            pass

        approval_worker_task = None

    print("[APPROVAL WORKER] Worker detenido.")


# ============================================================
# Active Directory - consulta de usuario
# ============================================================

@app.get(
    "/api/v1/users/{username}",
    dependencies=[Depends(verify_api_key)],
)
def read_user(
    username: str = Path(
        min_length=1,
        max_length=20,
    ),
):
    try:
        user = get_user(username)

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    except RuntimeError as exc:
        raise HTTPException(
            status_code=502,
            detail=str(exc),
        ) from exc

    if user is None:
        raise HTTPException(
            status_code=404,
            detail="Usuario no encontrado",
        )

    return user


# ============================================================
# Chat API interno
# ============================================================

@app.post(
    "/api/v1/chat",
    response_model=ChatResponse,
    dependencies=[Depends(verify_api_key)],
)
def chat(request: ChatRequest):
    try:
        agent = session_manager.get_agent(
            session_id=request.session_id,
            requester_id=request.requester_id,
        )

        response = agent.process(request.message)

        return ChatResponse(
            response=response,
        )

    except PermissionError as exc:
        raise HTTPException(
            status_code=403,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="No fue posible procesar la solicitud.",
        ) from exc


# ============================================================
# Microsoft Teams
# ============================================================

teams_app = App(
    client_id=settings.client_id,
    client_secret=settings.client_secret,
    tenant_id=settings.tenant_id,
    http_server_adapter=FastAPIAdapter(app=app),
)


@teams_app.on_message
async def handle_teams_message(ctx):
    """
    Procesa mensajes recibidos desde Microsoft Teams.
    """

    activity = ctx.activity

    print(
    "[TEAMS] from_.model_dump() =",
    activity.from_.model_dump(),
    )

    print(
    "[TEAMS] from_ =",
    activity.from_,
    )

    print(
    "[TEAMS] activity type =",
    type(activity.from_),
    )

    # Texto enviado por el usuario
    message = (activity.text or "").strip()

    if not message:
        await ctx.reply(
            "No recibí ningún mensaje. "
            "Indícame qué necesitas."
        )
        return

    # Prueba temporal de Adaptive Card.
    # Solo termina el procesamiento cuando el mensaje
    # corresponde realmente a esta prueba.
    if message.casefold() == "tarjeta prueba":
        await ctx.send(
            build_test_approval_card()
        )
        return

    # Identidad del usuario en Teams / Entra ID
    requester_id = activity.from_.aad_object_id

    print(
        "[TEAMS] requester_id obtenido =",
        repr(requester_id),
    )

    if not requester_id:
        print("[TEAMS] No se recibió aad_object_id.")

        await ctx.send(
            "No fue posible identificar tu cuenta de Microsoft. "
            "Intenta nuevamente."
        )
        return

    # Identificador de conversación de Teams
    conversation_id = activity.conversation.id

    global _MAIN_LOOP

    _MAIN_LOOP = asyncio.get_running_loop()

    save_conversation(
        requester_id=requester_id,
        conversation_id=conversation_id,
        service_url=getattr(
            getattr(ctx, "conversation_ref", None),
            "service_url",
            None,
        ),
    )

    print(
        f"[TEAMS] requester_id={requester_id} "
        f"conversation_id={conversation_id} "
        f"message={message!r}"
    )

    try:
        agent = session_manager.get_agent(
            session_id=f"teams:{conversation_id}",
            requester_id=requester_id,
            requester_name=activity.from_.name,
        )

        response = agent.process(message)

        print(
            f"[TEAMS] response={response!r}"
        )

        await ctx.reply(response)

    except PermissionError:
        await ctx.reply(
            "No fue posible validar la sesión "
            "de esta conversación."
        )

    except Exception as exc:
        print(
            f"[TEAMS] Error procesando mensaje: {exc}"
        )

        await ctx.reply(
            "No fue posible procesar la solicitud "
            "en este momento."
        )

@teams_app.on_card_action_execute("test_approve")
async def handle_test_approve(
    ctx: ActivityContext[AdaptiveCardInvokeActivity],
) -> AdaptiveCardInvokeResponse:
    print("[TEAMS CARD] Acción recibida: test_approve")

    await ctx.send(
        "Prueba exitosa: el botón Aprobar llegó correctamente al agente."
    )

    return AdaptiveCardActionMessageResponse(
        statusCode=200,
        type="application/vnd.microsoft.activity.message",
        value="Acción procesada correctamente.",
    )


@teams_app.on_card_action_execute("test_reject")
async def handle_test_reject(
    ctx: ActivityContext[AdaptiveCardInvokeActivity],
) -> AdaptiveCardInvokeResponse:
    print("[TEAMS CARD] Acción recibida: test_reject")

    await ctx.send(
        "Prueba exitosa: el botón Rechazar llegó correctamente al agente."
    )

    return AdaptiveCardActionMessageResponse(
        statusCode=200,
        type="application/vnd.microsoft.activity.message",
        value="Acción procesada correctamente.",
    )



# ============================================================
# Punto de entrada
# ============================================================

if __name__ == "__main__":
    import asyncio

    asyncio.run(
        teams_app.start()
    )



