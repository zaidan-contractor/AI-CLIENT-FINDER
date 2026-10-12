import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.core.database import SessionLocal
from app.core.security import get_password_hash, create_access_token, decode_access_token
from app.models.tenancy import User, Organization, Workspace, Membership

@pytest.fixture
def client():
    return TestClient(app)

@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()

@pytest.fixture(autouse=True)
def setup_auth_test_entities(db: Session):
    """Ensure test entities exist and are clean."""
    org = db.query(Organization).filter_by(id="org_demo").first()
    if not org:
        org = Organization(id="org_demo", name="Omni Enterprises", slug="omni-enterprises")
        db.add(org)
        db.flush()

    ws1 = db.query(Workspace).filter_by(id="ws_demo_1").first()
    if not ws1:
        ws1 = Workspace(id="ws_demo_1", name="Austin Healthcare", slug="austin-healthcare", organization_id="org_demo", is_active=True)
        db.add(ws1)

    ws2 = db.query(Workspace).filter_by(id="ws_demo_2").first()
    if not ws2:
        ws2 = Workspace(id="ws_demo_2", name="Dallas Logistics", slug="dallas-logistics", organization_id="org_demo", is_active=True)
        db.add(ws2)

    # Isolated other organization for cross-tenant testing
    other_org = db.query(Organization).filter_by(id="org_other_tenant").first()
    if not other_org:
        other_org = Organization(id="org_other_tenant", name="Other Org", slug="other-org")
        db.add(other_org)
        db.flush()

    other_ws = db.query(Workspace).filter_by(id="ws_other_tenant").first()
    if not other_ws:
        other_ws = Workspace(id="ws_other_tenant", name="Other Workspace", slug="other-ws", organization_id="org_other_tenant", is_active=True)
        db.add(other_ws)

    # Active demo admin
    user = db.query(User).filter_by(email="admin@omni.com").first()
    if not user:
        user = User(
            id="usr_demo_admin",
            email="admin@omni.com",
            hashed_password=get_password_hash("AdminPass123!"),
            full_name="Admin Operator",
            is_active=True
        )
        db.add(user)
        db.flush()
    else:
        user.hashed_password = get_password_hash("AdminPass123!")
        user.is_active = True
        db.flush()

    mem1 = db.query(Membership).filter_by(user_id=user.id, workspace_id="ws_demo_1").first()
    if not mem1:
        db.add(Membership(id="mem_demo_1", user_id=user.id, organization_id="org_demo", workspace_id="ws_demo_1", role="WorkspaceAdmin"))
    mem2 = db.query(Membership).filter_by(user_id=user.id, workspace_id="ws_demo_2").first()
    if not mem2:
        db.add(Membership(id="mem_demo_2", user_id=user.id, organization_id="org_demo", workspace_id="ws_demo_2", role="WorkspaceAdmin"))

    # Inactive test user
    inactive = db.query(User).filter_by(email="inactive@omni.com").first()
    if not inactive:
        inactive = User(
            id="usr_inactive_test",
            email="inactive@omni.com",
            hashed_password=get_password_hash("AdminPass123!"),
            full_name="Inactive User",
            is_active=False
        )
        db.add(inactive)
        db.flush()
        db.add(Membership(id="mem_inactive_1", user_id=inactive.id, organization_id="org_demo", workspace_id="ws_demo_1", role="Operator"))

    db.commit()


def test_login_success_with_demo_credentials(client: TestClient):
    """1. Correct demo credentials -> successful login."""
    response = client.post("/api/v1/auth/login", json={
        "email": "admin@omni.com",
        "password": "AdminPass123!"
    })
    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"
    assert data["user_id"] == "usr_demo_admin"
    assert data["organization_id"] == "org_demo"
    assert data["active_workspace_id"] in ["ws_demo_1", "ws_demo_2"]
    assert data["role"] == "WorkspaceAdmin"


def test_login_fails_with_wrong_password(client: TestClient):
    """2. Wrong password -> 401."""
    response = client.post("/api/v1/auth/login", json={
        "email": "admin@omni.com",
        "password": "IncorrectPassword999!"
    })
    assert response.status_code == 401
    assert "Incorrect email or password." in response.json()["detail"]


def test_login_fails_with_unknown_email(client: TestClient):
    """3. Unknown email -> 401."""
    response = client.post("/api/v1/auth/login", json={
        "email": "nonexistent_user@omni.com",
        "password": "AdminPass123!"
    })
    assert response.status_code == 401
    assert "Incorrect email or password." in response.json()["detail"]


def test_login_fails_for_inactive_user(client: TestClient):
    """4. Inactive user -> rejected."""
    response = client.post("/api/v1/auth/login", json={
        "email": "inactive@omni.com",
        "password": "AdminPass123!"
    })
    assert response.status_code == 401
    assert "deactivated" in response.json()["detail"].lower()


def test_demo_user_workspace_memberships_resolve_correctly(client: TestClient):
    """5. Demo user's workspace memberships resolve correctly."""
    # Login to get token
    login_res = client.post("/api/v1/auth/login", json={
        "email": "admin@omni.com",
        "password": "AdminPass123!"
    })
    token = login_res.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Query /me
    me_res = client.get("/api/v1/auth/me", headers=headers)
    assert me_res.status_code == 200
    me_data = me_res.json()
    assert me_data["email"] == "admin@omni.com"
    assert me_data["organization_id"] == "org_demo"

    # Query /workspaces
    ws_res = client.get("/api/v1/auth/workspaces", headers=headers)
    assert ws_res.status_code == 200
    ws_list = ws_res.json()
    ws_ids = [w["id"] for w in ws_list]
    assert "ws_demo_1" in ws_ids
    assert "ws_demo_2" in ws_ids


def test_cross_workspace_access_remains_blocked(client: TestClient):
    """6. Cross-workspace / cross-org access remains blocked."""
    login_res = client.post("/api/v1/auth/login", json={
        "email": "admin@omni.com",
        "password": "AdminPass123!"
    })
    token = login_res.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Try switching to workspace in another organization
    switch_res = client.post(
        "/api/v1/auth/switch-workspace",
        json={"workspace_id": "ws_other_tenant"},
        headers=headers
    )
    assert switch_res.status_code == 403
    assert "Access Denied" in switch_res.json()["detail"]


def test_token_and_session_generated_correctly(client: TestClient):
    """7. Token/session generated correctly."""
    login_res = client.post("/api/v1/auth/login", json={
        "email": "admin@omni.com",
        "password": "AdminPass123!"
    })
    token = login_res.json()["access_token"]
    payload = decode_access_token(token)
    assert payload["sub"] == "usr_demo_admin"
    assert payload["organization_id"] == "org_demo"
    assert payload["role"] == "WorkspaceAdmin"
    assert "exp" in payload
    assert "iat" in payload


def test_frontend_login_contract_compatibility(client: TestClient):
    """8. Frontend/API login contract remains compatible."""
    # Frontend sends JSON: { email, password }
    frontend_payload = {
        "email": "admin@omni.com",
        "password": "AdminPass123!"
    }
    response = client.post("/api/v1/auth/login", json=frontend_payload)
    assert response.status_code == 200
    data = response.json()

    # Frontend app.js expects data.access_token and data.active_workspace_id
    assert "access_token" in data
    assert isinstance(data["access_token"], str)
    assert len(data["access_token"]) > 20
    assert "active_workspace_id" in data
    assert data["active_workspace_id"] != ""
