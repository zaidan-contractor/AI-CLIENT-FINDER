import statistics
from datetime import date, timedelta
from decimal import Decimal
from typing import Tuple, Dict, Any, Optional, List
from sqlalchemy.orm import Session
from sqlalchemy import select, and_

from app.models.outreach import WorkspaceDailyCost
from app.models.tenancy import SystemAuditEvent, get_utc_now

class CostGuardrailService:
    """
    Enforces Gate 13: Cost & budget guardrails + Deterministic Historical Burn-Rate Anomaly Baseline.

    Specification:
    - Minimum History Required: At least 7 completed operating days.
    - Rolling Historical Window: Previous 14 completed operating days.
    - Baseline Statistic: Median daily spend across rolling window (historical_median_daily_burn).
    - Cold-Start Invariant: If < 7 completed operating days exist, historical-burn anomaly detection
      is NOT active; cold-start campaigns must not receive a fabricated baseline. Normal hard
      budgets strictly govern cold-start spend.
    - Anomaly Trigger Condition: current_projected_daily_burn > 3 * historical_median_daily_burn.
    - Trigger Actions: Instantly halt affected campaign, log SystemAuditEvent, return HUMAN_REVIEW_REQUIRED.
    """

    MIN_COMPLETED_DAYS = 7
    ROLLING_WINDOW_DAYS = 14
    ANOMALY_MULTIPLIER = 3

    @classmethod
    def evaluate_gate_13(
        cls,
        db: Session,
        workspace_id: str,
        campaign_id: str,
        projected_daily_burn: Decimal,
        hard_daily_budget: Decimal = Decimal("100.00"),
        as_of_date: Optional[date] = None
    ) -> Tuple[bool, Optional[str], Dict[str, Any]]:
        current_date = as_of_date or date.today()

        # 1. Hard daily budget ceiling check
        if projected_daily_burn > hard_daily_budget:
            return False, f"GATE 13 VIOLATION: Hard daily budget exceeded. Projected burn {projected_daily_burn} > limit {hard_daily_budget}.", {
                "reason": "HARD_BUDGET_EXCEEDED",
                "projected_daily_burn": float(projected_daily_burn),
                "hard_daily_budget": float(hard_daily_budget)
            }

        # 2. Historical burn-rate anomaly analysis over previous 14 completed days
        start_date = current_date - timedelta(days=cls.ROLLING_WINDOW_DAYS)

        completed_records = db.execute(
            select(WorkspaceDailyCost).where(
                WorkspaceDailyCost.workspace_id == workspace_id,
                WorkspaceDailyCost.is_completed_day == True,
                WorkspaceDailyCost.cost_date >= start_date,
                WorkspaceDailyCost.cost_date < current_date
            ).order_by(WorkspaceDailyCost.cost_date.desc())
        ).scalars().all()

        completed_count = len(completed_records)

        # Cold-Start Invariant: If < 7 completed operating days, anomaly detection is NOT active
        if completed_count < cls.MIN_COMPLETED_DAYS:
            return True, None, {
                "status": "COLD_START_ACTIVE",
                "completed_operating_days": completed_count,
                "historical_anomaly_detector_active": False,
                "projected_daily_burn": float(projected_daily_burn),
                "hard_daily_budget": float(hard_daily_budget)
            }

        # Calculate median daily burn
        burn_amounts = [float(r.amount) for r in completed_records]
        historical_median_burn = Decimal(str(round(statistics.median(burn_amounts), 4)))
        threshold = historical_median_burn * Decimal(str(cls.ANOMALY_MULTIPLIER))

        # Check Anomaly Trigger Condition
        if historical_median_burn > Decimal("0.00") and projected_daily_burn > threshold:
            # Trigger Action: Record immutable audit event
            audit_event = SystemAuditEvent(
                workspace_id=workspace_id,
                actor_id="SYSTEM_COST_GUARDRAIL",
                actor_type="SYSTEM",
                event_type="ANOMALY_HALT",
                payload={
                    "event": "HISTORICAL_BURN_RATE_ANOMALY_DETECTED",
                    "campaign_id": campaign_id,
                    "projected_daily_burn": str(projected_daily_burn),
                    "historical_median_daily_burn": str(historical_median_burn),
                    "threshold": str(threshold),
                    "completed_operating_days": completed_count,
                    "action_taken": "HUMAN_REVIEW_REQUIRED"
                }
            )
            db.add(audit_event)
            db.commit()

            return False, (
                f"GATE 13 VIOLATION: Historical burn-rate anomaly detected! "
                f"Projected daily burn ({projected_daily_burn}) exceeds 3x median baseline ({threshold}). "
                "Halted execution -> HUMAN_REVIEW_REQUIRED."
            ), {
                "status": "HUMAN_REVIEW_REQUIRED",
                "historical_median_burn": float(historical_median_burn),
                "threshold": float(threshold),
                "projected_daily_burn": float(projected_daily_burn),
                "completed_days": completed_count
            }

        return True, None, {
            "status": "APPROVED",
            "historical_median_burn": float(historical_median_burn),
            "threshold": float(threshold),
            "projected_daily_burn": float(projected_daily_burn),
            "completed_days": completed_count
        }
