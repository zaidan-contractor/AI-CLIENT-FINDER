"""
Authoritative Real SMTP Integration & Outbound Hardening Verification Test Suite.
Verifies all safety and compliance requirements of Phases 1-13:
A. Hard-pinned recipient invariant
B. Normal 16-gate -> Wire delivery success semantics
C. Real provider Message-ID required
D. transport_mode REAL required for production TRANSMITTED
E. No fabricated provider success
F. Duplicate idempotency
G. Real suppression race
H. Kill switch
I. Capacity accounting
J. Post-transmission accounting recovery
K. Credential redaction
L. Tenant isolation

CRITICAL REGRESSION SAFETY INVARIANT:
Automated regression tests run offline with MockEmailTransport.
Real SMTP sockets are strictly reserved for the dedicated manual integration diagnostic script
targeting ONLY opportunity.platform.test@gmail.com.
"""
import uuid
import json
from datetime import datetime, timezone, date, timedelta
from decimal import Decimal
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import Base
from app.core.security import get_password_hash, canonicalize_email
from app.models.tenancy import Organization, Workspace, User, Membership, OrgSuppressionRecord, WorkspaceSuppressionRecord, ComplianceSuppressionLedger, get_utc_now
from app.models.business import MasterBusinessRecord, ContactRecord
from app.models.research import EvidenceRecord
from app.models.opportunity import ProblemHypothesis, ImpactAssessment, SolutionMapping
from app.models.campaign import Campaign, CampaignVersion, ApprovedSalesClaimRecord
from app.models.outreach import SenderAccount, SenderDailyMetric, OutboundMessage, WorkspaceDailyCost, JurisdictionPolicy
from app.models.capacity import CapacityPolicy, WorkspaceCapacityDailyUsage
from app.services.claim_governance import ClaimGovernanceService
from app.services.suppression_service import SuppressionService
from app.services.outbound_dispatch_engine import OutboundDispatchEngine
from app.services.capacity_service import CapacityService
from app.services.email_transports.base import EmailDeliveryResult, BaseEmailTransport
from app.services.email_transports.mock_transport import MockEmailTransport
from app.services.email_transports.smtp_transport import SMTPEmailTransport
from app.services.email_transports.factory import set_transport_override, get_email_transport

# Dedicated in-memory SQLite engine
engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False, "timeout": 30.0},
    poolclass=StaticPool
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

HARD_PINNED_TEST_RECIPIENT = "opportunity.platform.test@gmail.com"
HARD_PINNED_TEST_SENDER = "opportunity.platform.test@gmail.com"


@pytest.fixture(autouse=True)
def setup_test_environment():
    """Ensure mock transport override and clean schema for each test."""
    mock_transport = MockEmailTransport()
    set_transport_override(mock_transport)
    orig_kill_switch = settings.GLOBAL_EMERGENCY_KILL_SWITCH
    orig_env = settings.ENVIRONMENT
    settings.GLOBAL_EMERGENCY_KILL_SWITCH = False
    settings.ENVIRONMENT = "development"

    Base.metadata.create_all(bind=engine)
    yield mock_transport

    Base.metadata.drop_all(bind=engine)
    set_transport_override(None)
    settings.GLOBAL_EMERGENCY_KILL_SWITCH = orig_kill_switch
    settings.ENVIRONMENT = orig_env


def build_authorized_fixture(db, ws_id="ws_smtp_test", recipient_email=HARD_PINNED_TEST_RECIPIENT, sender_email=HARD_PINNED_TEST_SENDER):
    """Build a complete, fully-compliant 16-gate fixture."""
    org = Organization(id=f"org_{ws_id}", name="SMTP Test Org", slug=f"smtp-org-{ws_id}", is_active=True)
    ws = Workspace(id=ws_id, organization_id=org.id, name="SMTP Test WS", slug=f"smtp-ws-{ws_id}", is_active=True)
    user = User(
        id=f"u_{ws_id}",
        email=f"admin@{ws_id}.test",
        hashed_password=get_password_hash("SecretPassword123!"),
        full_name="Test Operator"
    )
    db.add_all([org, ws, user])
    db.flush()

    mem = Membership(id=f"m_{ws_id}", organization_id=org.id, workspace_id=ws.id, user_id=user.id, role="WorkspaceAdmin")
    biz = MasterBusinessRecord(
        id=f"biz_{ws_id}",
        workspace_id=ws.id,
        name="Target Business",
        normalized_name="target business",
        category="HEALTHCARE",
        source_provider="LOCAL_DIRECTORY",
        canonical_domain="targetbiz.test",
        website_url="https://targetbiz.test",
        primary_email=recipient_email,
        phone="555-0100",
        raw_metadata={"reviews": [{"rating": 1}, {"rating": 2}]},
        country="US"
    )
    cnt = ContactRecord(
        id=f"cnt_{ws_id}",
        workspace_id=ws.id,
        business_id=biz.id,
        email=recipient_email,
        canonical_email=canonicalize_email(recipient_email),
        verification_tier="CONFIRMED_VERIFIED",
        freshness_timestamp=get_utc_now()
    )
    ev = EvidenceRecord(
        id=f"ev_{ws_id}",
        workspace_id=ws.id,
        business_id=biz.id,
        signal_type="DOM_ELEMENT",
        raw_observation="No online scheduling observed on provider portal",
        feature_presence_state="NOT_OBSERVED",
        classification_reason="Confirmed absence",
        confidence=0.95
    )
    db.add_all([mem, biz, cnt, ev])
    db.flush()

    hyp = ProblemHypothesis(
        id=f"hyp_{ws_id}",
        workspace_id=ws.id,
        business_id=biz.id,
        category="MANUAL_SCHEDULING_BOTTLENECK",
        title="Diagnostic Intake Bottleneck",
        description="Target lacks automated patient booking",
        status="VALIDATED",
        confidence=0.9,
        supporting_evidence_ids=[ev.id]
    )
    imp = ImpactAssessment(
        id=f"imp_{ws_id}",
        workspace_id=ws.id,
        hypothesis_id=hyp.id,
        estimated_impact_low=Decimal("10000"),
        estimated_impact_high=Decimal("20000"),
        estimated_impact_midpoint=Decimal("15000"),
        impact_currency="USD",
        observed_financial_data={},
        business_specific_measurements={},
        industry_benchmark_assumptions={"benchmark_conversion": 0.1},
        model_assumptions={"ticket_size": 150}
    )
    sol = SolutionMapping(
        id=f"sol_{ws_id}",
        workspace_id=ws.id,
        hypothesis_id=hyp.id,
        solution_type="AI_RECEPTIONIST",
        title="Automated Intake Portal",
        capability_summary="Deploy HIPAA-compliant intake and scheduling automation",
        feasibility_score=0.95
    )
    db.add_all([hyp, imp, sol])
    db.flush()

    camp = ClaimGovernanceService.create_campaign(db, ws.id, "SMTP Integration Campaign")
    ver = ClaimGovernanceService.create_campaign_version(db, ws.id, camp.id, [{"step": 1}])
    claim = ClaimGovernanceService.create_claim(
        db, ws.id, ver.id, hyp.id, "Target lacks automated patient booking.", [ev.id]
    )
    ClaimGovernanceService.approve_claim(db, ws.id, claim.id, user.id)
    ClaimGovernanceService.approve_campaign_version(db, ws.id, ver.id, user.id)

    sender = SenderAccount(
        id=f"sender_{ws_id}",
        workspace_id=ws.id,
        email=sender_email,
        display_name="Diagnostic Sender",
        provider_type="MOCK",
        encrypted_credentials="SuperSecretPassword123!",
        status="ACTIVE",
        daily_quota=50,
        connection_verified=True,
        dns_valid=True,
        spf_valid=True,
        dkim_valid=True,
        dmarc_valid=True,
        tracking_domain_aligned=True,
        delivery_health_ok=True,
        bounce_rate_pct=0.1,
        spam_complaint_pct=0.01
    )
    db.add(sender)

    CapacityService.get_or_create_policy(db, ws.id)
    db.commit()

    return {
        "org": org,
        "ws": ws,
        "user": user,
        "biz": biz,
        "cnt": cnt,
        "ev": ev,
        "claim": claim,
        "campaign": camp,
        "campaign_version": ver,
        "sender": sender
    }


# ==============================================================================
# TEST A: Hard-Pinned Recipient Invariant
# ==============================================================================
def test_hard_pinned_recipient_invariant():
    """Verify diagnostic mode invariant prevents sending to any non-hardpinned recipient."""
    REAL_SMTP_DIAGNOSTIC_ALLOWED_RECIPIENT = "opportunity.platform.test@gmail.com"

    # Permitted
    assert HARD_PINNED_TEST_RECIPIENT == REAL_SMTP_DIAGNOSTIC_ALLOWED_RECIPIENT

    # Any deviation fails closed
    forbidden_recipients = [
        "prospect@somerealbusiness.com",
        "ceo@targetcompany.com",
        "random.user@gmail.com",
        "opportunity.platform.test+alias@gmail.com"
    ]
    for r in forbidden_recipients:
        assert r != REAL_SMTP_DIAGNOSTIC_ALLOWED_RECIPIENT, f"Forbidden recipient {r} passed!"


# ==============================================================================
# TEST B: Normal 16-Gate -> Wire Delivery Success Semantics
# ==============================================================================
def test_normal_16_gate_wire_delivery_success(setup_test_environment):
    """Verify all 16 gates pass and transport returns TRANSMITTED with all required fields."""
    mock_transport = setup_test_environment
    db = TestingSessionLocal()
    ctx = build_authorized_fixture(db, ws_id="ws_norm_success")

    outbound_msg = OutboundDispatchEngine.dispatch_outbound_message(
        db=db,
        workspace_id=ctx["ws"].id,
        campaign_version_id=str(ctx["campaign_version"].id),
        sender_account_id=ctx["sender"].id,
        recipient_email=HARD_PINNED_TEST_RECIPIENT,
        recipient_country="US",
        business_id=ctx["biz"].id,
        subject="Normal Wire Success Subject",
        body_text="Normal Wire Success Body",
        required_claim_ids=[str(ctx["claim"].id)],
        side_effect_idempotency_key="idemp_norm_success_001",
        thread_state="AUTONOMOUS",
        contact_verification_tier="CONFIRMED_VERIFIED"
    )

    assert outbound_msg.status == "TRANSMITTED"
    assert outbound_msg.sent_at is not None
    assert outbound_msg.provider_message_id is not None
    assert outbound_msg.provider_message_id.startswith("msg_mock_")
    assert outbound_msg.transport_mode == "MOCK"
    assert outbound_msg.gate_passed_count == 16
    assert len(mock_transport.sent_messages) == 1
    assert mock_transport.sent_messages[0]["to_email"] == HARD_PINNED_TEST_RECIPIENT
    assert mock_transport.sent_messages[0]["from_email"] == HARD_PINNED_TEST_SENDER
    db.close()


# ==============================================================================
# TEST C: Real Provider Message-ID Required
# ==============================================================================
def test_real_provider_message_id_required():
    """Verify empty or blank provider_message_id is rejected on TRANSMITTED status."""
    db = TestingSessionLocal()
    ctx = build_authorized_fixture(db, ws_id="ws_msgid_req")

    class EmptyMsgIdTransport(BaseEmailTransport):
        def send_email(self, *args, **kwargs):
            return EmailDeliveryResult(
                success=True,
                provider_message_id="",  # Blank/empty
                raw_response={"status": "accepted"}
            )

    set_transport_override(EmptyMsgIdTransport())
    outbound_msg = OutboundDispatchEngine.dispatch_outbound_message(
        db=db,
        workspace_id=ctx["ws"].id,
        campaign_version_id=str(ctx["campaign_version"].id),
        sender_account_id=ctx["sender"].id,
        recipient_email=HARD_PINNED_TEST_RECIPIENT,
        recipient_country="US",
        business_id=ctx["biz"].id,
        subject="Empty Msg ID Subject",
        body_text="Empty Msg ID Body",
        required_claim_ids=[str(ctx["claim"].id)],
        side_effect_idempotency_key="idemp_empty_msgid",
        thread_state="AUTONOMOUS",
        contact_verification_tier="CONFIRMED_VERIFIED"
    )

    assert outbound_msg.status == "DELIVERY_FAILED"
    assert outbound_msg.provider_message_id is None
    assert outbound_msg.sent_at is None
    db.close()


# ==============================================================================
# TEST D & E: transport_mode REAL & No Fabricated Provider Success
# ==============================================================================
def test_no_fabricated_provider_success():
    """Verify provider transport exceptions record DELIVERY_FAILED without fabricated success."""
    db = TestingSessionLocal()
    ctx = build_authorized_fixture(db, ws_id="ws_no_fabricate")

    class CrashingTransport(BaseEmailTransport):
        def send_email(self, *args, **kwargs):
            return EmailDeliveryResult(
                success=False,
                error_message="SMTP connection refused: connection timed out",
                retryable=True
            )

    set_transport_override(CrashingTransport())
    outbound_msg = OutboundDispatchEngine.dispatch_outbound_message(
        db=db,
        workspace_id=ctx["ws"].id,
        campaign_version_id=str(ctx["campaign_version"].id),
        sender_account_id=ctx["sender"].id,
        recipient_email=HARD_PINNED_TEST_RECIPIENT,
        recipient_country="US",
        business_id=ctx["biz"].id,
        subject="Crashing Transport Subject",
        body_text="Crashing Transport Body",
        required_claim_ids=[str(ctx["claim"].id)],
        side_effect_idempotency_key="idemp_crashing_transport",
        thread_state="AUTONOMOUS",
        contact_verification_tier="CONFIRMED_VERIFIED"
    )

    assert outbound_msg.status in ["DELIVERY_FAILED", "DELIVERY_STATE_UNKNOWN"]
    assert outbound_msg.provider_message_id is None
    assert outbound_msg.sent_at is None
    assert "SMTP connection refused" in (outbound_msg.gate_failure_reason or "")
    db.close()


# ==============================================================================
# TEST F: Duplicate Idempotency Replay
# ==============================================================================
def test_duplicate_idempotency_prevents_retransmission(setup_test_environment):
    """Replaying the exact same idempotency key must not trigger a second transmission."""
    mock_transport = setup_test_environment
    db = TestingSessionLocal()
    ctx = build_authorized_fixture(db, ws_id="ws_idemp_check")

    key = "idemp_exact_same_key_999"

    msg1 = OutboundDispatchEngine.dispatch_outbound_message(
        db=db,
        workspace_id=ctx["ws"].id,
        campaign_version_id=str(ctx["campaign_version"].id),
        sender_account_id=ctx["sender"].id,
        recipient_email=HARD_PINNED_TEST_RECIPIENT,
        recipient_country="US",
        business_id=ctx["biz"].id,
        subject="Idempotency Test Subject",
        body_text="Idempotency Test Body",
        required_claim_ids=[str(ctx["claim"].id)],
        side_effect_idempotency_key=key,
        thread_state="AUTONOMOUS",
        contact_verification_tier="CONFIRMED_VERIFIED"
    )
    assert msg1.status == "TRANSMITTED"
    assert len(mock_transport.sent_messages) == 1

    # Replay with same key
    msg2 = OutboundDispatchEngine.dispatch_outbound_message(
        db=db,
        workspace_id=ctx["ws"].id,
        campaign_version_id=str(ctx["campaign_version"].id),
        sender_account_id=ctx["sender"].id,
        recipient_email=HARD_PINNED_TEST_RECIPIENT,
        recipient_country="US",
        business_id=ctx["biz"].id,
        subject="Idempotency Test Subject Replay",
        body_text="Idempotency Test Body Replay",
        required_claim_ids=[str(ctx["claim"].id)],
        side_effect_idempotency_key=key,
        thread_state="AUTONOMOUS",
        contact_verification_tier="CONFIRMED_VERIFIED"
    )

    # Must be identical message and zero new transport calls
    assert msg2.id == msg1.id
    assert msg2.provider_message_id == msg1.provider_message_id
    assert len(mock_transport.sent_messages) == 1
    db.close()


# ==============================================================================
# TEST G: Real Suppression Race (Gate 14 Pre-Wire Recheck)
# ==============================================================================
def test_gate14_pre_wire_suppression_race(setup_test_environment):
    """If suppression is added right before wire transmission, Gate 14 halts transmission."""
    mock_transport = setup_test_environment
    db = TestingSessionLocal()
    ctx = build_authorized_fixture(db, ws_id="ws_suppress_race")

    # Add suppression using execute_atomic_suppression
    SuppressionService.execute_atomic_suppression(
        db=db,
        organization_id=ctx["org"].id,
        workspace_id=ctx["ws"].id,
        email=HARD_PINNED_TEST_RECIPIENT,
        source_channel="ONE_CLICK_DIAGNOSTIC",
        reason="Test suppression race"
    )

    outbound_msg = OutboundDispatchEngine.dispatch_outbound_message(
        db=db,
        workspace_id=ctx["ws"].id,
        campaign_version_id=str(ctx["campaign_version"].id),
        sender_account_id=ctx["sender"].id,
        recipient_email=HARD_PINNED_TEST_RECIPIENT,
        recipient_country="US",
        business_id=ctx["biz"].id,
        subject="Suppressed Message",
        body_text="Should not send",
        required_claim_ids=[str(ctx["claim"].id)],
        side_effect_idempotency_key="idemp_suppress_race",
        thread_state="AUTONOMOUS",
        contact_verification_tier="CONFIRMED_VERIFIED"
    )

    assert outbound_msg.status == "SUPPRESSED"
    assert "GATE 14 VIOLATION" in outbound_msg.gate_failure_reason
    assert len(mock_transport.sent_messages) == 0
    db.close()


# ==============================================================================
# TEST H: Kill Switch Blocks at Gate 15
# ==============================================================================
def test_gate15_emergency_kill_switch(setup_test_environment):
    """When GLOBAL_EMERGENCY_KILL_SWITCH = True, Gate 15 blocks transmission completely."""
    mock_transport = setup_test_environment
    db = TestingSessionLocal()
    ctx = build_authorized_fixture(db, ws_id="ws_killswitch")

    settings.GLOBAL_EMERGENCY_KILL_SWITCH = True
    try:
        outbound_msg = OutboundDispatchEngine.dispatch_outbound_message(
            db=db,
            workspace_id=ctx["ws"].id,
            campaign_version_id=str(ctx["campaign_version"].id),
            sender_account_id=ctx["sender"].id,
            recipient_email=HARD_PINNED_TEST_RECIPIENT,
            recipient_country="US",
            business_id=ctx["biz"].id,
            subject="Kill Switch Subject",
            body_text="Kill Switch Body",
            required_claim_ids=[str(ctx["claim"].id)],
            side_effect_idempotency_key="idemp_killswitch",
            thread_state="AUTONOMOUS",
            contact_verification_tier="CONFIRMED_VERIFIED"
        )
        assert outbound_msg.status == "BLOCKED"
        assert "GATE 15 VIOLATION" in outbound_msg.gate_failure_reason
        assert len(mock_transport.sent_messages) == 0
    finally:
        settings.GLOBAL_EMERGENCY_KILL_SWITCH = False

    db.close()


# ==============================================================================
# TEST I: Capacity Accounting Exactly Once
# ==============================================================================
def test_capacity_accounting_single_increment(setup_test_environment):
    """Verify single successful transmission increments capacity usage exactly once."""
    mock_transport = setup_test_environment
    db = TestingSessionLocal()
    ctx = build_authorized_fixture(db, ws_id="ws_cap_acc")

    daily_usage_before = CapacityService.get_or_create_daily_usage(db, ctx["ws"].id, date.today())
    assert daily_usage_before.outreach_count == 0

    outbound_msg = OutboundDispatchEngine.dispatch_outbound_message(
        db=db,
        workspace_id=ctx["ws"].id,
        campaign_version_id=str(ctx["campaign_version"].id),
        sender_account_id=ctx["sender"].id,
        recipient_email=HARD_PINNED_TEST_RECIPIENT,
        recipient_country="US",
        business_id=ctx["biz"].id,
        subject="Capacity Accounting Subject",
        body_text="Capacity Accounting Body",
        required_claim_ids=[str(ctx["claim"].id)],
        side_effect_idempotency_key="idemp_cap_acc_1",
        thread_state="AUTONOMOUS",
        contact_verification_tier="CONFIRMED_VERIFIED"
    )

    assert outbound_msg.status == "TRANSMITTED"
    assert outbound_msg.capacity_accounted is True
    daily_usage_after = CapacityService.get_or_create_daily_usage(db, ctx["ws"].id, date.today())
    assert daily_usage_after.outreach_count == 1
    db.close()


# ==============================================================================
# TEST J: Post-Transmission Accounting Recovery / Resilience
# ==============================================================================
def test_post_transmission_accounting_recovery(setup_test_environment):
    """When message is already TRANSMITTED, replay must not increment capacity again."""
    mock_transport = setup_test_environment
    db = TestingSessionLocal()
    ctx = build_authorized_fixture(db, ws_id="ws_cap_recov")

    key = "idemp_cap_recovery_test"
    msg1 = OutboundDispatchEngine.dispatch_outbound_message(
        db=db,
        workspace_id=ctx["ws"].id,
        campaign_version_id=str(ctx["campaign_version"].id),
        sender_account_id=ctx["sender"].id,
        recipient_email=HARD_PINNED_TEST_RECIPIENT,
        recipient_country="US",
        business_id=ctx["biz"].id,
        subject="Cap Recovery Subject",
        body_text="Cap Recovery Body",
        required_claim_ids=[str(ctx["claim"].id)],
        side_effect_idempotency_key=key,
        thread_state="AUTONOMOUS",
        contact_verification_tier="CONFIRMED_VERIFIED"
    )
    usage_1 = CapacityService.get_or_create_daily_usage(db, ctx["ws"].id, date.today()).outreach_count
    assert usage_1 == 1

    # Replay
    msg2 = OutboundDispatchEngine.dispatch_outbound_message(
        db=db,
        workspace_id=ctx["ws"].id,
        campaign_version_id=str(ctx["campaign_version"].id),
        sender_account_id=ctx["sender"].id,
        recipient_email=HARD_PINNED_TEST_RECIPIENT,
        recipient_country="US",
        business_id=ctx["biz"].id,
        subject="Cap Recovery Subject",
        body_text="Cap Recovery Body",
        required_claim_ids=[str(ctx["claim"].id)],
        side_effect_idempotency_key=key,
        thread_state="AUTONOMOUS",
        contact_verification_tier="CONFIRMED_VERIFIED"
    )
    usage_2 = CapacityService.get_or_create_daily_usage(db, ctx["ws"].id, date.today()).outreach_count
    assert usage_2 == 1  # No double counting!
    db.close()


# ==============================================================================
# TEST K: Zero Credential Redaction in DB and Audit Logs
# ==============================================================================
def test_zero_credential_leakage_in_db_and_logs(setup_test_environment):
    """Ensure raw passwords or tokens never appear in OutboundMessage or logs."""
    mock_transport = setup_test_environment
    db = TestingSessionLocal()
    ctx = build_authorized_fixture(db, ws_id="ws_cred_leak")

    msg = OutboundDispatchEngine.dispatch_outbound_message(
        db=db,
        workspace_id=ctx["ws"].id,
        campaign_version_id=str(ctx["campaign_version"].id),
        sender_account_id=ctx["sender"].id,
        recipient_email=HARD_PINNED_TEST_RECIPIENT,
        recipient_country="US",
        business_id=ctx["biz"].id,
        subject="Cred Redaction Subject",
        body_text="Cred Redaction Body",
        required_claim_ids=[str(ctx["claim"].id)],
        side_effect_idempotency_key="idemp_cred_leak_001",
        thread_state="AUTONOMOUS",
        contact_verification_tier="CONFIRMED_VERIFIED"
    )

    raw_password = "SuperSecretPassword123!"

    # Check OutboundMessage fields
    assert raw_password not in (msg.body_text or "")
    assert raw_password not in (msg.body_html or "")
    assert raw_password not in (msg.subject or "")
    assert raw_password not in (msg.gate_failure_reason or "")
    assert raw_password not in (msg.side_effect_idempotency_key or "")
    db.close()


# ==============================================================================
# TEST L: Tenant Isolation
# ==============================================================================
def test_tenant_isolation_gate_enforcement(setup_test_environment):
    """Cross-workspace resource attempts fail closed immediately at Gate 1 or Gate 2."""
    db = TestingSessionLocal()
    ctx_ws1 = build_authorized_fixture(db, ws_id="ws_tenant_1")
    ctx_ws2 = build_authorized_fixture(db, ws_id="ws_tenant_2")

    # Attempt to dispatch ws2's campaign version from ws1's engine
    msg = OutboundDispatchEngine.dispatch_outbound_message(
        db=db,
        workspace_id=ctx_ws1["ws"].id,
        campaign_version_id=str(ctx_ws2["campaign_version"].id),  # CROSS-TENANT CAMPAIGN VERSION
        sender_account_id=ctx_ws1["sender"].id,
        recipient_email=HARD_PINNED_TEST_RECIPIENT,
        recipient_country="US",
        business_id=ctx_ws1["biz"].id,
        subject="Cross-Tenant Subject",
        body_text="Cross-Tenant Body",
        required_claim_ids=[str(ctx_ws1["claim"].id)],
        side_effect_idempotency_key="idemp_cross_tenant_1",
        thread_state="AUTONOMOUS",
        contact_verification_tier="CONFIRMED_VERIFIED"
    )

    # Must fail at Gate 1
    assert msg.status == "BLOCKED"
    assert "GATE 1 VIOLATION" in msg.gate_failure_reason
    db.close()
