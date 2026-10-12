import pytest
from datetime import datetime, timezone, date
from decimal import Decimal
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.core.config import settings
from app.core.database import Base, get_db
from app.core.security import get_password_hash, create_access_token
from app.models.tenancy import Organization, Workspace, User, Membership, OrgSuppressionRecord, get_utc_now
from app.models.business import MasterBusinessRecord, ContactRecord
from app.models.research import EvidenceRecord
from app.models.opportunity import ProblemHypothesis, ImpactAssessment, SolutionMapping
from app.models.campaign import Campaign, CampaignVersion, ApprovedSalesClaimRecord
from app.models.outreach import SenderAccount, OutboundMessage
from app.services.claim_governance import ClaimGovernanceService
from app.services.suppression_service import SuppressionService
from app.services.outbound_dispatch_engine import OutboundDispatchEngine

# In-memory test DB
SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def override_get_db():
    try:
        db = TestingSessionLocal()
        yield db
    finally:
        db.close()

client = TestClient(app)

@pytest.fixture(autouse=True)
def setup_db():
    from app.services.email_transports.mock_transport import MockEmailTransport
    from app.services.email_transports.factory import set_transport_override
    mock_transport = MockEmailTransport()
    set_transport_override(mock_transport)
    app.dependency_overrides[get_db] = override_get_db
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)
    app.dependency_overrides.pop(get_db, None)
    set_transport_override(None)

@pytest.fixture
def setup_system():
    db = TestingSessionLocal()
    org = Organization(id="org_full", name="Full 16-Gate Org", slug="full-org", is_active=True)
    ws = Workspace(id="ws_full", organization_id="org_full", name="Full WS", slug="full-ws", is_active=True)
    user = User(
        id="u_full",
        email="operator@full.test",
        hashed_password=get_password_hash("Secret123!"),
        full_name="Operator"
    )
    db.add_all([org, ws, user])
    db.commit()

    m = Membership(id="m_full", user_id="u_full", organization_id="org_full", workspace_id="ws_full", role="WorkspaceAdmin")
    db.add(m)
    db.commit()

    # Business eligible for outreach
    biz = MasterBusinessRecord(
        id="biz_full",
        workspace_id="ws_full",
        name="Apex Diagnostics",
        normalized_name="apex diagnostics",
        category="HEALTHCARE",
        source_provider="LOCAL_DIRECTORY",
        canonical_domain="apexdiagnostics.test",
        website_url="https://apexdiagnostics.test",
        primary_email="apex@diagnostics.test",
        phone="555-4321",
        raw_metadata={"reviews": [{"rating": 1}, {"rating": 2}]}
    )
    ev = EvidenceRecord(
        id="ev_full",
        workspace_id="ws_full",
        business_id="biz_full",
        signal_type="DOM_ELEMENT",
        raw_observation="No online patient intake",
        feature_presence_state="NOT_OBSERVED",
        classification_reason="Confirmed absence",
        confidence=0.9
    )
    ev2 = EvidenceRecord(
        id="ev_full_2",
        workspace_id="ws_full",
        business_id="biz_full",
        signal_type="REVIEW_SENTIMENT",
        raw_observation="Patients complain about manual intake bottlenecks",
        feature_presence_state="PRESENT",
        classification_reason="Review sentiment",
        confidence=0.9
    )
    hyp = ProblemHypothesis(
        id="hyp_full",
        workspace_id="ws_full",
        business_id="biz_full",
        category="MANUAL_SCHEDULING_BOTTLENECK",
        title="Manual Patient Intake",
        description="Patients cannot submit intake forms online",
        status="VALIDATED",
        confidence=0.88,
        supporting_evidence_ids=["ev_full", "ev_full_2"]
    )
    imp = ImpactAssessment(
        id="imp_full",
        workspace_id="ws_full",
        hypothesis_id="hyp_full",
        estimated_impact_low=Decimal("12000.00"),
        estimated_impact_high=Decimal("24000.00"),
        estimated_impact_midpoint=Decimal("18000.00"),
        impact_currency="USD",
        observed_financial_data={},
        business_specific_measurements={},
        industry_benchmark_assumptions={"benchmark_conversion": 0.1},
        model_assumptions={"ticket_size": 150}
    )
    sol = SolutionMapping(
        id="sol_full",
        workspace_id="ws_full",
        hypothesis_id="hyp_full",
        solution_type="AI_RECEPTIONIST",
        title="Automated Intake Portal",
        capability_summary="Online intake",
        feasibility_score=0.9
    )
    db.add_all([biz, ev, ev2, hyp, imp, sol])
    db.commit()

    # Campaign & Version
    camp = ClaimGovernanceService.create_campaign(db, "ws_full", "Healthcare Campaign")
    ver = ClaimGovernanceService.create_campaign_version(db, "ws_full", camp.id, [{"step": 1}])
    claim = ClaimGovernanceService.create_claim(
        db, "ws_full", ver.id, "hyp_full", "Diagnostic confirms manual intake delays.", ["ev_full"]
    )
    ClaimGovernanceService.approve_claim(db, "ws_full", claim.id, "u_full")
    ClaimGovernanceService.approve_campaign_version(db, "ws_full", ver.id, "u_full")

    # Sender account ready for outreach
    sender = SenderAccount(
        id="s_full",
        workspace_id="ws_full",
        email="contact@apex-systems.test",
        display_name="Apex Systems",
        provider_type="SMTP",
        encrypted_credentials="enc",
        status="ACTIVE",
        daily_quota=100,
        connection_verified=True,
        dns_valid=True,
        spf_valid=True,
        dkim_valid=True,
        dmarc_valid=True,
        tracking_domain_aligned=True,
        delivery_health_ok=True,
        bounce_rate_pct=0.5,
        spam_complaint_pct=0.01
    )
    db.add(sender)
    # Verified contacts in database (enforcing Gate 6 database authority)
    recipients = [
        "doctor@apexhealth.test",
        "suppressed-target@apexhealth.test",
        "test-kill@apexhealth.test",
        "takeover@apexhealth.test",
        "idemp@apexhealth.test",
        "api-target@apexhealth.test"
    ]
    for idx, r in enumerate(recipients):
        cnt = ContactRecord(
            id=f"cnt_full_{idx}",
            workspace_id="ws_full",
            business_id="biz_full",
            email=r,
            canonical_email=r.lower().strip(),
            verification_tier="CONFIRMED_VERIFIED",
            freshness_timestamp=get_utc_now()
        )
        db.add(cnt)
    db.commit()

    ver_id = str(ver.id)
    claim_id = str(claim.id)
    db.close()

    return {
        "org_id": "org_full",
        "workspace_id": "ws_full",
        "user_id": "u_full",
        "business_id": "biz_full",
        "campaign_version_id": ver_id,
        "sender_account_id": "s_full",
        "claim_id": claim_id
    }

def test_full_16_gates_successful_dispatch(setup_system):
    db = TestingSessionLocal()
    ctx = setup_system

    msg = OutboundDispatchEngine.dispatch_outbound_message(
        db=db,
        workspace_id=ctx["workspace_id"],
        campaign_version_id=ctx["campaign_version_id"],
        sender_account_id=ctx["sender_account_id"],
        recipient_email="doctor@apexhealth.test",
        recipient_country="US",
        business_id=ctx["business_id"],
        subject="Streamlining Apex Diagnostics intake",
        body_text="Hello, we conducted a diagnostic audit...",
        body_html="<p>Hello, we conducted a diagnostic audit...</p>",
        required_claim_ids=[ctx["claim_id"]],
        side_effect_idempotency_key="idemp_key_1001",
        thread_state="AUTONOMOUS",
        contact_verification_tier="CONFIRMED_VERIFIED"
    )

    # Must pass all 16 gates and be TRANSMITTED
    assert msg.gate_passed_count == 16
    assert msg.status == "TRANSMITTED"
    assert msg.sent_at is not None
    assert msg.provider_message_id is not None
    # RFC 8058 unsubscribe footer must be injected
    assert "Unsubscribe:" in msg.body_text
    db.close()

def test_gate_14_atomic_suppression_blocks_transmission(setup_system):
    db = TestingSessionLocal()
    ctx = setup_system
    recipient = "suppressed-target@apexhealth.test"

    # Write suppression record atomically
    SuppressionService.execute_atomic_suppression(
        db=db,
        organization_id=ctx["org_id"],
        workspace_id=ctx["workspace_id"],
        email=recipient,
        source_channel="UNSUBSCRIBE_LINK",
        reason="Prior unsubscribe request"
    )

    msg = OutboundDispatchEngine.dispatch_outbound_message(
        db=db,
        workspace_id=ctx["workspace_id"],
        campaign_version_id=ctx["campaign_version_id"],
        sender_account_id=ctx["sender_account_id"],
        recipient_email=recipient,
        recipient_country="US",
        business_id=ctx["business_id"],
        subject="Test outreach",
        body_text="Body content",
        body_html=None,
        required_claim_ids=[ctx["claim_id"]],
        side_effect_idempotency_key="idemp_key_supp_1",
        thread_state="AUTONOMOUS",
        contact_verification_tier="CONFIRMED_VERIFIED"
    )

    # Must fail Gate 14 and be marked SUPPRESSED
    assert msg.status == "SUPPRESSED"
    assert msg.gate_passed_count == 13
    assert "GATE 14 VIOLATION" in msg.gate_failure_reason
    assert msg.sent_at is None
    assert msg.provider_message_id is None
    db.close()

def test_gate_15_emergency_kill_switch_halts_outbound(setup_system, monkeypatch):
    db = TestingSessionLocal()
    ctx = setup_system

    # Activate emergency kill switch
    monkeypatch.setattr(settings, "GLOBAL_EMERGENCY_KILL_SWITCH", True)

    msg = OutboundDispatchEngine.dispatch_outbound_message(
        db=db,
        workspace_id=ctx["workspace_id"],
        campaign_version_id=ctx["campaign_version_id"],
        sender_account_id=ctx["sender_account_id"],
        recipient_email="test-kill@apexhealth.test",
        recipient_country="US",
        business_id=ctx["business_id"],
        subject="Test outreach",
        body_text="Body content",
        body_html=None,
        required_claim_ids=[ctx["claim_id"]],
        side_effect_idempotency_key="idemp_key_kill_1",
        thread_state="AUTONOMOUS",
        contact_verification_tier="CONFIRMED_VERIFIED"
    )

    assert msg.status == "BLOCKED"
    assert msg.gate_passed_count == 14
    assert "GATE 15 VIOLATION: Global emergency kill switch is ON" in msg.gate_failure_reason
    db.close()

def test_gate_16_human_takeover_blocks_autonomous_send(setup_system):
    db = TestingSessionLocal()
    ctx = setup_system

    msg = OutboundDispatchEngine.dispatch_outbound_message(
        db=db,
        workspace_id=ctx["workspace_id"],
        campaign_version_id=ctx["campaign_version_id"],
        sender_account_id=ctx["sender_account_id"],
        recipient_email="takeover@apexhealth.test",
        recipient_country="US",
        business_id=ctx["business_id"],
        subject="Test outreach",
        body_text="Body content",
        body_html=None,
        required_claim_ids=[ctx["claim_id"]],
        side_effect_idempotency_key="idemp_key_takeover_1",
        thread_state="HUMAN_TAKEOVER",  # Operator took over conversation
        contact_verification_tier="CONFIRMED_VERIFIED"
    )

    assert msg.status == "BLOCKED"
    assert msg.gate_passed_count == 15
    assert "GATE 16 VIOLATION: Thread is in state 'HUMAN_TAKEOVER'" in msg.gate_failure_reason
    db.close()

def test_side_effect_idempotency(setup_system):
    db = TestingSessionLocal()
    ctx = setup_system
    idemp_key = "idemp_duplicate_test"

    msg1 = OutboundDispatchEngine.dispatch_outbound_message(
        db=db,
        workspace_id=ctx["workspace_id"],
        campaign_version_id=ctx["campaign_version_id"],
        sender_account_id=ctx["sender_account_id"],
        recipient_email="idemp@apexhealth.test",
        recipient_country="US",
        business_id=ctx["business_id"],
        subject="Subject",
        body_text="Body",
        body_html=None,
        required_claim_ids=[ctx["claim_id"]],
        side_effect_idempotency_key=idemp_key
    )
    assert msg1.status == "TRANSMITTED"

    # Second dispatch with identical idempotency key returns existing record without re-sending
    msg2 = OutboundDispatchEngine.dispatch_outbound_message(
        db=db,
        workspace_id=ctx["workspace_id"],
        campaign_version_id=ctx["campaign_version_id"],
        sender_account_id=ctx["sender_account_id"],
        recipient_email="idemp@apexhealth.test",
        recipient_country="US",
        business_id=ctx["business_id"],
        subject="Subject",
        body_text="Body",
        body_html=None,
        required_claim_ids=[ctx["claim_id"]],
        side_effect_idempotency_key=idemp_key
    )
    assert msg1.id == msg2.id
    assert msg2.provider_message_id == msg1.provider_message_id
    db.close()

def test_outbound_api_endpoints(setup_system):
    ctx = setup_system
    token = create_access_token(
        user_id=ctx["user_id"],
        organization_id=ctx["org_id"],
        active_workspace_id=ctx["workspace_id"],
        role="WorkspaceAdmin"
    )
    headers = {"Authorization": f"Bearer {token}"}

    res = client.post("/api/v1/outbound/dispatch", json={
        "campaign_version_id": ctx["campaign_version_id"],
        "sender_account_id": ctx["sender_account_id"],
        "recipient_email": "api-target@apexhealth.test",
        "recipient_country": "US",
        "business_id": ctx["business_id"],
        "subject": "API Dispatch Test",
        "body_text": "Hello doctor",
        "required_claim_ids": [ctx["claim_id"]],
        "side_effect_idempotency_key": "api_idemp_key_1"
    }, headers=headers)

    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "TRANSMITTED"
    assert data["gate_passed_count"] == 16
    assert data["side_effect_idempotency_key"] == "api_idemp_key_1"

    # List messages
    res_list = client.get("/api/v1/outbound/messages", headers=headers)
    assert res_list.status_code == 200
    messages = res_list.json()
    assert len(messages) >= 1
    assert messages[0]["recipient_email"] == "api-target@apexhealth.test"
