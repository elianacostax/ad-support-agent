#!/usr/bin/env python3
"""
Tarea programada diaria: desbloqueo automático de cuentas AD.

Modo de uso:
  DRY_RUN=true  python scripts/daily_ad_unlock.py   # solo lista, no modifica AD
  DRY_RUN=false python scripts/daily_ad_unlock.py   # desbloquea realmente

Variables de entorno (reutiliza .env de la app):
  AD_DOMAIN, AD_SERVER, AD_BIND_USER, AD_BIND_PASSWORD
  DAILY_UNLOCK_ENABLED   -> "true" para activar el desbloqueo real (default "false")
  DAILY_UNLOCK_LOG       -> ruta del log de ejecución (default logs/daily_unlock.log)

Seguridad:
  - Solo desbloquea cuentas HABILITADAS con lockout activo.
  - Nunca toca cuentas deshabilitadas.
  - Nunca restablece contraseñas.
  - Todo queda auditado en logs/audit.jsonl (acción AUTO_UNLOCK) y en el log propio.
"""

import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Permite ejecutar el script desde fuera del paquete `app/`.
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from app.services.active_directory import list_locked_enabled_users, unlock_user
from app.services.audit import write_audit

LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_LOG = LOG_DIR / "daily_unlock.log"

logger = logging.getLogger("daily_ad_unlock")
logger.setLevel(logging.INFO)

_file_handler = logging.FileHandler(os.getenv("DAILY_UNLOCK_LOG", str(DEFAULT_LOG)), encoding="utf-8")
_file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
logger.addHandler(_file_handler)

_console_handler = logging.StreamHandler(sys.stdout)
_console_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
logger.addHandler(_console_handler)


def run(dry_run: bool = True) -> dict:
    """Ejecuta la revisión diaria y devuelve un resumen de resultados."""
    started_at = datetime.now(timezone.utc)
    logger.info("=== Inicio revisión diaria de desbloqueo AD (dry_run=%s) ===", dry_run)

    try:
        locked = list_locked_enabled_users()
    except Exception as exc:
        logger.exception("Error enumerando cuentas bloqueadas en AD")
        return {
            "started_at": started_at.isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "dry_run": dry_run,
            "error": str(exc),
            "locked_found": 0,
            "unlocked": [],
            "skipped": [],
            "failures": [],
        }

    unlocked: list[dict] = []
    skipped: list[dict] = []
    failures: list[dict] = []

    for user in locked:
        username = user["username"]

        if dry_run:
            skipped.append(
                {
                    "username": username,
                    "dn": user.get("dn"),
                    "lockout_time": user.get("lockout_time"),
                    "reason": "DRY_RUN_NO_CHANGE",
                }
            )
            logger.info("[DRY_RUN] %s estaría desbloqueado (no se modificó).", username)
            continue

        try:
            result = unlock_user(username)

            if result.get("unlocked"):
                unlocked.append(
                    {
                        "username": username,
                        "dn": user.get("dn"),
                        "reason": result.get("reason", "UNLOCKED"),
                    }
                )
                write_audit(
                    session_id="daily_ad_unlock",
                    requester_id="sistema",
                    action="AUTO_UNLOCK",
                    username=username,
                    result="COMPLETED",
                    reason=result.get("reason", "UNLOCKED"),
                )
                logger.info("Desbloqueado: %s (%s).", username, result.get("reason"))
            else:
                skipped.append(
                    {
                        "username": username,
                        "dn": user.get("dn"),
                        "reason": result.get("reason", "ACCOUNT_NOT_LOCKED_OR_DISABLED"),
                    }
                )
                logger.warning(
                    "No se desbloqueó %s: %s", username, result.get("reason")
                )

        except Exception as exc:
            failures.append(
                {
                    "username": username,
                    "dn": user.get("dn"),
                    "error": str(exc),
                }
            )
            logger.exception("Fallo desbloqueando %s", username)

    finished_at = datetime.now(timezone.utc)

    summary = {
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "dry_run": dry_run,
        "locked_found": len(locked),
        "unlocked": unlocked,
        "skipped": skipped,
        "failures": failures,
    }

    logger.info(
        "=== Fin revisión diaria: %d bloqueadas, %d desbloqueadas, %d omitidas, %d fallos ===",
        len(locked),
        len(unlocked),
        len(skipped),
        len(failures),
    )

    return summary


if __name__ == "__main__":
    dry_run = os.getenv("DRY_RUN", "true").lower() in {"1", "true", "yes", "on"}
    enabled = os.getenv("DAILY_UNLOCK_ENABLED", "false").lower() in {"1", "true", "yes", "on"}

    if not dry_run and not enabled:
        print(
            "DAILY_UNLOCK_ENABLED no está en 'true'. Ejecutando en DRY_RUN por seguridad.",
            flush=True,
        )
        dry_run = True

    summary = run(dry_run=dry_run)

    print("\n=== RESUMEN ===")
    print(f"Fecha inicio : {summary['started_at']}")
    print(f"Fecha fin    : {summary['finished_at']}")
    print(f"Dry run      : {summary['dry_run']}")
    print(f"Bloqueadas   : {summary['locked_found']}")
    print(f"Desbloqueadas: {len(summary['unlocked'])}")
    print(f"Omitidas     : {len(summary['skipped'])}")
    print(f"Fallos       : {len(summary['failures'])}")

    if summary["failures"]:
        print("\nFallos:")
        for f in summary["failures"]:
            print(f"  - {f['username']}: {f['error']}")

    sys.exit(1 if summary["failures"] else 0)
