from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import select, func

from app.models.tenancy import get_utc_now
from app.models.business import MasterBusinessRecord
from app.models.opportunity import ProblemHypothesis
from app.models.outreach import OutboundMessage
from app.models.inbound import InboundMessage
from app.models.learning import PatternInsight, RecommendationRecord

class LearningService:
    """
    Recommendation-Only Learning Engine (§6.14).
    Analyzes conversion patterns and surfaces recommendations on the operator dashboard.
    Enforces strict architectural invariant: ZERO silent mutations to active campaigns!
    """

    @classmethod
    def analyze_workspace_performance(cls, db: Session, workspace_id: str) -> Dict[str, Any]:
        total_biz = db.execute(
            select(func.count(MasterBusinessRecord.id)).where(MasterBusinessRecord.workspace_id == workspace_id)
        ).scalar() or 0

        total_hyps = db.execute(
            select(func.count(ProblemHypothesis.id)).where(ProblemHypothesis.workspace_id == workspace_id)
        ).scalar() or 0

        validated_hyps = db.execute(
            select(func.count(ProblemHypothesis.id)).where(
                ProblemHypothesis.workspace_id == workspace_id,
                ProblemHypothesis.status == "VALIDATED"
            )
        ).scalar() or 0

        total_outbound = db.execute(
            select(func.count(OutboundMessage.id)).where(OutboundMessage.workspace_id == workspace_id)
        ).scalar() or 0

        transmitted_outbound = db.execute(
            select(func.count(OutboundMessage.id)).where(
                OutboundMessage.workspace_id == workspace_id,
                OutboundMessage.status == "TRANSMITTED"
            )
        ).scalar() or 0

        suppressed_outbound = db.execute(
            select(func.count(OutboundMessage.id)).where(
                OutboundMessage.workspace_id == workspace_id,
                OutboundMessage.status == "SUPPRESSED"
            )
        ).scalar() or 0

        blocked_outbound = db.execute(
            select(func.count(OutboundMessage.id)).where(
                OutboundMessage.workspace_id == workspace_id,
                OutboundMessage.status == "BLOCKED"
            )
        ).scalar() or 0

        total_inbound = db.execute(
            select(func.count(InboundMessage.id)).where(InboundMessage.workspace_id == workspace_id)
        ).scalar() or 0

        hot_deals = db.execute(
            select(func.count(InboundMessage.id)).where(
                InboundMessage.workspace_id == workspace_id,
                InboundMessage.classified_intent == "HOT_DEAL"
            )
        ).scalar() or 0

        opt_outs = db.execute(
            select(func.count(InboundMessage.id)).where(
                InboundMessage.workspace_id == workspace_id,
                InboundMessage.classified_intent == "OPT_OUT"
            )
        ).scalar() or 0

        reply_rate_pct = round((total_inbound / transmitted_outbound * 100.0), 2) if transmitted_outbound > 0 else 0.0
        hot_deal_pct = round((hot_deals / transmitted_outbound * 100.0), 2) if transmitted_outbound > 0 else 0.0
        opt_out_pct = round((opt_outs / transmitted_outbound * 100.0), 2) if transmitted_outbound > 0 else 0.0

        return {
            "workspace_id": workspace_id,
            "businesses_discovered": total_biz,
            "hypotheses_total": total_hyps,
            "hypotheses_validated": validated_hyps,
            "outbound": {
                "total_queued": total_outbound,
                "transmitted": transmitted_outbound,
                "suppressed_gate14": suppressed_outbound,
                "blocked_by_gates": blocked_outbound
            },
            "inbound": {
                "total_replies": total_inbound,
                "hot_deals": hot_deals,
                "opt_outs": opt_outs
            },
            "conversion_metrics": {
                "reply_rate_pct": reply_rate_pct,
                "hot_deal_conversion_pct": hot_deal_pct,
                "opt_out_rate_pct": opt_out_pct
            }
        }

    @classmethod
    def generate_recommendations(cls, db: Session, workspace_id: str) -> List[RecommendationRecord]:
        metrics = cls.analyze_workspace_performance(db, workspace_id)
        recommendations = []

        # Pattern 1: High Opt-Out Rate (> 5%)
        if metrics["conversion_metrics"]["opt_out_rate_pct"] > 5.0:
            rec = RecommendationRecord(
                workspace_id=workspace_id,
                recommendation_type="COPY_IMPROVEMENT",
                headline="Elevated Opt-Out Rate: Tone Softening Recommended",
                suggested_action="Adjust sequence opening hooks from aggressive diagnostic claims to consultative peer inquiries.",
                expected_benefit="Projected 35% reduction in opt-outs and preserved domain sender reputation.",
                status="PROPOSED"
            )
            db.add(rec)
            recommendations.append(rec)

        # Pattern 2: Strong Hot Deal Rate (> 10%)
        if metrics["conversion_metrics"]["hot_deal_conversion_pct"] > 10.0:
            rec = RecommendationRecord(
                workspace_id=workspace_id,
                recommendation_type="AUDIENCE_TARGETING",
                headline="High Conversion Velocity: Scale Campaign Volume",
                suggested_action="Expand daily prospect discovery in healthcare vertical by 25% while maintaining current 16-gate filters.",
                expected_benefit="Anticipated 2x qualified deal flow without degrading sender metrics.",
                status="PROPOSED"
            )
            db.add(rec)
            recommendations.append(rec)

        # Pattern 3: Baseline Consultative Recommendation
        if not recommendations:
            rec = RecommendationRecord(
                workspace_id=workspace_id,
                recommendation_type="CATEGORY_FOCUS",
                headline="Opportunity Calibration: Prioritize High-Impact Hypotheses",
                suggested_action="Focus outreach on businesses exhibiting both after-hours phone gaps and manual booking friction.",
                expected_benefit="Maximizes composite Opportunity Score alignment and response resonance.",
                status="PROPOSED"
            )
            db.add(rec)
            recommendations.append(rec)

        db.commit()
        for r in recommendations:
            db.refresh(r)
        return recommendations

    @classmethod
    def update_recommendation_status(
        cls,
        db: Session,
        workspace_id: str,
        recommendation_id: str,
        new_status: str,
        operator_user_id: str
    ) -> RecommendationRecord:
        if new_status not in ["ACCEPTED", "DISMISSED"]:
            raise ValueError(f"Invalid status '{new_status}'. Allowed: 'ACCEPTED', 'DISMISSED'.")

        rec = db.execute(
            select(RecommendationRecord).where(
                RecommendationRecord.id == recommendation_id,
                RecommendationRecord.workspace_id == workspace_id
            )
        ).scalars().first()
        if not rec:
            raise ValueError("Recommendation not found.")

        rec.status = new_status
        rec.applied_by_user_id = operator_user_id
        rec.applied_at = get_utc_now()
        db.commit()
        db.refresh(rec)
        return rec
