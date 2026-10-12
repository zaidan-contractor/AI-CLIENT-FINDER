from typing import Tuple, Dict, Any, Optional
from app.models.outreach import SenderAccount

class SenderReadinessService:
    """
    Evaluates 8-dimension multi-sender readiness and health.
    Enforces Gate 7: Assigned SenderAccount is READY_FOR_OUTREACH.
    Explicitly distinguishes 'Connected' from 'Ready for Outreach'.
    Zero deliverability guarantees are made.
    """

    @classmethod
    def evaluate_account(cls, sender: SenderAccount) -> Dict[str, Any]:
        d1_dns = bool(sender.dns_valid)
        d2_spf = bool(sender.spf_valid)
        d3_dkim = bool(sender.dkim_valid)
        d4_dmarc = bool(sender.dmarc_valid)
        d5_tracking = bool(sender.tracking_domain_aligned)
        d6_conn = bool(sender.connection_verified)
        bounce_rate = float(sender.bounce_rate_pct) if sender.bounce_rate_pct is not None else 0.0
        spam_complaint = float(sender.spam_complaint_pct) if sender.spam_complaint_pct is not None else 0.0
        d7_health = bool(
            sender.delivery_health_ok and
            bounce_rate < 5.0 and
            spam_complaint < 0.1
        )
        d8_status = sender.status in ["ACTIVE", "WARMING_UP"]

        dimensions = {
            "d1_dns_records": d1_dns,
            "d2_spf_validity": d2_spf,
            "d3_dkim_verification": d3_dkim,
            "d4_dmarc_policy": d4_dmarc,
            "d5_custom_tracking_alignment": d5_tracking,
            "d6_provider_connection": d6_conn,
            "d7_delivery_health": d7_health,
            "d8_account_status": d8_status
        }

        all_ready = all(dimensions.values())
        connected_not_ready = d6_conn and not all_ready

        if all_ready:
            state = "READY_FOR_OUTREACH"
        elif connected_not_ready:
            state = "CONNECTED_NOT_READY"
        else:
            state = "NOT_CONNECTED"

        failed_dimensions = [k for k, v in dimensions.items() if not v]

        return {
            "sender_account_id": sender.id,
            "email": sender.email,
            "readiness_state": state,
            "is_ready_for_outreach": all_ready,
            "dimensions": dimensions,
            "failed_dimensions": failed_dimensions,
            "disclaimer": "Readiness indicates diagnostic infrastructure compliance. Zero deliverability guarantees are implied or made."
        }

    @classmethod
    def verify_gate_7(cls, sender: Optional[SenderAccount]) -> Tuple[bool, Optional[str]]:
        """
        Gate 7 Evaluation: Assigned SenderAccount is READY_FOR_OUTREACH.
        """
        if not sender:
            return False, "GATE 7 VIOLATION: No SenderAccount assigned for dispatch."

        evaluation = cls.evaluate_account(sender)
        if not evaluation["is_ready_for_outreach"]:
            reasons = ", ".join(evaluation["failed_dimensions"])
            return False, f"GATE 7 VIOLATION: Sender '{sender.email}' is '{evaluation['readiness_state']}'. Failed dimensions: {reasons}."

        return True, None
