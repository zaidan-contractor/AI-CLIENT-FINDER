import pytest
from datetime import date, timedelta
from decimal import Decimal
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.core.database import Base, get_db
from app.core.security import get_password_hash, create_access_token, decrypt_credential
from app.models.tenancy import Organization, Workspace, User, Membership, SystemAuditEvent
from app.models.campaign import Campaign
from app.models.outreach import SenderAccount, SenderDailyMetric, WorkspaceDailyCost
from app.services.sender_readiness import SenderReadinessService
from app.services.policy_enforcer import FourTierPolicyEnforcer
from app.services.cost_guardrail import CostGuardrailService
from app.services.jurisdiction_engine import JurisdictionEngine

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
    app.dependency_overrides[get_db] = override_get_db
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)
    app.dependency_overrides.pop(get_db, None)

@pytest.fixture
def setup_env():
    db = TestingSessionLocal()
    org = Organization(id="org_p", name="Policy Org", slug="policy-org", is_active=True)
    ws = Workspace(id="ws_p", organization_id="org_p", name="Policy WS", slug="policy-ws", is_active=True)
    user = User(
        id="u_admin",
        email="admin@policy.test",
        hashed_password=get_password_hash("Secret123!"),
        full_name="Admin"
    )
    db.add_all([org, ws, user])
    db.commit()

    m = Membership(id="m_admin", user_id="u_admin", organization_id="org_p", workspace_id="ws_p", role="WorkspaceAdmin")
    camp = Campaign(id="camp_p", workspace_id="ws_p", name="Policy Campaign", status="ACTIVE")
    db.add_all([m, camp])
    db.commit()
    db.close()
    return {"org_id": "org_p", "workspace_id": "ws_p", "user_id": "u_admin", "campaign_id": "camp_p"}

def test_sender_readiness_and_gate_7():
    # 1. Connected but NOT ready (DNS and SPF invalid)
    sender_unready = SenderAccount(
        id="s_unready",
        workspace_id="ws_test",
        email="sales@brand.com",
        display_name="Sales",
        provider_type="SMTP",
        encrypted_credentials="enc",
        connection_verified=True,
        dns_valid=False,  # Failed
        spf_valid=False,  # Failed
        dkim_valid=True,
        dmarc_valid=True,
        tracking_domain_aligned=True,
        delivery_health_ok=True,
        status="ACTIVE"
    )
    eval_unready = SenderReadinessService.evaluate_account(sender_unready)
    assert eval_unready["readiness_state"] == "CONNECTED_NOT_READY"
    assert eval_unready["is_ready_for_outreach"] is False
    assert "d1_dns_records" in eval_unready["failed_dimensions"]
    assert "d2_spf_validity" in eval_unready["failed_dimensions"]

    ok, err = SenderReadinessService.verify_gate_7(sender_unready)
    assert ok is False
    assert "GATE 7 VIOLATION" in err

    # 2. Ready for outreach (All 8 dimensions pass)
    sender_ready = SenderAccount(
        id="s_ready",
        workspace_id="ws_test",
        email="outreach@verified.com",
        display_name="Outreach",
        provider_type="RESEND",
        encrypted_credentials="enc",
        connection_verified=True,
        dns_valid=True,
        spf_valid=True,
        dkim_valid=True,
        dmarc_valid=True,
        tracking_domain_aligned=True,
        delivery_health_ok=True,
        bounce_rate_pct=1.2,
        spam_complaint_pct=0.01,
        status="WARMING_UP"
    )
    eval_ready = SenderReadinessService.evaluate_account(sender_ready)
    assert eval_ready["readiness_state"] == "READY_FOR_OUTREACH"
    assert eval_ready["is_ready_for_outreach"] is True
    ok, err = SenderReadinessService.verify_gate_7(sender_ready)
    assert ok is True
    assert err is None

def test_four_tier_policy_enforcement(setup_env):
    db = TestingSessionLocal()
    org_id = setup_env["org_id"]
    ws_id = setup_env["workspace_id"]
    camp_id = setup_env["campaign_id"]

    sender = SenderAccount(
        id="s_tier",
        workspace_id=ws_id,
        email="sender@tier.test",
        display_name="Tier Sender",
        provider_type="SMTP",
        encrypted_credentials="enc",
        daily_quota=100,
        warmup_day=3,
        warmup_max_daily=15,  # Warmup cap
        status="WARMING_UP"
    )
    db.add(sender)
    db.commit()

    # Successful evaluation: most restrictive remaining is sender warmup limit (15)
    ok, err, details = FourTierPolicyEnforcer.evaluate_gates_8_to_11(db, org_id, ws_id, camp_id, sender.id)
    assert ok is True
    assert err is None
    assert details["most_restrictive_remaining"] == 15

    # Simulate sending 15 emails today -> quota exhausted
    metric = SenderDailyMetric(
        workspace_id=ws_id,
        sender_account_id=sender.id,
        metric_date=date.today(),
        sent_count=15
    )
    db.add(metric)
    db.commit()

    # Gate 11 Violation: Sender daily quota exceeded
    ok, err, _ = FourTierPolicyEnforcer.evaluate_gates_8_to_11(db, org_id, ws_id, camp_id, sender.id)
    assert ok is False
    assert "GATE 11 VIOLATION" in err
    db.close()

def test_cost_guardrail_cold_start_and_anomaly_detection(setup_env):
    db = TestingSessionLocal()
    ws_id = setup_env["workspace_id"]
    camp_id = setup_env["campaign_id"]
    today = date.today()

    # 1. Cold-Start scenario: Only 3 completed operating days (< 7)
    for i in range(1, 4):
        db.add(WorkspaceDailyCost(
            workspace_id=ws_id,
            cost_date=today - timedelta(days=i),
            amount=Decimal("12.0000"),
            currency="USD",
            is_completed_day=True
        ))
    db.commit()

    ok, err, details = CostGuardrailService.evaluate_gate_13(
        db, ws_id, camp_id, projected_daily_burn=Decimal("25.00"), as_of_date=today
    )
    assert ok is True
    assert details["status"] == "COLD_START_ACTIVE"
    assert details["historical_anomaly_detector_active"] is False

    # 2. Add 7 more completed days (total 10 completed days >= 7) with normal spend around $10
    for i in range(4, 11):
        db.add(WorkspaceDailyCost(
            workspace_id=ws_id,
            cost_date=today - timedelta(days=i),
            amount=Decimal("10.0000"),
            currency="USD",
            is_completed_day=True
        ))
    db.commit()

    # Normal projected burn ($15) within 3x median ($10 * 3 = $30) -> PASS
    ok, err, details = CostGuardrailService.evaluate_gate_13(
        db, ws_id, camp_id, projected_daily_burn=Decimal("15.00"), as_of_date=today
    )
    assert ok is True
    assert details["status"] == "APPROVED"

    # Sudden anomaly spike ($45 > 3x median $30) -> FAIL Gate 13, triggers HUMAN_REVIEW_REQUIRED
    ok, err, details = CostGuardrailService.evaluate_gate_13(
        db, ws_id, camp_id, projected_daily_burn=Decimal("45.00"), as_of_date=today
    )
    assert ok is False
    assert "GATE 13 VIOLATION: Historical burn-rate anomaly detected" in err
    assert details["status"] == "HUMAN_REVIEW_REQUIRED"

    # Verify immutable audit event was committed
    audit_evt = db.query(SystemAuditEvent).filter(SystemAuditEvent.event_type == "ANOMALY_HALT").first()
    assert audit_evt is not None
    assert audit_evt.payload["action_taken"] == "HUMAN_REVIEW_REQUIRED"
    db.close()

def test_jurisdiction_engine_gate_12():
    db = TestingSessionLocal()
    # 1. US -> ALLOWED (CAN-SPAM)
    ok, err, details = JurisdictionEngine.evaluate_gate_12(db, "US")
    assert ok is True
    assert details["decision"] == "ALLOWED"

    # 2. CA -> HUMAN_REVIEW_REQUIRED (CASL strict consent)
    ok, err, details = JurisdictionEngine.evaluate_gate_12(db, "CA")
    assert ok is False
    assert "GATE 12 VIOLATION" in err
    assert details["decision"] == "HUMAN_REVIEW_REQUIRED"

    # 3. Unknown / Missing jurisdiction -> Invariant: UNKNOWN MUST NOT silently become ALLOWED
    ok, err, details = JurisdictionEngine.evaluate_gate_12(db, "")
    assert ok is False
    assert "UNKNOWN" in err

    # 4. Unregistered Country -> HUMAN_REVIEW_REQUIRED
    ok, err, details = JurisdictionEngine.evaluate_gate_12(db, "XX")
    assert ok is False
    assert details["decision"] == "HUMAN_REVIEW_REQUIRED"
    db.close()

def test_sender_api_with_encryption_and_rbac(setup_env):
    token = create_access_token(
        user_id=setup_env["user_id"],
        organization_id=setup_env["org_id"],
        active_workspace_id=setup_env["workspace_id"],
        role="WorkspaceAdmin"
    )
    headers = {"Authorization": f"Bearer {token}"}

    # Register Sender via API
    res = client.post("/api/v1/senders", json={
        "email": "outreach-api@company.com",
        "display_name": "API Outreach",
        "provider_type": "SMTP",
        "credentials_secret": "my-smtp-super-secret-password-123",
        "daily_quota": 60,
        "warmup_max_daily": 25
    }, headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["email"] == "outreach-api@company.com"
    assert data["readiness_state"] == "CONNECTED_NOT_READY"
    assert data["is_ready_for_outreach"] is False

    # Verify database record: Credentials MUST be envelope encrypted (not plaintext)
    db = TestingSessionLocal()
    sender_db = db.query(SenderAccount).filter(SenderAccount.id == data["id"]).first()
    assert sender_db.encrypted_credentials != "my-smtp-super-secret-password-123"
    # Decrypt to verify round-trip
    decrypted = decrypt_credential(sender_db.encrypted_credentials)
    assert decrypted == "my-smtp-super-secret-password-123"
    db.close()

    # Get readiness via API - Sender is CONNECTED_NOT_READY until DNS verified
    res = client.get(f"/api/v1/senders/{data['id']}/readiness", headers=headers)
    assert res.status_code == 200
    readiness_data = res.json()
    assert readiness_data["is_ready_for_outreach"] is False
    assert readiness_data["readiness_state"] == "CONNECTED_NOT_READY"
    assert "disclaimer" in readiness_data
