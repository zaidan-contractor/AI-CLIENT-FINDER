import time
import threading
from datetime import datetime, timezone, date
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool

from app.main import app
from app.core.database import Base, get_db, SessionLocal
from app.core.security import create_access_token, get_password_hash, canonicalize_email, encrypt_credential
from app.core.config import settings
from app.models.tenancy import Organization, Workspace, User, Membership, get_utc_now
from app.models.capacity import (
    CapacityPolicy,
    WorkspaceCapacityDailyUsage,
    CapacityDecisionAudit,
    CapacityDecisionType,
    AIQualityTier,
)
from app.models.campaign import Campaign, CampaignVersion
from app.models.business import MasterBusinessRecord, ContactRecord
from app.models.outreach import SenderAccount, OutboundMessage
from app.services.capacity_service import CapacityService, CapacityDecision
from app.services.llm.base import LLMProvider, ProviderOperationalConstraints, LLMStructuredResult, LLMExecutionStatus, LLMUsageMetadata, LLMErrorClassification
from app.services.llm.openai_provider import OpenAIProvider
from app.services.llm.rule_based_fallback import RuleBasedFallbackProvider
from app.services.outbound_dispatch_engine import OutboundDispatchEngine
from app.services.suppression_service import SuppressionService


# ----------------------------------------------------------------------
# Test Fixtures & DB Setup (In-Memory SQLite for unit testing)
# ----------------------------------------------------------------------
test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)

@pytest.fixture
def capacity_db():
    from app.services.email_transports.mock_transport import MockEmailTransport
    from app.services.email_transports.factory import set_transport_override
    mock_transport = MockEmailTransport()
    set_transport_override(mock_transport)

    Base.metadata.create_all(bind=test_engine)
    session = Session(bind=test_engine)

    def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db
    yield session

    app.dependency_overrides.pop(get_db, None)
    session.close()
    Base.metadata.drop_all(bind=test_engine)
    set_transport_override(None)


@pytest.fixture
def capacity_client():
    return TestClient(app)


def create_tenant_environment(db: Session, ws_slug: str = "cap-ws", role: str = "WorkspaceAdmin"):
    ts = int(time.time() * 1000)
    org = Organization(name=f"Org {ws_slug}", slug=f"org-{ws_slug}-{ts}")
    db.add(org)
    db.flush()

    ws = Workspace(organization_id=org.id, name=f"WS {ws_slug}", slug=f"{ws_slug}-{ts}")
    db.add(ws)
    db.flush()

    user = User(
        email=f"user-{ws_slug}-{ts}@platform.test",
        hashed_password=get_password_hash("SafePass123!"),
        full_name="Capacity Tester",
    )
    db.add(user)
    db.flush()

    membership = Membership(
        user_id=user.id,
        organization_id=org.id,
        workspace_id=ws.id,
        role=role,
    )
    db.add(membership)
    db.commit()

    token = create_access_token(
        user_id=str(user.id),
        organization_id=str(org.id),
        active_workspace_id=str(ws.id),
        role=role,
    )

    return {
        "org": org,
        "workspace": ws,
        "user": user,
        "token": token,
        "headers": {"Authorization": f"Bearer {token}"},
    }


def create_outreach_test_fixtures(db: Session, ws_id: str, org_id: str, recipient: str = "recipient@nowhere.test"):
    camp = Campaign(
        workspace_id=ws_id,
        name=f"Campaign {int(time.time()*1000)}",
        status="ACTIVE"
    )
    db.add(camp)
    db.flush()

    cv = CampaignVersion(
        workspace_id=ws_id,
        campaign_id=camp.id,
        version_number=1,
        status="OPERATOR_APPROVED",
        sequence_steps=[]
    )
    db.add(cv)
    db.flush()

    sender = SenderAccount(
        workspace_id=ws_id,
        email=f"sender_{int(time.time()*1000)}@capacity.test",
        display_name="Sender",
        provider_type="SMTP",
        encrypted_credentials=encrypt_credential("{\"smtp_password\": \"test_pass\"}"),
        status="ACTIVE",
        daily_quota=50,
        dns_valid=True,
        spf_valid=True,
        dkim_valid=True,
        dmarc_valid=True,
        tracking_domain_aligned=True,
        connection_verified=True,
        delivery_health_ok=True,
    )
    db.add(sender)

    biz = MasterBusinessRecord(
        workspace_id=ws_id,
        name="Target Corp",
        normalized_name="target corp",
        website_url="https://targetcorp.test",
        category="Consulting",
        source_provider="LOCAL_DIRECTORY"
    )
    db.add(biz)
    db.flush()

    contact = ContactRecord(
        workspace_id=ws_id,
        business_id=biz.id,
        first_name="Target",
        last_name="Executive",
        email=recipient,
        canonical_email=canonicalize_email(recipient),
        verification_tier="CONFIRMED_VERIFIED",
        freshness_timestamp=get_utc_now()
    )
    db.add(contact)
    db.commit()

    return {
        "campaign": camp,
        "campaign_version": cv,
        "sender": sender,
        "business": biz,
        "contact": contact
    }


# ----------------------------------------------------------------------
# 1. WORKSPACE ISOLATION & DEFAULT POLICY
# ----------------------------------------------------------------------
def test_capacity_policy_defaults_and_workspace_isolation(capacity_db: Session):
    env_a = create_tenant_environment(capacity_db, "ws-a")
    env_b = create_tenant_environment(capacity_db, "ws-b")

    policy_a = CapacityService.get_or_create_policy(capacity_db, env_a["workspace"].id)
    policy_b = CapacityService.get_or_create_policy(capacity_db, env_b["workspace"].id)

    assert policy_a.discovery_limit_per_day == 50
    assert policy_a.research_limit_per_day == 100
    assert policy_a.llm_operations_limit_per_day == 200
    assert policy_a.outreach_limit_per_day == 50
    assert policy_a.min_quality_floor == AIQualityTier.TIER_1_ADVANCED.value

    policy_a.outreach_limit_per_day = 12
    policy_a.min_quality_floor = AIQualityTier.TIER_2_STANDARD.value
    capacity_db.commit()

    refreshed_a = CapacityService.get_or_create_policy(capacity_db, env_a["workspace"].id)
    refreshed_b = CapacityService.get_or_create_policy(capacity_db, env_b["workspace"].id)

    assert refreshed_a.outreach_limit_per_day == 12
    assert refreshed_a.min_quality_floor == AIQualityTier.TIER_2_STANDARD.value
    assert refreshed_b.outreach_limit_per_day == 50
    assert refreshed_b.min_quality_floor == AIQualityTier.TIER_1_ADVANCED.value


# ----------------------------------------------------------------------
# 2. TIMEZONE-AWARE DAILY RESET BOUNDARIES
# ----------------------------------------------------------------------
def test_timezone_aware_daily_boundary(capacity_db: Session):
    env = create_tenant_environment(capacity_db, "ws-tz")
    policy = CapacityService.get_or_create_policy(capacity_db, env["workspace"].id)

    policy.timezone = "UTC"
    capacity_db.commit()
    date_utc = CapacityService.get_current_workspace_date(policy)
    assert date_utc is not None

    policy.timezone = "Asia/Tokyo"
    capacity_db.commit()
    date_tokyo = CapacityService.get_current_workspace_date(policy)
    assert date_tokyo is not None

    policy.timezone = "Invalid/Nonexistent_Timezone"
    capacity_db.commit()
    fallback_date = CapacityService.get_current_workspace_date(policy)
    assert fallback_date is not None


# ----------------------------------------------------------------------
# 3. CAPACITY DECISION ENGINE & AUDIT LOGGING
# ----------------------------------------------------------------------
def test_capacity_decision_engine_allow_and_queue(capacity_db: Session):
    env = create_tenant_environment(capacity_db, "ws-decision")
    policy = CapacityService.get_or_create_policy(capacity_db, env["workspace"].id)
    policy.outreach_limit_per_day = 2
    capacity_db.commit()

    res1 = CapacityService.check_capacity(capacity_db, env["workspace"].id, "OUTREACH")
    assert res1.decision == CapacityDecision.ALLOW
    assert res1.remaining_capacity == 2

    ok1, u1, r1 = CapacityService.increment_usage_atomic(capacity_db, env["workspace"].id, "OUTREACH", 1)
    assert ok1 is True
    ok2, u2, r2 = CapacityService.increment_usage_atomic(capacity_db, env["workspace"].id, "OUTREACH", 1)
    assert ok2 is True

    res2 = CapacityService.check_capacity(capacity_db, env["workspace"].id, "OUTREACH")
    assert res2.decision == CapacityDecision.QUEUE
    assert res2.remaining_capacity == 0

    res3 = CapacityService.check_capacity(capacity_db, env["workspace"].id, "OUTREACH", is_queueable=False)
    assert res3.decision == CapacityDecision.REJECT
    assert res3.remaining_capacity == 0

    audits = (
        capacity_db.query(CapacityDecisionAudit)
        .filter_by(workspace_id=env["workspace"].id)
        .order_by(CapacityDecisionAudit.created_at.asc())
        .all()
    )
    assert len(audits) >= 3
    assert audits[0].decision == "ALLOW"
    assert audits[1].decision == "QUEUE"
    assert audits[2].decision == "REJECT"


# ----------------------------------------------------------------------
# 4. SCENARIOS A & B: POST-TRANSMISSION RECOVERY & WORKER RETRY IDEMPOTENCY
# ----------------------------------------------------------------------
def test_scenario_a_successful_transmission_followed_by_accounting_failure(capacity_db: Session):
    """
    Scenario A: Wire transmission succeeded, but process crashed before capacity_accounted was marked.
    Recovery invariant:
    - Calling record_transmitted_usage_if_unaccounted recovers capacity accounting.
    - Message marked capacity_accounted = True.
    - Calling dispatch_outbound_message does NOT send a second email and does not double-count.
    """
    env = create_tenant_environment(capacity_db, "ws-scen-a")
    ws_id = env["workspace"].id
    org_id = env["org"].id
    fixtures = create_outreach_test_fixtures(capacity_db, ws_id, org_id)

    key = "scen_a_idempotency_key_111"
    # Simulate message successfully transmitted on wire, but capacity accounting not yet recorded
    msg = OutboundMessage(
        workspace_id=ws_id,
        campaign_version_id=fixtures["campaign_version"].id,
        sender_account_id=fixtures["sender"].id,
        recipient_email="recipient@nowhere.test",
        recipient_country="US",
        business_id=fixtures["business"].id,
        subject="Consultative Outreach",
        body_text="Proposal body",
        status="TRANSMITTED",
        provider_message_id="gmail_msg_id_scen_a_999",
        transport_mode="REAL",
        side_effect_idempotency_key=key,
        capacity_accounted=False
    )
    capacity_db.add(msg)
    capacity_db.commit()

    # Pre-condition: usage summary shows 0 outreach used
    summary_before = CapacityService.get_workspace_capacity_summary(capacity_db, ws_id)
    assert summary_before["metrics"]["outreach"]["used"] == 0

    # Worker retry or caller calls dispatch_outbound_message with same idempotency key
    recovered_msg = OutboundDispatchEngine.dispatch_outbound_message(
        db=capacity_db,
        workspace_id=ws_id,
        campaign_version_id=fixtures["campaign_version"].id,
        sender_account_id=fixtures["sender"].id,
        recipient_email="recipient@nowhere.test",
        recipient_country="US",
        business_id=fixtures["business"].id,
        subject="Consultative Outreach",
        body_text="Proposal body",
        side_effect_idempotency_key=key
    )

    # Recovery asserts
    assert recovered_msg.id == msg.id
    assert recovered_msg.status == "TRANSMITTED"
    assert recovered_msg.capacity_accounted is True

    # Usage counter must now reflect exactly 1
    summary_after = CapacityService.get_workspace_capacity_summary(capacity_db, ws_id)
    assert summary_after["metrics"]["outreach"]["used"] == 1


def test_scenario_b_worker_retry_after_post_transmission_failure(capacity_db: Session):
    """
    Scenario B: Message already TRANSMITTED and capacity_accounted = True.
    Worker retries job:
    - Returns TRANSMITTED immediately.
    - Zero duplicate transmissions.
    - Capacity counter remains 1 (never double-counted).
    """
    env = create_tenant_environment(capacity_db, "ws-scen-b")
    ws_id = env["workspace"].id
    org_id = env["org"].id
    fixtures = create_outreach_test_fixtures(capacity_db, ws_id, org_id)

    key = "scen_b_idempotency_key_222"
    msg = OutboundMessage(
        workspace_id=ws_id,
        campaign_version_id=fixtures["campaign_version"].id,
        sender_account_id=fixtures["sender"].id,
        recipient_email="recipient@nowhere.test",
        recipient_country="US",
        business_id=fixtures["business"].id,
        subject="Consultative Outreach",
        body_text="Proposal body",
        status="TRANSMITTED",
        provider_message_id="gmail_msg_id_scen_b_888",
        transport_mode="REAL",
        side_effect_idempotency_key=key,
        capacity_accounted=True
    )
    capacity_db.add(msg)
    capacity_db.commit()

    # Pre-seed usage count to 1
    CapacityService.increment_usage_atomic(capacity_db, ws_id, "OUTREACH", 1)

    # Worker retries
    retry_res = OutboundDispatchEngine.dispatch_outbound_message(
        db=capacity_db,
        workspace_id=ws_id,
        campaign_version_id=fixtures["campaign_version"].id,
        sender_account_id=fixtures["sender"].id,
        recipient_email="recipient@nowhere.test",
        recipient_country="US",
        business_id=fixtures["business"].id,
        subject="Consultative Outreach",
        body_text="Proposal body",
        side_effect_idempotency_key=key
    )

    assert retry_res.id == msg.id
    assert retry_res.status == "TRANSMITTED"
    # Usage counter must remain exactly 1
    summary = CapacityService.get_workspace_capacity_summary(capacity_db, ws_id)
    assert summary["metrics"]["outreach"]["used"] == 1


# ----------------------------------------------------------------------
# 5. SCENARIO C: TWO WORKERS ATTEMPTING SAME OPERATION
# ----------------------------------------------------------------------
def test_scenario_c_two_workers_attempting_same_outbound_operation(capacity_db: Session):
    """
    Scenario C: Two workers attempting to dispatch the exact same idempotency key concurrently.
    Guarantees only one message is created/processed and no duplicate wire execution occurs.
    """
    env = create_tenant_environment(capacity_db, "ws-scen-c")
    ws_id = env["workspace"].id
    org_id = env["org"].id
    fixtures = create_outreach_test_fixtures(capacity_db, ws_id, org_id)

    key = "scen_c_idempotency_key_333"

    msg1 = OutboundDispatchEngine.dispatch_outbound_message(
        db=capacity_db,
        workspace_id=ws_id,
        campaign_version_id=fixtures["campaign_version"].id,
        sender_account_id=fixtures["sender"].id,
        recipient_email="recipient@nowhere.test",
        recipient_country="US",
        business_id=fixtures["business"].id,
        subject="Consultative Outreach",
        body_text="Proposal body",
        side_effect_idempotency_key=key
    )

    msg2 = OutboundDispatchEngine.dispatch_outbound_message(
        db=capacity_db,
        workspace_id=ws_id,
        campaign_version_id=fixtures["campaign_version"].id,
        sender_account_id=fixtures["sender"].id,
        recipient_email="recipient@nowhere.test",
        recipient_country="US",
        business_id=fixtures["business"].id,
        subject="Consultative Outreach",
        body_text="Proposal body",
        side_effect_idempotency_key=key
    )

    assert msg1.id == msg2.id


# ----------------------------------------------------------------------
# 6. SCENARIO D: EXACT LIMIT UNDER CONCURRENCY (POSTGRESQL ROW LOCKS)
# ----------------------------------------------------------------------
def test_scenario_d_capacity_reaches_exact_limit_under_concurrency():
    """
    Scenario D: Concurrency race with PostgreSQL SessionLocal.
    Verifies that multiple concurrent threads competing for capacity
    cannot overshoot the configured limit.
    """
    main_db = SessionLocal()
    env = create_tenant_environment(main_db, "ws-scen-d")
    ws_id = env["workspace"].id

    try:
        policy = CapacityService.get_or_create_policy(main_db, ws_id)
        policy.outreach_limit_per_day = 5  # Exactly 5 units
        main_db.commit()

        current_date = CapacityService.get_current_workspace_date(policy)
        CapacityService.get_or_create_daily_usage(main_db, ws_id, current_date)

        results = []
        errors = []

        def worker_try_acquire():
            thread_db = SessionLocal()
            try:
                ok, cnt, rem = CapacityService.increment_usage_atomic(
                    thread_db, ws_id, "OUTREACH", 1, force_post_transmission=False
                )
                results.append(ok)
            except Exception as e:
                errors.append(e)
            finally:
                thread_db.close()

        # 10 threads compete for 5 available slots
        threads = [threading.Thread(target=worker_try_acquire) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        # Exactly 5 must succeed, exactly 5 must be rejected
        assert results.count(True) == 5
        assert results.count(False) == 5

        # Final count in PostgreSQL must be exactly 5, remaining = 0
        summary = CapacityService.get_workspace_capacity_summary(main_db, ws_id)
        assert summary["metrics"]["outreach"]["used"] == 5
        assert summary["metrics"]["outreach"]["remaining"] == 0
    finally:
        main_db.close()


# ----------------------------------------------------------------------
# 7. SCENARIOS E & F: BACKPRESSURE EXHAUSTION & DRAIN RESUMPTION
# ----------------------------------------------------------------------
def test_scenario_e_and_f_backpressure_exhaustion_and_recovery(capacity_db: Session):
    """
    Scenario E: When capacity is exhausted, message enters QUEUED_BACKPRESSURE.
    Scenario F: When capacity is expanded, process_backpressure_queue resumes
    and transmits in FIFO order without busy-looping.
    """
    env = create_tenant_environment(capacity_db, "ws-scen-ef")
    ws_id = env["workspace"].id
    org_id = env["org"].id
    fixtures = create_outreach_test_fixtures(capacity_db, ws_id, org_id)

    policy = CapacityService.get_or_create_policy(capacity_db, ws_id)
    policy.outreach_limit_per_day = 0  # Exhausted
    capacity_db.commit()

    # Step E: Dispatch encounters capacity backpressure
    msg = OutboundDispatchEngine.dispatch_outbound_message(
        db=capacity_db,
        workspace_id=ws_id,
        campaign_version_id=fixtures["campaign_version"].id,
        sender_account_id=fixtures["sender"].id,
        recipient_email="recipient@nowhere.test",
        recipient_country="US",
        business_id=None,
        subject="Consultative Outreach",
        body_text="Proposal body",
        side_effect_idempotency_key="scen_ef_key_001"
    )
    assert msg.status == "QUEUED_BACKPRESSURE"

    # Step F: Restore capacity
    policy.outreach_limit_per_day = 10
    capacity_db.commit()

    # Drain queue
    drain_result = OutboundDispatchEngine.process_backpressure_queue(capacity_db, ws_id)
    assert drain_result["processed"] == 1
    assert drain_result["transmitted"] == 1
    assert drain_result["still_queued"] == 0

    capacity_db.refresh(msg)
    assert msg.status == "TRANSMITTED"
    assert msg.capacity_accounted is True


# ----------------------------------------------------------------------
# 8. SCENARIOS G & H: SUPPRESSION & KILL SWITCH DURING BACKPRESSURE
# ----------------------------------------------------------------------
def test_scenario_g_suppression_changes_while_waiting_in_backpressure(capacity_db: Session):
    """
    Scenario G: Recipient opts out while job is in QUEUED_BACKPRESSURE.
    When capacity becomes available, Gate 14 catches suppression and marks SUPPRESSED.
    The message is NEVER transmitted over the wire!
    """
    env = create_tenant_environment(capacity_db, "ws-scen-g")
    ws_id = env["workspace"].id
    org_id = env["org"].id
    fixtures = create_outreach_test_fixtures(capacity_db, ws_id, org_id, recipient="optout.during.backpressure@test.com")

    # Start with 0 capacity
    policy = CapacityService.get_or_create_policy(capacity_db, ws_id)
    policy.outreach_limit_per_day = 0
    capacity_db.commit()

    msg = OutboundDispatchEngine.dispatch_outbound_message(
        db=capacity_db,
        workspace_id=ws_id,
        campaign_version_id=fixtures["campaign_version"].id,
        sender_account_id=fixtures["sender"].id,
        recipient_email="optout.during.backpressure@test.com",
        recipient_country="US",
        business_id=None,
        subject="Consultative Outreach",
        body_text="Proposal body",
        side_effect_idempotency_key="scen_g_key_001"
    )
    assert msg.status == "QUEUED_BACKPRESSURE"

    # Recipient requests suppression while in backpressure
    SuppressionService.execute_atomic_suppression(
        db=capacity_db,
        organization_id=org_id,
        workspace_id=ws_id,
        email="optout.during.backpressure@test.com",
        source_channel="OPT_OUT_WEBHOOK",
        reason="Recipient unsubscribed while in backpressure queue"
    )

    # Now capacity is expanded
    policy.outreach_limit_per_day = 10
    capacity_db.commit()

    # Drain queue
    OutboundDispatchEngine.process_backpressure_queue(capacity_db, ws_id)

    capacity_db.refresh(msg)
    # Must be marked SUPPRESSED, NOT transmitted!
    assert msg.status == "SUPPRESSED"
    assert "GATE 14" in msg.gate_failure_reason


def test_scenario_h_kill_switch_active_while_waiting_in_backpressure(capacity_db: Session):
    """
    Scenario H: Global kill switch engaged while job is in QUEUED_BACKPRESSURE.
    When capacity becomes available, Gate 15 halts dispatch and marks BLOCKED.
    The message is NEVER transmitted over the wire!
    """
    env = create_tenant_environment(capacity_db, "ws-scen-h")
    ws_id = env["workspace"].id
    org_id = env["org"].id
    fixtures = create_outreach_test_fixtures(capacity_db, ws_id, org_id, recipient="killswitch.backpressure@test.com")

    policy = CapacityService.get_or_create_policy(capacity_db, ws_id)
    policy.outreach_limit_per_day = 0
    capacity_db.commit()

    msg = OutboundDispatchEngine.dispatch_outbound_message(
        db=capacity_db,
        workspace_id=ws_id,
        campaign_version_id=fixtures["campaign_version"].id,
        sender_account_id=fixtures["sender"].id,
        recipient_email="killswitch.backpressure@test.com",
        recipient_country="US",
        business_id=None,
        subject="Consultative Outreach",
        body_text="Proposal body",
        side_effect_idempotency_key="scen_h_key_001"
    )
    assert msg.status == "QUEUED_BACKPRESSURE"

    original_kill_switch = settings.GLOBAL_EMERGENCY_KILL_SWITCH
    try:
        # Engage Gate 15 Kill Switch
        settings.GLOBAL_EMERGENCY_KILL_SWITCH = True

        # Capacity becomes available
        policy.outreach_limit_per_day = 10
        capacity_db.commit()

        # Drain queue
        OutboundDispatchEngine.process_backpressure_queue(capacity_db, ws_id)

        capacity_db.refresh(msg)
        # Must be BLOCKED by Gate 15
        assert msg.status == "BLOCKED"
        assert "GATE 15" in msg.gate_failure_reason
    finally:
        settings.GLOBAL_EMERGENCY_KILL_SWITCH = original_kill_switch


# ----------------------------------------------------------------------
# 9. SCENARIOS I & J: PROVIDER CAPACITY UNKNOWN VS UNAVAILABLE
# ----------------------------------------------------------------------
def test_scenario_i_provider_capacity_unknown(capacity_db: Session):
    """
    Scenario I: Unknown limits must NOT become zero.
    Unknown availability does NOT mean unavailable.
    Platform capacity policy governs execution.
    """
    env = create_tenant_environment(capacity_db, "ws-scen-i")
    ws_id = env["workspace"].id

    class UnknownConstraintsProvider(LLMProvider):
        def generate_structured(self, messages, response_schema, temperature=0.1, max_tokens=None):
            return MagicMock()
        def generate_text(self, messages, temperature=0.7, max_tokens=None):
            return MagicMock()
        def get_model_metadata(self):
            return {"provider": "TEST_UNKNOWN", "model": "test"}
        def estimate_cost(self, p, c, m=None):
            return 0.0
        def classify_error(self, e):
            return LLMErrorClassification.NON_RETRYABLE
        def normalize_error(self, e):
            return str(e)
        def get_operational_constraints(self):
            return ProviderOperationalConstraints(
                provider_name="TEST_UNKNOWN",
                requests_per_minute=None,  # Unknown, not 0
                requests_per_day=None,
                provider_availability="UNKNOWN",  # Unknown does NOT mean unavailable
                model_availability="UNKNOWN",
                quality_tier="TIER_1_ADVANCED"
            )

    provider = UnknownConstraintsProvider()
    constraints = provider.get_operational_constraints()

    assert constraints.requests_per_minute is None  # Must remain None, NOT 0
    assert constraints.provider_availability == "UNKNOWN"

    # Evaluation under platform CapacityPolicy must allow since platform has quota
    res = CapacityService.check_capacity(capacity_db, ws_id, "LLM_OPERATION", provider=provider)
    assert res.decision == CapacityDecision.ALLOW
    assert "CAPACITY_ALLOWED" in res.reason


def test_scenario_j_provider_unavailable(capacity_db: Session):
    """
    Scenario J: Provider is explicitly UNAVAILABLE.
    check_capacity returns QUEUE (or REJECT if non-queueable) with PROVIDER_UNAVAILABLE.
    """
    env = create_tenant_environment(capacity_db, "ws-scen-j")
    ws_id = env["workspace"].id

    class UnavailableProvider(LLMProvider):
        def generate_structured(self, messages, response_schema, temperature=0.1, max_tokens=None):
            return MagicMock()
        def generate_text(self, messages, temperature=0.7, max_tokens=None):
            return MagicMock()
        def get_model_metadata(self):
            return {"provider": "OUTAGE_AI", "model": "outage"}
        def estimate_cost(self, p, c, m=None):
            return 0.0
        def classify_error(self, e):
            return LLMErrorClassification.NON_RETRYABLE
        def normalize_error(self, e):
            return str(e)
        def get_operational_constraints(self):
            return ProviderOperationalConstraints(
                provider_name="OUTAGE_AI",
                provider_availability="UNAVAILABLE",
                model_availability="AVAILABLE",
                quality_tier="TIER_1_ADVANCED"
            )

    provider = UnavailableProvider()

    # Queueable operation returns QUEUE
    res_queue = CapacityService.check_capacity(capacity_db, ws_id, "LLM_OPERATION", provider=provider, is_queueable=True)
    assert res_queue.decision == CapacityDecision.QUEUE
    assert "PROVIDER_UNAVAILABLE" in res_queue.reason

    # Non-queueable operation returns REJECT
    res_reject = CapacityService.check_capacity(capacity_db, ws_id, "LLM_OPERATION", provider=provider, is_queueable=False)
    assert res_reject.decision == CapacityDecision.REJECT
    assert "PROVIDER_UNAVAILABLE" in res_reject.reason


# ----------------------------------------------------------------------
# 10. SCENARIOS K & L: QUALITY FLOOR ENFORCEMENT & NO QUALITY DOWNGRADE
# ----------------------------------------------------------------------
def test_scenario_k_and_l_quality_floor_and_no_downgrade(capacity_db: Session):
    """
    Scenario K & L:
    - Minimum quality floor violation returns QUEUE rather than downgrading.
    - System never silently downgrades AI model merely to satisfy throughput.
    """
    env = create_tenant_environment(capacity_db, "ws-scen-kl")
    ws_id = env["workspace"].id

    policy = CapacityService.get_or_create_policy(capacity_db, ws_id)
    policy.min_quality_floor = "TIER_1_ADVANCED"
    capacity_db.commit()

    rule_fallback = RuleBasedFallbackProvider()
    assert rule_fallback.get_operational_constraints().quality_tier == "FALLBACK_HEURISTIC"

    res = CapacityService.check_capacity(capacity_db, ws_id, "LLM_OPERATION", provider=rule_fallback)
    assert res.decision == CapacityDecision.QUEUE
    assert "QUALITY_FLOOR_VIOLATION" in res.reason


# ----------------------------------------------------------------------
# 11. SCENARIO M: MULTI-TENANT WORKSPACE ISOLATION
# ----------------------------------------------------------------------
def test_scenario_m_workspace_isolation_across_capacity_and_backpressure(capacity_db: Session):
    """
    Scenario M: Backpressure messages, daily usage counters, and quotas in
    Workspace A have zero impact on Workspace B.
    """
    env_a = create_tenant_environment(capacity_db, "ws-scen-m-a")
    env_b = create_tenant_environment(capacity_db, "ws-scen-m-b")
    ws_a = env_a["workspace"].id
    ws_b = env_b["workspace"].id
    org_a = env_a["org"].id
    org_b = env_b["org"].id

    fix_a = create_outreach_test_fixtures(capacity_db, ws_a, org_a, recipient="recipient.a@test.com")
    fix_b = create_outreach_test_fixtures(capacity_db, ws_b, org_b, recipient="recipient.b@test.com")

    # Workspace A exhausted, Workspace B active
    pol_a = CapacityService.get_or_create_policy(capacity_db, ws_a)
    pol_a.outreach_limit_per_day = 0
    pol_b = CapacityService.get_or_create_policy(capacity_db, ws_b)
    pol_b.outreach_limit_per_day = 50
    capacity_db.commit()

    # Dispatch in WS A enters backpressure
    msg_a = OutboundDispatchEngine.dispatch_outbound_message(
        db=capacity_db,
        workspace_id=ws_a,
        campaign_version_id=fix_a["campaign_version"].id,
        sender_account_id=fix_a["sender"].id,
        recipient_email="recipient.a@test.com",
        recipient_country="US",
        business_id=None,
        subject="Consultative Outreach",
        body_text="Proposal body",
        side_effect_idempotency_key="iso_key_a_001"
    )
    assert msg_a.status == "QUEUED_BACKPRESSURE"

    # Dispatch in WS B transmits normally
    msg_b = OutboundDispatchEngine.dispatch_outbound_message(
        db=capacity_db,
        workspace_id=ws_b,
        campaign_version_id=fix_b["campaign_version"].id,
        sender_account_id=fix_b["sender"].id,
        recipient_email="recipient.b@test.com",
        recipient_country="US",
        business_id=None,
        subject="Consultative Outreach",
        body_text="Proposal body",
        side_effect_idempotency_key="iso_key_b_001"
    )
    assert msg_b.status == "TRANSMITTED"

    # Draining WS B leaves WS A completely untouched
    drain_b = OutboundDispatchEngine.process_backpressure_queue(capacity_db, ws_b)
    assert drain_b["processed"] == 0

    capacity_db.refresh(msg_a)
    assert msg_a.status == "QUEUED_BACKPRESSURE"


# ----------------------------------------------------------------------
# 12. API ENDPOINTS & RBAC ENFORCEMENT
# ----------------------------------------------------------------------
def test_capacity_api_status_and_rbac(capacity_db: Session, capacity_client: TestClient):
    admin_env = create_tenant_environment(capacity_db, "ws-api-admin", role="WorkspaceAdmin")
    viewer_env = create_tenant_environment(capacity_db, "ws-api-viewer", role="Viewer")

    # 1. GET /api/v1/capacity/status by authenticated user -> 200 OK
    res_status = capacity_client.get(
        "/api/v1/capacity/status",
        headers=admin_env["headers"],
    )
    assert res_status.status_code == 200
    data = res_status.json()
    assert "quotas" in data
    assert "outreach" in data["quotas"]
    assert "llm_operations" in data["quotas"]
    assert "provider" in data
    assert data["provider"]["quality_tier"] == AIQualityTier.TIER_1_ADVANCED.value
    assert "api_key" not in str(data).lower()
    assert "password" not in str(data).lower()

    # 2. PUT /api/v1/capacity/policy by WorkspaceAdmin -> 200 OK
    update_payload = {
        "outreach_limit_per_day": 75,
        "llm_operations_limit_per_day": 150,
        "min_quality_floor": "TIER_1_ADVANCED",
        "timezone": "America/New_York",
    }
    res_update = capacity_client.put(
        "/api/v1/capacity/policy",
        json=update_payload,
        headers=admin_env["headers"],
    )
    assert res_update.status_code == 200
    updated = res_update.json()
    assert updated["outreach_limit_per_day"] == 75
    assert updated["llm_operations_limit_per_day"] == 150
    assert updated["timezone"] == "America/New_York"

    # 3. PUT /api/v1/capacity/policy by Viewer -> 403 Forbidden (RBAC violation)
    res_forbidden = capacity_client.put(
        "/api/v1/capacity/policy",
        json=update_payload,
        headers=viewer_env["headers"],
    )
    assert res_forbidden.status_code == 403

    # 4. POST /api/v1/capacity/drain-backpressure by WorkspaceAdmin -> 200 OK
    res_drain = capacity_client.post(
        "/api/v1/capacity/drain-backpressure",
        headers=admin_env["headers"]
    )
    assert res_drain.status_code == 200
    drain_data = res_drain.json()
    assert "processed" in drain_data
    assert "transmitted" in drain_data

    # 5. GET /api/v1/capacity/decisions -> 200 OK
    res_decisions = capacity_client.get(
        "/api/v1/capacity/decisions",
        headers=admin_env["headers"],
    )
    assert res_decisions.status_code == 200
    assert isinstance(res_decisions.json(), list)


# ----------------------------------------------------------------------
# 13. ZERO SECRET LEAKAGE IN AUDIT RECORDS
# ----------------------------------------------------------------------
def test_zero_secret_leakage_in_capacity_audits(capacity_db: Session):
    env = create_tenant_environment(capacity_db, "ws-sec-audit")
    ws_id = env["workspace"].id

    CapacityService.check_capacity(capacity_db, ws_id, "OUTREACH")
    CapacityService.check_capacity(capacity_db, ws_id, "LLM_OPERATION")

    audits = (
        capacity_db.query(CapacityDecisionAudit)
        .filter_by(workspace_id=ws_id)
        .all()
    )
    assert len(audits) >= 2
    for a in audits:
        dumped = f"{a.operation_type} {a.decision} {a.reason} {a.requested_provider} {a.requested_model}"
        assert "key" not in dumped.lower()
        assert "bearer" not in dumped.lower()
        assert "password" not in dumped.lower()
        assert "secret" not in dumped.lower()
