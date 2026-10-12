import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.main import app
from app.models.tenancy import Organization, Workspace, User, Membership
from app.models.business import MasterBusinessRecord, ContactRecord, MarketTarget
from app.core.security import get_password_hash
from app.core.currency import seed_currency_registry, is_valid_currency_code, get_currency_minor_units
from app.services.market_engine import MarketEngine, canonicalize_domain, normalize_business_name

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
def tenant_fixture(db_session):
    seed_currency_registry(db_session)

    org = Organization(name="Omni Discovery Org", slug="omni-discovery")
    db_session.add(org)
    db_session.flush()

    ws1 = Workspace(name="Austin Market", slug="austin-market", organization_id=org.id)
    ws2 = Workspace(name="Dallas Market", slug="dallas-market", organization_id=org.id)
    db_session.add_all([ws1, ws2])
    db_session.flush()

    user = User(
        email="lead_finder@omni.com",
        hashed_password=get_password_hash("SecretPass123!"),
        full_name="Lead Finder"
    )
    db_session.add(user)
    db_session.flush()

    mem1 = Membership(user_id=user.id, organization_id=org.id, workspace_id=ws1.id, role="WorkspaceAdmin")
    mem2 = Membership(user_id=user.id, organization_id=org.id, workspace_id=ws2.id, role="WorkspaceAdmin")
    db_session.add_all([mem1, mem2])
    db_session.commit()

    return {
        "org": org,
        "ws1": ws1,
        "ws2": ws2,
        "user": user
    }

def test_currency_registry_global_iso4217(db_session):
    seed_currency_registry(db_session)
    assert is_valid_currency_code("USD", db_session) is True
    assert is_valid_currency_code("EUR", db_session) is True
    assert is_valid_currency_code("INR", db_session) is True
    assert is_valid_currency_code("JPY", db_session) is True
    assert is_valid_currency_code("INVALID_XYZ", db_session) is False

    # Minor units precision
    assert get_currency_minor_units("USD", db_session) == 2
    assert get_currency_minor_units("JPY", db_session) == 0

def test_domain_canonicalization():
    assert canonicalize_domain("https://www.example.com/path?query=1") == "example.com"
    assert canonicalize_domain("http://sub.domain.org:8080/index.html") == "sub.domain.org"
    assert canonicalize_domain("WWW.CLINIC.CO.UK/") == "clinic.co.uk"
    assert canonicalize_domain(None) is None

def test_business_name_normalization():
    assert normalize_business_name("  Austin Dental, LLC.  ") == "austin dental llc"
    assert normalize_business_name("Elite & Co. - Services!") == "elite co services"

def test_market_target_creation_and_discovery(client, db_session, tenant_fixture):
    # 1. Login
    login_resp = client.post("/api/v1/auth/login", json={
        "email": "lead_finder@omni.com",
        "password": "SecretPass123!"
    })
    token = login_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 2. Create Market Target
    target_resp = client.post("/api/v1/markets", json={
        "query_text": "dental clinics in Austin",
        "location": "Austin",
        "target_category": "healthcare"
    }, headers=headers)
    assert target_resp.status_code == 200
    target_id = target_resp.json()["id"]

    # 3. Run Discovery
    run_resp = client.post(f"/api/v1/markets/{target_id}/run", headers=headers)
    assert run_resp.status_code == 200
    run_data = run_resp.json()
    assert run_data["status"] == "COMPLETED"
    assert run_data["newly_discovered"] > 0

    # 4. Check Businesses in Workspace
    biz_resp = client.get("/api/v1/businesses", headers=headers)
    assert biz_resp.status_code == 200
    businesses = biz_resp.json()
    assert len(businesses) >= run_data["newly_discovered"]

    # Verify Contacts were automatically created for businesses with primary_email
    first_biz = businesses[0]
    detail_resp = client.get(f"/api/v1/businesses/{first_biz['id']}", headers=headers)
    assert detail_resp.status_code == 200
    detail_data = detail_resp.json()
    if detail_data["primary_email"]:
        assert len(detail_data["contacts"]) >= 1
        assert detail_data["contacts"][0]["email"] == detail_data["primary_email"]

def test_discovery_deduplication(client, db_session, tenant_fixture):
    # Login
    login_resp = client.post("/api/v1/auth/login", json={
        "email": "lead_finder@omni.com",
        "password": "SecretPass123!"
    })
    token = login_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Create target
    target_resp = client.post("/api/v1/markets", json={
        "query_text": "dental clinics",
        "location": "Austin",
        "target_category": "healthcare"
    }, headers=headers)
    target_id = target_resp.json()["id"]

    # First run
    run1 = client.post(f"/api/v1/markets/{target_id}/run", headers=headers).json()
    assert run1["newly_discovered"] > 0
    initial_count = run1["newly_discovered"]

    # Second run on same target
    run2 = client.post(f"/api/v1/markets/{target_id}/run", headers=headers).json()
    assert run2["newly_discovered"] == 0
    assert run2["duplicates_skipped"] == initial_count

def test_workspace_isolation_of_businesses(client, db_session, tenant_fixture):
    ws1_id = tenant_fixture["ws1"].id
    ws2_id = tenant_fixture["ws2"].id

    # Create business directly in ws1
    biz_ws1 = MasterBusinessRecord(
        workspace_id=ws1_id,
        name="Workspace 1 Exclusive Medical",
        normalized_name="workspace 1 exclusive medical",
        website_url="https://ws1medical.example.com",
        canonical_domain="ws1medical.example.com",
        category="healthcare",
        source_provider="MANUAL"
    )
    db_session.add(biz_ws1)
    db_session.commit()

    # Login to ws1
    login_resp = client.post("/api/v1/auth/login", json={
        "email": "lead_finder@omni.com",
        "password": "SecretPass123!"
    })
    token_ws1 = login_resp.json()["access_token"]

    resp1 = client.get("/api/v1/businesses", headers={"Authorization": f"Bearer {token_ws1}"})
    biz_names_ws1 = [b["name"] for b in resp1.json()]
    assert "Workspace 1 Exclusive Medical" in biz_names_ws1

    # Switch to ws2
    switch_resp = client.post(
        "/api/v1/auth/switch-workspace",
        json={"workspace_id": ws2_id},
        headers={"Authorization": f"Bearer {token_ws1}"}
    )
    token_ws2 = switch_resp.json()["access_token"]

    resp2 = client.get("/api/v1/businesses", headers={"Authorization": f"Bearer {token_ws2}"})
    biz_names_ws2 = [b["name"] for b in resp2.json()]
    assert "Workspace 1 Exclusive Medical" not in biz_names_ws2
