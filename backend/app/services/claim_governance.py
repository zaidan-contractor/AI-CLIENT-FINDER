from datetime import datetime, timezone
from typing import List, Optional, Tuple, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import select, and_, desc

from app.models.campaign import Campaign, CampaignVersion, ApprovedSalesClaimRecord
from app.models.opportunity import ProblemHypothesis
from app.models.tenancy import get_utc_now

class ClaimGovernanceService:
    """
    Cryptographic and human-operator governance service for Sales Claims and Campaigns.
    Enforces Gates 1 to 4 of the Outbound Enforcement Pipeline:
    - Gate 1: CampaignVersion in OPERATOR_APPROVED status
    - Gate 2: ApprovedSalesClaim exists for all generated assertions
    - Gate 3: Every required ApprovedSalesClaim is OPERATOR_APPROVED
    - Gate 4: Approved claim canonical content hash remains valid and untampered
    """

    @classmethod
    def create_campaign(
        cls,
        db: Session,
        workspace_id: str,
        name: str,
        description: Optional[str] = None
    ) -> Campaign:
        campaign = Campaign(
            workspace_id=workspace_id,
            name=name,
            description=description,
            status="DRAFT"
        )
        db.add(campaign)
        db.commit()
        db.refresh(campaign)
        return campaign

    @classmethod
    def create_campaign_version(
        cls,
        db: Session,
        workspace_id: str,
        campaign_id: str,
        sequence_steps: Optional[List[Dict[str, Any]]] = None
    ) -> CampaignVersion:
        campaign = db.execute(
            select(Campaign).where(Campaign.id == campaign_id, Campaign.workspace_id == workspace_id)
        ).scalars().first()
        if not campaign:
            raise ValueError("Campaign not found.")

        # Find highest existing version number
        latest_ver = db.execute(
            select(CampaignVersion)
            .where(CampaignVersion.campaign_id == campaign_id)
            .order_by(desc(CampaignVersion.version_number))
        ).scalars().first()

        next_ver_num = (latest_ver.version_number + 1) if latest_ver else 1

        version = CampaignVersion(
            workspace_id=workspace_id,
            campaign_id=campaign_id,
            version_number=next_ver_num,
            status="DRAFT",
            sequence_steps=sequence_steps or []
        )
        db.add(version)
        db.commit()
        db.refresh(version)
        return version

    @classmethod
    def create_claim(
        cls,
        db: Session,
        workspace_id: str,
        campaign_version_id: str,
        hypothesis_id: str,
        claim_text: str,
        evidence_ids: Optional[List[str]] = None
    ) -> ApprovedSalesClaimRecord:
        ver = db.execute(
            select(CampaignVersion).where(
                CampaignVersion.id == campaign_version_id,
                CampaignVersion.workspace_id == workspace_id
            )
        ).scalars().first()
        if not ver:
            raise ValueError("Campaign version not found.")

        hyp = db.execute(
            select(ProblemHypothesis).where(
                ProblemHypothesis.id == hypothesis_id,
                ProblemHypothesis.workspace_id == workspace_id
            )
        ).scalars().first()
        if not hyp:
            raise ValueError("Problem hypothesis not found.")

        ev_ids = evidence_ids or []
        content_hash = ApprovedSalesClaimRecord.compute_canonical_hash(
            claim_text=claim_text,
            evidence_ids=ev_ids,
            hypothesis_id=hypothesis_id,
            campaign_version_id=campaign_version_id,
            claim_version=1
        )

        claim = ApprovedSalesClaimRecord(
            workspace_id=workspace_id,
            campaign_version_id=campaign_version_id,
            hypothesis_id=hypothesis_id,
            claim_text=claim_text.strip(),
            claim_version=1,
            evidence_ids=ev_ids,
            content_hash=content_hash,
            approval_status="PROPOSED"
        )
        db.add(claim)
        db.commit()
        db.refresh(claim)
        return claim

    @classmethod
    def approve_claim(
        cls,
        db: Session,
        workspace_id: str,
        claim_id: str,
        operator_user_id: str
    ) -> ApprovedSalesClaimRecord:
        claim = db.execute(
            select(ApprovedSalesClaimRecord).where(
                ApprovedSalesClaimRecord.id == claim_id,
                ApprovedSalesClaimRecord.workspace_id == workspace_id
            )
        ).scalars().first()
        if not claim:
            raise ValueError("Claim not found.")

        # Re-verify integrity prior to approval
        if not claim.verify_integrity():
            raise ValueError("Claim integrity compromised. Content hash does not match claim text or inputs.")

        claim.approval_status = "OPERATOR_APPROVED"
        claim.approved_by_user_id = operator_user_id
        claim.approved_at = get_utc_now()
        db.commit()
        db.refresh(claim)
        return claim

    @classmethod
    def mutate_claim_text(
        cls,
        db: Session,
        workspace_id: str,
        claim_id: str,
        new_claim_text: str
    ) -> ApprovedSalesClaimRecord:
        """
        When claim text is mutated:
        Existing claim transitions to SUPERSEDED.
        A new claim version is created in PROPOSED status requiring fresh operator approval!
        """
        claim = db.execute(
            select(ApprovedSalesClaimRecord).where(
                ApprovedSalesClaimRecord.id == claim_id,
                ApprovedSalesClaimRecord.workspace_id == workspace_id
            )
        ).scalars().first()
        if not claim:
            raise ValueError("Claim not found.")

        claim.approval_status = "SUPERSEDED"
        new_version_num = claim.claim_version + 1

        new_hash = ApprovedSalesClaimRecord.compute_canonical_hash(
            claim_text=new_claim_text,
            evidence_ids=claim.evidence_ids or [],
            hypothesis_id=claim.hypothesis_id,
            campaign_version_id=claim.campaign_version_id,
            claim_version=new_version_num
        )

        new_claim = ApprovedSalesClaimRecord(
            workspace_id=workspace_id,
            campaign_version_id=claim.campaign_version_id,
            hypothesis_id=claim.hypothesis_id,
            claim_text=new_claim_text.strip(),
            claim_version=new_version_num,
            evidence_ids=claim.evidence_ids or [],
            content_hash=new_hash,
            approval_status="PROPOSED"
        )
        db.add(new_claim)
        db.commit()
        db.refresh(new_claim)
        return new_claim

    @classmethod
    def approve_campaign_version(
        cls,
        db: Session,
        workspace_id: str,
        campaign_version_id: str,
        operator_user_id: str
    ) -> CampaignVersion:
        version = db.execute(
            select(CampaignVersion).where(
                CampaignVersion.id == campaign_version_id,
                CampaignVersion.workspace_id == workspace_id
            )
        ).scalars().first()
        if not version:
            raise ValueError("Campaign version not found.")

        # Verify all claims attached to this version are OPERATOR_APPROVED and untampered
        claims = db.execute(
            select(ApprovedSalesClaimRecord).where(
                ApprovedSalesClaimRecord.campaign_version_id == campaign_version_id,
                ApprovedSalesClaimRecord.workspace_id == workspace_id
            )
        ).scalars().all()

        for c in claims:
            if c.approval_status != "OPERATOR_APPROVED":
                raise ValueError(f"Cannot approve campaign version: Claim {c.id} is in status {c.approval_status}, not OPERATOR_APPROVED.")
            if not c.verify_integrity():
                raise ValueError(f"Cannot approve campaign version: Claim {c.id} failed integrity verification.")

        # Supersede any previously approved versions of this campaign
        prior_approved = db.execute(
            select(CampaignVersion).where(
                CampaignVersion.campaign_id == version.campaign_id,
                CampaignVersion.workspace_id == workspace_id,
                CampaignVersion.status == "OPERATOR_APPROVED",
                CampaignVersion.id != version.id
            )
        ).scalars().all()

        for p in prior_approved:
            p.status = "SUPERSEDED"

        version.status = "OPERATOR_APPROVED"
        version.approved_by_user_id = operator_user_id
        version.approved_at = get_utc_now()

        # Compute version approval hash across approved claims
        claim_hashes = sorted([c.content_hash for c in claims])
        version.approval_hash = ApprovedSalesClaimRecord.compute_canonical_hash(
            claim_text=f"campaign:{version.campaign_id}:v{version.version_number}",
            evidence_ids=claim_hashes,
            hypothesis_id="root",
            campaign_version_id=version.id,
            claim_version=version.version_number
        )

        db.commit()
        db.refresh(version)
        return version

    @classmethod
    def verify_outbound_claim_gates(
        cls,
        db: Session,
        workspace_id: str,
        campaign_version_id: str,
        required_claim_ids: List[str]
    ) -> Tuple[bool, Optional[str]]:
        """
        Validates Gates 1 through 4:
        - Gate 1: CampaignVersion in OPERATOR_APPROVED status
        - Gate 2: ApprovedSalesClaim exists for all generated assertions
        - Gate 3: Every required ApprovedSalesClaim is OPERATOR_APPROVED
        - Gate 4: Approved claim canonical content hash remains valid and untampered
        """
        version = db.execute(
            select(CampaignVersion).where(
                CampaignVersion.id == campaign_version_id,
                CampaignVersion.workspace_id == workspace_id
            )
        ).scalars().first()

        # Gate 1
        if not version or version.status != "OPERATOR_APPROVED":
            return False, f"GATE 1 VIOLATION: CampaignVersion is in status '{version.status if version else 'NOT_FOUND'}', not OPERATOR_APPROVED."

        if not required_claim_ids:
            return True, None

        # Fetch required claims
        claims = db.execute(
            select(ApprovedSalesClaimRecord).where(
                ApprovedSalesClaimRecord.id.in_(required_claim_ids),
                ApprovedSalesClaimRecord.workspace_id == workspace_id
            )
        ).scalars().all()

        claim_map = {c.id: c for c in claims}

        # Gate 2
        for cid in required_claim_ids:
            if cid not in claim_map:
                return False, f"GATE 2 VIOLATION: Missing ApprovedSalesClaim for required assertion id '{cid}'."

        for c in claims:
            # Gate 3
            if c.approval_status != "OPERATOR_APPROVED":
                return False, f"GATE 3 VIOLATION: Required ApprovedSalesClaim '{c.id}' is in status '{c.approval_status}', not OPERATOR_APPROVED."

            # Gate 4
            if not c.verify_integrity():
                return False, f"GATE 4 VIOLATION: ApprovedSalesClaim '{c.id}' content hash verification failed. Text or inputs were modified after approval."

        return True, None
