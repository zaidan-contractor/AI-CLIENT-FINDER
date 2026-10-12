import json
import time
import hmac
import hashlib
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status, Request, Header
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.core.database import get_db
from app.core.config import settings
from app.core.dependencies import get_current_context, require_role, AuthContext
from app.models.inbound import InboundMessage, InboundWebhookEventRecord
from app.models.outreach import SenderAccount
from app.services.inbound_processor import InboundProcessorService

router = APIRouter(prefix="/inbound", tags=["Inbound & AI Agent Boundary"])

@router.post("/webhook/{provider}")
async def receive_provider_webhook(
    provider: str,
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Public External Provider Webhook Boundary (Unauthenticated by user JWT).
    Enforces HMAC signature verification, replay protection, payload size limits,
    and event deduplication before routing to InboundProcessorService.
    """
    raw_body = await request.body()

    # 1. Payload size limit (5MB)
    if len(raw_body) > 5 * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Webhook payload exceeds 5MB limit."
        )

    # 2. Replay attack protection via timestamp header
    timestamp_header = request.headers.get("X-Webhook-Timestamp")
    if timestamp_header:
        try:
            ts = float(timestamp_header)
            current_ts = time.time()
            if abs(current_ts - ts) > 300:  # 5 minutes window
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Webhook timestamp outside acceptable 5-minute replay window."
                )
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid X-Webhook-Timestamp header format."
            )

    # 3. HMAC Signature Verification
    signature = request.headers.get("X-Webhook-Signature") or request.headers.get("X-Signature")
    if not signature:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing provider webhook signature header."
        )

    expected_sig = hmac.new(
        settings.INBOUND_WEBHOOK_SECRET.encode("utf-8"),
        raw_body,
        hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(signature.lower(), expected_sig.lower()):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid provider webhook signature."
        )

    # 4. Deduplication via Event ID
    event_id = request.headers.get("X-Event-ID")
    if not event_id:
        event_id = hashlib.sha256(raw_body).hexdigest()

    existing_event = db.execute(
        select(InboundWebhookEventRecord).where(
            InboundWebhookEventRecord.provider == provider.lower(),
            InboundWebhookEventRecord.event_id == event_id
        )
    ).scalars().first()

    if existing_event:
        return {"status": "ALREADY_PROCESSED", "event_id": event_id}

    # Parse JSON payload
    try:
        data = json.loads(raw_body.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="Malformed JSON in webhook body.")

    # 5. Resolve Workspace by recipient email (which maps to workspace's SenderAccount)
    recipient_email = data.get("recipient_email") or data.get("to") or ""
    sender = db.execute(
        select(SenderAccount).where(SenderAccount.email == recipient_email.lower().strip())
    ).scalars().first()

    if not sender:
        workspace_id = data.get("workspace_id")
        if not workspace_id:
            raise HTTPException(
                status_code=404,
                detail=f"No matching sender or workspace found for recipient '{recipient_email}'."
            )
    else:
        workspace_id = sender.workspace_id

    # Record deduplication event
    event_rec = InboundWebhookEventRecord(
        provider=provider.lower(),
        event_id=event_id
    )
    db.add(event_rec)
    db.commit()

    # Process inbound message
    msg = InboundProcessorService.process_inbound_email(
        db=db,
        workspace_id=workspace_id,
        sender_email=data.get("sender_email") or data.get("from"),
        recipient_email=recipient_email,
        subject=data.get("subject", ""),
        body_text=data.get("body_text") or data.get("text", ""),
        raw_headers=data.get("raw_headers", {}),
        attachments=data.get("attachments", [])
    )

    return {
        "status": "ACCEPTED",
        "event_id": event_id,
        "message_id": msg.id,
        "classified_intent": msg.classified_intent,
        "thread_state": msg.thread_state
    }

class InboundEmailPayload(BaseModel):
    sender_email: str
    recipient_email: str
    subject: str
    body_text: str
    raw_headers: Dict[str, Any] = {}
    attachments: List[Dict[str, Any]] = []

class InboundResponse(BaseModel):
    id: str
    workspace_id: str
    sender_email: str
    recipient_email: str
    subject: str
    classified_intent: str
    thread_state: str
    escalation_reason: Optional[str]
    proposed_ai_reply: Optional[str]
    ai_reply_authorized_by_operator: bool
    attachments_metadata: List[Dict[str, Any]]

@router.post("/process", response_model=InboundResponse)
def process_inbound(
    payload: InboundEmailPayload,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_role(["OrgAdmin", "WorkspaceAdmin", "Operator"]))
):
    try:
        msg = InboundProcessorService.process_inbound_email(
            db=db,
            workspace_id=ctx.active_workspace_id,
            sender_email=payload.sender_email,
            recipient_email=payload.recipient_email,
            subject=payload.subject,
            body_text=payload.body_text,
            raw_headers=payload.raw_headers,
            attachments=payload.attachments
        )
        return InboundResponse(
            id=msg.id,
            workspace_id=msg.workspace_id,
            sender_email=msg.sender_email,
            recipient_email=msg.recipient_email,
            subject=msg.subject,
            classified_intent=msg.classified_intent,
            thread_state=msg.thread_state,
            escalation_reason=msg.escalation_reason,
            proposed_ai_reply=msg.proposed_ai_reply,
            ai_reply_authorized_by_operator=msg.ai_reply_authorized_by_operator,
            attachments_metadata=msg.attachments_metadata
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

@router.get("/messages", response_model=List[InboundResponse])
def list_inbound(
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_role(["OrgAdmin", "WorkspaceAdmin", "Operator", "Auditor"]))
):
    messages = db.execute(
        select(InboundMessage).where(InboundMessage.workspace_id == ctx.active_workspace_id)
        .order_by(InboundMessage.created_at.desc())
    ).scalars().all()

    return [
        InboundResponse(
            id=m.id,
            workspace_id=m.workspace_id,
            sender_email=m.sender_email,
            recipient_email=m.recipient_email,
            subject=m.subject,
            classified_intent=m.classified_intent,
            thread_state=m.thread_state,
            escalation_reason=m.escalation_reason,
            proposed_ai_reply=m.proposed_ai_reply,
            ai_reply_authorized_by_operator=m.ai_reply_authorized_by_operator,
            attachments_metadata=m.attachments_metadata
        )
        for m in messages
    ]

@router.post("/messages/{message_id}/takeover", response_model=InboundResponse)
def takeover_thread(
    message_id: str,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_role(["OrgAdmin", "WorkspaceAdmin", "Operator"]))
):
    """
    Operator manual takeover:
    Halts autonomous replies and sets thread state to HUMAN_TAKEOVER.
    """
    msg = db.execute(
        select(InboundMessage).where(
            InboundMessage.id == message_id,
            InboundMessage.workspace_id == ctx.active_workspace_id
        )
    ).scalars().first()
    if not msg:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found.")

    msg.thread_state = "HUMAN_TAKEOVER"
    msg.escalation_reason = f"Operator {ctx.user.email} manually initiated human takeover."
    db.commit()
    db.refresh(msg)

    return InboundResponse(
        id=msg.id,
        workspace_id=msg.workspace_id,
        sender_email=msg.sender_email,
        recipient_email=msg.recipient_email,
        subject=msg.subject,
        classified_intent=msg.classified_intent,
        thread_state=msg.thread_state,
        escalation_reason=msg.escalation_reason,
        proposed_ai_reply=msg.proposed_ai_reply,
        ai_reply_authorized_by_operator=msg.ai_reply_authorized_by_operator,
        attachments_metadata=msg.attachments_metadata
    )
