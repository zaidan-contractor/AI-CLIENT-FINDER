import uuid
from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy import (
    Column, String, Boolean, DateTime, Integer, Float, ForeignKey, Text, JSON, Numeric
)
from sqlalchemy.orm import relationship
from app.core.database import Base
from app.models.tenancy import generate_uuid, get_utc_now


class ResearchRun(Base):
    __tablename__ = "research_runs"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    workspace_id = Column(String(36), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    business_id = Column(String(36), ForeignKey("master_business_records.id", ondelete="CASCADE"), nullable=False, index=True)
    status = Column(String(50), default="QUEUED", nullable=False, index=True)
    # States: "QUEUED", "RUNNING", "PARTIAL", "COMPLETED", "FAILED", "CANCELLED"
    trigger_type = Column(String(50), default="MANUAL", nullable=False)  # "MANUAL", "DISCOVERY_AUTO", "SCHEDULED"
    requested_scope = Column(JSON, default=list, nullable=False)
    evidence_count = Column(Integer, default=0, nullable=False)
    error_message = Column(Text, nullable=True)
    model_name = Column(String(100), nullable=True)
    prompt_tokens = Column(Integer, default=0, nullable=False)
    completion_tokens = Column(Integer, default=0, nullable=False)
    estimated_cost_usd = Column(Numeric(10, 6), default=Decimal("0.000000"), nullable=False)
    idempotency_key = Column(String(255), nullable=True, index=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=get_utc_now, onupdate=get_utc_now, nullable=False)

    business = relationship("MasterBusinessRecord", backref="research_runs")
    evidence_records = relationship("EvidenceRecord", back_populates="research_run", cascade="all, delete-orphan")


class EvidenceRecord(Base):
    __tablename__ = "evidence_records"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    workspace_id = Column(String(36), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    business_id = Column(String(36), ForeignKey("master_business_records.id", ondelete="CASCADE"), nullable=False, index=True)
    research_run_id = Column(String(36), ForeignKey("research_runs.id", ondelete="SET NULL"), nullable=True, index=True)
    signal_type = Column(String(50), nullable=False, index=True)
    # Types: "DOM_SELECTOR", "META_TAG", "TECH_STACK", "REVIEW_TEXT", "INFRA_PROBE", "API_LOOKUP", "CONTACT_DISCOVERY"
    source_url = Column(String(512), nullable=True)
    raw_observation = Column(Text, nullable=False)
    feature_presence_state = Column(String(50), nullable=False, index=True)
    # States: "PRESENT", "NOT_OBSERVED", "ABSENCE_CONFIRMED", "UNKNOWN"
    epistemic_tier = Column(String(50), default="FACT", nullable=False, index=True)
    # Epistemic Tiers: "FACT", "INFERENCE", "HYPOTHESIS", "UNKNOWN"
    content_hash = Column(String(64), nullable=True, index=True)
    confidence = Column(Float, nullable=False, default=1.0)
    classification_reason = Column(Text, nullable=False)
    source_scope = Column(String(50), default="SINGLE_SOURCE", nullable=False)
    source_provider = Column(String(50), nullable=True)
    observation_type = Column(String(50), nullable=True)
    corroboration_count = Column(Integer, default=1, nullable=False)
    created_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False)

    business = relationship("MasterBusinessRecord", backref="evidence_records")
    research_run = relationship("ResearchRun", back_populates="evidence_records")
