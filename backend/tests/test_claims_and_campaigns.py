import pytest
from datetime import datetime, timezone
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.core.database import Base, get_db
from app.core.security import get_password_hash, create_access_token
from app.models.tenancy import Organization, Workspace, User, Membership
from app.models.business import MasterBusinessRecord
from app.models.opportunity import ProblemHypothesis
from app.models.campaign import Campaign, CampaignVersion, ApprovedSalesClaimRecord
from app.services.claim_governance import ClaimGovernanceService

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
def test_data():
    db = TestingSessionLocal()
    org = Organization(id="org_gov", name="Governance Org", slug="gov-org")
    ws = Workspace(id="ws_gov", organization_id="org_gov", name="Gov WS", slug="gov-ws")
    ws_other = Workspace(id="ws_other", organization_id="org_gov", name="Other WS", slug="other-ws")
    user = User(
        id="u_op",
        email="operator@gov.test",
        hashed_password=get_password_hash("Secret123!"),
        full_name="Operator Jane"
    )
    db.add_all([org, ws, ws_other, user])
    db.commit()

    m = Membership(id="m_gov", user_id="u_op", organization_id="org_gov", workspace_id="ws_gov", role="WorkspaceAdmin")
    db.add(m)
    db.commit()

    biz = MasterBusinessRecord(
        id="biz_c1",
        workspace_id="ws_gov",
        name="Apex Clinic",
        normalized_name="apex clinic",
        category="HEALTHCARE",
        source_provider="LOCAL_DIRECTORY",
        primary_email="apex@clinic.test"
    )
    db.add(biz)
    db.commit()

    hyp = ProblemHypothesis(
        id="hyp_c1",
        workspace_id="ws_gov",
        business_id="biz_c1",
        category="MISSED_CALLS_AFTER_HOURS",
        title="Unanswered emergency inquiries",
        description="Patients cannot reach clinic after 6 PM",
        status="VALIDATED",
        confidence=0.92,
        supporting_evidence_ids=["ev_1"]
    )
    db.add(hyp)
    db.commit()
    db.close()
    return {"org_id": "org_gov", "workspace_id": "ws_gov", "user_id": "u_op"}

def test_claim_creation_and_tamper_detection(test_data):
    db = TestingSessionLocal()
    ws_id = test_data["workspace_id"]
    u_id = test_data["user_id"]

    camp = ClaimGovernanceService.create_campaign(db, ws_id, "Q4 Dental Campaign")
    ver = ClaimGovernanceService.create_campaign_version(db, ws_id, camp.id, [{"step": 1, "type": "EMAIL"}])

    claim = ClaimGovernanceService.create_claim(
        db,
        ws_id,
        ver.id,
        "hyp_c1",
        "Your clinic is losing off-hours patients due to unhandled phone inquiries.",
        ["ev_1"]
    )
    assert claim.approval_status == "PROPOSED"
    assert claim.claim_version == 1
    assert claim.verify_integrity() is True

    # Tamper check: directly modify claim_text in database
    claim.claim_text = "Your clinic is losing $50,000 every single month guaranteed!"
    db.commit()

    assert claim.verify_integrity() is False

    # Attempting to approve a tampered claim must fail
    with pytest.raises(ValueError, match="integrity compromised"):
        ClaimGovernanceService.approve_claim(db, ws_id, claim.id, u_id)

    db.close()

def test_operator_approval_and_version_supersession(test_data):
    db = TestingSessionLocal()
    ws_id = test_data["workspace_id"]
    u_id = test_data["user_id"]

    camp = ClaimGovernanceService.create_campaign(db, ws_id, "Multi-Version Campaign")
    ver1 = ClaimGovernanceService.create_campaign_version(db, ws_id, camp.id)
    claim1 = ClaimGovernanceService.create_claim(
        db, ws_id, ver1.id, "hyp_c1", "Evidence indicates off-hours calls are unhandled.", ["ev_1"]
    )

    # Approve claim1
    approved_claim = ClaimGovernanceService.approve_claim(db, ws_id, claim1.id, u_id)
    assert approved_claim.approval_status == "OPERATOR_APPROVED"
    assert approved_claim.approved_by_user_id == u_id
    assert approved_claim.approved_at is not None

    # Approve version 1
    app_ver1 = ClaimGovernanceService.approve_campaign_version(db, ws_id, ver1.id, u_id)
    assert app_ver1.status == "OPERATOR_APPROVED"
    assert app_ver1.approval_hash is not None

    # Create version 2
    ver2 = ClaimGovernanceService.create_campaign_version(db, ws_id, camp.id)
    claim2 = ClaimGovernanceService.create_claim(
        db, ws_id, ver2.id, "hyp_c1", "Updated diagnostic: off-hours phone routing gap.", ["ev_1"]
    )
    ClaimGovernanceService.approve_claim(db, ws_id, claim2.id, u_id)
    app_ver2 = ClaimGovernanceService.approve_campaign_version(db, ws_id, ver2.id, u_id)

    assert app_ver2.status == "OPERATOR_APPROVED"
    # Version 1 must now be SUPERSEDED
    db.refresh(ver1)
    assert ver1.status == "SUPERSEDED"
    db.close()

def test_claim_mutation_requires_fresh_approval(test_data):
    db = TestingSessionLocal()
    ws_id = test_data["workspace_id"]
    u_id = test_data["user_id"]

    camp = ClaimGovernanceService.create_campaign(db, ws_id, "Mutation Campaign")
    ver = ClaimGovernanceService.create_campaign_version(db, ws_id, camp.id)
    claim = ClaimGovernanceService.create_claim(
        db, ws_id, ver.id, "hyp_c1", "Original claim statement.", ["ev_1"]
    )
    ClaimGovernanceService.approve_claim(db, ws_id, claim.id, u_id)
    assert claim.approval_status == "OPERATOR_APPROVED"

    # Mutate claim
    new_claim = ClaimGovernanceService.mutate_claim_text(
        db, ws_id, claim.id, "Refined claim statement with additional precision."
    )
    # Old claim is superseded
    db.refresh(claim)
    assert claim.approval_status == "SUPERSEDED"

    # New claim is in PROPOSED status and claim_version = 2
    assert new_claim.approval_status == "PROPOSED"
    assert new_claim.claim_version == 2
    assert new_claim.verify_integrity() is True

    # Gates 1-4 check fails because new claim is not approved
    ok, err = ClaimGovernanceService.verify_outbound_claim_gates(db, ws_id, ver.id, [new_claim.id])
    assert ok is False
    assert "GATE" in err
    db.close()

def test_authoritative_gates_1_to_4_verification(test_data):
    db = TestingSessionLocal()
    ws_id = test_data["workspace_id"]
    u_id = test_data["user_id"]

    camp = ClaimGovernanceService.create_campaign(db, ws_id, "Gates 1-4 Campaign")
    ver = ClaimGovernanceService.create_campaign_version(db, ws_id, camp.id)
    claim = ClaimGovernanceService.create_claim(
        db, ws_id, ver.id, "hyp_c1", "Automated booking gap confirmed.", ["ev_1"]
    )

    # Gate 1 Fail: CampaignVersion is in DRAFT status
    ok, err = ClaimGovernanceService.verify_outbound_claim_gates(db, ws_id, ver.id, [claim.id])
    assert ok is False
    assert "GATE 1 VIOLATION" in err

    # Approve claim first
    ClaimGovernanceService.approve_claim(db, ws_id, claim.id, u_id)
    # Approve version
    ClaimGovernanceService.approve_campaign_version(db, ws_id, ver.id, u_id)

    # Gate 2 Fail: Requesting a non-existent claim id
    ok, err = ClaimGovernanceService.verify_outbound_claim_gates(db, ws_id, ver.id, [claim.id, "non_existent_id"])
    assert ok is False
    assert "GATE 2 VIOLATION" in err

    # All 4 Gates Pass!
    ok, err = ClaimGovernanceService.verify_outbound_claim_gates(db, ws_id, ver.id, [claim.id])
    assert ok is True
    assert err is None

    # Gate 4 Fail: Tampering with claim text after operator approval
    claim.claim_text = "Illegally altered claim text after approval"
    db.commit()
    ok, err = ClaimGovernanceService.verify_outbound_claim_gates(db, ws_id, ver.id, [claim.id])
    assert ok is False
    assert "GATE 4 VIOLATION" in err
    db.close()

def test_campaign_and_claim_api_with_rbac(test_data):
    token = create_access_token(
        user_id=test_data["user_id"],
        organization_id=test_data["org_id"],
        active_workspace_id=test_data["workspace_id"],
        role="WorkspaceAdmin"
    )
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Create Campaign via API
    res = client.post("/api/v1/campaigns", json={"name": "API Campaign", "description": "Testing via HTTP"}, headers=headers)
    assert res.status_code == 200
    camp_data = res.json()
    camp_id = camp_data["id"]
    assert camp_data["status"] == "DRAFT"

    # 2. Create Version via API
    res = client.post(f"/api/v1/campaigns/{camp_id}/versions", json={"sequence_steps": [{"step": 1}]}, headers=headers)
    assert res.status_code == 200
    ver_data = res.json()
    ver_id = ver_data["id"]
    assert ver_data["version_number"] == 1
    assert ver_data["status"] == "DRAFT"

    # 3. Create Claim via API
    res = client.post("/api/v1/claims", json={
        "campaign_version_id": ver_id,
        "hypothesis_id": "hyp_c1",
        "claim_text": "Audit proves lack of online scheduling.",
        "evidence_ids": ["ev_1"]
    }, headers=headers)
    assert res.status_code == 200
    claim_data = res.json()
    claim_id = claim_data["id"]
    assert claim_data["approval_status"] == "PROPOSED"
    assert claim_data["integrity_valid"] is True

    # 4. Approve Claim via API
    res = client.post(f"/api/v1/claims/{claim_id}/approve", headers=headers)
    assert res.status_code == 200
    app_claim = res.json()
    assert app_claim["approval_status"] == "OPERATOR_APPROVED"
    assert app_claim["approved_by_user_id"] == test_data["user_id"]

    # 5. Approve Version via API (Gate 1 Operator Approval)
    res = client.post(f"/api/v1/campaigns/{camp_id}/versions/{ver_id}/approve", headers=headers)
    assert res.status_code == 200
    app_ver = res.json()
    assert app_ver["status"] == "OPERATOR_APPROVED"
    assert app_ver["approval_hash"] is not None

    # 6. Cross-tenant isolation check: Attempt to access from unassigned workspace
    other_token = create_access_token(
        user_id=test_data["user_id"],
        organization_id=test_data["org_id"],
        active_workspace_id="ws_unauthorized",
        role="WorkspaceAdmin"
    )
    res = client.get("/api/v1/campaigns", headers={"Authorization": f"Bearer {other_token}"})
    assert res.status_code == 403
