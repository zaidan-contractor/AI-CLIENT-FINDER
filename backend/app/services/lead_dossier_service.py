"""
Lead Dossier Service.

Aggregates all signals for a business into a single structured dossier.
This is a READ-ONLY aggregation — it does not mutate any state.

No secrets, credentials, or internal identifiers that would aid attacks are exposed.
"""
import logging
from decimal import Decimal
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import select, desc

from app.models.business import MasterBusinessRecord, ContactRecord
from app.models.research import EvidenceRecord, ResearchRun
from app.models.opportunity import ProblemHypothesis, ImpactAssessment, SolutionMapping
from app.models.lead import LeadQualification, EmailDraft
from app.models.campaign import Campaign, CampaignVersion

logger = logging.getLogger(__name__)


class LeadDossierService:
    """
    Read-only aggregation service for the Lead Dossier view.

    Returns a structured dict exposing:
    - Business identity & provenance
    - Research status & history
    - Evidence (with epistemic tiers)
    - Opportunities (primary + secondary, with evidence traceability)
    - Contacts (with server-side verification status)
    - Qualification state & audit
    - Email draft(s) with version history
    - Campaign membership

    Does NOT expose:
    - Encrypted credentials
    - Internal SMTP configuration
    - Suppression hashes or HMAC keys
    - Other workspaces' data
    """

    @classmethod
    def build_dossier(
        cls,
        db: Session,
        workspace_id: str,
        business_id: str
    ) -> Dict[str, Any]:
        """
        Build the full Lead Dossier for a business in the given workspace.
        Raises ValueError if business not found or cross-workspace access attempted.
        """
        # --- Business ---
        biz = db.execute(
            select(MasterBusinessRecord).where(
                MasterBusinessRecord.id == business_id,
                MasterBusinessRecord.workspace_id == workspace_id
            )
        ).scalars().first()
        if not biz:
            raise ValueError(f"Business '{business_id}' not found in workspace '{workspace_id}'.")

        business_section = {
            "id": biz.id,
            "name": biz.name,
            "normalized_name": biz.normalized_name,
            "category": biz.category,
            "city": biz.city,
            "state": biz.state,
            "country": biz.country,
            "website_url": biz.website_url,
            "canonical_domain": biz.canonical_domain,
            "phone": biz.phone,
            "normalized_phone": biz.normalized_phone,
            "primary_email": biz.primary_email,
            "source_provider": biz.source_provider,
            "identity_confidence": biz.identity_confidence,
            "possible_duplicate_of_id": biz.possible_duplicate_of_id,
            "created_at": biz.created_at.isoformat() if biz.created_at else None,
        }

        # --- Research ---
        research_runs = db.execute(
            select(ResearchRun).where(
                ResearchRun.business_id == business_id,
                ResearchRun.workspace_id == workspace_id
            ).order_by(desc(ResearchRun.created_at))
        ).scalars().all()

        latest_run = research_runs[0] if research_runs else None
        research_section = {
            "run_count": len(research_runs),
            "latest_status": latest_run.status if latest_run else "NOT_STARTED",
            "latest_run_id": latest_run.id if latest_run else None,
            "latest_completed_at": (
                latest_run.completed_at.isoformat()
                if latest_run and latest_run.completed_at else None
            ),
            "total_evidence_collected": sum(r.evidence_count for r in research_runs),
        }

        # --- Evidence ---
        evidence_list = db.execute(
            select(EvidenceRecord).where(
                EvidenceRecord.business_id == business_id,
                EvidenceRecord.workspace_id == workspace_id
            ).order_by(desc(EvidenceRecord.created_at))
        ).scalars().all()

        evidence_section = [
            {
                "id": e.id,
                "signal_type": e.signal_type,
                "source_url": e.source_url,
                "raw_observation": e.raw_observation[:500],   # truncated for safety
                "feature_presence_state": e.feature_presence_state,
                "epistemic_tier": e.epistemic_tier,
                "confidence": e.confidence,
                "content_hash": e.content_hash,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in evidence_list
        ]

        # --- Qualification (Loaded first so authoritative linkage is preserved) ---
        lq = db.execute(
            select(LeadQualification).where(
                LeadQualification.business_id == business_id,
                LeadQualification.workspace_id == workspace_id
            )
        ).scalars().first()

        qualification_section = None
        if lq:
            qualification_section = {
                "id": lq.id,
                "status": lq.status,
                "rationale": lq.rationale,
                "qualification_notes": lq.qualification_notes,
                "evidence_count": lq.evidence_count,
                "fact_evidence_count": lq.fact_evidence_count,
                "validated_hypothesis_count": lq.validated_hypothesis_count,
                "has_eligible_contact": lq.has_eligible_contact,
                "contact_verification_tier": lq.contact_verification_tier,
                "primary_contact_id": lq.primary_contact_id,
                "primary_opportunity_id": lq.primary_opportunity_id,
                "solution_fit_type": lq.solution_fit_type,
                "opportunity_score_snapshot": lq.opportunity_score_snapshot,
                "disqualification_reason": lq.disqualification_reason,
                "qualified_at": lq.qualified_at.isoformat() if lq.qualified_at else None,
                "approved_for_draft_at": (
                    lq.approved_for_draft_at.isoformat() if lq.approved_for_draft_at else None
                ),
                "approved_for_outbound_at": (
                    lq.approved_for_outbound_at.isoformat() if lq.approved_for_outbound_at else None
                ),
                "created_at": lq.created_at.isoformat() if lq.created_at else None,
            }

        # --- Opportunities ---
        hypotheses = db.execute(
            select(ProblemHypothesis).where(
                ProblemHypothesis.business_id == business_id,
                ProblemHypothesis.workspace_id == workspace_id
            )
        ).scalars().all()

        validated_hyps = [h for h in hypotheses if h.status == "VALIDATED"]

        # Select primary opportunity: prioritize authoritative qualification selection,
        # otherwise highest impact midpoint among VALIDATED hypotheses
        primary_opp: Optional[ProblemHypothesis] = None
        secondary_opps: List[ProblemHypothesis] = []

        if lq and lq.primary_opportunity_id:
            primary_opp = next((h for h in hypotheses if h.id == lq.primary_opportunity_id), None)

        if not primary_opp and validated_hyps:
            def _impact_midpoint(h: ProblemHypothesis) -> Decimal:
                if h.impact:
                    return Decimal(str(h.impact.estimated_impact_midpoint))
                return Decimal("0")

            sorted_hyps = sorted(validated_hyps, key=_impact_midpoint, reverse=True)
            primary_opp = sorted_hyps[0]
            secondary_opps = sorted_hyps[1:]
        elif primary_opp:
            secondary_opps = [h for h in hypotheses if h.id != primary_opp.id]
        else:
            secondary_opps = hypotheses

        def _serialize_hyp(h: ProblemHypothesis, is_primary: bool) -> Dict[str, Any]:
            best_sol_type = "CATALOGUE_SOLUTION"
            if h.solutions:
                best_sol_type = h.solutions[0].solution_type
                for s in h.solutions:
                    if s.solution_type == "CATALOGUE_SOLUTION":
                        best_sol_type = "CATALOGUE_SOLUTION"
                        break

            impact_data = None
            if h.impact:
                is_calc = getattr(h.impact, "financial_impact_status", "CALCULATED") == "CALCULATED" and h.impact.estimated_impact_midpoint is not None
                impact_data = {
                    "status": "CALCULATED" if is_calc else "UNKNOWN",
                    "currency": h.impact.impact_currency,
                    "low": float(h.impact.estimated_impact_low) if is_calc else None,
                    "high": float(h.impact.estimated_impact_high) if is_calc else None,
                    "midpoint": float(h.impact.estimated_impact_midpoint) if is_calc else None,
                    "formula_assumptions": h.impact.model_assumptions,
                    "observed_financial_data": h.impact.observed_financial_data,
                    "industry_benchmark_assumptions": h.impact.industry_benchmark_assumptions,
                }

            return {
                "id": h.id,
                "is_primary": is_primary,
                "category": h.category,
                "title": h.title,
                "description": h.description,
                "status": h.status,
                "opportunity_state": getattr(h, "opportunity_state", "EVIDENCE_BACKED"),
                "evidence_strength": getattr(h, "evidence_strength", "MODERATE"),
                "unknowns": getattr(h, "unknowns", []),
                "confidence": h.confidence,
                "solution_type": best_sol_type,
                "supporting_evidence_ids": h.supporting_evidence_ids or [],
                "impact": impact_data,
                "solutions": [
                    {
                        "id": s.id,
                        "title": s.title,
                        "solution_type": s.solution_type,
                        "capability_summary": s.capability_summary,
                        "feasibility_score": s.feasibility_score,
                        "operator_approved": s.operator_approved,
                    }
                    for s in h.solutions
                ],
            }

        opportunities_section = {
            "primary": _serialize_hyp(primary_opp, True) if primary_opp else None,
            "secondary": [_serialize_hyp(h, False) for h in secondary_opps],
            "all_hypotheses_count": len(hypotheses),
            "validated_count": len(validated_hyps),
        }

        # --- Entity Classification Section ---
        entity_section = {
            "entity_type": getattr(biz, "entity_type", "UNKNOWN"),
            "confidence": getattr(biz, "entity_type_confidence", 0.5),
            "sales_eligibility_state": getattr(biz, "sales_eligibility_state", "UNKNOWN_ENTITY_TYPE"),
            "classification_reasons": (biz.raw_metadata or {}).get("entity_classification_reasons", []),
            "supporting_signals": (biz.raw_metadata or {}).get("entity_supporting_signals", {})
        }

        # --- Digital Presence Section ---
        web_ev = [
            e for e in evidence_list
            if "website" in e.raw_observation.lower()
            or getattr(e, "observation_type", "") in ["WEBSITE_CONFIRMED", "WEBSITE_NOT_OBSERVED", "WEBSITE_CONFIRMED_ABSENT"]
        ]
        web_status = "WEBSITE_NOT_OBSERVED"
        if biz.website_url:
            web_status = "WEBSITE_CONFIRMED"
        elif any(e.feature_presence_state == "ABSENCE_CONFIRMED" for e in web_ev):
            web_status = "WEBSITE_CONFIRMED_ABSENT"
        elif any(e.feature_presence_state == "UNKNOWN" for e in web_ev):
            web_status = "WEBSITE_UNKNOWN"

        digital_presence_section = {
            "website_url": biz.website_url,
            "status": web_status,
            "canonical_domain": biz.canonical_domain,
            "sources_checked": list({getattr(e, "source_provider", biz.source_provider) for e in web_ev} or {biz.source_provider}),
            "evidence": [e.raw_observation for e in web_ev]
        }

        # --- Booking Section ---
        booking_ev = [
            e for e in evidence_list
            if "booking" in e.raw_observation.lower()
            or "appointment" in e.raw_observation.lower()
            or getattr(e, "observation_type", "") in ["BOOKING_CONFIRMED", "BOOKING_NOT_OBSERVED_ON_RESEARCHED_SURFACES", "BOOKING_INACCESSIBLE"]
        ]
        booking_status = "BOOKING_NOT_OBSERVED_ON_RESEARCHED_SURFACES"
        if any(e.feature_presence_state == "PRESENT" for e in booking_ev):
            booking_status = "BOOKING_CONFIRMED"
        elif any(getattr(e, "observation_type", "") == "BOOKING_INACCESSIBLE" or e.feature_presence_state == "UNKNOWN" for e in booking_ev):
            booking_status = "BOOKING_INACCESSIBLE"
        elif any(e.feature_presence_state == "ABSENCE_CONFIRMED" for e in booking_ev):
            booking_status = "BOOKING_CONFIRMED_ABSENT"

        booking_section = {
            "status": booking_status,
            "surfaces_checked": [e.source_url for e in booking_ev if e.source_url] or [biz.website_url or "discovery_source"],
            "evidence": [e.raw_observation for e in booking_ev]
        }

        # --- Contacts ---
        contacts = db.execute(
            select(ContactRecord).where(
                ContactRecord.business_id == business_id,
                ContactRecord.workspace_id == workspace_id
            ).order_by(ContactRecord.created_at)
        ).scalars().all()

        # Prioritize lq.primary_contact_id if authoritative qualification selected one
        primary_contact: Optional[ContactRecord] = None
        if lq and lq.primary_contact_id:
            primary_contact = next((c for c in contacts if c.id == lq.primary_contact_id), None)

        if not primary_contact:
            eligible_contacts = [c for c in contacts if c.verification_tier in ("CONFIRMED_VERIFIED", "PROVIDER_VERIFIED")]
            primary_contact = eligible_contacts[0] if eligible_contacts else (contacts[0] if contacts else None)

        def _serialize_contact(c: Optional[ContactRecord]) -> Optional[Dict[str, Any]]:
            if not c:
                return None
            return {
                "id": c.id,
                "email": c.email,
                "first_name": c.first_name,
                "last_name": c.last_name,
                "role_title": c.role_title,
                "verification_tier": c.verification_tier,
                "freshness_timestamp": (
                    c.freshness_timestamp.isoformat() if c.freshness_timestamp else None
                ),
                "source": c.verification_metadata.get("source") if c.verification_metadata else None,
            }

        contacts_section = [_serialize_contact(c) for c in contacts if c is not None]
        primary_contact_section = _serialize_contact(primary_contact)

        # --- Email Drafts ---
        drafts = []
        if lq:
            draft_records = db.execute(
                select(EmailDraft).where(
                    EmailDraft.lead_qualification_id == lq.id,
                    EmailDraft.workspace_id == workspace_id
                ).order_by(desc(EmailDraft.created_at))
            ).scalars().all()

            for d in draft_records:
                versions = [
                    {
                        "version_number": v.version_number,
                        "editor_type": v.editor_type,
                        "editor_user_id": v.editor_user_id,
                        "changed_fields": v.changed_fields,
                        "approval_state_snapshot": v.approval_state_snapshot,
                        "content_hash": v.content_hash,
                        "created_at": v.created_at.isoformat() if v.created_at else None,
                    }
                    for v in d.versions
                ]
                drafts.append({
                    "id": d.id,
                    "status": d.status,
                    "subject": d.subject,
                    "body": d.body,
                    "personalization_points": d.personalization_points,
                    "supporting_evidence_ids": d.supporting_evidence_ids,
                    "model_name": d.model_name,
                    "provider": d.provider,
                    "prompt_version": d.prompt_version,
                    "prompt_tokens": d.prompt_tokens,
                    "completion_tokens": d.completion_tokens,
                    "estimated_cost_usd": str(d.estimated_cost_usd),
                    "ai_audit_ledger_id": d.ai_audit_ledger_id,
                    "opportunity_id": d.opportunity_id,
                    "contact_id": d.contact_id,
                    "approved_by_user_id": d.approved_by_user_id,
                    "approved_at": d.approved_at.isoformat() if d.approved_at else None,
                    "rejection_reason": d.rejection_reason,
                    "version_count": d.version_count,
                    "versions": versions,
                    "created_at": d.created_at.isoformat() if d.created_at else None,
                    "updated_at": d.updated_at.isoformat() if d.updated_at else None,
                })

        return {
            "business": business_section,
            "entity": entity_section,
            "digital_presence": digital_presence_section,
            "booking": booking_section,
            "research": research_section,
            "evidence": evidence_section,
            "evidence_records": evidence_section,
            "opportunities": opportunities_section,
            "primary_opportunity": _serialize_hyp(primary_opp, True) if primary_opp else None,
            "contacts": contacts_section,
            "primary_contact": primary_contact_section,
            "qualification": qualification_section,
            "email_drafts": drafts,
        }
