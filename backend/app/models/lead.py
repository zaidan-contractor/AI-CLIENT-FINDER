"""
Lead Qualification, Email Draft, and Campaign Lead models.

These models form the bridge between the Opportunity Intelligence pipeline
(MasterBusinessRecord, EvidenceRecord, ProblemHypothesis) and the existing
Outbound Dispatch Engine (16-gate pipeline).

CRITICAL INVARIANT:
  LeadQualification.status == "APPROVED_FOR_OUTBOUND" is a DATA STATE only.
  It NEVER itself authorizes email transmission.
  The existing OutboundDispatchEngine (all 16 gates) remains the sole transmission authority.
"""
import hashlib
import json
from decimal import Decimal
from typing import Optional
from sqlalchemy import (
    Column, String, Boolean, DateTime, Integer, Float,
    ForeignKey, Text, JSON, Numeric, UniqueConstraint
)
from sqlalchemy.orm import relationship
from app.core.database import Base
from app.models.tenancy import generate_uuid, get_utc_now


class LeadQualification(Base):
    """
    Server-side lead qualification record.

    Lifecycle states (progressive, never skipped):
      DISCOVERED          — Business found via discovery, no research yet
      RESEARCH_PENDING    — Research has been queued/triggered
      RESEARCHED          — At least one ResearchRun completed (COMPLETED or PARTIAL)
      OPPORTUNITY_FOUND   — At least one VALIDATED ProblemHypothesis exists
      QUALIFIED           — Meets all qualification criteria (evidence, contact, solution)
      DISQUALIFIED        — Explicitly ruled out (missing contact, suppressed, evidence gap)
      NEEDS_REVIEW        — Marginal — human operator must decide
      APPROVED_FOR_DRAFT  — Operator has approved generating an AI email draft
      DRAFT_READY         — At least one EmailDraft in GENERATED/NEEDS_REVIEW/EDITED state
      APPROVED_FOR_OUTBOUND — EmailDraft is APPROVED by operator; ready for 16-gate pipeline

    DOES NOT AUTHORIZE OUTBOUND BY ITSELF.
    """
    __tablename__ = "lead_qualifications"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    workspace_id = Column(String(36), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    business_id = Column(String(36), ForeignKey("master_business_records.id", ondelete="CASCADE"), nullable=False, index=True)

    # Qualification state machine
    status = Column(String(50), default="DISCOVERED", nullable=False, index=True)

    # Qualification signal snapshot (informational at time of qualification)
    evidence_count = Column(Integer, default=0, nullable=False)
    fact_evidence_count = Column(Integer, default=0, nullable=False)
    validated_hypothesis_count = Column(Integer, default=0, nullable=False)
    has_eligible_contact = Column(Boolean, default=False, nullable=False)
    contact_verification_tier = Column(String(50), nullable=True)   # tier at time of qualification
    primary_contact_id = Column(String(36), nullable=True, index=True)
    primary_opportunity_id = Column(String(36), nullable=True, index=True)  # ProblemHypothesis.id
    solution_fit_type = Column(String(50), nullable=True)           # CATALOGUE_SOLUTION / NEEDS_DISCOVERY
    opportunity_score_snapshot = Column(Float, nullable=True)       # ScoringEngine.opportunity_score at time of qualification
    entity_type = Column(String(50), default="UNKNOWN", nullable=False)
    sales_eligibility_state = Column(String(50), default="UNKNOWN_ENTITY_TYPE", nullable=False)

    # Human operator review fields
    rationale = Column(Text, nullable=True)        # AI-generated or system-generated rationale
    qualification_notes = Column(Text, nullable=True)  # Human operator notes
    qualified_by_user_id = Column(String(36), nullable=True, index=True)  # NULL = system-auto
    disqualification_reason = Column(Text, nullable=True)

    # Timestamps
    qualified_at = Column(DateTime(timezone=True), nullable=True)
    approved_for_draft_at = Column(DateTime(timezone=True), nullable=True)
    approved_for_outbound_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=get_utc_now, onupdate=get_utc_now, nullable=False)

    # Relationships
    business = relationship("MasterBusinessRecord", foreign_keys=[business_id])
    drafts = relationship("EmailDraft", back_populates="lead_qualification", cascade="all, delete-orphan")
    campaign_leads = relationship("CampaignLead", back_populates="lead_qualification")

    __table_args__ = (
        # Only one active qualification record per business per workspace
        UniqueConstraint("workspace_id", "business_id", name="uq_lead_qual_workspace_business"),
    )


class EmailDraft(Base):
    """
    AI-generated personalised consultative email draft.

    CRITICAL PERSONALIZATION INVARIANTS:
    - All factual claims MUST reference a valid EvidenceRecord in `supporting_evidence_ids`.
    - The AI MUST NOT invent facts not grounded in verified public evidence.
    - Approval requires a human operator — AI cannot self-approve.
    - Approved draft → APPROVED status, which is a prerequisite for APPROVED_FOR_OUTBOUND.
    - Approved draft ≠ send authorization. The 16-gate engine is the sole send authority.

    Draft statuses:
      GENERATED      — Fresh AI output, awaiting human review
      NEEDS_REVIEW   — System-flagged concern, human must intervene
      EDITED         — Human has made at least one edit
      APPROVED       — Explicitly approved by a human operator
      REJECTED       — Explicitly rejected; must regenerate
      SUPERSEDED     — This version has been replaced by a newer draft for the same lead
    """
    __tablename__ = "email_drafts"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    workspace_id = Column(String(36), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    business_id = Column(String(36), ForeignKey("master_business_records.id", ondelete="CASCADE"), nullable=False, index=True)
    lead_qualification_id = Column(String(36), ForeignKey("lead_qualifications.id", ondelete="CASCADE"), nullable=False, index=True)
    opportunity_id = Column(String(36), nullable=True, index=True)   # ProblemHypothesis.id (informational)
    contact_id = Column(String(36), nullable=True, index=True)        # ContactRecord.id (informational)

    # Email content
    subject = Column(String(500), nullable=False)
    body = Column(Text, nullable=False)
    personalization_points = Column(JSON, default=list, nullable=False)   # List[str] — human-readable rationale
    supporting_evidence_ids = Column(JSON, default=list, nullable=False)  # List[EvidenceRecord.id]

    # AI provenance
    model_name = Column(String(100), nullable=False, default="rule_based_fallback_v1")
    provider = Column(String(50), nullable=False, default="RULE_BASED")
    prompt_version = Column(String(50), nullable=False, default="v1")
    prompt_tokens = Column(Integer, default=0, nullable=False)
    completion_tokens = Column(Integer, default=0, nullable=False)
    estimated_cost_usd = Column(Numeric(10, 6), default=Decimal("0.000000"), nullable=False)
    ai_audit_ledger_id = Column(String(36), nullable=True, index=True)

    # Draft lifecycle
    status = Column(String(50), default="GENERATED", nullable=False, index=True)
    approved_by_user_id = Column(String(36), nullable=True, index=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    rejection_reason = Column(Text, nullable=True)
    version_count = Column(Integer, default=1, nullable=False)

    created_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=get_utc_now, onupdate=get_utc_now, nullable=False)

    # Relationships
    lead_qualification = relationship("LeadQualification", back_populates="drafts")
    versions = relationship("EmailDraftVersion", back_populates="draft", cascade="all, delete-orphan",
                            order_by="EmailDraftVersion.version_number")

    @staticmethod
    def compute_content_hash(subject: str, body: str) -> str:
        """SHA-256 hash of canonicalized subject+body for integrity tracking."""
        canonical = json.dumps({"subject": subject.strip(), "body": body.strip()},
                               sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class EmailDraftVersion(Base):
    """
    Immutable point-in-time snapshot of an EmailDraft.
    Preserves the original AI output and every human edit for audit purposes.

    Answers:
    - "What did the AI originally generate?"
    - "What did the human change, and when?"
    """
    __tablename__ = "email_draft_versions"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    workspace_id = Column(String(36), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    draft_id = Column(String(36), ForeignKey("email_drafts.id", ondelete="CASCADE"), nullable=False, index=True)
    version_number = Column(Integer, nullable=False)
    subject = Column(String(500), nullable=False)
    body = Column(Text, nullable=False)
    content_hash = Column(String(64), nullable=False, index=True)        # SHA-256 of subject+body
    previous_hash = Column(String(64), nullable=True, index=True)        # Prior version hash for chain
    changed_fields = Column(JSON, default=list, nullable=False)          # ["subject", "body"] etc.
    editor_user_id = Column(String(36), nullable=True, index=True)       # NULL = AI-generated
    editor_type = Column(String(20), nullable=False, default="AI")       # "AI" or "HUMAN"
    approval_state_snapshot = Column(String(50), nullable=False)         # draft.status at time of snapshot
    created_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False)

    draft = relationship("EmailDraft", back_populates="versions")


class CampaignLead(Base):
    """
    Links an approved lead (LeadQualification + EmailDraft) to a Campaign.

    INVARIANTS:
    - Only drafts with status == "APPROVED" can be enrolled.
    - CampaignLead.status reflects enrollment state, not send authorization.
    - Sending still requires passing ALL 16 outbound gates.

    Statuses:
      ENROLLED   — Added to campaign, awaiting scheduling
      REMOVED    — Operator removed from campaign
    """
    __tablename__ = "campaign_leads"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    workspace_id = Column(String(36), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    campaign_id = Column(String(36), ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False, index=True)
    lead_qualification_id = Column(String(36), ForeignKey("lead_qualifications.id", ondelete="CASCADE"), nullable=False, index=True)
    email_draft_id = Column(String(36), ForeignKey("email_drafts.id", ondelete="CASCADE"), nullable=False, index=True)

    status = Column(String(50), default="ENROLLED", nullable=False, index=True)
    enrolled_by_user_id = Column(String(36), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=get_utc_now, onupdate=get_utc_now, nullable=False)

    # Relationships
    lead_qualification = relationship("LeadQualification", back_populates="campaign_leads")
    draft = relationship("EmailDraft", foreign_keys=[email_draft_id])

    __table_args__ = (
        UniqueConstraint("campaign_id", "lead_qualification_id", name="uq_campaign_lead"),
    )
