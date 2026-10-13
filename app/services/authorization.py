from app.config import settings


def get_ad_approvers() -> set[str]:
    return {
        value.strip().casefold()
        for value in settings.ad_approvers.split(",")
        if value.strip()
    }


def is_ad_approver(analyst_id: str) -> bool:
    if not analyst_id:
        return False

    normalized = analyst_id.strip().casefold()

    approvers = get_ad_approvers()

    if normalized in approvers:
        return True

    # Permite que AD_APPROVERS contenga "sacosta"
    # mientras Microsoft Entra entregue "sacosta@sitti.com.co".
    if "@" in normalized:
        local_part = normalized.split("@", 1)[0]

        if local_part in approvers:
            return True

    return False