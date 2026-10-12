from decimal import Decimal
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.models.business import MasterBusinessRecord, ContactRecord
from app.models.research import EvidenceRecord
from app.models.opportunity import ProblemHypothesis, ImpactAssessment, SolutionMapping
from app.services.confidence_engine import ConfidenceEngine

class ScoringEngine:
    """
    Deterministic Heuristic Opportunity Scorer (0 - 100).
    Enforces exact architectural weights:
    W1 = 0.25 (Problem Severity)
    W2 = 0.25 (Financial Impact)
    W3 = 0.15 (Solution Feasibility)
    W4 = 0.15 (Commercial Readiness)
    W5 = 0.10 (Market Sentiment Pain)
    W6 = 0.10 (Digital Adoption Gap)
    """

    W1 = 0.25
    W2 = 0.25
    W3 = 0.15
    W4 = 0.15
    W5 = 0.10
    W6 = 0.10

    @classmethod
    def evaluate_business(
        cls,
        db: Session,
        workspace_id: str,
        business_id: str
    ) -> Dict[str, Any]:
        biz = db.execute(
            select(MasterBusinessRecord).where(
                MasterBusinessRecord.id == business_id,
                MasterBusinessRecord.workspace_id == workspace_id
            )
        ).scalars().first()
        if not biz:
            raise ValueError("Business not found.")

        evidence_list = db.execute(
            select(EvidenceRecord).where(
                EvidenceRecord.business_id == business_id,
                EvidenceRecord.workspace_id == workspace_id
            )
        ).scalars().all()

        hypotheses = db.execute(
            select(ProblemHypothesis).where(
                ProblemHypothesis.business_id == business_id,
                ProblemHypothesis.workspace_id == workspace_id
            )
        ).scalars().all()

        validated_hyps = [h for h in hypotheses if h.status == "VALIDATED"]

        # --- S1: Problem Severity (0 - 100) ---
        # Based on cumulative validated hypothesis count & severity (up to 3.5 points)
        m_sev = min(3.5, len(validated_hyps) * 1.5)
        s1 = round(min(100.0, (m_sev / 3.5) * 100.0))

        # --- S2: Financial / Operational Impact (0 - 100) ---
        # Sum of annualized midpoint impact across validated hypotheses
        total_midpoint = Decimal("0.00")
        for h in validated_hyps:
            if h.impact:
                total_midpoint += Decimal(str(h.impact.estimated_impact_midpoint))
        # Benchmark: $15,000 annual impact = 100 score
        s2 = round(min(100.0, float(total_midpoint / Decimal("15000.00")) * 100.0))

        # --- S3: Solution Feasibility (0 - 100) ---
        if validated_hyps:
            avg_feasibility = sum(
                sol.feasibility_score
                for h in validated_hyps
                for sol in h.solutions
            ) / max(1, sum(len(h.solutions) for h in validated_hyps))
            s3 = round(avg_feasibility * 100.0)
        else:
            s3 = 50

        # --- S4: Commercial / Digital Readiness (0 - 100) ---
        # Evaluated from infrastructure signals (website, SSL, contact email)
        readiness_pts = 0
        if biz.website_url:
            readiness_pts += 35
        if biz.primary_email:
            readiness_pts += 35
        if biz.phone:
            readiness_pts += 30
        s4 = readiness_pts

        # --- S5: Market Sentiment Pain (0 - 100) ---
        # S5 = min(100, R_count * 15.0 * (5.0 - R_score) / 4.0)
        reviews = biz.raw_metadata.get("reviews", [])
        if reviews:
            r_count = len(reviews)
            avg_rating = sum(r.get("rating", 5) for r in reviews) / r_count
            pain = r_count * 15.0 * max(0.0, (5.0 - avg_rating) / 4.0)
            s5 = round(min(100.0, pain))
        else:
            s5 = 40  # neutral benchmark

        # --- S6: Digital Solution Adoption Gap (0 - 100) ---
        # Evaluated from absence of modern tools (booking, chat, SSL)
        gap_pts = 0
        if not biz.website_url:
            gap_pts += 50
        if any(e.feature_presence_state in ["ABSENCE_CONFIRMED", "NOT_OBSERVED"] for e in evidence_list):
            gap_pts += 50
        s6_raw = min(100, gap_pts)

        # S6 Cap Rule Invariant:
        # If S1 < 40 or S2 < 40, S6 is strictly capped at 30.
        # A bad website cannot reward a high opportunity score without operational activity & validated problems!
        if s1 < 40 or s2 < 40:
            s6 = min(30, s6_raw)
        else:
            s6 = s6_raw

        # --- Multi-Factor Confidence C in [0, 1] ---
        c = ConfidenceEngine.calculate_confidence(evidence_list)

        # --- Opportunity Score Calculation ---
        weighted_sum = (
            (cls.W1 * s1) +
            (cls.W2 * s2) +
            (cls.W3 * s3) +
            (cls.W4 * s4) +
            (cls.W5 * s5) +
            (cls.W6 * s6)
        )
        opportunity_score = round(max(0.0, min(100.0, weighted_sum * c)))

        # --- Outreach Eligibility ---
        # Eligible requires: score >= 50, at least 1 validated hypothesis, contact email present
        is_eligible = (
            opportunity_score >= 50 and
            len(validated_hyps) >= 1 and
            biz.primary_email is not None
        )

        return {
            "business_id": business_id,
            "opportunity_score": opportunity_score,
            "score_semantics": "PRIORITIZATION_HEURISTIC",
            "confidence": c,
            "dimensions": {
                "S1_problem_severity": s1,
                "S2_financial_impact": s2,
                "S3_solution_feasibility": s3,
                "S4_commercial_readiness": s4,
                "S5_market_sentiment": s5,
                "S6_adoption_gap": s6,
                "S6_capped": s6 < s6_raw
            },
            "outreach_eligible": is_eligible,
            "validated_hypotheses_count": len(validated_hyps),
            "total_annual_impact_midpoint": str(total_midpoint)
        }
