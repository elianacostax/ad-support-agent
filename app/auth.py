from fastapi import Header, HTTPException

from app.config import settings


def verify_api_key(
    x_api_key: str | None = Header(default=None),
):
    if not x_api_key:
        raise HTTPException(
            status_code=401,
            detail="API Key requerida.",
        )

    if x_api_key != settings.api_key:
        raise HTTPException(
            status_code=403,
            detail="API Key inválida.",
        )