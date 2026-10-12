import uuid
from datetime import datetime, timezone, date
from decimal import Decimal
from typing import Dict, Any, Optional
from sqlalchemy import (
    Column, String, Boolean, DateTime, Date, Integer, Float, ForeignKey, Text, JSON, Numeric
)
from sqlalchemy.orm import relationship
from app.core.database import Base
from app.models.tenancy import generate_uuid, get_utc_now

class SenderAccount(Base):
    __tablename__ = "sender_accounts"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    workspace_id = Column(String(36), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    email = Column(String(255), nullable=False, index=True)
    display_name = Column(String(255), nullable=False)
    provider_type = Column(String(50), nullable=False)  # "SMTP", "SENDGRID", "MAILGUN", "RESEND"
    encrypted_credentials = Column(Text, nullable=False)  # Envelope-encrypted DEK+Ciphertext
    status = Column(String(50), default="ACTIVE", nullable=False, index=True)
    # Statuses: "ACTIVE", "WARMING_UP", "PAUSED", "DISABLED"
    daily_quota = Column(Integer, default=50, nullable=False)
    warmup_day = Column(Integer, default=1, nullable=False)
    warmup_max_daily = Column(Integer, default=20, nullable=False)

    # 8-Dimension Readiness Flags (evaluated by SenderReadinessService)
    dns_valid = Column(Boolean, default=False, nullable=False)
    spf_valid = Column(Boolean, default=False, nullable=False)
    dkim_valid = Column(Boolean, default=False, nullable=False)
    dmarc_valid = Column(Boolean, default=False, nullable=False)
    tracking_domain_aligned = Column(Boolean, default=False, nullable=False)
    connection_verified = Column(Boolean, default=False, nullable=False)
    delivery_health_ok = Column(Boolean, default=False, nullable=False)
    bounce_rate_pct = Column(Float, default=0.0, nullable=False)
    spam_complaint_pct = Column(Float, default=0.0, nullable=False)

    created_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=get_utc_now, onupdate=get_utc_now, nullable=False)


class SenderDailyMetric(Base):
    __tablename__ = "sender_daily_metrics"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    workspace_id = Column(String(36), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    sender_account_id = Column(String(36), ForeignKey("sender_accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    metric_date = Column(Date, nullable=False, index=True)
    sent_count = Column(Integer, default=0, nullable=False)
    bounce_count = Column(Integer, default=0, nullable=False)
    complaint_count = Column(Integer, default=0, nullable=False)


class WorkspaceDailyCost(Base):
    __tablename__ = "workspace_daily_costs"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    workspace_id = Column(String(36), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    cost_date = Column(Date, nullable=False, index=True)
    amount = Column(Numeric(14, 4), default=Decimal("0.0000"), nullable=False)
    currency = Column(String(3), default="USD", nullable=False)
    is_completed_day = Column(Boolean, default=False, nullable=False)


class JurisdictionPolicy(Base):
    __tablename__ = "jurisdiction_policies"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    country_code = Column(String(3), nullable=False, index=True)  # ISO-3166-1 alpha-2 or alpha-3, e.g. "US", "CA", "GB", "DE"
    policy_version = Column(String(50), nullable=False, default="v1.0")
    decision = Column(String(50), nullable=False)  # "ALLOWED", "BLOCKED", "HUMAN_REVIEW_REQUIRED"
    lawful_basis = Column(String(100), nullable=False)  # "LEGITIMATE_INTEREST", "OPT_OUT", "EXPLICIT_CONSENT"
    requires_one_click_unsubscribe = Column(Boolean, default=True, nullable=False)
    requires_physical_address = Column(Boolean, default=True, nullable=False)
    description = Column(Text, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False)


class OutboundMessage(Base):
    __tablename__ = "outbound_messages"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    workspace_id = Column(String(36), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    campaign_version_id = Column(String(36), ForeignKey("campaign_versions.id", ondelete="CASCADE"), nullable=False, index=True)
    sender_account_id = Column(String(36), ForeignKey("sender_accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    recipient_email = Column(String(255), nullable=False, index=True)
    recipient_country = Column(String(3), nullable=True, default="US")
    business_id = Column(String(36), nullable=True, index=True)
    subject = Column(String(255), nullable=False)
    body_text = Column(Text, nullable=False)
    body_html = Column(Text, nullable=True)
    status = Column(String(50), default="QUEUED", nullable=False, index=True)
    # Statuses: "QUEUED", "GATES_PASSED", "TRANSMITTED", "SUPPRESSED", "BLOCKED", "DELIVERY_STATE_UNKNOWN"
    side_effect_idempotency_key = Column(String(128), unique=True, nullable=False, index=True)
    provider_message_id = Column(String(255), nullable=True)
    transport_mode = Column(String(20), nullable=True, default="REAL")  # "REAL", "MOCK"
    gate_failure_reason = Column(Text, nullable=True)
    gate_passed_count = Column(Integer, default=0, nullable=False)
    thread_state = Column(String(50), default="AUTONOMOUS", nullable=False)  # "AUTONOMOUS", "HUMAN_REVIEW_REQUIRED", "HUMAN_TAKEOVER"
    capacity_accounted = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False)
    sent_at = Column(DateTime(timezone=True), nullable=True)

