import json
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
AUDIT_DIR = BASE_DIR / "logs"
AUDIT_FILE = AUDIT_DIR / "audit.jsonl"


def write_audit(
    *,
    session_id: str,
    requester_id: str | None,
    action: str,
    username: str | None,
    result: str,
    reason: str | None = None,
):
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)

    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "session_id": session_id,
        "requester_id": requester_id,
        "action": action,
        "username": username,
        "result": result,
        "reason": reason,
    }

    with AUDIT_FILE.open(
        "a",
        encoding="utf-8",
    ) as file:
        file.write(
            json.dumps(
                event,
                ensure_ascii=False,
            )
            + "\n"
        )