import uuid
from datetime import datetime, timezone, date
from decimal import Decimal
from typing import Dict, Any, Optional
from sqlalchemy import (
    Column, String, Boolean, DateTime, Date, Integer, Float, ForeignKey, Text, JSON, Numeric, UniqueConstraint
)
from sqlalchemy.orm import relationship
from app.core.database import Base
from app.models.tenancy import generate_uuid, get_utc_now


class AIActionAuditLedger(Base):
    """
    Immutable audit ledger for all AI/LLM invocations.
    Records metadata, hashes, token usage, cost accounting, actions proposed vs executed.
    NEVER stores raw secrets or untracked state mutations.
    """
    __tablename__ = "ai_action_audit_ledger"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    workspace_id = Column(String(36), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    actor_id = Column(String(36), nullable=False, index=True)
    actor_type = Column(String(50), nullable=False, default="SYSTEM")  # "USER", "SYSTEM", "OPERATOR"
    provider = Column(String(50), nullable=False, index=True)          # "OPENAI", "MOCK", "RULE_BASED"
    model_name = Column(String(100), nullable=False)                   # "gpt-4o-mini", etc.
    purpose = Column(String(100), nullable=False, index=True)          # "BUSINESS_RESEARCH_ANALYSIS", etc.
    input_hash = Column(String(64), nullable=False, index=True)        # SHA-256 of context payload
    output_schema_name = Column(String(100), nullable=False)
    status = Column(String(50), nullable=False, index=True)            # "LLM_SUCCESS", "LLM_FALLBACK", "LLM_FAILED", "LLM_BLOCKED"
    prompt_tokens = Column(Integer, default=0, nullable=False)
    completion_tokens = Column(Integer, default=0, nullable=False)
    total_tokens = Column(Integer, default=0, nullable=False)
    estimated_cost_usd = Column(Numeric(10, 6), default=Decimal("0.000000"), nullable=False)
    is_cost_estimated = Column(Boolean, default=False, nullable=False)
    action_proposed = Column(JSON, nullable=True)                      # Proposed business action / structured payload
    action_executed = Column(JSON, nullable=True)                      # Resulting deterministic application action
    approval_state = Column(String(50), default="AUTO_GATED", nullable=False, index=True) # "PENDING_APPROVAL", "OPERATOR_APPROVED", "AUTO_GATED", "REJECTED"
    created_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False, index=True)


class AIWorkspaceDailyCost(Base):
    """
    Enforces deterministic daily AI budget guardrails per workspace.
    """
    __tablename__ = "ai_workspace_daily_costs"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    workspace_id = Column(String(36), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    usage_date = Column(Date, nullable=False, index=True)
    total_tokens = Column(Integer, default=0, nullable=False)
    total_cost_usd = Column(Numeric(10, 4), default=Decimal("0.0000"), nullable=False)
    run_count = Column(Integer, default=0, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=get_utc_now, onupdate=get_utc_now, nullable=False)

    __table_args__ = (
        UniqueConstraint("workspace_id", "usage_date", name="uq_workspace_ai_usage_date"),
    )
