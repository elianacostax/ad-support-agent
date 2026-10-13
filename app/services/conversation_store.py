from threading import Lock

_conversations: dict[str, dict[str, str]] = {}
_lock = Lock()


def save_conversation(
    requester_id: str,
    conversation_id: str,
    service_url: str | None = None,
) -> None:
    if not requester_id or not conversation_id:
        return

    with _lock:
        _conversations[requester_id] = {
            "conversation_id": conversation_id,
            "service_url": service_url or "",
        }


def get_conversation(requester_id: str) -> dict | None:
    with _lock:
        return _conversations.get(requester_id)
