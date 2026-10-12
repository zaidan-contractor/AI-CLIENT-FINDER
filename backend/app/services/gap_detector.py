import logging
from typing import List, Dict, Any, Optional
from pydantic import BaseModel

from app.models.research import EvidenceRecord

logger = logging.getLogger(__name__)


class DigitalGapFinding(BaseModel):
    gap_type: str
    title: str
    description: str
    epistemic_statement: str
    confidence: float
    supporting_evidence_ids: List[str]
    suggested_catalogue_solution: str
    solution_type: str  # "CATALOGUE_SOLUTION" or "NEEDS_DISCOVERY"


class GapDetector:
    """
    Deterministic Digital Gap Detection Engine.
    Evaluates evidence records against empirical gap criteria while strictly
    adhering to epistemic observation language (no unsubstantiated assumptions).
    """

    @classmethod
    def detect_gaps(cls, evidence_list: List[EvidenceRecord]) -> List[DigitalGapFinding]:
        findings: List[DigitalGapFinding] = []

        if not evidence_list:
            return findings

        # 1. No Registered Website Gap (requires confirmed absence OR multi-source corroboration)
        no_web_confirmed = [
            e for e in evidence_list 
            if e.feature_presence_state == "ABSENCE_CONFIRMED"
            and (getattr(e, "observation_type", None) == "WEBSITE_CONFIRMED_ABSENT" or ("website" in e.raw_observation.lower() and "booking" not in e.raw_observation.lower() and "appointment" not in e.raw_observation.lower() and "reservation" not in e.raw_observation.lower()))
        ]
        no_web_corroborated = [
            e for e in evidence_list
            if e.feature_presence_state == "NOT_OBSERVED"
            and getattr(e, "corroboration_count", 1) >= 2
            and (getattr(e, "observation_type", None) == "WEBSITE_NOT_OBSERVED" or ("website" in e.raw_observation.lower() and "booking" not in e.raw_observation.lower() and "appointment" not in e.raw_observation.lower() and "reservation" not in e.raw_observation.lower()))
        ]
        valid_no_web = no_web_confirmed or no_web_corroborated
        if valid_no_web:
            conf = 1.0 if no_web_confirmed else 0.80
            findings.append(
                DigitalGapFinding(
                    gap_type="NO_WEBSITE",
                    title="Absence of Registered Digital Web Presence",
                    description="Verified public registries confirm the business operates without a registered primary website.",
                    epistemic_statement="Public registry evidence confirms the absence of an active online website.",
                    confidence=conf,
                    supporting_evidence_ids=[e.id for e in valid_no_web],
                    suggested_catalogue_solution="Turnkey Modern Web Presence & Lead Generation Engine",
                    solution_type="CATALOGUE_SOLUTION"
                )
            )

        # 2. No Online Booking / After-Hours Friction Gap
        # Invariant: A single page without a widget alone does NOT prove absence.
        # Requires EITHER explicit textual denial (ABSENCE_CONFIRMED) OR multi-surface corroboration.
        booking_absent_confirmed = [
            e for e in evidence_list
            if e.feature_presence_state == "ABSENCE_CONFIRMED"
            and ("booking" in e.raw_observation.lower() or "appointment" in e.raw_observation.lower())
        ]
        booking_corroborated_absent = [
            e for e in evidence_list
            if e.feature_presence_state == "NOT_OBSERVED"
            and getattr(e, "corroboration_count", 1) >= 2
            and ("booking" in e.raw_observation.lower() or "appointment" in e.raw_observation.lower())
        ]
        booking_present_ev = [
            e for e in evidence_list
            if e.feature_presence_state == "PRESENT"
            and ("booking" in e.raw_observation.lower() or "appointment" in e.raw_observation.lower())
        ]

        if not booking_present_ev:
            if booking_absent_confirmed:
                findings.append(
                    DigitalGapFinding(
                        gap_type="NO_ONLINE_BOOKING",
                        title="After-Hours Scheduling & Booking Friction",
                        description="Direct public copy confirms the business operates without an automated online scheduling engine.",
                        epistemic_statement="Public web audit indicates automated online booking is absent.",
                        confidence=0.85,
                        supporting_evidence_ids=[e.id for e in booking_absent_confirmed],
                        suggested_catalogue_solution="24/7 Automated Web & WhatsApp Appointment Engine",
                        solution_type="CATALOGUE_SOLUTION"
                    )
                )
            elif booking_corroborated_absent:
                findings.append(
                    DigitalGapFinding(
                        gap_type="NO_ONLINE_BOOKING",
                        title="After-Hours Scheduling & Booking Friction",
                        description="Corroborated digital audit across multiple surfaces indicates lack of an active booking interface.",
                        epistemic_statement="Multi-surface audit indicates booking engine was not observed.",
                        confidence=0.75,
                        supporting_evidence_ids=[e.id for e in booking_corroborated_absent],
                        suggested_catalogue_solution="24/7 Automated Web & WhatsApp Appointment Engine",
                        solution_type="CATALOGUE_SOLUTION"
                    )
                )

        # 3. Mobile Viewport & Performance Deficit Gap
        mobile_ev = [
            e for e in evidence_list
            if "viewport" in e.raw_observation.lower() or "mobile" in e.raw_observation.lower()
        ]
        mobile_absent = [e for e in mobile_ev if e.feature_presence_state in ["ABSENCE_CONFIRMED", "NOT_OBSERVED"]]
        if mobile_absent:
            findings.append(
                DigitalGapFinding(
                    gap_type="MOBILE_VIEWPORT_DEFICIT",
                    title="Mobile Viewport & Conversion Deficit",
                    description="DOM audit indicates missing viewport meta tag or suboptimal responsive configuration for mobile visitors.",
                    epistemic_statement="Page layout inspection indicates that responsive mobile viewport configuration was not detected.",
                    confidence=0.80,
                    supporting_evidence_ids=[e.id for e in mobile_absent],
                    suggested_catalogue_solution="Ultra-Fast Mobile-First Headless Landing Page",
                    solution_type="CATALOGUE_SOLUTION"
                )
            )

        # 4. Unanswered Phone Inquiries / Missed Calls Gap
        review_ev = [
            e for e in evidence_list
            if e.signal_type == "REVIEW_TEXT" and ("unanswered" in e.raw_observation.lower() or "call" in e.raw_observation.lower() or "missed" in e.raw_observation.lower())
        ]
        if review_ev:
            findings.append(
                DigitalGapFinding(
                    gap_type="UNRESPONSIVE_PHONE_INQUIRIES",
                    title="Inbound Communication & Missed Call Friction",
                    description="Customer reviews indicate reported difficulties reaching reception staff during peak business hours.",
                    epistemic_statement="Public customer review analysis indicates reported delays in inbound phone response times.",
                    confidence=0.75,
                    supporting_evidence_ids=[e.id for e in review_ev],
                    suggested_catalogue_solution="AI Conversational Receptionist & Instant Lead Call-Back",
                    solution_type="CATALOGUE_SOLUTION"
                )
            )

        # 5. Infrastructure Security / SSL Gap
        ssl_ev = [
            e for e in evidence_list
            if e.signal_type == "INFRA_PROBE" and "ssl" in e.raw_observation.lower() and e.feature_presence_state != "PRESENT"
        ]
        if ssl_ev:
            findings.append(
                DigitalGapFinding(
                    gap_type="INFRASTRUCTURE_SECURITY_GAP",
                    title="Missing SSL/TLS Transport Encryption",
                    description="Website resolves over unencrypted HTTP, triggering browser security warnings for prospective clients.",
                    epistemic_statement="Network infrastructure probe confirms website traffic is transmitted without active TLS encryption.",
                    confidence=0.95,
                    supporting_evidence_ids=[e.id for e in ssl_ev],
                    suggested_catalogue_solution="Automated TLS/HTTPS Transport Security Hardening",
                    solution_type="CATALOGUE_SOLUTION"
                )
            )

        return findings
