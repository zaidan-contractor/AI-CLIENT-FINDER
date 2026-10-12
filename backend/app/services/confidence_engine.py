from datetime import datetime, timezone
from typing import List
from app.models.research import EvidenceRecord

class ConfidenceEngine:
    """
    Computes deterministic multi-factor confidence C in [0.0, 1.0]:
    C = clamp(C_evidence * C_diversity * C_freshness, 0.0, 1.0)
    """

    @staticmethod
    def calculate_confidence(evidence_records: List[EvidenceRecord]) -> float:
        if not evidence_records:
            return 0.0

        now = datetime.now(timezone.utc)

        # 1. Evidence Quality (average confidence of supporting records)
        conf_scores = []
        for e in evidence_records:
            val = getattr(e, "confidence", None)
            conf_scores.append(float(val) if val is not None else 0.8)
        c_evidence = sum(conf_scores) / len(conf_scores)

        # 2. Source Diversity (number of distinct signal types observed)
        distinct_signal_types = len(set(e.signal_type for e in evidence_records))
        # 1 type: 0.75, 2 types: 0.90, >=3 types: 1.0
        if distinct_signal_types >= 3:
            c_diversity = 1.0
        elif distinct_signal_types == 2:
            c_diversity = 0.90
        else:
            c_diversity = 0.75

        # 3. Freshness (decay if oldest evidence > 30 days old)
        # Fresh <= 30 days -> 1.0, 90 days -> 0.5, >=120 days -> 0.25
        max_age_days = 0.0
        for e in evidence_records:
            obs = getattr(e, "created_at", None) or getattr(e, "observed_at", None)
            if obs:
                if obs.tzinfo is None:
                    obs = obs.replace(tzinfo=timezone.utc)
                age = (now - obs).total_seconds() / 86400.0
                if age > max_age_days:
                    max_age_days = age

        if max_age_days <= 30:
            c_freshness = 1.0
        elif max_age_days <= 90:
            c_freshness = 1.0 - ((max_age_days - 30) / 60.0) * 0.5  # 1.0 -> 0.5
        else:
            c_freshness = max(0.25, 0.5 - ((max_age_days - 90) / 90.0) * 0.25)

        raw_c = c_evidence * c_diversity * c_freshness
        return max(0.0, min(1.0, round(raw_c, 3)))
