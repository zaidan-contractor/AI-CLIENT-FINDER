from datetime import datetime, timezone, date
from enum import Enum
from sqlalchemy import (
    Column, String, Boolean, DateTime, Date, Integer, ForeignKey, Text, UniqueConstraint
)
from sqlalchemy.orm import relationship
from app.core.database import Base
from app.models.tenancy import generate_uuid, get_utc_now


class CapacityDecisionType(str, Enum):
    ALLOW = "ALLOW"
    QUEUE = "QUEUE"
    REJECT = "REJECT"


class AIQualityTier(str, Enum):
    TIER_1_ADVANCED = "TIER_1_ADVANCED"
    TIER_2_STANDARD = "TIER_2_STANDARD"
    FALLBACK_HEURISTIC = "FALLBACK_HEURISTIC"
    UNKNOWN = "UNKNOWN"


class CapacityPolicy(Base):
    """
    Workspace-level capacity policy establishing daily throughput limits and quality floor.
    Ensures that external provider limits do not define platform behavior.
    """
    __tablename__ = "capacity_policies"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    workspace_id = Column(String(36), ForeignKey("workspaces.id", ondelete="CASCADE"), unique=True, nullable=False, index=True)
    discovery_limit_per_day = Column(Integer, default=50, nullable=False)
    research_limit_per_day = Column(Integer, default=100, nullable=False)
    llm_operations_limit_per_day = Column(Integer, default=200, nullable=False)
    outreach_limit_per_day = Column(Integer, default=50, nullable=False)
    sender_limit_per_day = Column(Integer, default=30, nullable=False)
    campaign_limit_per_day = Column(Integer, default=10, nullable=False)
    min_quality_floor = Column(String(50), default="TIER_1_ADVANCED", nullable=False)
    timezone = Column(String(50), default="UTC", nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=get_utc_now, onupdate=get_utc_now, nullable=False)

    workspace = relationship("Workspace", backref="capacity_policy")


class WorkspaceCapacityDailyUsage(Base):
    """
    Durable, concurrency-safe daily usage counters per workspace.
    Determined using the workspace's local calendar day.
    """
    __tablename__ = "workspace_capacity_daily_usages"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    workspace_id = Column(String(36), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    usage_date = Column(Date, nullable=False, index=True)
    discovery_count = Column(Integer, default=0, nullable=False)
    research_count = Column(Integer, default=0, nullable=False)
    llm_operations_count = Column(Integer, default=0, nullable=False)
    outreach_count = Column(Integer, default=0, nullable=False)
    sender_usage_count = Column(Integer, default=0, nullable=False)
    campaign_usage_count = Column(Integer, default=0, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=get_utc_now, onupdate=get_utc_now, nullable=False)

    __table_args__ = (
        UniqueConstraint("workspace_id", "usage_date", name="uq_workspace_capacity_date"),
    )


class CapacityDecisionAudit(Base):
    """
    Immutable audit record for every evaluated capacity decision.
    Tracks decisions (ALLOW, QUEUE, REJECT) without logging secrets or PII.
    """
    __tablename__ = "capacity_decision_audits"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    workspace_id = Column(String(36), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    operation_type = Column(String(50), nullable=False, index=True)  # DISCOVERY, RESEARCH, LLM_OPERATION, OUTREACH, SENDER, CAMPAIGN
    requested_provider = Column(String(50), nullable=True)
    requested_model = Column(String(100), nullable=True)
    decision = Column(String(20), nullable=False, index=True)        # ALLOW, QUEUE, REJECT
    reason = Column(Text, nullable=False)
    remaining_capacity = Column(Integer, nullable=False, default=0)
    job_id = Column(String(128), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False, index=True)
