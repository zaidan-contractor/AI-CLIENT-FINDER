"""
Regression Test Suite: Lead Qualification and Lead Dossier State/Data Consistency.

Verifies:
1. Exact reproduction scenario:
   research -> evidence -> opportunity -> qualify -> dossier
   - Evidence count matches between qualification and dossier
   - Evidence IDs match
   - Verified contact identity & status matches (server-authoritative Gate 6)
   - Opportunity identity matches (no fallback to "General Digital Opportunity")
   - Workspace_id, business_id, lead_id match
2. Negative scenario:
   - Business with no verified contact and no evidence
   - Dossier displays no contact, zero evidence, and does not invent opportunities
3. Invariant:
   - Lead Dossier is a read-only aggregation view and never downgrades verification
4. Tenant isolation:
   - Cross-workspace dossier access is blocked (HTTP 404)
"""
import uuid
from decimal import Decimal
from datetime import datetime, timezone
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from sqlalchemy.pool import StaticPool

from app.main import app
from app.core.database import Base, get_db
from app.core.security import get_password_hash, create_access_token
from app.models.tenancy import Organization, Workspace, User, Membership, get_utc_now
from app.models.business import MasterBusinessRecord, ContactRecord
from app.models.research import ResearchRun, EvidenceRecord
from app.models.opportunity import ProblemHypothesis, ImpactAssessment, SolutionMapping
from app.models.lead import LeadQualification
from app.services.email_transports.mock_transport import MockEmailTransport
from app.services.email_transports.factory import set_transport_override

# Isolated in-memory SQLite for testing
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
def setup_test_environment():
    mock_transport = MockEmailTransport()
    set_transport_override(mock_transport)
    app.dependency_overrides[get_db] = override_get_db
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)
    app.dependency_overrides.pop(get_db, None)
    set_transport_override(None)

@pytest.fixture
def dossier_env():
    db = TestingSessionLocal()
    org = Organization(id="org_dossier", name="Dossier Org", slug="dossier-org", is_active=True)
    ws1 = Workspace(id="ws_dossier_1", organization_id="org_dossier", name="Workspace 1", slug="ws-1", is_active=True)
    ws2 = Workspace(id="ws_dossier_2", organization_id="org_dossier", name="Workspace 2", slug="ws-2", is_active=True)

    user1 = User(
        id="u_dossier_op1",
        email="operator1@dossier.test",
        hashed_password=get_password_hash("Secret123!"),
        full_name="Dossier Operator 1",
        is_active=True
    )
    user2 = User(
        id="u_dossier_op2",
        email="operator2@dossier.test",
        hashed_password=get_password_hash("Secret123!"),
        full_name="Dossier Operator 2",
        is_active=True
    )
    m1 = Membership(id="m_d_1", user_id="u_dossier_op1", organization_id="org_dossier", workspace_id="ws_dossier_1", role="WorkspaceAdmin")
    m2 = Membership(id="m_d_2", user_id="u_dossier_op2", organization_id="org_dossier", workspace_id="ws_dossier_2", role="WorkspaceAdmin")

    # Business in ws1 (Austin Family Health equivalent)
    biz1 = MasterBusinessRecord(
        id="biz_austin_test",
        workspace_id="ws_dossier_1",
        name="Austin Family Health",
        normalized_name="austin family health",
        canonical_domain="austinfamilyhealth.example.com",
        website_url="https://austinfamilyhealth.example.com",
        phone="512-555-0101",
        primary_email="contact@austinfamilyhealth.example.com",
        address="100 Congress Ave",
        city="Austin",
        category="healthcare",
        source_provider="MANUAL"
    )

    # Empty Business in ws1 (For negative tests)
    biz_empty = MasterBusinessRecord(
        id="biz_empty_test",
        workspace_id="ws_dossier_1",
        name="Unresearched Business",
        normalized_name="unresearched business",
        canonical_domain="unresearched.test",
        website_url="https://unresearched.test",
        city="Austin",
        category="services",
        source_provider="MANUAL"
    )

    # Business in ws2 (For tenant isolation)
    biz2 = MasterBusinessRecord(
        id="biz_ws2_test",
        workspace_id="ws_dossier_2",
        name="Workspace 2 Business",
        normalized_name="workspace 2 business",
        canonical_domain="ws2.test",
        city="Austin",
        category="services",
        source_provider="MANUAL"
    )

    # Contact for biz1
    cnt1 = ContactRecord(
        id="cnt_austin_test_1",
        workspace_id="ws_dossier_1",
        business_id="biz_austin_test",
        email="contact@austinfamilyhealth.example.com",
        canonical_email="contact@austinfamilyhealth.example.com",
        first_name="Alice",
        last_name="Smith",
        role_title="Clinical Director",
        verification_tier="CONFIRMED_VERIFIED",
        freshness_timestamp=get_utc_now(),
        verification_metadata={"source": "Direct MX Handshake", "status": "VERIFIED"}
    )

    # Research Run for biz1
    run1 = ResearchRun(
        id="run_austin_test_1",
        workspace_id="ws_dossier_1",
        business_id="biz_austin_test",
        status="COMPLETED",
        trigger_type="MANUAL",
        evidence_count=2,
        started_at=get_utc_now(),
        completed_at=get_utc_now()
    )

    # Evidence for biz1
    ev1 = EvidenceRecord(
        id="ev_austin_test_1",
        workspace_id="ws_dossier_1",
        business_id="biz_austin_test",
        research_run_id="run_austin_test_1",
        signal_type="DOM_ELEMENT",
        raw_observation="No digital patient registration or appointment form on website.",
        feature_presence_state="NOT_OBSERVED",
        epistemic_tier="FACT",
        classification_reason="Confirmed absence during diagnostic crawl",
        confidence=0.92
    )
    ev2 = EvidenceRecord(
        id="ev_austin_test_2",
        workspace_id="ws_dossier_1",
        business_id="biz_austin_test",
        research_run_id="run_austin_test_1",
        signal_type="REVIEW_SENTIMENT",
        raw_observation="Patients cite telephone hold times exceeding 15 minutes.",
        feature_presence_state="PRESENT",
        epistemic_tier="FACT",
        classification_reason="Review sentiment analysis",
        confidence=0.88
    )

    # Problem Hypothesis for biz1
    hyp1 = ProblemHypothesis(
        id="hyp_austin_test_1",
        workspace_id="ws_dossier_1",
        business_id="biz_austin_test",
        category="MANUAL_SCHEDULING_BOTTLENECK",
        title="After-Hours Booking & Scheduling Friction",
        description="Patient intake relies entirely on telephone reception causing booking abandonment.",
        status="VALIDATED",
        confidence=0.90,
        supporting_evidence_ids=["ev_austin_test_1", "ev_austin_test_2"]
    )
    imp1 = ImpactAssessment(
        id="imp_austin_test_1",
        workspace_id="ws_dossier_1",
        hypothesis_id="hyp_austin_test_1",
        estimated_impact_low=Decimal("18000.00"),
        estimated_impact_high=Decimal("36000.00"),
        estimated_impact_midpoint=Decimal("27000.00"),
        impact_currency="USD"
    )
    sol1 = SolutionMapping(
        id="sol_austin_test_1",
        workspace_id="ws_dossier_1",
        hypothesis_id="hyp_austin_test_1",
        solution_type="CATALOGUE_SOLUTION",
        title="Automated Patient Reception & Scheduling Assistant",
        capability_summary="24/7 web booking widget with EHR integration",
        feasibility_score=0.95
    )

    db.add_all([
        org, ws1, ws2, user1, user2, m1, m2,
        biz1, biz_empty, biz2, cnt1, run1, ev1, ev2, hyp1, imp1, sol1
    ])
    db.commit()

    token1 = create_access_token(
        user_id="u_dossier_op1",
        organization_id="org_dossier",
        active_workspace_id="ws_dossier_1",
        role="WorkspaceAdmin"
    )
    token2 = create_access_token(
        user_id="u_dossier_op2",
        organization_id="org_dossier",
        active_workspace_id="ws_dossier_2",
        role="WorkspaceAdmin"
    )

    return {
        "headers1": {"Authorization": f"Bearer {token1}"},
        "headers2": {"Authorization": f"Bearer {token2}"},
        "biz1_id": "biz_austin_test",
        "biz_empty_id": "biz_empty_test",
        "biz2_id": "biz_ws2_test",
        "cnt1_id": "cnt_austin_test_1",
        "hyp1_id": "hyp_austin_test_1"
    }


def test_lead_qualification_and_dossier_exact_consistency(dossier_env):
    """
    Assert that qualification and dossier maintain 100% data consistency:
    - Evidence count is identical between qualification and dossier
    - Evidence IDs match exactly
    - Verified contact identity and status matches Gate 6 authoritative record
    - Opportunity matches authoritative ProblemHypothesis title and solution type
    - Workspace ID, business ID, and lead ID match
    - Dossier does NOT fall back to generic opportunity text
    """
    headers = dossier_env["headers1"]
    biz_id = dossier_env["biz1_id"]

    # 1. Trigger lead qualification
    q_resp = client.post(
        f"/api/v1/leads/business/{biz_id}/qualify",
        headers=headers,
        json={"operator_notes": "Operator verification of Austin Family Health"}
    )
    assert q_resp.status_code == 200
    qual = q_resp.json()

    assert qual["status"] == "QUALIFIED"
    assert qual["business_id"] == biz_id
    assert qual["evidence_count"] == 2
    assert qual["fact_evidence_count"] == 2
    assert qual["validated_hypothesis_count"] == 1
    assert qual["has_eligible_contact"] is True
    assert qual["contact_verification_tier"] == "CONFIRMED_VERIFIED"
    assert qual["primary_contact_id"] == dossier_env["cnt1_id"]
    assert qual["primary_opportunity_id"] == dossier_env["hyp1_id"]
    assert qual["solution_fit_type"] == "CATALOGUE_SOLUTION"
    lead_id = qual["id"]

    # Verify directly in SQLite/Postgres persistence
    db = TestingSessionLocal()
    persisted_lq = db.query(LeadQualification).filter(LeadQualification.id == lead_id).first()
    assert persisted_lq is not None
    assert persisted_lq.status == "QUALIFIED"
    assert persisted_lq.evidence_count == 2
    assert persisted_lq.has_eligible_contact is True
    assert persisted_lq.primary_contact_id == "cnt_austin_test_1"
    assert persisted_lq.primary_opportunity_id == "hyp_austin_test_1"
    db.close()

    # 2. Fetch Lead Dossier
    d_resp = client.get(f"/api/v1/leads/{lead_id}/dossier", headers=headers)
    assert d_resp.status_code == 200
    dossier = d_resp.json()

    # Assert workspace, business, lead linkage
    assert dossier["business"]["id"] == biz_id
    assert dossier["qualification"]["id"] == lead_id
    assert dossier["qualification"]["status"] == "QUALIFIED"

    # Assert evidence count and IDs are identical
    dossier_evs = dossier.get("evidence", [])
    assert len(dossier_evs) == qual["evidence_count"] == 2
    dossier_ev_ids = {e["id"] for e in dossier_evs}
    assert dossier_ev_ids == {"ev_austin_test_1", "ev_austin_test_2"}

    # Assert contact linkage and Gate 6 authoritative tier
    primary_contact = dossier.get("primary_contact")
    assert primary_contact is not None
    assert primary_contact["id"] == dossier_env["cnt1_id"]
    assert primary_contact["email"] == "contact@austinfamilyhealth.example.com"
    assert primary_contact["verification_tier"] == "CONFIRMED_VERIFIED"
    assert primary_contact["role_title"] == "Clinical Director"

    # Assert opportunity linkage (NOT "General Digital Opportunity")
    primary_opp = dossier.get("primary_opportunity")
    assert primary_opp is not None
    assert primary_opp["id"] == dossier_env["hyp1_id"]
    assert primary_opp["title"] == "After-Hours Booking & Scheduling Friction"
    assert primary_opp["title"] != "General Digital Opportunity"
    assert primary_opp["solution_type"] == "CATALOGUE_SOLUTION"
    assert "Patient intake relies entirely on telephone reception" in primary_opp["description"]
    assert primary_opp["supporting_evidence_ids"] == ["ev_austin_test_1", "ev_austin_test_2"]


def test_lead_dossier_negative_scenario_no_evidence_or_contact(dossier_env):
    """
    Negative Test:
    When a business has no evidence, no completed research, and no verified contact:
    - Qualification computes RESEARCH_PENDING
    - Dossier shows zero evidence
    - Dossier primary_contact is None (no eligible contact)
    - Dossier primary_opportunity is None (does NOT invent a qualified opportunity)
    """
    headers = dossier_env["headers1"]
    biz_id = dossier_env["biz_empty_id"]

    # 1. Qualify empty business
    q_resp = client.post(
        f"/api/v1/leads/business/{biz_id}/qualify",
        headers=headers
    )
    assert q_resp.status_code == 200
    qual = q_resp.json()
    assert qual["status"] == "RESEARCH_PENDING"
    assert qual["evidence_count"] == 0
    assert qual["has_eligible_contact"] is False
    assert qual["primary_contact_id"] is None
    assert qual["primary_opportunity_id"] is None
    lead_id = qual["id"]

    # 2. Fetch Dossier
    d_resp = client.get(f"/api/v1/leads/{lead_id}/dossier", headers=headers)
    assert d_resp.status_code == 200
    dossier = d_resp.json()

    assert dossier["qualification"]["status"] == "RESEARCH_PENDING"
    assert len(dossier["evidence"]) == 0
    assert dossier["primary_contact"] is None
    assert dossier["primary_opportunity"] is None
    assert dossier["opportunities"]["validated_count"] == 0


def test_cross_workspace_dossier_isolation(dossier_env):
    """Multi-tenant security: Workspace 2 cannot retrieve Workspace 1's lead dossier."""
    headers1 = dossier_env["headers1"]
    headers2 = dossier_env["headers2"]
    biz_id = dossier_env["biz1_id"]

    q_resp = client.post(f"/api/v1/leads/business/{biz_id}/qualify", headers=headers1)
    lead_id = q_resp.json()["id"]

    # WS2 attempts to get WS1's dossier -> 404
    idor_resp = client.get(f"/api/v1/leads/{lead_id}/dossier", headers=headers2)
    assert idor_resp.status_code == 404
