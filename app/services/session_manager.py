from app.services.agent import ADSupportAgent


class SessionManager:
    def __init__(self):
        self._sessions: dict[str, ADSupportAgent] = {}

    def get_agent(
        self,
        session_id: str,
        requester_id: str | None = None,
        requester_name: str | None = None,
    ) -> ADSupportAgent:

        if session_id in self._sessions:
            agent = self._sessions[session_id]

            if agent.requester_id != requester_id:
                raise PermissionError(
                    "La sesión no pertenece al solicitante indicado."
                )

            return agent

        agent = ADSupportAgent(
            session_id=session_id,
            requester_id=requester_id,
            requester_name=requester_name,
        )

        self._sessions[session_id] = agent

        return agent

    def remove_session(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)
