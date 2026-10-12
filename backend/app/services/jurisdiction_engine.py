from typing import Tuple, Optional, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.models.outreach import JurisdictionPolicy

class JurisdictionEngine:
    """
    Enforces Gate 12: JurisdictionPolicy compliance decision evaluates to ALLOWED.
    Deterministic Compliance Engine. The LLM MUST NOT decide legal compliance.

    Invariant:
    UNKNOWN compliance state MUST NOT silently become ALLOWED.
    Unknown jurisdictions or unverified legal grounds strictly resolve to BLOCKED
    or trigger HUMAN_REVIEW_REQUIRED.
    """

    DEFAULT_POLICIES = {
        "US": {
            "decision": "ALLOWED",
            "lawful_basis": "OPT_OUT",
            "requires_one_click_unsubscribe": True,
            "requires_physical_address": True,
            "description": "CAN-SPAM compliant B2B outreach with mandatory opt-out and physical address."
        },
        "CA": {
            "decision": "HUMAN_REVIEW_REQUIRED",
            "lawful_basis": "EXPLICIT_OR_IMPLIED_CONSENT",
            "requires_one_click_unsubscribe": True,
            "requires_physical_address": True,
            "description": "CASL requires verified implied or explicit consent. Operator sign-off required."
        },
        "GB": {
            "decision": "ALLOWED",
            "lawful_basis": "LEGITIMATE_INTEREST",
            "requires_one_click_unsubscribe": True,
            "requires_physical_address": True,
            "description": "UK GDPR B2B legitimate interest assessment required."
        },
        "DE": {
            "decision": "HUMAN_REVIEW_REQUIRED",
            "lawful_basis": "EXPLICIT_CONSENT",
            "requires_one_click_unsubscribe": True,
            "requires_physical_address": True,
            "description": "UWG strict B2B double opt-in consent requirement. Human operator review mandated."
        }
    }

    @classmethod
    def evaluate_gate_12(
        cls,
        db: Session,
        country_code: Optional[str]
    ) -> Tuple[bool, Optional[str], Dict[str, Any]]:
        # Invariant: Missing or unknown jurisdiction MUST NOT silently become ALLOWED
        if not country_code or not country_code.strip():
            return False, "GATE 12 VIOLATION: Recipient jurisdiction is UNKNOWN. Autonomous outreach prohibited without verified legal basis.", {
                "decision": "BLOCKED",
                "reason": "UNKNOWN_JURISDICTION"
            }

        norm_country = country_code.strip().upper()

        # Check database for custom or overridden policies
        db_policy = db.execute(
            select(JurisdictionPolicy).where(
                JurisdictionPolicy.country_code == norm_country,
                JurisdictionPolicy.is_active == True
            )
        ).scalars().first()

        if db_policy:
            decision = db_policy.decision
            lawful_basis = db_policy.lawful_basis
            version = db_policy.policy_version
        elif norm_country in cls.DEFAULT_POLICIES:
            default_p = cls.DEFAULT_POLICIES[norm_country]
            decision = default_p["decision"]
            lawful_basis = default_p["lawful_basis"]
            version = "v1.0-default"
        else:
            # Unregistered country -> Invariant: MUST NOT silently become ALLOWED
            return False, f"GATE 12 VIOLATION: Country '{norm_country}' has no registered JurisdictionPolicy. Requires HUMAN_REVIEW_REQUIRED.", {
                "decision": "HUMAN_REVIEW_REQUIRED",
                "reason": "UNREGISTERED_JURISDICTION",
                "country_code": norm_country
            }

        if decision != "ALLOWED":
            return False, f"GATE 12 VIOLATION: JurisdictionPolicy for '{norm_country}' evaluates to '{decision}' ({lawful_basis}). Dispatch blocked.", {
                "decision": decision,
                "lawful_basis": lawful_basis,
                "country_code": norm_country,
                "policy_version": version
            }

        return True, None, {
            "decision": "ALLOWED",
            "lawful_basis": lawful_basis,
            "country_code": norm_country,
            "policy_version": version
        }
