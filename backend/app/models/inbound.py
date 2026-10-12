import uuid
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from sqlalchemy import (
    Column, String, Boolean, DateTime, ForeignKey, Text, JSON
)
from sqlalchemy.orm import relationship
from app.core.database import Base
from app.models.tenancy import generate_uuid, get_utc_now

class InboundMessage(Base):
    __tablename__ = "inbound_messages"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    workspace_id = Column(String(36), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    sender_email = Column(String(255), nullable=False, index=True)
    recipient_email = Column(String(255), nullable=False, index=True)
    subject = Column(String(255), nullable=False)
    body_text = Column(Text, nullable=False)
    raw_headers = Column(JSON, default=dict, nullable=False)
    attachments_metadata = Column(JSON, default=list, nullable=False)
    # Each item: {"filename": str, "content_type": str, "size_bytes": int, "security_state": "SAFE" | "QUARANTINED" | "REJECTED"}

    classified_intent = Column(String(50), nullable=False, index=True)
    # Intents: "OPT_OUT", "HOT_DEAL", "TECHNICAL_INQUIRY", "OBJECTION", "PROMPT_INJECTION_ATTEMPT", "GENERAL"
    intent_confidence = Column(String(20), default="HIGH", nullable=False)
    thread_state = Column(String(50), default="AUTONOMOUS", nullable=False, index=True)
    # States: "AUTONOMOUS", "HUMAN_REVIEW_REQUIRED", "HUMAN_TAKEOVER", "OPT_OUT_SUPPRESSED"
    escalation_reason = Column(Text, nullable=True)

    proposed_ai_reply = Column(Text, nullable=True)
    ai_reply_authorized_by_operator = Column(Boolean, default=False, nullable=False)

    created_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False)


class InboundWebhookEventRecord(Base):
    __tablename__ = "inbound_webhook_events"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    provider = Column(String(50), nullable=False, index=True)
    event_id = Column(String(128), unique=True, nullable=False, index=True)
    received_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False)
