import hashlib
import json
from datetime import datetime, timezone
from typing import List, Optional
from sqlalchemy import (
    Column, String, Boolean, DateTime, Integer, ForeignKey, Text, JSON
)
from sqlalchemy.orm import relationship
from app.core.database import Base
from app.models.tenancy import generate_uuid, get_utc_now

class Campaign(Base):
    __tablename__ = "campaigns"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    workspace_id = Column(String(36), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    status = Column(String(50), default="DRAFT", nullable=False, index=True)
    # Statuses: "DRAFT", "ACTIVE", "PAUSED", "ARCHIVED"
    created_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=get_utc_now, onupdate=get_utc_now, nullable=False)

    versions = relationship("CampaignVersion", back_populates="campaign", cascade="all, delete-orphan", order_by="CampaignVersion.version_number")


class CampaignVersion(Base):
    __tablename__ = "campaign_versions"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    workspace_id = Column(String(36), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    campaign_id = Column(String(36), ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False, index=True)
    version_number = Column(Integer, nullable=False, default=1)
    status = Column(String(50), default="DRAFT", nullable=False, index=True)
    # Statuses: "DRAFT", "OPERATOR_APPROVED", "SUPERSEDED", "REJECTED"
    sequence_steps = Column(JSON, default=list, nullable=False)
    approval_hash = Column(String(64), nullable=True)
    approved_by_user_id = Column(String(36), ForeignKey("users.id"), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False)

    campaign = relationship("Campaign", back_populates="versions")
    claims = relationship("ApprovedSalesClaimRecord", back_populates="campaign_version", cascade="all, delete-orphan")


class ApprovedSalesClaimRecord(Base):
    __tablename__ = "approved_sales_claims"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    workspace_id = Column(String(36), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    campaign_version_id = Column(String(36), ForeignKey("campaign_versions.id", ondelete="CASCADE"), nullable=False, index=True)
    hypothesis_id = Column(String(36), ForeignKey("problem_hypotheses.id", ondelete="CASCADE"), nullable=False, index=True)
    claim_text = Column(Text, nullable=False)
    claim_version = Column(Integer, nullable=False, default=1)
    evidence_ids = Column(JSON, default=list, nullable=False)
    content_hash = Column(String(64), nullable=False)
    approval_status = Column(String(50), default="PROPOSED", nullable=False, index=True)
    # Statuses: "PROPOSED", "OPERATOR_APPROVED", "REJECTED", "SUPERSEDED"
    approved_by_user_id = Column(String(36), ForeignKey("users.id"), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=get_utc_now, onupdate=get_utc_now, nullable=False)

    campaign_version = relationship("CampaignVersion", back_populates="claims")
    hypothesis = relationship("ProblemHypothesis")

    @staticmethod
    def compute_canonical_hash(
        claim_text: str,
        evidence_ids: List[str],
        hypothesis_id: str,
        campaign_version_id: str,
        claim_version: int,
    ) -> str:
        canonical_dict = {
            "campaign_version_id": campaign_version_id,
            "claim_text": claim_text.strip(),
            "claim_version": claim_version,
            "evidence_ids": sorted(evidence_ids),
            "hypothesis_id": hypothesis_id,
        }
        canonical_json = json.dumps(canonical_dict, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()

    def verify_integrity(self) -> bool:
        expected = self.compute_canonical_hash(
            claim_text=self.claim_text,
            evidence_ids=self.evidence_ids or [],
            hypothesis_id=self.hypothesis_id,
            campaign_version_id=self.campaign_version_id,
            claim_version=self.claim_version
        )
        return self.content_hash == expected
