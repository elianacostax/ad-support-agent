from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ApprovalResponse(BaseModel):
    id: int
    requester_id: str
    ad_username: str
    action: str
    status: str
    reason: str | None
    approved_by: str | None
    approved_at: datetime | None
    rejected_by: str | None
    rejected_at: datetime | None
    completed_at: datetime | None
    result: str | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ApprovalActionRequest(BaseModel):
    analyst_id: str


class ApprovalRejectRequest(BaseModel):
    analyst_id: str
    reason: str | None = None
