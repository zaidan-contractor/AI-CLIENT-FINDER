from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.core.database import get_db
from app.core.dependencies import get_current_context, require_role, AuthContext
from app.models.outreach import OutboundMessage
from app.services.outbound_dispatch_engine import OutboundDispatchEngine

router = APIRouter(prefix="/outbound", tags=["Outbound 16-Gate Dispatch"])

class DispatchRequest(BaseModel):
    campaign_version_id: str
    sender_account_id: str
    recipient_email: str
    recipient_country: str = "US"
    business_id: Optional[str] = None
    subject: str
    body_text: str
    body_html: Optional[str] = None
    required_claim_ids: List[str] = []
    side_effect_idempotency_key: str
    thread_state: str = "AUTONOMOUS"
    contact_verification_tier: str = "CONFIRMED_VERIFIED"

class DispatchResponse(BaseModel):
    id: str
    workspace_id: str
    campaign_version_id: str
    sender_account_id: str
    recipient_email: str
    recipient_country: str
    status: str
    side_effect_idempotency_key: str
    provider_message_id: Optional[str]
    transport_mode: Optional[str] = "REAL"
    gate_failure_reason: Optional[str]
    gate_passed_count: int
    sent_at: Optional[Any]

class EnqueueJobResponse(BaseModel):
    job_id: str
    status: str
    mode: str
    side_effect_idempotency_key: str

@router.post("/enqueue", response_model=EnqueueJobResponse)
def enqueue_message(
    req: DispatchRequest,
    ctx: AuthContext = Depends(require_role(["OrgAdmin", "WorkspaceAdmin", "Operator"]))
):
    """
    Asynchronous Queue Boundary (Redis/RQ Worker Pool).
    Enqueues outbound job safely without passing credentials.
    """
    import uuid
    from app.core.queue import JobQueueManager, OutboundJobPayload
    job_id = f"job_{uuid.uuid4().hex[:16]}"
    payload = OutboundJobPayload(
        job_id=job_id,
        workspace_id=ctx.active_workspace_id,
        organization_id=ctx.organization_id,
        campaign_version_id=req.campaign_version_id,
        sender_account_id=req.sender_account_id,
        recipient_email=req.recipient_email,
        recipient_country=req.recipient_country or "US",
        business_id=req.business_id,
        subject=req.subject,
        body_text=req.body_text,
        body_html=req.body_html,
        required_claim_ids=req.required_claim_ids,
        side_effect_idempotency_key=req.side_effect_idempotency_key,
        thread_state=req.thread_state
    )
    result = JobQueueManager.enqueue_outbound_dispatch(payload)
    return EnqueueJobResponse(
        job_id=result.get("job_id", job_id),
        status=result.get("status", "QUEUED"),
        mode=result.get("mode", "UNKNOWN"),
        side_effect_idempotency_key=req.side_effect_idempotency_key
    )

@router.post("/dispatch", response_model=DispatchResponse)
def dispatch_message(
    req: DispatchRequest,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_role(["OrgAdmin", "WorkspaceAdmin", "Operator"]))
):
    """
    Submits outbound message to the Authoritative 16-Gate Pipeline.
    Strictly evaluates Gates 1 to 16 before side-effect transmission.
    """
    msg = OutboundDispatchEngine.dispatch_outbound_message(
        db=db,
        workspace_id=ctx.active_workspace_id,
        campaign_version_id=req.campaign_version_id,
        sender_account_id=req.sender_account_id,
        recipient_email=req.recipient_email,
        recipient_country=req.recipient_country,
        business_id=req.business_id,
        subject=req.subject,
        body_text=req.body_text,
        body_html=req.body_html,
        required_claim_ids=req.required_claim_ids,
        side_effect_idempotency_key=req.side_effect_idempotency_key,
        thread_state=req.thread_state
    )

    return DispatchResponse(
        id=msg.id,
        workspace_id=msg.workspace_id,
        campaign_version_id=msg.campaign_version_id,
        sender_account_id=msg.sender_account_id,
        recipient_email=msg.recipient_email,
        recipient_country=msg.recipient_country or "US",
        status=msg.status,
        side_effect_idempotency_key=msg.side_effect_idempotency_key,
        provider_message_id=msg.provider_message_id,
        transport_mode=msg.transport_mode,
        gate_failure_reason=msg.gate_failure_reason,
        gate_passed_count=msg.gate_passed_count,
        sent_at=msg.sent_at
    )

@router.get("/messages", response_model=List[DispatchResponse])
def list_messages(
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_role(["OrgAdmin", "WorkspaceAdmin", "Operator", "Auditor"]))
):
    messages = db.execute(
        select(OutboundMessage).where(OutboundMessage.workspace_id == ctx.active_workspace_id)
        .order_by(OutboundMessage.created_at.desc())
    ).scalars().all()

    return [
        DispatchResponse(
            id=m.id,
            workspace_id=m.workspace_id,
            campaign_version_id=m.campaign_version_id,
            sender_account_id=m.sender_account_id,
            recipient_email=m.recipient_email,
            recipient_country=m.recipient_country or "US",
            status=m.status,
            side_effect_idempotency_key=m.side_effect_idempotency_key,
            provider_message_id=m.provider_message_id,
            transport_mode=m.transport_mode,
            gate_failure_reason=m.gate_failure_reason,
            gate_passed_count=m.gate_passed_count,
            sent_at=m.sent_at
        )
        for m in messages
    ]
