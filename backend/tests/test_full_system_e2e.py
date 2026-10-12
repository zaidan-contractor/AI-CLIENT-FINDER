"""
End-to-End System Integration Test
Verifies the complete lifecycle of the Generic AI Opportunity Discovery & Sales Platform
as a unified, single integrated system across all 16 gates and subsystems.
"""

import pytest
from decimal import Decimal
from sqlalchemy.orm import Session
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.main import app
from app.core.security import create_access_token, get_password_hash
from app.core.config import settings

from app.models.tenancy import Organization, Workspace, User, Membership
from app.models.business import MarketTarget, MasterBusinessRecord, ContactRecord
from app.models.research import EvidenceRecord
from app.models.opportunity import ProblemHypothesis, ImpactAssessment, SolutionMapping
from app.models.campaign import Campaign, CampaignVersion, ApprovedSalesClaimRecord
from app.models.outreach import SenderAccount, OutboundMessage, JurisdictionPolicy
from app.models.inbound import InboundMessage
from app.models.learning import RecommendationRecord

from app.services.scoring_engine import ScoringEngine
from app.services.claim_governance import ClaimGovernanceService
from app.services.sender_readiness import SenderReadinessService
from app.services.outbound_dispatch_engine import OutboundDispatchEngine
from app.services.inbound_processor import InboundProcessorService
from app.services.learning_service import LearningService
from app.services.suppression_service import SuppressionService


@pytest.fixture
def e2e_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    session = Session(bind=engine)
    
    # Register FastAPI dependency override
    def override_get_db():
        yield session
    app.dependency_overrides[get_db] = override_get_db
    
    from app.services.email_transports.mock_transport import MockEmailTransport
    from app.services.email_transports.factory import set_transport_override
    mock_transport = MockEmailTransport()
    set_transport_override(mock_transport)
    
    yield session
    
    set_transport_override(None)
    app.dependency_overrides.pop(get_db, None)
    session.close()
    Base.metadata.drop_all(bind=engine)


def test_complete_autonomous_lifecycle_e2e(e2e_db: Session):
    """
    Simulate the complete, unified end-to-end operational pipeline:
    1. Multi-Tenant Provisioning (Org, Workspace, Operator User)
    2. Market & Business Discovery with Contact Verification
    3. Epistemic Evidence Research with Verified Absence
    4. Hypothesis & Impact Modeling with Epistemic Separation & Decimal Math
    5. Multidimensional Opportunity Scoring with Prioritization Heuristic Semantics
    6. Campaign Blueprint & Cryptographic Claim Governance with SHA-256 Hashing
    7. Multi-Sender 8-Dimension Readiness & 4-Tier Policy Limits
    8. 16-Gate Monotonic Outbound Dispatch Runtime
    9. Inbound Reply Processing with Opt-Out Atomic Suppression
    10. Re-dispatch Blocked at Gate 14 Suppression Boundary
    11. Recommendation-Only Pattern Synthesis with Zero Silent Mutations
    """
    
    # -------------------------------------------------------------
    # STEP 1: Multi-Tenant Provisioning
    # -------------------------------------------------------------
    org = Organization(name="Integrated Tech Enterprises", slug="integ-tech")
    e2e_db.add(org)
    e2e_db.flush()

    ws = Workspace(organization_id=org.id, name="US Midmarket Growth", slug="us-midmarket")
    e2e_db.add(ws)
    e2e_db.flush()

    user = User(
        email="operator@integtech.test",
        hashed_password=get_password_hash("SecurePassword99!"),
        full_name="Integrated Systems Operator",
        is_superuser=False
    )
    e2e_db.add(user)
    e2e_db.flush()

    org_member = Membership(organization_id=org.id, workspace_id=None, user_id=user.id, role="OrgAdmin")
    ws_member = Membership(organization_id=org.id, workspace_id=ws.id, user_id=user.id, role="Operator")
    e2e_db.add_all([org_member, ws_member])
    e2e_db.commit()

    assert org.id is not None
    assert ws.id is not None
    assert user.id is not None

    # -------------------------------------------------------------
    # STEP 2: Market & Normalized Business Discovery
    # -------------------------------------------------------------
    market = MarketTarget(
        workspace_id=ws.id,
        query_text="Specialized Medical Clinics",
        location="US",
        target_category="Healthcare"
    )
    e2e_db.add(market)
    e2e_db.flush()

    biz = MasterBusinessRecord(
        workspace_id=ws.id,
        name="Precision Orthopedics LLC",
        normalized_name="precision orthopedics llc",
        canonical_domain="precisionortho.test",
        website_url="https://precisionortho.test",
        category="Healthcare",
        source_provider="LOCAL_DIRECTORY",
        phone="+1 555-404-5050",
        primary_email="contact@precisionortho.test"
    )
    e2e_db.add(biz)
    e2e_db.flush()

    contact = ContactRecord(
        workspace_id=ws.id,
        business_id=biz.id,
        email="dr.smith@precisionortho.test",
        canonical_email="dr.smith@precisionortho.test",
        first_name="Dr. Alan",
        last_name="Smith",
        role_title="Managing Director",
        verification_tier="CONFIRMED_VERIFIED"
    )
    e2e_db.add(contact)
    e2e_db.commit()

    # -------------------------------------------------------------
    # STEP 3: Epistemic Evidence Research (Verified Absence)
    # -------------------------------------------------------------
    ev1 = EvidenceRecord(
        workspace_id=ws.id,
        business_id=biz.id,
        signal_type="DOM_SELECTOR",
        source_url="https://precisionortho.test/patient-intake",
        raw_observation="Probe of DOM found 0 elements matching after-hours consultation booking widget.",
        feature_presence_state="ABSENCE_CONFIRMED",
        confidence=0.92,
        classification_reason="Affirmative DOM probe across 5 samples verified missing intake form."
    )
    ev2 = EvidenceRecord(
        workspace_id=ws.id,
        business_id=biz.id,
        signal_type="INFRA_PROBE",
        source_url="https://precisionortho.test/booking",
        raw_observation="Headless audit found viewport 375px overflow and missing mobile intake.",
        feature_presence_state="ABSENCE_CONFIRMED",
        confidence=0.88,
        classification_reason="Affirmative viewport rendering test failed on mobile intake."
    )
    e2e_db.add_all([ev1, ev2])
    e2e_db.commit()

    # -------------------------------------------------------------
    # STEP 4: Problem Hypothesis & Decimal Impact Modeling
    # -------------------------------------------------------------
    hyp = ProblemHypothesis(
        workspace_id=ws.id,
        business_id=biz.id,
        category="INQUIRY_INTAKE_LEAK",
        title="Uncaptured After-Hours Patient Consultation Requests",
        description="Clinical intake form fails outside business hours leading to estimated 20 patient drop-offs monthly.",
        confidence=0.85,
        status="VALIDATED"
    )
    hyp2 = ProblemHypothesis(
        workspace_id=ws.id,
        business_id=biz.id,
        category="SLOW_LEAD_RESPONSE",
        title="Delayed Triage Response",
        description="Intake triage takes 48+ hours to confirm consultations.",
        confidence=0.80,
        status="VALIDATED"
    )
    e2e_db.add_all([hyp, hyp2])
    e2e_db.flush()

    impact = ImpactAssessment(
        workspace_id=ws.id,
        hypothesis_id=hyp.id,
        estimated_impact_low=Decimal("12000.00"),
        estimated_impact_high=Decimal("18000.00"),
        estimated_impact_midpoint=Decimal("15000.00"),
        impact_currency="USD",
        observed_financial_data={"reported_ticket_floor": "$300"},
        business_specific_measurements={},
        industry_benchmark_assumptions={"conversion_rate_source": "Healthcare Intake Benchmark 2025"},
        model_assumptions={"formula": "N × V × C × 12"}
    )
    sol = SolutionMapping(
        workspace_id=ws.id,
        hypothesis_id=hyp.id,
        solution_type="CATALOGUE_SOLUTION",
        title="Automated Intake & After-Hours Appointment Scheduling",
        capability_summary="Instant automated intake and calendar booking widget",
        feasibility_score=0.95
    )
    e2e_db.add_all([impact, sol])
    e2e_db.commit()

    # -------------------------------------------------------------
    # STEP 5: Multidimensional Scoring Engine
    # -------------------------------------------------------------
    score_result = ScoringEngine.evaluate_business(e2e_db, ws.id, biz.id)
    assert score_result["opportunity_score"] >= 50.0
    assert score_result["score_semantics"] == "PRIORITIZATION_HEURISTIC"
    assert "S1_problem_severity" in score_result["dimensions"]
    assert "S6_adoption_gap" in score_result["dimensions"]
    assert score_result["outreach_eligible"] is True

    # -------------------------------------------------------------
    # STEP 6: Campaign & Cryptographic Claims Governance
    # -------------------------------------------------------------
    camp = Campaign(
        workspace_id=ws.id,
        name="Precision Ortho Intake Campaign",
        status="ACTIVE"
    )
    e2e_db.add(camp)
    e2e_db.flush()

    c_ver = CampaignVersion(
        workspace_id=ws.id,
        campaign_id=camp.id,
        version_number=1,
        status="PROPOSED",
        sequence_steps=[{"step": 1, "template": "consultative_diagnostics_v1"}]
    )
    e2e_db.add(c_ver)
    e2e_db.flush()

    # Create Claim in PROPOSED status
    claim = ClaimGovernanceService.create_claim(
        db=e2e_db,
        workspace_id=ws.id,
        campaign_version_id=c_ver.id,
        hypothesis_id=hyp.id,
        claim_text="Precision Orthopedics patient intake portal was confirmed unreachable outside 9am-5pm hours.",
        evidence_ids=[ev1.id]
    )
    assert claim.approval_status == "PROPOSED"
    assert claim.verify_integrity() is True

    # Operator Signs Off on Claim & Campaign Version
    approved_claim = ClaimGovernanceService.approve_claim(
        db=e2e_db,
        workspace_id=ws.id,
        claim_id=claim.id,
        operator_user_id=user.id
    )
    assert approved_claim.approval_status == "OPERATOR_APPROVED"

    c_ver.status = "OPERATOR_APPROVED"
    e2e_db.commit()

    # -------------------------------------------------------------
    # STEP 7: Multi-Sender 8-Dimension Readiness & 4-Tier Policy
    # -------------------------------------------------------------
    sender = SenderAccount(
        workspace_id=ws.id,
        email="consulting@integtech.test",
        display_name="IntegTech Advisory",
        provider_type="SMTP",
        encrypted_credentials="enc_sender_credentials_test",
        daily_quota=50,
        dns_valid=True,
        spf_valid=True,
        dkim_valid=True,
        dmarc_valid=True,
        tracking_domain_aligned=True,
        connection_verified=True,
        delivery_health_ok=True,
        bounce_rate_pct=0.8,
        spam_complaint_pct=0.01,
        status="ACTIVE"
    )
    e2e_db.add(sender)
    e2e_db.commit()

    readiness = SenderReadinessService.evaluate_account(sender)
    assert readiness["is_ready_for_outreach"] is True
    assert readiness["readiness_state"] == "READY_FOR_OUTREACH"

    # Ensure US Jurisdiction Policy allows outreach
    us_policy = e2e_db.query(JurisdictionPolicy).filter_by(country_code="US").first()
    if not us_policy:
        us_policy = JurisdictionPolicy(
            country_code="US",
            policy_version="v1.0",
            decision="ALLOWED",
            lawful_basis="OPT_OUT",
            requires_one_click_unsubscribe=True,
            requires_physical_address=True,
            description="CAN-SPAM compliant cold consultative outreach",
            is_active=True
        )
        e2e_db.add(us_policy)
        e2e_db.commit()

    # -------------------------------------------------------------
    # STEP 8: Outbound 16-Gate Monotonic Dispatch Runtime
    # -------------------------------------------------------------
    dispatch_msg = OutboundDispatchEngine.dispatch_outbound_message(
        db=e2e_db,
        workspace_id=ws.id,
        campaign_version_id=c_ver.id,
        sender_account_id=sender.id,
        recipient_email="dr.smith@precisionortho.test",
        recipient_country="US",
        business_id=biz.id,
        subject="Precision Orthopedics Consultation Intake Diagnostics",
        body_text="Dear Dr. Smith, our diagnostics observed an after-hours intake deficit.",
        body_html="<p>Dear Dr. Smith, our diagnostics observed an after-hours intake deficit.</p>",
        required_claim_ids=[approved_claim.id],
        side_effect_idempotency_key="e2e_idem_key_001"
    )
    assert dispatch_msg.status == "TRANSMITTED"
    assert dispatch_msg.gate_passed_count == 16

    # Test Idempotency Replay
    replay_msg = OutboundDispatchEngine.dispatch_outbound_message(
        db=e2e_db,
        workspace_id=ws.id,
        campaign_version_id=c_ver.id,
        sender_account_id=sender.id,
        recipient_email="dr.smith@precisionortho.test",
        recipient_country="US",
        business_id=biz.id,
        subject="Precision Orthopedics Consultation Intake Diagnostics",
        body_text="Dear Dr. Smith, our diagnostics observed an after-hours intake deficit.",
        body_html="<p>Dear Dr. Smith, our diagnostics observed an after-hours intake deficit.</p>",
        required_claim_ids=[approved_claim.id],
        side_effect_idempotency_key="e2e_idem_key_001"
    )
    assert replay_msg.id == dispatch_msg.id
    assert replay_msg.status == "TRANSMITTED"

    # -------------------------------------------------------------
    # STEP 9: Inbound Reply Processing with Opt-Out Atomic Suppression
    # -------------------------------------------------------------
    inbound_reply = InboundProcessorService.process_inbound_email(
        db=e2e_db,
        workspace_id=ws.id,
        sender_email="dr.smith@precisionortho.test",
        recipient_email="consulting@integtech.test",
        subject="Re: Intake diagnostics",
        body_text="Please stop emailing me. Unsubscribe immediately.",
        raw_headers={"Message-ID": "<reply-001@precisionortho.test>"}
    )
    assert inbound_reply.classified_intent == "OPT_OUT"
    assert inbound_reply.thread_state == "OPT_OUT_SUPPRESSED"

    # Verify atomic multi-tier suppression was executed immediately
    assert SuppressionService.is_suppressed(e2e_db, org.id, ws.id, "dr.smith@precisionortho.test") is True

    # -------------------------------------------------------------
    # STEP 10: Re-dispatch Blocked at Gate 14 Suppression Boundary
    # -------------------------------------------------------------
    blocked_dispatch = OutboundDispatchEngine.dispatch_outbound_message(
        db=e2e_db,
        workspace_id=ws.id,
        campaign_version_id=c_ver.id,
        sender_account_id=sender.id,
        recipient_email="dr.smith@precisionortho.test",
        recipient_country="US",
        business_id=biz.id,
        subject="Follow up",
        body_text="Following up",
        body_html=None,
        required_claim_ids=[approved_claim.id],
        side_effect_idempotency_key="e2e_idem_key_002"
    )
    assert blocked_dispatch.status == "SUPPRESSED"
    assert "GATE 14" in blocked_dispatch.gate_failure_reason

    # -------------------------------------------------------------
    # STEP 11: Learning Engine & Recommendations (Zero Silent Mutations)
    # -------------------------------------------------------------
    recs = LearningService.generate_recommendations(e2e_db, ws.id)
    assert len(recs) > 0
    assert all(r.status == "PROPOSED" for r in recs)

    # Active campaign was NOT mutated silently
    e2e_db.refresh(c_ver)
    assert c_ver.status == "OPERATOR_APPROVED"

    # Operator explicitly acts on recommendation
    updated_rec = LearningService.update_recommendation_status(
        db=e2e_db,
        workspace_id=ws.id,
        recommendation_id=recs[0].id,
        new_status="ACCEPTED",
        operator_user_id=user.id
    )
    assert updated_rec.status == "ACCEPTED"
