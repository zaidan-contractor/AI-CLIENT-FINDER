import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.database import SessionLocal, Base, engine
from app.models.tenancy import Organization, Workspace, User, Membership, get_utc_now
from app.models.business import MasterBusinessRecord, ContactRecord
from app.models.research import EvidenceRecord, ResearchRun
from app.models.opportunity import ProblemHypothesis, ImpactAssessment, SolutionMapping
from app.models.campaign import Campaign, CampaignVersion, ApprovedSalesClaimRecord
from app.models.outreach import SenderAccount
from app.models.inbound import InboundMessage
from app.models.learning import RecommendationRecord
from app.services.claim_governance import ClaimGovernanceService
from app.core.security import get_password_hash, encrypt_credential

def seed(force: bool = False):
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    db_url_masked = engine.url.render_as_string(hide_password=True)
    print(f"Connected to database: {db_url_masked}")

    existing_user = db.query(User).filter(User.email == "admin@omni.com").first()
    if existing_user:
        # Guarantee credentials and active status are always valid
        existing_user.hashed_password = get_password_hash("AdminPass123!")
        existing_user.is_active = True
        
        # Verify organization and workspaces
        org = db.query(Organization).filter_by(id="org_demo").first()
        if not org:
            org = Organization(id="org_demo", name="Omni Enterprises", slug="omni-enterprises")
            db.add(org)
            db.flush()

        ws1 = db.query(Workspace).filter_by(id="ws_demo_1").first()
        if not ws1:
            ws1 = Workspace(id="ws_demo_1", name="Austin Healthcare", slug="austin-healthcare", organization_id="org_demo")
            db.add(ws1)
        ws2 = db.query(Workspace).filter_by(id="ws_demo_2").first()
        if not ws2:
            ws2 = Workspace(id="ws_demo_2", name="Dallas Logistics", slug="dallas-logistics", organization_id="org_demo")
            db.add(ws2)
        db.flush()

        # Verify memberships
        mem1 = db.query(Membership).filter_by(user_id=existing_user.id, workspace_id="ws_demo_1").first()
        if not mem1:
            db.add(Membership(id="mem_demo_1", user_id=existing_user.id, organization_id="org_demo", workspace_id="ws_demo_1", role="WorkspaceAdmin"))
        mem2 = db.query(Membership).filter_by(user_id=existing_user.id, workspace_id="ws_demo_2").first()
        if not mem2:
            db.add(Membership(id="mem_demo_2", user_id=existing_user.id, organization_id="org_demo", workspace_id="ws_demo_2", role="WorkspaceAdmin"))
        
        db.commit()
        db.close()
        print("Demo database verified: admin@omni.com / AdminPass123! is active with memberships in ws_demo_1 and ws_demo_2.")
        return

    print("Seeding demo database for real browser validation...")

    org = Organization(id="org_demo", name="Omni Enterprises", slug="omni-enterprises")
    ws1 = Workspace(id="ws_demo_1", name="Austin Healthcare", slug="austin-healthcare", organization_id="org_demo")
    ws2 = Workspace(id="ws_demo_2", name="Dallas Logistics", slug="dallas-logistics", organization_id="org_demo")
    db.add_all([org, ws1, ws2])
    db.flush()

    user = User(
        id="usr_demo_admin",
        email="admin@omni.com",
        hashed_password=get_password_hash("AdminPass123!"),
        full_name="Admin Operator"
    )
    db.add(user)
    db.flush()

    mem1 = Membership(id="mem_demo_1", user_id=user.id, organization_id=org.id, workspace_id=ws1.id, role="WorkspaceAdmin")
    mem2 = Membership(id="mem_demo_2", user_id=user.id, organization_id=org.id, workspace_id=ws2.id, role="WorkspaceAdmin")
    db.add_all([mem1, mem2])
    db.flush()

    # Businesses in ws1
    biz1 = MasterBusinessRecord(
        id="biz_austin_1",
        workspace_id=ws1.id,
        name="Austin Family Health",
        normalized_name="austin family health",
        website_url="https://austinfamilyhealth.example.com",
        canonical_domain="austinfamilyhealth.example.com",
        primary_email="contact@austinfamilyhealth.example.com",
        phone="512-555-0101",
        address="100 Congress Ave",
        city="Austin",
        state="TX",
        country="US",
        category="healthcare",
        source_provider="MANUAL"
    )
    biz2 = MasterBusinessRecord(
        id="biz_austin_2",
        workspace_id=ws1.id,
        name="Hill Country Dental",
        normalized_name="hill country dental",
        website_url="https://hillcountrydental.example.com",
        canonical_domain="hillcountrydental.example.com",
        primary_email="appointments@hillcountrydental.example.com",
        phone="512-555-0102",
        address="2500 Bee Cave Rd",
        city="Austin",
        state="TX",
        country="US",
        category="healthcare",
        source_provider="MANUAL"
    )
    db.add_all([biz1, biz2])
    db.flush()

    # Contact in ws1
    cnt1 = ContactRecord(
        id="cnt_austin_1",
        workspace_id=ws1.id,
        business_id=biz1.id,
        email="contact@austinfamilyhealth.example.com",
        canonical_email="contact@austinfamilyhealth.example.com",
        verification_tier="CONFIRMED_VERIFIED",
        freshness_timestamp=get_utc_now()
    )
    db.add(cnt1)

    # Research run for biz1
    run1 = ResearchRun(
        id="run_demo_1",
        workspace_id=ws1.id,
        business_id=biz1.id,
        status="COMPLETED",
        trigger_type="MANUAL",
        evidence_count=2,
        started_at=get_utc_now(),
        completed_at=get_utc_now()
    )
    db.add(run1)
    db.flush()

    # Evidence, Hypothesis, Impact, Solution for biz1
    ev1 = EvidenceRecord(
        id="ev_demo_1",
        workspace_id=ws1.id,
        business_id=biz1.id,
        research_run_id=run1.id,
        signal_type="DOM_ELEMENT",
        raw_observation="No digital patient registration or appointment form on website.",
        feature_presence_state="NOT_OBSERVED",
        classification_reason="Confirmed absence during diagnostic crawl",
        confidence=0.92
    )
    ev2 = EvidenceRecord(
        id="ev_demo_2",
        workspace_id=ws1.id,
        business_id=biz1.id,
        research_run_id=run1.id,
        signal_type="REVIEW_SENTIMENT",
        raw_observation="Patients cite telephone hold times exceeding 15 minutes.",
        feature_presence_state="PRESENT",
        classification_reason="Review sentiment analysis",
        confidence=0.88
    )
    hyp1 = ProblemHypothesis(
        id="hyp_demo_1",
        workspace_id=ws1.id,
        business_id=biz1.id,
        category="MANUAL_SCHEDULING_BOTTLENECK",
        title="Manual Scheduling & Phone Bottleneck",
        description="Patient intake relies entirely on telephone reception causing booking abandonment.",
        status="VALIDATED",
        confidence=0.90,
        supporting_evidence_ids=["ev_demo_1", "ev_demo_2"]
    )
    imp1 = ImpactAssessment(
        id="imp_demo_1",
        workspace_id=ws1.id,
        hypothesis_id="hyp_demo_1",
        estimated_impact_low=Decimal("18000.00"),
        estimated_impact_high=Decimal("36000.00"),
        estimated_impact_midpoint=Decimal("27000.00"),
        impact_currency="USD"
    )
    sol1 = SolutionMapping(
        id="sol_demo_1",
        workspace_id=ws1.id,
        hypothesis_id="hyp_demo_1",
        solution_type="AI_RECEPTIONIST",
        title="24/7 Automated Patient Booking Assistant",
        capability_summary="Instant automated intake and digital scheduling",
        feasibility_score=0.95
    )
    db.add_all([ev1, ev2, hyp1, imp1, sol1])
    db.flush()

    # Sender account in ws1
    sender = SenderAccount(
        id="s_demo_1",
        workspace_id=ws1.id,
        email="audit@omni-health.test",
        display_name="Omni Healthcare Audits",
        provider_type="SMTP",
        encrypted_credentials=encrypt_credential("demo-smtp-secret-pass"),
        status="ACTIVE",
        daily_quota=100,
        connection_verified=True,
        dns_valid=True,
        spf_valid=True,
        dkim_valid=True,
        dmarc_valid=True,
        tracking_domain_aligned=True,
        delivery_health_ok=True,
        bounce_rate_pct=0.1,
        spam_complaint_pct=0.0
    )
    db.add(sender)
    db.commit()

    # Campaign & Claim in ws1
    camp = ClaimGovernanceService.create_campaign(db, ws1.id, "Austin Healthcare Efficiency 2026")
    ver = ClaimGovernanceService.create_campaign_version(db, ws1.id, camp.id, [{"step": 1}])
    claim = ClaimGovernanceService.create_claim(
        db=db,
        workspace_id=ws1.id,
        campaign_version_id=ver.id,
        hypothesis_id=hyp1.id,
        claim_text="Clinic intake requires telephone calls leading to measurable scheduling delays.",
        evidence_ids=[ev1.id]
    )
    ClaimGovernanceService.approve_claim(db, ws1.id, claim.id, user.id)
    ClaimGovernanceService.approve_campaign_version(db, ws1.id, ver.id, user.id)

    # Inbound message
    inbound1 = InboundMessage(
        id="inb_demo_1",
        workspace_id=ws1.id,
        sender_email="dr.roberts@austinfamilyhealth.example.com",
        recipient_email="audit@omni-health.test",
        subject="Re: Scheduling efficiency diagnostic",
        body_text="We have been looking to automate our front desk for months. Can we discuss pricing and implementation?",
        raw_headers={},
        attachments_metadata=[],
        classified_intent="HOT_DEAL",
        intent_confidence="HIGH",
        thread_state="HUMAN_TAKEOVER",
        escalation_reason="Hot inbound lead requested pricing; operator takeover initiated."
    )
    db.add(inbound1)

    # Recommendation
    rec = RecommendationRecord(
        id="rec_demo_1",
        workspace_id=ws1.id,
        headline="Highlight 24/7 After-Hours Booking Capability",
        suggested_action="Emphasize after-hours booking in outbound sales claims.",
        expected_benefit="Clinics with manual phone bottlenecks convert 34% higher when after-hours booking is highlighted in claims.",
        recommendation_type="MESSAGE_TEMPLATES",
        status="PROPOSED"
    )
    db.add(rec)
    db.commit()
    db.close()
    print("Demo database successfully seeded!")

if __name__ == "__main__":
    seed()
