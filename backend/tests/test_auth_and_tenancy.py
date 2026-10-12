import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.main import app
from app.models.tenancy import Organization, Workspace, User, Membership
from app.core.security import get_password_hash

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
def setup_tenants(db_session):
    # Org A
    org_a = Organization(name="Tenant Alpha", slug="tenant-alpha")
    db_session.add(org_a)
    db_session.flush()

    ws_a1 = Workspace(name="Alpha Production", slug="alpha-prod", organization_id=org_a.id)
    ws_a2 = Workspace(name="Alpha Staging", slug="alpha-staging", organization_id=org_a.id)
    db_session.add_all([ws_a1, ws_a2])
    db_session.flush()

    # User A1 (OrgAdmin)
    user_admin = User(
        email="admin@alpha.com",
        hashed_password=get_password_hash("Password123!"),
        full_name="Alpha Admin"
    )
    # User A2 (Operator)
    user_op = User(
        email="operator@alpha.com",
        hashed_password=get_password_hash("Password123!"),
        full_name="Alpha Operator"
    )
    db_session.add_all([user_admin, user_op])
    db_session.flush()

    mem_admin = Membership(user_id=user_admin.id, organization_id=org_a.id, workspace_id=ws_a1.id, role="OrgAdmin")
    mem_op = Membership(user_id=user_op.id, organization_id=org_a.id, workspace_id=ws_a1.id, role="Operator")
    db_session.add_all([mem_admin, mem_op])

    # Org B (Rival Org)
    org_b = Organization(name="Tenant Beta", slug="tenant-beta")
    db_session.add(org_b)
    db_session.flush()

    ws_b1 = Workspace(name="Beta Secret", slug="beta-secret", organization_id=org_b.id)
    db_session.add(ws_b1)
    db_session.commit()

    return {
        "org_a": org_a,
        "ws_a1": ws_a1,
        "ws_a2": ws_a2,
        "user_admin": user_admin,
        "user_op": user_op,
        "org_b": org_b,
        "ws_b1": ws_b1
    }

def test_login_success(client, setup_tenants):
    resp = client.post("/api/v1/auth/login", json={
        "email": "admin@alpha.com",
        "password": "Password123!"
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert data["role"] == "OrgAdmin"
    assert data["organization_id"] == setup_tenants["org_a"].id
    assert data["active_workspace_id"] == setup_tenants["ws_a1"].id

def test_login_invalid_password(client, setup_tenants):
    resp = client.post("/api/v1/auth/login", json={
        "email": "admin@alpha.com",
        "password": "WrongPassword!"
    })
    assert resp.status_code == 401

def test_auth_me(client, setup_tenants):
    login_resp = client.post("/api/v1/auth/login", json={
        "email": "admin@alpha.com",
        "password": "Password123!"
    })
    token = login_resp.json()["access_token"]

    resp = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["email"] == "admin@alpha.com"
    assert resp.json()["role"] == "OrgAdmin"

def test_switch_workspace_within_same_org(client, setup_tenants):
    login_resp = client.post("/api/v1/auth/login", json={
        "email": "admin@alpha.com",
        "password": "Password123!"
    })
    token = login_resp.json()["access_token"]
    ws_a2_id = setup_tenants["ws_a2"].id

    switch_resp = client.post(
        "/api/v1/auth/switch-workspace",
        json={"workspace_id": ws_a2_id},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert switch_resp.status_code == 200
    assert switch_resp.json()["active_workspace_id"] == ws_a2_id

def test_switch_workspace_cross_tenant_idor_blocked(client, setup_tenants):
    """Attempting to switch to workspace belonging to another organization must be strictly rejected with 403."""
    login_resp = client.post("/api/v1/auth/login", json={
        "email": "admin@alpha.com",
        "password": "Password123!"
    })
    token = login_resp.json()["access_token"]
    ws_b1_id = setup_tenants["ws_b1"].id

    switch_resp = client.post(
        "/api/v1/auth/switch-workspace",
        json={"workspace_id": ws_b1_id},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert switch_resp.status_code == 403
    assert "Access Denied: Target workspace belongs to another organization" in switch_resp.json()["detail"]

def test_rbac_workspace_creation_operator_denied(client, setup_tenants):
    """Operator role cannot create workspaces; requires OrgAdmin or SuperAdmin."""
    login_resp = client.post("/api/v1/auth/login", json={
        "email": "operator@alpha.com",
        "password": "Password123!"
    })
    token = login_resp.json()["access_token"]

    resp = client.post(
        "/api/v1/workspaces",
        json={"name": "Alpha Rogue", "slug": "alpha-rogue"},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 403
    assert "Operation requires one of roles ['SuperAdmin', 'OrgAdmin']" in resp.json()["detail"]

def test_rbac_workspace_creation_orgadmin_allowed(client, setup_tenants):
    login_resp = client.post("/api/v1/auth/login", json={
        "email": "admin@alpha.com",
        "password": "Password123!"
    })
    token = login_resp.json()["access_token"]

    resp = client.post(
        "/api/v1/workspaces",
        json={"name": "Alpha New Workspace", "slug": "alpha-new"},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    assert resp.json()["slug"] == "alpha-new"
    assert resp.json()["organization_id"] == setup_tenants["org_a"].id

def test_workspace_listing_tenant_isolation(client, setup_tenants):
    login_resp = client.post("/api/v1/auth/login", json={
        "email": "admin@alpha.com",
        "password": "Password123!"
    })
    token = login_resp.json()["access_token"]

    resp = client.get("/api/v1/workspaces", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    workspaces = resp.json()
    # Should only contain Alpha workspaces, never Beta
    slugs = [w["slug"] for w in workspaces]
    assert "alpha-prod" in slugs
    assert "alpha-staging" in slugs
    assert "beta-secret" not in slugs
