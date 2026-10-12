"""
Lead Qualification Service.

Server-side, deterministic service that evaluates a business against all
qualification signals and assigns a lifecycle status.

CRITICAL SAFETY INVARIANTS:
  - NEVER authorizes outbound email transmission.
  - Qualification status "APPROVED_FOR_OUTBOUND" is a DATA marker only.
  - Gate 6 (authoritative contact verification) remains in OutboundDispatchEngine.
  - Suppression checks are performed in OutboundDispatchEngine (Gate 14), NOT here.
  - A qualified lead with an eligible contact is NOT cleared to send.
  - Scores are INFORMATIONAL only.
"""
import logging
from decimal import Decimal
from typing import Optional, Tuple, Dict, Any
from datetime import timezone
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.models.tenancy import get_utc_now, Workspace
from app.models.business import MasterBusinessRecord, ContactRecord
from app.models.research import EvidenceRecord, ResearchRun
from app.models.opportunity import ProblemHypothesis, ImpactAssessment, SolutionMapping
from app.services.entity_classifier import EntityClassifier
from app.models.lead import LeadQualification
from app.services.scoring_engine import ScoringEngine
from app.services.suppression_service import SuppressionService

logger = logging.getLogger(__name__)

# Minimum thresholds for QUALIFIED status
MIN_EVIDENCE_COUNT = 1
MIN_VALIDATED_HYPOTHESES = 1
ELIGIBLE_VERIFICATION_TIERS = frozenset(["CONFIRMED_VERIFIED", "PROVIDER_VERIFIED"])


class LeadQualificationService:
    """
    Server-side Lead Qualification engine.

    Qualification evaluates:
    1. Research completeness (ResearchRun status)
    2. Evidence depth (total count, FACT evidence count)
    3. Opportunity strength (VALIDATED ProblemHypothesis count)
    4. Contact eligibility (server-side DB verification tier; NOT client-supplied)
    5. Solution fit (CATALOGUE_SOLUTION preferred, NEEDS_DISCOVERY accepted)
    6. Financial impact availability (ImpactAssessment presence)

    Output → LeadQualification record with deterministic status.
    """

    @classmethod
    def get_or_create(cls, db: Session, workspace_id: str, business_id: str) -> Tuple[LeadQualification, bool]:
        """
        Retrieve existing or create a new LeadQualification for this business.
        Returns (record, is_new).
        """
        existing = db.execute(
            select(LeadQualification).where(
                LeadQualification.workspace_id == workspace_id,
                LeadQualification.business_id == business_id
            )
        ).scalars().first()
        if existing:
            return existing, False

        lq = LeadQualification(
            workspace_id=workspace_id,
            business_id=business_id,
            status="DISCOVERED"
        )
        db.add(lq)
        db.commit()
        db.refresh(lq)
        return lq, True

    @classmethod
    def qualify_lead(
        cls,
        db: Session,
        workspace_id: str,
        business_id: str,
        operator_user_id: Optional[str] = None,
        operator_notes: Optional[str] = None,
        force_status: Optional[str] = None
    ) -> LeadQualification:
        """
        Evaluate qualification signals server-side and update the LeadQualification record.

        If `force_status` is provided by an operator (e.g. "DISQUALIFIED"), it takes precedence
        only for manual override statuses: DISQUALIFIED, NEEDS_REVIEW, APPROVED_FOR_DRAFT.
        APPROVED_FOR_OUTBOUND can only be set by `approve_for_outbound()`.

        Returns the updated LeadQualification.
        """
        # Verify business belongs to workspace
        biz = db.execute(
            select(MasterBusinessRecord).where(
                MasterBusinessRecord.id == business_id,
                MasterBusinessRecord.workspace_id == workspace_id
            )
        ).scalars().first()
        if not biz:
            raise ValueError(f"Business '{business_id}' not found in workspace '{workspace_id}'.")

        lq, _ = cls.get_or_create(db, workspace_id, business_id)

        # --- Signal 1: Evidence ---
        evidence_list = db.execute(
            select(EvidenceRecord).where(
                EvidenceRecord.business_id == business_id,
                EvidenceRecord.workspace_id == workspace_id
            )
        ).scalars().all()
        total_evidence = len(evidence_list)
        fact_evidence = sum(1 for e in evidence_list if e.epistemic_tier == "FACT")

        # --- Signal 2: Research completion ---
        research_runs = db.execute(
            select(ResearchRun).where(
                ResearchRun.business_id == business_id,
                ResearchRun.workspace_id == workspace_id
            )
        ).scalars().all()
        has_completed_research = (
            any(r.status in ("COMPLETED", "PARTIAL") for r in research_runs)
            or (total_evidence > 0 and len(research_runs) == 0)
        )

        # --- Signal 3: Validated hypotheses ---
        hypotheses = db.execute(
            select(ProblemHypothesis).where(
                ProblemHypothesis.business_id == business_id,
                ProblemHypothesis.workspace_id == workspace_id
            )
        ).scalars().all()
        validated_hyps = [h for h in hypotheses if h.status == "VALIDATED"]

        # --- Signal 4: Contact eligibility (SERVER-SIDE DB lookup — never trust client) ---
        contacts = db.execute(
            select(ContactRecord).where(
                ContactRecord.business_id == business_id,
                ContactRecord.workspace_id == workspace_id
            )
        ).scalars().all()

        ws = db.execute(select(Workspace).where(Workspace.id == workspace_id)).scalars().first()
        org_id = ws.organization_id if ws else None

        eligible_contact: Optional[ContactRecord] = None
        for c in contacts:
            if c.verification_tier in ELIGIBLE_VERIFICATION_TIERS:
                # Early suppression check (informational optimization — Gate 14 remains authoritative)
                if org_id and c.email and SuppressionService.is_suppressed(db, org_id, workspace_id, c.email):
                    continue
                # Freshness check: max 90 days (informational only here; Gate 6 re-checks)
                if c.freshness_timestamp:
                    ts = c.freshness_timestamp
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    days_old = (get_utc_now() - ts).days
                    if days_old > 90:
                        continue
                eligible_contact = c
                break

        # --- Signal 5: Solution fit ---
        solution_fit: Optional[str] = None
        primary_opp: Optional[ProblemHypothesis] = None
        if validated_hyps:
            # Primary opportunity = VALIDATED hypothesis with highest impact midpoint
            best_hyp = None
            best_midpoint = Decimal("-1")
            for h in validated_hyps:
                if h.impact and h.impact.estimated_impact_midpoint > best_midpoint:
                    best_midpoint = h.impact.estimated_impact_midpoint
                    best_hyp = h
            primary_opp = best_hyp or validated_hyps[0]

            # Best solution type for primary
            for sol in primary_opp.solutions:
                if sol.solution_type == "CATALOGUE_SOLUTION":
                    solution_fit = "CATALOGUE_SOLUTION"
                    break
            if not solution_fit:
                for sol in primary_opp.solutions:
                    if sol.solution_type == "NEEDS_DISCOVERY":
                        solution_fit = "NEEDS_DISCOVERY"
                        break

        # --- Signal 6: Opportunity score snapshot (informational) ---
        score_snapshot: Optional[float] = None
        try:
            score_data = ScoringEngine.evaluate_business(db, workspace_id, business_id)
            score_snapshot = float(score_data.get("opportunity_score", 0))
        except Exception:
            pass

        # --- Signal 7: Entity Type & Sales Eligibility ---
        entity_type = getattr(biz, "entity_type", None)
        sales_eligibility_state = getattr(biz, "sales_eligibility_state", None)
        if not entity_type or entity_type == "UNKNOWN" or not sales_eligibility_state or sales_eligibility_state == "UNKNOWN_ENTITY_TYPE":
            classif = EntityClassifier.classify_entity(
                name=biz.name,
                category=biz.category,
                raw_metadata=biz.raw_metadata or {},
                source_provider=biz.source_provider
            )
            entity_type = classif.entity_type
            sales_eligibility_state = classif.sales_eligibility_state

        is_sales_eligible = (
            sales_eligibility_state == "SALES_ELIGIBLE"
            or (sales_eligibility_state not in ["INSTITUTIONAL", "RESEARCH_ONLY"] and entity_type not in ["GOVERNMENT_PUBLIC_FACILITY", "MUNICIPAL_SPORTS_COMPLEX", "SCHOOL_COLLEGE_FACILITY", "NONPROFIT_OR_COMMUNITY_FACILITY"])
        )

        # --- Determine lifecycle status ---
        computed_status = cls._compute_status(
            has_completed_research=has_completed_research,
            total_evidence=total_evidence,
            validated_hyp_count=len(validated_hyps),
            eligible_contact=eligible_contact,
            solution_fit=solution_fit,
            is_sales_eligible=is_sales_eligible
        )

        # --- Apply operator override for allowed manual statuses ---
        MANUAL_OVERRIDE_ALLOWED = {"DISQUALIFIED", "NEEDS_REVIEW", "APPROVED_FOR_DRAFT"}
        final_status = computed_status
        if force_status and force_status in MANUAL_OVERRIDE_ALLOWED:
            if force_status == "APPROVED_FOR_DRAFT":
                if not is_sales_eligible or computed_status == "RESEARCH_ONLY":
                    raise ValueError(
                        f"Cannot manually set APPROVED_FOR_DRAFT: Entity is classified as non-commercial / institutional "
                        f"({entity_type}, {sales_eligibility_state}) and is restricted to RESEARCH_ONLY."
                    )
                # Only allow APPROVED_FOR_DRAFT if system computed at least QUALIFIED
                STATUS_ORDER = [
                    "DISCOVERED", "RESEARCH_PENDING", "RESEARCHED",
                    "RESEARCH_ONLY", "OPPORTUNITY_FOUND", "QUALIFIED", "DISQUALIFIED",
                    "NEEDS_REVIEW", "APPROVED_FOR_DRAFT", "DRAFT_READY",
                    "APPROVED_FOR_OUTBOUND"
                ]
                sys_idx = STATUS_ORDER.index(computed_status) if computed_status in STATUS_ORDER else 0
                qualified_idx = STATUS_ORDER.index("QUALIFIED")
                if sys_idx < qualified_idx:
                    raise ValueError(
                        f"Cannot manually set APPROVED_FOR_DRAFT: business does not yet meet QUALIFIED criteria "
                        f"(current computed status: {computed_status})."
                    )
            final_status = force_status

        # --- Build rationale ---
        rationale_parts = [
            f"Entity: {entity_type} ({sales_eligibility_state})",
            f"Evidence: {total_evidence} total ({fact_evidence} FACT)",
            f"Validated hypotheses: {len(validated_hyps)}",
            f"Eligible contact: {'YES' if eligible_contact else 'NO'}" +
            (f" ({eligible_contact.verification_tier})" if eligible_contact else ""),
            f"Solution fit: {solution_fit or 'NONE'}",
            f"Opportunity score: {score_snapshot if score_snapshot is not None else 'N/A'}"
        ]
        if validated_hyps:
            rationale_parts.append(
                f"Primary opportunity: {primary_opp.title} ({primary_opp.category})"
            )
        rationale = "; ".join(rationale_parts)

        # --- Persist ---
        lq.evidence_count = total_evidence
        lq.fact_evidence_count = fact_evidence
        lq.validated_hypothesis_count = len(validated_hyps)
        lq.has_eligible_contact = eligible_contact is not None
        lq.contact_verification_tier = eligible_contact.verification_tier if eligible_contact else None
        lq.primary_contact_id = eligible_contact.id if eligible_contact else None
        lq.primary_opportunity_id = primary_opp.id if primary_opp else None
        lq.solution_fit_type = solution_fit
        lq.opportunity_score_snapshot = score_snapshot
        lq.entity_type = entity_type
        lq.sales_eligibility_state = sales_eligibility_state
        lq.status = final_status
        lq.rationale = rationale
        lq.qualified_by_user_id = operator_user_id
        lq.updated_at = get_utc_now()

        if operator_notes:
            lq.qualification_notes = operator_notes

        if final_status in ("QUALIFIED", "APPROVED_FOR_DRAFT") and not lq.qualified_at:
            lq.qualified_at = get_utc_now()

        if final_status == "APPROVED_FOR_DRAFT" and not lq.approved_for_draft_at:
            lq.approved_for_draft_at = get_utc_now()

        db.commit()
        db.refresh(lq)
        return lq

    @classmethod
    def approve_for_outbound(
        cls,
        db: Session,
        workspace_id: str,
        lead_qualification_id: str,
        operator_user_id: str
    ) -> LeadQualification:
        """
        Mark a lead as APPROVED_FOR_OUTBOUND after its email draft has been approved.

        Called after EmailDraftService.approve_draft() — not directly by any automation.
        This is a DATA MARKER only. Actual outbound transmission still requires all 16 gates.
        """
        lq = db.execute(
            select(LeadQualification).where(
                LeadQualification.id == lead_qualification_id,
                LeadQualification.workspace_id == workspace_id
            )
        ).scalars().first()
        if not lq:
            raise ValueError(f"LeadQualification '{lead_qualification_id}' not found.")
        if lq.status not in ("DRAFT_READY", "APPROVED_FOR_DRAFT"):
            raise ValueError(
                f"LeadQualification must be in DRAFT_READY to mark APPROVED_FOR_OUTBOUND. "
                f"Current: {lq.status}"
            )

        lq.status = "APPROVED_FOR_OUTBOUND"
        lq.approved_for_outbound_at = get_utc_now()
        lq.qualified_by_user_id = operator_user_id
        db.commit()
        db.refresh(lq)
        return lq

    @classmethod
    def _compute_status(
        cls,
        has_completed_research: bool,
        total_evidence: int,
        validated_hyp_count: int,
        eligible_contact: Optional[ContactRecord],
        solution_fit: Optional[str],
        is_sales_eligible: bool = True
    ) -> str:
        """
        Deterministic status computation. Never skips stages.
        """
        if not has_completed_research:
            return "RESEARCH_PENDING"

        if not is_sales_eligible:
            return "RESEARCH_ONLY"

        if total_evidence == 0:
            return "RESEARCHED"  # Research ran but nothing found

        if validated_hyp_count == 0:
            return "RESEARCHED"

        if validated_hyp_count >= MIN_VALIDATED_HYPOTHESES:
            # We have opportunities
            if not eligible_contact:
                # Has opportunities but no reachable contact — needs review
                return "OPPORTUNITY_FOUND"

            if solution_fit is None:
                return "NEEDS_REVIEW"

            # Full qualification met
            return "QUALIFIED"

        return "OPPORTUNITY_FOUND"

    @classmethod
    def disqualify(
        cls,
        db: Session,
        workspace_id: str,
        lead_qualification_id: str,
        operator_user_id: str,
        reason: str
    ) -> LeadQualification:
        """Explicit operator disqualification with audit trail."""
        lq = db.execute(
            select(LeadQualification).where(
                LeadQualification.id == lead_qualification_id,
                LeadQualification.workspace_id == workspace_id
            )
        ).scalars().first()
        if not lq:
            raise ValueError(f"LeadQualification '{lead_qualification_id}' not found.")

        lq.status = "DISQUALIFIED"
        lq.disqualification_reason = reason
        lq.qualified_by_user_id = operator_user_id
        lq.updated_at = get_utc_now()
        db.commit()
        db.refresh(lq)
        return lq
