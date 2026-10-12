import os
import pytest
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.core.database import Base, get_db
from app.core.config import settings
from app.core.security import get_password_hash, create_access_token, canonicalize_email
from app.models.tenancy import Organization, Workspace, User, Membership
from app.models.business import MasterBusinessRecord, ContactRecord
from app.models.campaign import Campaign, CampaignVersion, ApprovedSalesClaimRecord
from app.models.outreach import SenderAccount, OutboundMessage
from app.models.learning import RecommendationRecord
from app.models.capacity import CapacityPolicy
from app.models.inbound import InboundMessage
from app.services.llm.provider_status_tracker import ProviderStatusTracker, ProviderOperationalState
from app.services.llm.openai_provider import OpenAIProvider
from app.services.suppression_service import SuppressionService

# In-memory isolated test DB
SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def override_get_db():
    db = TestingSessionLocal()
    try:
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
def test_env():
    db = TestingSessionLocal()
    org = Organization(id="org_rem", name="Remediation Org", slug="rem-org", is_active=True)
    ws1 = Workspace(id="ws_rem_1", organization_id="org_rem", name="WS 1", slug="ws-1", is_active=True)
    ws2 = Workspace(id="ws_rem_2", organization_id="org_rem", name="WS 2", slug="ws-2", is_active=True)

    user1 = User(
        id="u_rem_1",
        email="operator1@rem.test",
        hashed_password=get_password_hash("Secret123!"),
        full_name="Operator One",
        is_active=True
    )
    user2 = User(
        id="u_rem_2",
        email="operator2@rem.test",
        hashed_password=get_password_hash("Secret123!"),
        full_name="Operator Two",
        is_active=True
    )
    m1 = Membership(id="m_rem_1", user_id="u_rem_1", organization_id="org_rem", workspace_id="ws_rem_1", role="WorkspaceAdmin")
    m2 = Membership(id="m_rem_2", user_id="u_rem_2", organization_id="org_rem", workspace_id="ws_rem_2", role="WorkspaceAdmin")

    # Campaign & Version in ws1
    camp1 = Campaign(id="camp_rem_1", workspace_id="ws_rem_1", name="Campaign 1", status="ACTIVE")
    ver1 = CampaignVersion(
        id="ver_rem_1",
        workspace_id="ws_rem_1",
        campaign_id="camp_rem_1",
        version_number=1,
        status="OPERATOR_APPROVED"
    )

    # Sender account in ws1
    sender1 = SenderAccount(
        id="s_rem_1",
        workspace_id="ws_rem_1",
        email="outreach@consultative.test",
        display_name="Consultative Outreach",
        provider_type="SMTP",
        encrypted_credentials="enc_test_key",
        status="ACTIVE",
        daily_quota=50,
        warmup_day=30,
        warmup_max_daily=100,
        dns_valid=True,
        spf_valid=True,
        dkim_valid=True,
        dmarc_valid=True,
        tracking_domain_aligned=True,
        connection_verified=True,
        delivery_health_ok=True,
        bounce_rate_pct=0.01,
        spam_complaint_pct=0.001
    )

    # Capacity policy in ws1
    cap1 = CapacityPolicy(
        id="cap_rem_1",
        workspace_id="ws_rem_1",
        discovery_limit_per_day=100,
        research_limit_per_day=100,
        llm_operations_limit_per_day=100,
        outreach_limit_per_day=100,
        sender_limit_per_day=100,
        campaign_limit_per_day=10,
        min_quality_floor="TIER_1_ADVANCED",
        is_active=True
    )

    db.add_all([org, ws1, ws2, user1, user2, m1, m2, camp1, ver1, sender1, cap1])
    db.commit()

    token1 = create_access_token(
        user_id="u_rem_1",
        organization_id="org_rem",
        active_workspace_id="ws_rem_1",
        role="WorkspaceAdmin"
    )
    token2 = create_access_token(
        user_id="u_rem_2",
        organization_id="org_rem",
        active_workspace_id="ws_rem_2",
        role="WorkspaceAdmin"
    )

    db.close()
    return {
        "headers_ws1": {"Authorization": f"Bearer {token1}"},
        "headers_ws2": {"Authorization": f"Bearer {token2}"},
        "ws1_id": "ws_rem_1",
        "ws2_id": "ws_rem_2",
        "ver1_id": "ver_rem_1",
        "sender1_id": "s_rem_1",
        "camp1_id": "camp_rem_1",
    }


# =============================================================================
# 1. LEARNING & ANALYTICS CANONICAL ROUTE & SCHEMA CONTRACT
# =============================================================================
def test_learning_canonical_route_and_schema(test_env):
    """Verifies GET /api/v1/learning/recommendations returns 200 with canonical schema keys."""
    db = TestingSessionLocal()
    rec = RecommendationRecord(
        id="rec_canon_1",
        workspace_id="ws_rem_1",
        campaign_id="camp_rem_1",
        recommendation_type="SEQUENCE_HOOK_OPTIMIZATION",
        headline="Shorten Subject Line to 4 Words",
        suggested_action="Adjust sequence opening hooks from aggressive diagnostic claims to consultative peer inquiries.",
        expected_benefit="Projected 18% improvement in reply rate based on industry benchmark.",
        status="PROPOSED"
    )
    db.add(rec)
    db.commit()
    db.close()

    res = client.get("/api/v1/learning/recommendations", headers=test_env["headers_ws1"])
    assert res.status_code == 200
    data = res.json()
    assert len(data) >= 1

    item = data[0]
    # Exact required canonical vocabulary:
    assert item["id"] == "rec_canon_1"
    assert item["headline"] == "Shorten Subject Line to 4 Words"
    assert item["suggested_action"] == "Adjust sequence opening hooks from aggressive diagnostic claims to consultative peer inquiries."
    assert item["rationale"] == "Projected 18% improvement in reply rate based on industry benchmark."
    assert item["status"] == "PROPOSED"


def test_learning_action_endpoint_accept_and_dismiss(test_env):
    """Verifies POST /api/v1/learning/recommendations/{id}/action supports ACCEPTED and DISMISSED."""
    db = TestingSessionLocal()
    rec1 = RecommendationRecord(
        id="rec_act_1",
        workspace_id="ws_rem_1",
        recommendation_type="TIMING",
        headline="Send mid-morning",
        suggested_action="Schedule dispatch between 9:00-11:00 AM local time.",
        expected_benefit="Improved open rate",
        status="PROPOSED"
    )
    rec2 = RecommendationRecord(
        id="rec_act_2",
        workspace_id="ws_rem_1",
        recommendation_type="CHANNEL",
        headline="Include phone inquiry",
        suggested_action="Follow up via phone",
        expected_benefit="Higher conversion",
        status="PROPOSED"
    )
    db.add_all([rec1, rec2])
    db.commit()
    db.close()

    # 1. Accept recommendation
    res1 = client.post(
        "/api/v1/learning/recommendations/rec_act_1/action",
        json={"action": "ACCEPTED"},
        headers=test_env["headers_ws1"]
    )
    assert res1.status_code == 200
    assert res1.json()["status"] == "ACCEPTED"
    assert res1.json()["applied_by_user_id"] == "u_rem_1"

    # 2. Dismiss recommendation
    res2 = client.post(
        "/api/v1/learning/recommendations/rec_act_2/action",
        json={"action": "DISMISSED"},
        headers=test_env["headers_ws1"]
    )
    assert res2.status_code == 200
    assert res2.json()["status"] == "DISMISSED"

    # 3. Invalid action rejected
    res_bad = client.post(
        "/api/v1/learning/recommendations/rec_act_1/action",
        json={"action": "MUTATE_CAMPAIGN_SILENTLY"},
        headers=test_env["headers_ws1"]
    )
    assert res_bad.status_code == 422


def test_learning_cannot_mutate_campaigns(test_env):
    """Invariant: Learning remains strictly recommendation-only and CANNOT mutate campaigns."""
    db = TestingSessionLocal()
    rec = RecommendationRecord(
        id="rec_safe_1",
        workspace_id="ws_rem_1",
        campaign_id="camp_rem_1",
        recommendation_type="POLICY_ADJUSTMENT",
        headline="Proposed campaign update",
        suggested_action="Suggests changing campaign daily quota to 200",
        expected_benefit="Higher throughput",
        status="PROPOSED"
    )
    db.add(rec)
    db.commit()
    db.close()

    # Accept recommendation
    res = client.post(
        "/api/v1/learning/recommendations/rec_safe_1/action",
        json={"action": "ACCEPTED"},
        headers=test_env["headers_ws1"]
    )
    assert res.status_code == 200

    # Verify campaign version was NOT mutated
    db = TestingSessionLocal()
    ver = db.query(CampaignVersion).filter(CampaignVersion.id == "ver_rem_1").first()
    assert ver.version_number == 1
    assert ver.status == "OPERATOR_APPROVED"

    # Verify capacity policy was NOT mutated
    cap = db.query(CapacityPolicy).filter(CapacityPolicy.workspace_id == "ws_rem_1").first()
    assert cap.outreach_limit_per_day == 100

    # Verify zero outbound messages were triggered
    msgs = db.query(OutboundMessage).filter(OutboundMessage.workspace_id == "ws_rem_1").all()
    assert len(msgs) == 0
    db.close()


# =============================================================================
# 2. TRUTHFUL LLM PROVIDER STATUS MODEL
# =============================================================================
def test_provider_status_state_transitions():
    """Tests all explicit provider operational states: CONFIGURED, REACHABLE, AVAILABLE, QUOTA_EXHAUSTED, ERROR."""
    ProviderStatusTracker.reset_for_tests()

    # 1. Missing credentials -> ERROR
    state, meta = ProviderStatusTracker.get_provider_state("OPENAI", "gpt-4o-mini", has_credentials=False)
    assert state == ProviderOperationalState.ERROR
    assert meta["is_usable"] is False
    assert "credentials" in meta["error_message"].lower()

    # 2. Configured with credentials but untested -> CONFIGURED
    state, meta = ProviderStatusTracker.get_provider_state("OPENAI", "gpt-4o-mini", has_credentials=True)
    assert state == ProviderOperationalState.CONFIGURED
    assert meta["is_usable"] is False

    # 3. Reachable check recorded -> REACHABLE
    ProviderStatusTracker.record_reachable("OPENAI", "gpt-4o-mini")
    state, meta = ProviderStatusTracker.get_provider_state("OPENAI", "gpt-4o-mini", has_credentials=True)
    assert state == ProviderOperationalState.REACHABLE
    assert meta["is_usable"] is False

    # 4. HTTP 429 Quota Exhaustion -> QUOTA_EXHAUSTED (Must NOT be AVAILABLE or ONLINE)
    ProviderStatusTracker.record_http_error(
        "OPENAI", "gpt-4o-mini", 429, "You have exceeded your current quota, please check your plan and billing details."
    )
    state, meta = ProviderStatusTracker.get_provider_state("OPENAI", "gpt-4o-mini", has_credentials=True)
    assert state == ProviderOperationalState.QUOTA_EXHAUSTED
    assert meta["is_usable"] is False
    assert meta["status_code"] == 429

    # 5. HTTP 500 Operational Failure -> ERROR
    ProviderStatusTracker.record_http_error("OPENAI", "gpt-4o-mini", 500, "Internal Server Error")
    state, meta = ProviderStatusTracker.get_provider_state("OPENAI", "gpt-4o-mini", has_credentials=True)
    assert state == ProviderOperationalState.ERROR
    assert meta["is_usable"] is False

    # 6. Timeout -> ERROR
    ProviderStatusTracker.record_diagnostic(
        "OPENAI", "gpt-4o-mini", ProviderOperationalState.ERROR, error_message="ConnectTimeout: Request timed out"
    )
    state, meta = ProviderStatusTracker.get_provider_state("OPENAI", "gpt-4o-mini", has_credentials=True)
    assert state == ProviderOperationalState.ERROR
    assert meta["is_usable"] is False

    # 7. Recovery from quota exhausted or error state -> AVAILABLE
    ProviderStatusTracker.record_success("OPENAI", "gpt-4o-mini")
    state, meta = ProviderStatusTracker.get_provider_state("OPENAI", "gpt-4o-mini", has_credentials=True)
    assert state == ProviderOperationalState.AVAILABLE
    assert meta["is_usable"] is True
    assert meta["status_code"] == 200
    ProviderStatusTracker.reset_for_tests(seed_default=True)


def test_capacity_status_reports_quota_exhausted_truthfully(test_env):
    """Verifies that /api/v1/capacity/status returns state=QUOTA_EXHAUSTED and is_available=False when 429 occurs."""
    ProviderStatusTracker.reset_for_tests()
    ProviderStatusTracker.record_http_error(
        "OPENAI", "gpt-4o-mini", 429, "HTTP 429: Insufficient credits"
    )

    res = client.get("/api/v1/capacity/status", headers=test_env["headers_ws1"])
    assert res.status_code == 200
    data = res.json()
    provider = data["provider_status"]

    assert provider["state"] == "QUOTA_EXHAUSTED"
    assert provider["is_available"] is False
    assert provider["provider_availability"] == "QUOTA_EXHAUSTED"


# =============================================================================
# 3. CONTACT VERIFICATION & 16-GATE AUTHORITY
# =============================================================================
def test_gate_6_cannot_be_bypassed_by_client_verification_tier(test_env):
    """Proves that passing a client verification tier cannot override database ContactRecord verification."""
    db = TestingSessionLocal()
    # Create an UNVERIFIED contact in DB
    unverified_contact = ContactRecord(
        id="cnt_unverified",
        workspace_id="ws_rem_1",
        business_id="biz_test",
        email="unverified.doctor@clinic.test",
        canonical_email=canonicalize_email("unverified.doctor@clinic.test"),
        verification_tier="UNVERIFIED",
        freshness_timestamp=datetime.now(timezone.utc)
    )
    db.add(unverified_contact)
    db.commit()
    db.close()

    # Client attempts to spoof contact verification in the request payload
    dispatch_payload = {
        "campaign_version_id": test_env["ver1_id"],
        "sender_account_id": test_env["sender1_id"],
        "recipient_email": "unverified.doctor@clinic.test",
        "recipient_country": "US",
        "subject": "Consultative inquiry",
        "body_text": "Evaluating scheduling automation",
        "side_effect_idempotency_key": "idemp_test_gate6_spoof",
        "thread_state": "AUTONOMOUS",
        "contact_verification_tier": "CONFIRMED_VERIFIED"  # Spoofed client tier!
    }

    res = client.post("/api/v1/outbound/dispatch", json=dispatch_payload, headers=test_env["headers_ws1"])
    assert res.status_code == 200
    data = res.json()

    # Must be BLOCKED at Gate 6!
    assert data["status"] == "BLOCKED"
    assert "GATE 6 VIOLATION" in data["gate_failure_reason"]
    assert data["gate_passed_count"] == 5  # Gate 6 failed


def test_gate_14_suppression_cannot_be_bypassed(test_env):
    """Proves recipient on suppression list is strictly blocked at Gate 14."""
    db = TestingSessionLocal()
    # Add verified contact
    cnt = ContactRecord(
        id="cnt_suppressed",
        workspace_id="ws_rem_1",
        business_id="biz_test_2",
        email="optout.user@domain.test",
        canonical_email=canonicalize_email("optout.user@domain.test"),
        verification_tier="CONFIRMED_VERIFIED",
        freshness_timestamp=datetime.now(timezone.utc)
    )
    db.add(cnt)
    db.commit()

    # Execute atomic suppression
    SuppressionService.execute_atomic_suppression(
        db=db,
        organization_id="org_rem",
        workspace_id="ws_rem_1",
        email="optout.user@domain.test",
        source_channel="UNSUBSCRIBE_LINK",
        reason="Prior unsubscribe request"
    )
    db.close()

    dispatch_payload = {
        "campaign_version_id": test_env["ver1_id"],
        "sender_account_id": test_env["sender1_id"],
        "recipient_email": "optout.user@domain.test",
        "recipient_country": "US",
        "subject": "Consultative inquiry",
        "body_text": "Evaluating scheduling automation",
        "side_effect_idempotency_key": "idemp_test_gate14_supp",
        "thread_state": "AUTONOMOUS"
    }

    res = client.post("/api/v1/outbound/dispatch", json=dispatch_payload, headers=test_env["headers_ws1"])
    assert res.status_code == 200
    data = res.json()

    assert data["status"] == "SUPPRESSED"
    assert "GATE 14 VIOLATION" in data["gate_failure_reason"]
    assert data["gate_passed_count"] == 13  # Gate 14 failed


def test_gate_15_kill_switch_cannot_be_bypassed(test_env):
    """Proves global emergency kill switch strictly blocks dispatch at Gate 15."""
    settings.GLOBAL_EMERGENCY_KILL_SWITCH = True
    try:
        db = TestingSessionLocal()
        cnt = ContactRecord(
            id="cnt_kill",
            workspace_id="ws_rem_1",
            business_id="biz_test_3",
            email="valid.target@domain.test",
            canonical_email=canonicalize_email("valid.target@domain.test"),
            verification_tier="CONFIRMED_VERIFIED",
            freshness_timestamp=datetime.now(timezone.utc)
        )
        db.add(cnt)
        db.commit()
        db.close()

        dispatch_payload = {
            "campaign_version_id": test_env["ver1_id"],
            "sender_account_id": test_env["sender1_id"],
            "recipient_email": "valid.target@domain.test",
            "recipient_country": "US",
            "subject": "Consultative inquiry",
            "body_text": "Evaluating scheduling automation",
            "side_effect_idempotency_key": "idemp_test_gate15_kill",
            "thread_state": "AUTONOMOUS"
        }

        res = client.post("/api/v1/outbound/dispatch", json=dispatch_payload, headers=test_env["headers_ws1"])
        assert res.status_code == 200
        data = res.json()

        assert data["status"] == "BLOCKED"
        assert "GATE 15 VIOLATION" in data["gate_failure_reason"]
        assert data["gate_passed_count"] == 14  # Gate 15 failed
    finally:
        settings.GLOBAL_EMERGENCY_KILL_SWITCH = False


def test_gate_16_human_takeover_cannot_be_bypassed(test_env):
    """Proves thread in HUMAN_TAKEOVER strictly blocks AI dispatch at Gate 16."""
    db = TestingSessionLocal()
    cnt = ContactRecord(
        id="cnt_takeover",
        workspace_id="ws_rem_1",
        business_id="biz_test_4",
        email="deal.prospect@domain.test",
        canonical_email=canonicalize_email("deal.prospect@domain.test"),
        verification_tier="CONFIRMED_VERIFIED",
        freshness_timestamp=datetime.now(timezone.utc)
    )
    db.add(cnt)
    db.commit()
    db.close()

    dispatch_payload = {
        "campaign_version_id": test_env["ver1_id"],
        "sender_account_id": test_env["sender1_id"],
        "recipient_email": "deal.prospect@domain.test",
        "recipient_country": "US",
        "subject": "Consultative inquiry",
        "body_text": "Evaluating scheduling automation",
        "side_effect_idempotency_key": "idemp_test_gate16_takeover",
        "thread_state": "HUMAN_TAKEOVER"
    }

    res = client.post("/api/v1/outbound/dispatch", json=dispatch_payload, headers=test_env["headers_ws1"])
    assert res.status_code == 200
    data = res.json()

    assert data["status"] == "BLOCKED"
    assert "GATE 16 VIOLATION" in data["gate_failure_reason"]
    assert data["gate_passed_count"] == 15  # Gate 16 failed


# =============================================================================
# 4. DEMO FIXTURE VS REAL DISCOVERY MARKERS
# =============================================================================
def test_demo_fixture_and_real_discovery_markers(test_env):
    """Verifies that demo/synthetic fixtures are explicitly marked and real discoveries are not falsely tagged."""
    db = TestingSessionLocal()
    # 1. Demo fixture (source_provider="MANUAL", domain=".example.com")
    demo_biz = MasterBusinessRecord(
        id="biz_demo_fixture",
        workspace_id="ws_rem_1",
        name="Austin Family Health Demo",
        normalized_name="austin family health demo",
        canonical_domain="austinfamilyhealth.example.com",
        category="healthcare",
        source_provider="MANUAL"
    )

    # 2. Real discovered business (source_provider="OPENSTREETMAP", real domain)
    real_biz = MasterBusinessRecord(
        id="biz_real_discovery",
        workspace_id="ws_rem_1",
        name="Apex Diagnostics Center",
        normalized_name="apex diagnostics center",
        canonical_domain="apexdiagnosticcenter.org",
        category="healthcare",
        source_provider="OPENSTREETMAP"
    )
    db.add_all([demo_biz, real_biz])
    db.commit()
    db.close()

    res = client.get("/api/v1/businesses", headers=test_env["headers_ws1"])
    assert res.status_code == 200
    businesses = {b["id"]: b for b in res.json()}

    # Demo record check:
    assert "biz_demo_fixture" in businesses
    demo = businesses["biz_demo_fixture"]
    assert demo["is_demo_fixture"] is True
    assert demo["data_origin"] == "DEMO_FIXTURE"

    # Real record check:
    assert "biz_real_discovery" in businesses
    real = businesses["biz_real_discovery"]
    assert real["is_demo_fixture"] is False
    assert real["data_origin"] == "REAL_DISCOVERY"


# =============================================================================
# 5. TENANCY & WORKSPACE ISOLATION
# =============================================================================
def test_workspace_isolation_maintained(test_env):
    """Verifies complete cross-workspace isolation across learning and businesses."""
    db = TestingSessionLocal()
    rec_ws2 = RecommendationRecord(
        id="rec_ws2_secret",
        workspace_id="ws_rem_2",
        recommendation_type="PRICING",
        headline="Confidential WS2 Recommendation",
        suggested_action="Internal suggestion for WS2",
        expected_benefit="WS2 confidential benefit",
        status="PROPOSED"
    )
    db.add(rec_ws2)
    db.commit()
    db.close()

    # User 1 queries recommendations -> must NOT see WS2 recommendation
    res1 = client.get("/api/v1/learning/recommendations", headers=test_env["headers_ws1"])
    assert res1.status_code == 200
    ids_ws1 = [r["id"] for r in res1.json()]
    assert "rec_ws2_secret" not in ids_ws1

    # User 1 attempts to act on WS2 recommendation -> must fail with 400
    res_act = client.post(
        "/api/v1/learning/recommendations/rec_ws2_secret/action",
        json={"action": "ACCEPTED"},
        headers=test_env["headers_ws1"]
    )
    assert res_act.status_code == 400
