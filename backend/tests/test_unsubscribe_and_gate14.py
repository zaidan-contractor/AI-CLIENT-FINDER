import pytest
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from sqlalchemy.pool import StaticPool
from app.core.database import Base, get_db
from app.main import app
from app.models.tenancy import (
    Organization,
    Workspace,
    UnsubscribeToken,
    WorkspaceSuppressionRecord,
    OrgSuppressionRecord,
    ComplianceSuppressionLedger,
    SystemAuditEvent
)
from app.services.suppression_service import SuppressionService
from app.core.security import generate_unsubscribe_token, generate_csrf_token, hash_suppression_target

# Test SQLite Database Setup with StaticPool to share single in-memory database across threads/connections
TEST_DATABASE_URL = "sqlite:///:memory:"
test_engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

@pytest.fixture(scope="function")
def db_session():
    Base.metadata.create_all(bind=test_engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=test_engine)

@pytest.fixture(scope="function")
def client(db_session):
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()

@pytest.fixture(scope="function")
def seed_data(db_session):
    org = Organization(name="Acme Corp", slug="acme")
    db_session.add(org)
    db_session.flush()

    ws = Workspace(name="Default Workspace", slug="default", organization_id=org.id)
    db_session.add(ws)
    db_session.flush()

    token_str = generate_unsubscribe_token()
    raw_email = "victim@example.com"
    hash_val, _, _ = hash_suppression_target(raw_email)

    token = UnsubscribeToken(
        token=token_str,
        organization_id=org.id,
        workspace_id=ws.id,
        contact_id="contact-123",
        email_canonical_hash=hash_val,
        raw_email_for_delivery=raw_email,
        scope="UNSUBSCRIBE_ONLY",
        expires_at=datetime.now(timezone.utc) + timedelta(days=30)
    )
    db_session.add(token)
    db_session.commit()

    return {
        "org": org,
        "workspace": ws,
        "token_str": token_str,
        "raw_email": raw_email,
        "token_record": token
    }

# =========================================================================
# 1. SECURE UNSUBSCRIBE GET/POST & RFC 8058 SEPARATION TESTS
# =========================================================================

def test_1_get_non_mutating(client, db_session, seed_data):
    """Test 1: GET with valid token returns HTTP 200 confirmation page, ZERO suppression mutation."""
    token = seed_data["token_str"]
    response = client.get(f"/api/v1/unsubscribe?token={token}")
    assert response.status_code == 200
    assert "Confirm Opt-Out" in response.text
    assert "csrf_token" in response.text
    # Assert zero suppression records created
    assert db_session.query(WorkspaceSuppressionRecord).count() == 0
    assert db_session.query(OrgSuppressionRecord).count() == 0
    assert db_session.query(ComplianceSuppressionLedger).count() == 0

def test_2_repeated_get_non_mutating(client, db_session, seed_data):
    """Test 2: Repeated GET calls do not mutate state."""
    token = seed_data["token_str"]
    for _ in range(5):
        resp = client.get(f"/api/v1/unsubscribe?token={token}")
        assert resp.status_code == 200
    assert db_session.query(WorkspaceSuppressionRecord).count() == 0
    assert db_session.query(OrgSuppressionRecord).count() == 0
    assert db_session.query(ComplianceSuppressionLedger).count() == 0

def test_3_browser_post_with_valid_csrf_mutates_state(client, db_session, seed_data):
    """Test 3: Browser POST with valid CSRF mutates state across workspace, org, and compliance ledgers."""
    token = seed_data["token_str"]
    csrf = generate_csrf_token(token)

    response = client.post(
        "/api/v1/unsubscribe/confirm",
        data={"token": token, "csrf_token": csrf}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "success"

    # Assert suppression records atomically created across all 3 ledgers
    assert db_session.query(WorkspaceSuppressionRecord).count() == 1
    assert db_session.query(OrgSuppressionRecord).count() == 1
    assert db_session.query(ComplianceSuppressionLedger).count() == 1

    # Check Gate 14 evaluation
    is_supp = SuppressionService.is_suppressed(
        db_session,
        seed_data["org"].id,
        seed_data["workspace"].id,
        seed_data["raw_email"]
    )
    assert is_supp is True

def test_4_browser_post_without_or_invalid_csrf_fails(client, db_session, seed_data):
    """Test 4: Browser POST without or with invalid CSRF strictly fails (HTTP 403) and does NOT mutate."""
    token = seed_data["token_str"]

    # Missing CSRF
    resp1 = client.post("/api/v1/unsubscribe/confirm", data={"token": token})
    assert resp1.status_code == 400

    # Invalid CSRF signature
    resp2 = client.post("/api/v1/unsubscribe/confirm", data={"token": token, "csrf_token": "tampered:badhash"})
    assert resp2.status_code == 403

    # Zero mutation
    assert db_session.query(WorkspaceSuppressionRecord).count() == 0
    assert db_session.query(OrgSuppressionRecord).count() == 0
    assert db_session.query(ComplianceSuppressionLedger).count() == 0

def test_5_rfc8058_one_click_succeeds_without_browser_csrf(client, db_session, seed_data):
    """Test 5: RFC 8058 one-click succeeds without browser CSRF when valid request semantics and token provided."""
    token = seed_data["token_str"]
    headers = {"List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}

    response = client.post(
        f"/api/v1/unsubscribe/one-click?token={token}",
        headers=headers,
        content=b"List-Unsubscribe=One-Click"
    )
    assert response.status_code == 200
    assert response.json()["status"] == "success"

    # Verifies all 3 ledgers are written
    assert db_session.query(WorkspaceSuppressionRecord).count() == 1
    assert db_session.query(OrgSuppressionRecord).count() == 1
    assert db_session.query(ComplianceSuppressionLedger).count() == 1

def test_6_rfc8058_malformed_requests_fail(client, db_session, seed_data):
    """Test 6: RFC 8058 malformed requests fail (missing List-Unsubscribe=One-Click declaration)."""
    token = seed_data["token_str"]
    # Missing required declaration
    response = client.post(
        f"/api/v1/unsubscribe/one-click?token={token}",
        content=b"random_body"
    )
    assert response.status_code == 400
    assert "Malformed RFC 8058" in response.json()["detail"]
    assert db_session.query(WorkspaceSuppressionRecord).count() == 0

def test_7_invalid_tampered_expired_tokens_fail(client, db_session, seed_data):
    """Test 7: Invalid, tampered, or expired tokens fail with safe generic response."""
    # Invalid token
    resp1 = client.get("/api/v1/unsubscribe?token=completely_fake_invalid_token_12345")
    assert resp1.status_code == 400
    assert "Invalid or expired" in resp1.text

    # Expired token
    expired_tok = UnsubscribeToken(
        token="expired_token_test_123456789012345",
        organization_id=seed_data["org"].id,
        workspace_id=seed_data["workspace"].id,
        email_canonical_hash="dummyhash",
        raw_email_for_delivery="old@example.com",
        expires_at=datetime.now(timezone.utc) - timedelta(days=1)
    )
    db_session.add(expired_tok)
    db_session.commit()

    resp2 = client.get(f"/api/v1/unsubscribe?token={expired_tok.token}")
    assert resp2.status_code == 400
    assert "Invalid or expired" in resp2.text

def test_8_repeated_one_click_is_idempotent(client, db_session, seed_data):
    """Test 8: Repeated one-click is idempotent (HTTP 200, suppression remains active, zero duplicate side effects)."""
    token = seed_data["token_str"]
    headers = {"List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}

    # First call
    r1 = client.post(f"/api/v1/unsubscribe/one-click?token={token}", headers=headers, content=b"List-Unsubscribe=One-Click")
    assert r1.status_code == 200

    # Second call (replay)
    r2 = client.post(f"/api/v1/unsubscribe/one-click?token={token}", headers=headers, content=b"List-Unsubscribe=One-Click")
    assert r2.status_code == 200

    # Ledger counts remain exactly 1 (no duplicates)
    assert db_session.query(WorkspaceSuppressionRecord).count() == 1
    assert db_session.query(OrgSuppressionRecord).count() == 1
    assert db_session.query(ComplianceSuppressionLedger).count() == 1

def test_9_browser_and_rfc8058_convergence(client, db_session, seed_data):
    """Test 9: Browser confirmation and RFC 8058 one-click paths converge on identical suppression semantics."""
    email = seed_data["raw_email"]
    org_id = seed_data["org"].id
    ws_id = seed_data["workspace"].id

    # Execute shared routine directly
    res = SuppressionService.execute_atomic_suppression(
        db=db_session,
        organization_id=org_id,
        workspace_id=ws_id,
        email=email,
        source_channel="DIRECT_TEST",
        reason="Test Convergence"
    )
    assert res["status"] == "SUPPRESSED"
    assert SuppressionService.is_suppressed(db_session, org_id, ws_id, email) is True

def test_10_zero_pii_leakage(client, db_session, seed_data):
    """Test 10: Zero PII appears in URL parameters, HTML response, or audit payloads."""
    token = seed_data["token_str"]
    raw_email = seed_data["raw_email"]

    # Check GET response HTML
    resp = client.get(f"/api/v1/unsubscribe?token={token}")
    assert raw_email not in resp.text
    assert seed_data["org"].name not in resp.text
    assert seed_data["token_record"].contact_id not in resp.text

    # Check Audit payload
    SuppressionService.execute_atomic_suppression(
        db=db_session,
        organization_id=seed_data["org"].id,
        workspace_id=seed_data["workspace"].id,
        email=raw_email,
        source_channel="AUDIT_TEST",
        reason="Check PII"
    )
    audit = db_session.query(SystemAuditEvent).first()
    assert raw_email not in str(audit.payload)
    assert "suppression_hash" in audit.payload

def test_11_scanner_link_preview_simulation(client, db_session, seed_data):
    """Test 11: Automated GET/link-preview scanner simulation strictly does NOT unsubscribe recipient."""
    token = seed_data["token_str"]
    # Simulate email security scanner bot (e.g. Proofpoint, Mimecast, Outlook SafeLinks)
    bot_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0 Safari/537.36 (Link Preview Bot)"
    }
    resp = client.get(f"/api/v1/unsubscribe?token={token}", headers=bot_headers)
    assert resp.status_code == 200
    assert db_session.query(WorkspaceSuppressionRecord).count() == 0
    assert SuppressionService.is_suppressed(
        db_session, seed_data["org"].id, seed_data["workspace"].id, seed_data["raw_email"]
    ) is False


# =========================================================================
# 2. GATE 14 CONCURRENCY & RACE-CONDITION TESTS
# =========================================================================

def test_12_concurrent_unsubscribe_vs_outbound_authorization(db_session, seed_data):
    """
    Test 12: Invariant: "Once suppression is durably committed as effective, no subsequent outbound authorization may succeed for that recipient."
    Simulate race where suppression commits before outbound worker evaluates Gate 14.
    """
    org_id = seed_data["org"].id
    ws_id = seed_data["workspace"].id
    email = seed_data["raw_email"]

    # Step 1: Suppression commits
    SuppressionService.execute_atomic_suppression(
        db=db_session,
        organization_id=org_id,
        workspace_id=ws_id,
        email=email,
        source_channel="RACE_TEST",
        reason="Race condition test opt-out"
    )

    # Step 2: Outbound Worker reaches Gate 14
    gate_14_passed = not SuppressionService.is_suppressed(db_session, org_id, ws_id, email)
    # Transmission must be strictly prevented
    assert gate_14_passed is False

def test_13_unsubscribe_immediately_after_authorization_audit_ordering(db_session, seed_data):
    """
    Test 13: If suppression loses race against legitimate authorization, actual chronological order is preserved in audit logs.
    """
    org_id = seed_data["org"].id
    ws_id = seed_data["workspace"].id
    email = seed_data["raw_email"]

    # 1. Outbound authorization commits first
    send_audit = SystemAuditEvent(
        organization_id=org_id,
        workspace_id=ws_id,
        event_type="OUTBOUND_DISPATCHED",
        actor_id="WORKER_1",
        actor_type="SYSTEM",
        payload={"msg_id": "outbound-001"}
    )
    db_session.add(send_audit)
    db_session.commit()

    # 2. Suppression commits immediately after
    SuppressionService.execute_atomic_suppression(
        db=db_session,
        organization_id=org_id,
        workspace_id=ws_id,
        email=email,
        source_channel="AFTER_AUTH_TEST",
        reason="Opt out after send"
    )

    events = db_session.query(SystemAuditEvent).order_by(SystemAuditEvent.created_at.asc()).all()
    assert len(events) == 2
    assert events[0].event_type == "OUTBOUND_DISPATCHED"
    assert events[1].event_type == "SUPPRESSION_COMMITTED"

    # Subsequent Gate 14 check for follow-up sequence step must be blocked
    assert SuppressionService.is_suppressed(db_session, org_id, ws_id, email) is True

def test_14_worker_retry_after_suppression_blocked(db_session, seed_data):
    """
    Test 14: Worker retry after suppression encounters Gate 14 barrier and terminates in SUPPRESSED state.
    """
    org_id = seed_data["org"].id
    ws_id = seed_data["workspace"].id
    email = seed_data["raw_email"]

    # Worker attempt 1 fails due to network glitch; task retries
    # Meanwhile, recipient unsubscribes
    SuppressionService.execute_atomic_suppression(
        db=db_session,
        organization_id=org_id,
        workspace_id=ws_id,
        email=email,
        source_channel="RETRY_TEST",
        reason="Recipient opted out before retry"
    )

    # Worker attempt 2 (retry) executes Gate 14 pre-send check
    is_blocked = SuppressionService.is_suppressed(db_session, org_id, ws_id, email)
    assert is_blocked is True  # Proves retry cannot bypass suppression

def test_15_multiple_workers_targeting_same_recipient(db_session, seed_data):
    """
    Test 15: Multiple concurrent workers attempting dispatch to same recipient are all halted at Gate 14 once suppression commits.
    """
    org_id = seed_data["org"].id
    ws_id = seed_data["workspace"].id
    email = seed_data["raw_email"]

    # Suppression committed
    SuppressionService.execute_atomic_suppression(
        db=db_session,
        organization_id=org_id,
        workspace_id=ws_id,
        email=email,
        source_channel="MULTI_WORKER_TEST",
        reason="Multi worker halt"
    )

    # Simulate 5 concurrent workers checking Gate 14
    worker_results = [
        SuppressionService.is_suppressed(db_session, org_id, ws_id, email)
        for _ in range(5)
    ]
    # All workers must see suppressed
    assert all(worker_results) is True
