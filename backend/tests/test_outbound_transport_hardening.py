import pytest
import json
from decimal import Decimal
from unittest.mock import MagicMock, patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import Base
from app.core.security import encrypt_credential, decrypt_credential
from app.models.tenancy import Organization, Workspace, User, Membership, OrgSuppressionRecord, get_utc_now
from app.models.business import MasterBusinessRecord, ContactRecord
from app.models.research import EvidenceRecord
from app.models.opportunity import ProblemHypothesis, ImpactAssessment, SolutionMapping
from app.models.campaign import Campaign, CampaignVersion, ApprovedSalesClaimRecord
from app.models.outreach import SenderAccount, OutboundMessage
from app.services.claim_governance import ClaimGovernanceService
from app.services.suppression_service import SuppressionService
from app.services.outbound_dispatch_engine import OutboundDispatchEngine
from app.services.email_transports.base import EmailDeliveryResult
from app.services.email_transports.factory import get_email_transport, set_transport_override
from app.services.email_transports.smtp_transport import SMTPEmailTransport
from app.services.email_transports.mock_transport import MockEmailTransport

# In-memory SQLite DB for isolated unit tests
engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

@pytest.fixture(autouse=True)
def init_test_db():
    Base.metadata.create_all(bind=engine)
    set_transport_override(None)
    orig_env = settings.ENVIRONMENT
    orig_host = settings.SMTP_HOST
    orig_pwd = settings.SMTP_PASSWORD
    orig_user = settings.SMTP_USERNAME
    orig_port = settings.SMTP_PORT
    yield
    set_transport_override(None)
    settings.ENVIRONMENT = orig_env
    settings.SMTP_HOST = orig_host
    settings.SMTP_PASSWORD = orig_pwd
    settings.SMTP_USERNAME = orig_user
    settings.SMTP_PORT = orig_port
    Base.metadata.drop_all(bind=engine)

@pytest.fixture
def base_context():
    db = TestingSessionLocal()
    org = Organization(id="org_h1", name="Hardening Org", slug="hardening-org")
    ws = Workspace(id="ws_h1", organization_id=org.id, name="Hardening WS", slug="hardening-ws")
    user = User(id="u_h1", email="admin@hardening.test", hashed_password="pw")
    db.add_all([org, ws, user])
    db.flush()

    mem = Membership(id="m_h1", organization_id=org.id, workspace_id=ws.id, user_id=user.id, role="OrgAdmin")
    biz = MasterBusinessRecord(
        id="biz_h1",
        workspace_id=ws.id,
        name="Target Health",
        normalized_name="target health",
        category="HEALTHCARE",
        source_provider="LOCAL_DIRECTORY",
        canonical_domain="targethealth.test",
        website_url="https://targethealth.test",
        primary_email="contact@targethealth.test",
        phone="555-1234",
        raw_metadata={"reviews": [{"rating": 1}, {"rating": 2}]},
        country="US"
    )
    cnt = ContactRecord(
        id="cnt_h1",
        workspace_id=ws.id,
        business_id=biz.id,
        email="contact@targethealth.test",
        canonical_email="contact@targethealth.test",
        verification_tier="CONFIRMED_VERIFIED",
        freshness_timestamp=get_utc_now()
    )
    ev = EvidenceRecord(
        id="ev_h1",
        workspace_id=ws.id,
        business_id=biz.id,
        signal_type="DOM_ELEMENT",
        raw_observation="No online intake",
        feature_presence_state="NOT_OBSERVED",
        classification_reason="Confirmed absence",
        confidence=0.9
    )
    ev2 = EvidenceRecord(
        id="ev_h2",
        workspace_id=ws.id,
        business_id=biz.id,
        signal_type="REVIEW_SENTIMENT",
        raw_observation="Patients complain about intake bottlenecks",
        feature_presence_state="PRESENT",
        classification_reason="Review sentiment",
        confidence=0.9
    )
    hyp = ProblemHypothesis(
        id="hyp_h1",
        workspace_id=ws.id,
        business_id=biz.id,
        category="MANUAL_SCHEDULING_BOTTLENECK",
        title="Intake Bottleneck",
        description="Patients experience intake delays.",
        status="VALIDATED",
        confidence=0.9,
        supporting_evidence_ids=["ev_h1", "ev_h2"]
    )
    hyp2 = ProblemHypothesis(
        id="hyp_h2",
        workspace_id=ws.id,
        business_id=biz.id,
        category="AFTER_HOURS_CALL_DEFICIT",
        title="After Hours Deficit",
        description="Calls are missed after business hours.",
        status="VALIDATED",
        confidence=0.9,
        supporting_evidence_ids=["ev_h1", "ev_h2"]
    )
    imp = ImpactAssessment(
        id="imp_h1",
        workspace_id=ws.id,
        hypothesis_id=hyp.id,
        estimated_impact_low=Decimal("10000.00"),
        estimated_impact_high=Decimal("20000.00"),
        estimated_impact_midpoint=Decimal("15000.00"),
        impact_currency="USD"
    )
    imp2 = ImpactAssessment(
        id="imp_h2",
        workspace_id=ws.id,
        hypothesis_id=hyp2.id,
        estimated_impact_low=Decimal("10000.00"),
        estimated_impact_high=Decimal("20000.00"),
        estimated_impact_midpoint=Decimal("15000.00"),
        impact_currency="USD"
    )
    sol = SolutionMapping(
        id="sol_h1",
        workspace_id=ws.id,
        hypothesis_id=hyp.id,
        solution_type="AI_RECEPTIONIST",
        title="Automated Portal",
        capability_summary="Online intake",
        feasibility_score=0.9
    )
    sol2 = SolutionMapping(
        id="sol_h2",
        workspace_id=ws.id,
        hypothesis_id=hyp2.id,
        solution_type="AI_RECEPTIONIST",
        title="After Hours Receptionist",
        capability_summary="24/7 call triage",
        feasibility_score=0.9
    )
    db.add_all([mem, biz, cnt, ev, ev2, hyp, hyp2, imp, imp2, sol, sol2])
    db.commit()

    camp = ClaimGovernanceService.create_campaign(db, ws.id, "Hardening Campaign")
    ver = ClaimGovernanceService.create_campaign_version(db, ws.id, camp.id, [{"step": 1, "template": "test"}])
    claim = ClaimGovernanceService.create_claim(db, ws.id, ver.id, hyp.id, "Verified HIPAA compliance reduction.", ["ev_h1"])
    ClaimGovernanceService.approve_claim(db, ws.id, claim.id, user.id)
    ClaimGovernanceService.approve_campaign_version(db, ws.id, ver.id, user.id)

    ctx = {
        "org_id": org.id,
        "ws_id": ws.id,
        "user_id": user.id,
        "biz_id": biz.id,
        "cnt_email": cnt.email,
        "camp_id": camp.id,
        "cv_id": ver.id,
        "claim_id": claim.id
    }
    db.close()
    return ctx

def create_sender(db, ws_id, sender_id="snd_h1", provider_type="SMTP", creds="env_managed"):
    sender = SenderAccount(
        id=sender_id,
        workspace_id=ws_id,
        email="sender@outreach.test",
        display_name="Secure Outreach",
        provider_type=provider_type,
        encrypted_credentials=creds,
        daily_quota=100,
        dns_valid=True,
        spf_valid=True,
        dkim_valid=True,
        dmarc_valid=True,
        tracking_domain_aligned=True,
        connection_verified=True,
        delivery_health_ok=True,
        bounce_rate_pct=0.1,
        spam_complaint_pct=0.0,
        status="ACTIVE"
    )
    db.add(sender)
    db.commit()
    return sender

# -----------------------------------------------------------------------------
# 1. env_managed + valid SMTP env credentials -> REAL SMTP transport selected, sandbox false
# -----------------------------------------------------------------------------
def test_1_env_managed_with_valid_credentials_selects_real_smtp(base_context):
    db = TestingSessionLocal()
    ctx = base_context
    create_sender(db, ctx["ws_id"], sender_id="snd_1", creds="env_managed")

    settings.SMTP_HOST = "smtp.mockserver.test"
    settings.SMTP_PORT = 587
    settings.SMTP_USERNAME = "user@mockserver.test"
    settings.SMTP_PASSWORD = "mock_valid_password"
    settings.SMTP_USE_TLS = True

    with patch("smtplib.SMTP") as mock_smtp_cls:
        mock_server = MagicMock()
        mock_smtp_cls.return_value.__enter__.return_value = mock_server
        mock_server.sendmail.return_value = {}  # No refused recipients

        msg = OutboundDispatchEngine.dispatch_outbound_message(
            db=db,
            workspace_id=ctx["ws_id"],
            campaign_version_id=ctx["cv_id"],
            sender_account_id="snd_1",
            recipient_email=ctx["cnt_email"],
            recipient_country="US",
            business_id=ctx["biz_id"],
            subject="Test 1 Subject",
            body_text="Test 1 Body",
            body_html=None,
            required_claim_ids=[ctx["claim_id"]],
            side_effect_idempotency_key="idemp_test_1"
        )

        assert msg.status == "TRANSMITTED"
        assert msg.transport_mode == "REAL"
        assert msg.provider_message_id is not None
        # Verify smtplib was actually called
        mock_smtp_cls.assert_called_once_with("smtp.mockserver.test", 587, timeout=10.0)
        mock_server.starttls.assert_called_once()
        mock_server.login.assert_called_once_with("user@mockserver.test", "mock_valid_password")
        mock_server.sendmail.assert_called_once()
    db.close()

# -----------------------------------------------------------------------------
# 2. env_managed + missing SMTP password -> FAIL CLOSED, no TRANSMITTED status
# -----------------------------------------------------------------------------
def test_2_env_managed_with_missing_password_fails_closed(base_context):
    db = TestingSessionLocal()
    ctx = base_context
    create_sender(db, ctx["ws_id"], sender_id="snd_2", creds="env_managed")

    settings.SMTP_HOST = "smtp.mockserver.test"
    settings.SMTP_PASSWORD = None  # Missing password

    msg = OutboundDispatchEngine.dispatch_outbound_message(
        db=db,
        workspace_id=ctx["ws_id"],
        campaign_version_id=ctx["cv_id"],
        sender_account_id="snd_2",
        recipient_email=ctx["cnt_email"],
        recipient_country="US",
        business_id=ctx["biz_id"],
        subject="Test 2 Subject",
        body_text="Test 2 Body",
        body_html=None,
        required_claim_ids=[ctx["claim_id"]],
        side_effect_idempotency_key="idemp_test_2"
    )

    assert msg.status == "BLOCKED"
    assert msg.status != "TRANSMITTED"
    assert "REAL_TRANSPORT_NOT_CONFIGURED" in (msg.gate_failure_reason or "")
    db.close()

# -----------------------------------------------------------------------------
# 3. arbitrary non-v1 credential string -> MUST NOT automatically imply sandbox
# -----------------------------------------------------------------------------
def test_3_arbitrary_non_v1_credential_does_not_imply_sandbox(base_context):
    db = TestingSessionLocal()
    ctx = base_context
    create_sender(db, ctx["ws_id"], sender_id="snd_3", creds="plain_unencrypted_token_12345")

    # In development mode, arbitrary non-v1 string is loaded as password, NOT as sandbox=True
    settings.ENVIRONMENT = "development"
    settings.SMTP_HOST = "smtp.devhost.test"

    with patch("smtplib.SMTP") as mock_smtp_cls:
        mock_server = MagicMock()
        mock_smtp_cls.return_value.__enter__.return_value = mock_server
        mock_server.sendmail.return_value = {}

        msg = OutboundDispatchEngine.dispatch_outbound_message(
            db=db,
            workspace_id=ctx["ws_id"],
            campaign_version_id=ctx["cv_id"],
            sender_account_id="snd_3",
            recipient_email=ctx["cnt_email"],
            recipient_country="US",
            business_id=ctx["biz_id"],
            subject="Test 3 Subject",
            body_text="Test 3 Body",
            body_html=None,
            required_claim_ids=[ctx["claim_id"]],
            side_effect_idempotency_key="idemp_test_3"
        )
        print("MSG RESULT:", msg.status, msg.gate_passed_count, msg.gate_failure_reason)
        assert msg.status == "TRANSMITTED"
        assert msg.transport_mode == "REAL"
        mock_server.sendmail.assert_called_once()

    # In production mode, arbitrary unencrypted strings MUST fail closed
    settings.ENVIRONMENT = "production"
    msg_prod = OutboundDispatchEngine.dispatch_outbound_message(
        db=db,
        workspace_id=ctx["ws_id"],
        campaign_version_id=ctx["cv_id"],
        sender_account_id="snd_3",
        recipient_email=ctx["cnt_email"],
        recipient_country="US",
        business_id=ctx["biz_id"],
        subject="Test 3 Prod Subject",
        body_text="Test 3 Prod Body",
        body_html=None,
        required_claim_ids=[ctx["claim_id"]],
        side_effect_idempotency_key="idemp_test_3_prod"
    )
    assert msg_prod.status == "BLOCKED"
    assert "SECURITY_VIOLATION" in msg_prod.gate_failure_reason
    db.close()

# -----------------------------------------------------------------------------
# 4. explicit mock/test configuration -> mock transport allowed
# -----------------------------------------------------------------------------
def test_4_explicit_mock_transport_allowed_in_dev_test(base_context):
    db = TestingSessionLocal()
    ctx = base_context
    create_sender(db, ctx["ws_id"], sender_id="snd_4", provider_type="MOCK", creds="env_managed")

    settings.ENVIRONMENT = "development"

    msg = OutboundDispatchEngine.dispatch_outbound_message(
        db=db,
        workspace_id=ctx["ws_id"],
        campaign_version_id=ctx["cv_id"],
        sender_account_id="snd_4",
        recipient_email=ctx["cnt_email"],
        recipient_country="US",
        business_id=ctx["biz_id"],
        subject="Test 4 Subject",
        body_text="Test 4 Body",
        body_html=None,
        required_claim_ids=[ctx["claim_id"]],
        side_effect_idempotency_key="idemp_test_4"
    )

    assert msg.status == "TRANSMITTED"
    assert msg.transport_mode == "MOCK"
    assert msg.provider_message_id.startswith("msg_mock_")
    db.close()

# -----------------------------------------------------------------------------
# 5. production mode + mock transport -> reject configuration
# -----------------------------------------------------------------------------
def test_5_production_mode_rejects_mock_transport(base_context):
    db = TestingSessionLocal()
    ctx = base_context
    create_sender(db, ctx["ws_id"], sender_id="snd_5", provider_type="MOCK", creds="env_managed")

    settings.ENVIRONMENT = "production"

    # Factory must refuse to return mock transport in production
    with pytest.raises(ValueError, match="strictly forbidden in production"):
        get_email_transport("MOCK")

    # Override with mock in production must be refused
    with pytest.raises(ValueError, match="strictly forbidden in production"):
        set_transport_override(MockEmailTransport())

    # Dispatch engine must fail closed in production with mock provider
    msg = OutboundDispatchEngine.dispatch_outbound_message(
        db=db,
        workspace_id=ctx["ws_id"],
        campaign_version_id=ctx["cv_id"],
        sender_account_id="snd_5",
        recipient_email=ctx["cnt_email"],
        recipient_country="US",
        business_id=ctx["biz_id"],
        subject="Test 5 Subject",
        body_text="Test 5 Body",
        body_html=None,
        required_claim_ids=[ctx["claim_id"]],
        side_effect_idempotency_key="idemp_test_5"
    )

    assert msg.status == "BLOCKED"
    assert "REAL_TRANSPORT_NOT_CONFIGURED" in msg.gate_failure_reason
    db.close()

# -----------------------------------------------------------------------------
# 6. simulated provider success -> MUST NOT create TRANSMITTED production record
# -----------------------------------------------------------------------------
def test_6_simulated_success_cannot_mark_transmitted_in_production(base_context):
    db = TestingSessionLocal()
    ctx = base_context
    create_sender(db, ctx["ws_id"], sender_id="snd_6", provider_type="SMTP", creds="env_managed")

    settings.ENVIRONMENT = "production"
    settings.SMTP_HOST = "smtp.production.test"
    settings.SMTP_PASSWORD = "production_password"

    # Suppose a fake transport returns success=True with transport_mode="MOCK"
    mock_fake = MagicMock()
    mock_fake.send_email.return_value = EmailDeliveryResult(
        success=True,
        provider_message_id="fake_simulated_id",
        transport_mode="MOCK",
        details={"transport": "SMTP_SANDBOX"}
    )

    with patch("app.services.email_transports.factory.get_email_transport", return_value=mock_fake):
        msg = OutboundDispatchEngine.dispatch_outbound_message(
            db=db,
            workspace_id=ctx["ws_id"],
            campaign_version_id=ctx["cv_id"],
            sender_account_id="snd_6",
            recipient_email=ctx["cnt_email"],
            recipient_country="US",
            business_id=ctx["biz_id"],
            subject="Test 6 Subject",
            body_text="Test 6 Body",
            body_html=None,
            required_claim_ids=[ctx["claim_id"]],
            side_effect_idempotency_key="idemp_test_6"
        )

        assert msg.status == "BLOCKED"
        assert msg.status != "TRANSMITTED"
        assert "SECURITY_VIOLATION" in msg.gate_failure_reason
    db.close()

# -----------------------------------------------------------------------------
# 7. real SMTP transport success -> TRANSMITTED only after actual provider call
# -----------------------------------------------------------------------------
def test_7_real_smtp_success_marks_transmitted(base_context):
    db = TestingSessionLocal()
    ctx = base_context
    create_sender(db, ctx["ws_id"], sender_id="snd_7", provider_type="SMTP", creds="env_managed")

    settings.ENVIRONMENT = "production"
    settings.SMTP_HOST = "smtp.realprovider.test"
    settings.SMTP_PORT = 587
    settings.SMTP_USERNAME = "user@realprovider.test"
    settings.SMTP_PASSWORD = "real_password"
    settings.SMTP_USE_TLS = True

    with patch("smtplib.SMTP") as mock_smtp:
        server = MagicMock()
        mock_smtp.return_value.__enter__.return_value = server
        server.sendmail.return_value = {}

        msg = OutboundDispatchEngine.dispatch_outbound_message(
            db=db,
            workspace_id=ctx["ws_id"],
            campaign_version_id=ctx["cv_id"],
            sender_account_id="snd_7",
            recipient_email=ctx["cnt_email"],
            recipient_country="US",
            business_id=ctx["biz_id"],
            subject="Test 7 Subject",
            body_text="Test 7 Body",
            body_html=None,
            required_claim_ids=[ctx["claim_id"]],
            side_effect_idempotency_key="idemp_test_7"
        )

        assert msg.status == "TRANSMITTED"
        assert msg.transport_mode == "REAL"
        assert msg.provider_message_id is not None
        assert msg.sent_at is not None
        server.login.assert_called_once_with("user@realprovider.test", "real_password")
        server.sendmail.assert_called_once()
    db.close()

# -----------------------------------------------------------------------------
# 8. real SMTP failure -> failure/retry state, never TRANSMITTED
# -----------------------------------------------------------------------------
def test_8_real_smtp_failure_marks_delivery_failed_never_transmitted(base_context):
    import smtplib
    db = TestingSessionLocal()
    ctx = base_context
    create_sender(db, ctx["ws_id"], sender_id="snd_8", provider_type="SMTP", creds="env_managed")

    settings.ENVIRONMENT = "production"
    settings.SMTP_HOST = "smtp.realprovider.test"
    settings.SMTP_PORT = 587
    settings.SMTP_USERNAME = "user@realprovider.test"
    settings.SMTP_PASSWORD = "real_password"

    with patch("smtplib.SMTP") as mock_smtp:
        server = MagicMock()
        mock_smtp.return_value.__enter__.return_value = server
        server.login.side_effect = smtplib.SMTPAuthenticationError(535, b"5.7.8 Username and Password not accepted")

        msg = OutboundDispatchEngine.dispatch_outbound_message(
            db=db,
            workspace_id=ctx["ws_id"],
            campaign_version_id=ctx["cv_id"],
            sender_account_id="snd_8",
            recipient_email=ctx["cnt_email"],
            recipient_country="US",
            business_id=ctx["biz_id"],
            subject="Test 8 Subject",
            body_text="Test 8 Body",
            body_html=None,
            required_claim_ids=[ctx["claim_id"]],
            side_effect_idempotency_key="idemp_test_8"
        )

        assert msg.status == "DELIVERY_FAILED"
        assert msg.status != "TRANSMITTED"
        assert msg.transport_mode == "REAL"
        assert "SMTP Auth Error" in msg.gate_failure_reason
    db.close()

# -----------------------------------------------------------------------------
# 9. idempotency replay -> no second provider transmission
# -----------------------------------------------------------------------------
def test_9_idempotency_replay_prevents_second_transmission(base_context):
    db = TestingSessionLocal()
    ctx = base_context
    create_sender(db, ctx["ws_id"], sender_id="snd_9", provider_type="SMTP", creds="env_managed")

    settings.SMTP_HOST = "smtp.realprovider.test"
    settings.SMTP_PASSWORD = "real_password"

    with patch("smtplib.SMTP") as mock_smtp:
        server = MagicMock()
        mock_smtp.return_value.__enter__.return_value = server
        server.sendmail.return_value = {}

        msg1 = OutboundDispatchEngine.dispatch_outbound_message(
            db=db,
            workspace_id=ctx["ws_id"],
            campaign_version_id=ctx["cv_id"],
            sender_account_id="snd_9",
            recipient_email=ctx["cnt_email"],
            recipient_country="US",
            business_id=ctx["biz_id"],
            subject="Test 9 Subject",
            body_text="Test 9 Body",
            body_html=None,
            required_claim_ids=[ctx["claim_id"]],
            side_effect_idempotency_key="idemp_test_9_replay"
        )
        assert msg1.status == "TRANSMITTED"
        assert server.sendmail.call_count == 1

        # Second dispatch with identical idempotency key
        msg2 = OutboundDispatchEngine.dispatch_outbound_message(
            db=db,
            workspace_id=ctx["ws_id"],
            campaign_version_id=ctx["cv_id"],
            sender_account_id="snd_9",
            recipient_email=ctx["cnt_email"],
            recipient_country="US",
            business_id=ctx["biz_id"],
            subject="Test 9 Subject",
            body_text="Test 9 Body",
            body_html=None,
            required_claim_ids=[ctx["claim_id"]],
            side_effect_idempotency_key="idemp_test_9_replay"
        )
        assert msg2.id == msg1.id
        assert msg2.status == "TRANSMITTED"
        # Crucial check: sendmail was NOT called a second time!
        assert server.sendmail.call_count == 1
    db.close()

# -----------------------------------------------------------------------------
# 10. Gate 14 suppression immediately before transmission -> still enforced
# -----------------------------------------------------------------------------
def test_10_gate_14_suppression_immediately_before_transmission(base_context):
    db = TestingSessionLocal()
    ctx = base_context
    create_sender(db, ctx["ws_id"], sender_id="snd_10", provider_type="SMTP", creds="env_managed")

    settings.SMTP_HOST = "smtp.realprovider.test"
    settings.SMTP_PASSWORD = "real_password"

    # Suppress recipient at org level
    supp = OrgSuppressionRecord(
        id="supp_h1",
        organization_id=ctx["org_id"],
        email_canonical=ctx["cnt_email"],
        reason="USER_OPT_OUT",
        source_channel="EMAIL"
    )
    db.add(supp)
    db.commit()

    with patch("smtplib.SMTP") as mock_smtp:
        server = MagicMock()
        mock_smtp.return_value.__enter__.return_value = server

        msg = OutboundDispatchEngine.dispatch_outbound_message(
            db=db,
            workspace_id=ctx["ws_id"],
            campaign_version_id=ctx["cv_id"],
            sender_account_id="snd_10",
            recipient_email=ctx["cnt_email"],
            recipient_country="US",
            business_id=ctx["biz_id"],
            subject="Test 10 Subject",
            body_text="Test 10 Body",
            body_html=None,
            required_claim_ids=[ctx["claim_id"]],
            side_effect_idempotency_key="idemp_test_10"
        )

        assert msg.status == "SUPPRESSED"
        assert msg.status != "TRANSMITTED"
        assert "GATE 14 VIOLATION" in msg.gate_failure_reason
        # Transport was never called
        server.sendmail.assert_not_called()
    db.close()
