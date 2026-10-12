import uuid
from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy import (
    Column, String, Boolean, DateTime, Integer, Float, ForeignKey, Text, JSON, Numeric
)
from sqlalchemy.orm import relationship
from app.core.database import Base
from app.models.tenancy import generate_uuid, get_utc_now

class ProblemHypothesis(Base):
    __tablename__ = "problem_hypotheses"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    workspace_id = Column(String(36), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    business_id = Column(String(36), ForeignKey("master_business_records.id", ondelete="CASCADE"), nullable=False, index=True)
    category = Column(String(100), nullable=False, index=True)
    # 8 Problem Categories
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=False)
    status = Column(String(50), default="PROPOSED", nullable=False, index=True)
    # States: "PROPOSED", "VALIDATED", "REFUTED"
    opportunity_state = Column(String(50), default="EVIDENCE_BACKED", nullable=False, index=True)
    # States: "EVIDENCE_BACKED", "NEEDS_DISCOVERY", "INSUFFICIENT_EVIDENCE", "RESEARCH_ONLY"
    evidence_strength = Column(String(50), default="MODERATE", nullable=False)
    # "STRONG", "MODERATE", "WEAK"
    unknowns = Column(JSON, default=list, nullable=False)
    confidence = Column(Float, nullable=False)  # 0.0 to 1.0; validation requires >= 0.70
    supporting_evidence_ids = Column(JSON, default=list, nullable=False)
    refutation_reason = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=get_utc_now, onupdate=get_utc_now, nullable=False)

    impact = relationship("ImpactAssessment", back_populates="hypothesis", uselist=False, cascade="all, delete-orphan")
    solutions = relationship("SolutionMapping", back_populates="hypothesis", cascade="all, delete-orphan")


class ImpactAssessment(Base):
    __tablename__ = "impact_assessments"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    workspace_id = Column(String(36), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    hypothesis_id = Column(String(36), ForeignKey("problem_hypotheses.id", ondelete="CASCADE"), unique=True, nullable=False, index=True)
    impact_currency = Column(String(3), nullable=False)  # ISO-4217, e.g. "USD", "INR"
    financial_impact_status = Column(String(50), default="CALCULATED", nullable=False)  # "CALCULATED", "UNKNOWN"
    estimated_impact_low = Column(Numeric(14, 2), nullable=True)
    estimated_impact_high = Column(Numeric(14, 2), nullable=True)
    estimated_impact_midpoint = Column(Numeric(14, 2), nullable=True)
    # Provenance separation:
    observed_financial_data = Column(JSON, default=dict, nullable=False)
    business_specific_measurements = Column(JSON, default=dict, nullable=False)
    industry_benchmark_assumptions = Column(JSON, default=dict, nullable=False)
    model_assumptions = Column(JSON, default=dict, nullable=False)
    created_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False)

    hypothesis = relationship("ProblemHypothesis", back_populates="impact")


class SolutionMapping(Base):
    __tablename__ = "solution_mappings"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    workspace_id = Column(String(36), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    hypothesis_id = Column(String(36), ForeignKey("problem_hypotheses.id", ondelete="CASCADE"), nullable=False, index=True)
    solution_type = Column(String(50), nullable=False)  # "CATALOGUE_SOLUTION" or "BESPOKE_PROPOSAL"
    title = Column(String(255), nullable=False)
    capability_summary = Column(Text, nullable=False)
    feasibility_score = Column(Float, nullable=False, default=1.0)
    operator_approved = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False)

    hypothesis = relationship("ProblemHypothesis", back_populates="solutions")
