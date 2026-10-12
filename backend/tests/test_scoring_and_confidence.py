import pytest
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.core.database import Base, get_db
from app.core.security import get_password_hash, create_access_token
from app.models.tenancy import Organization, Workspace, User, Membership
from app.models.business import MasterBusinessRecord
from app.models.research import EvidenceRecord
from app.models.opportunity import ProblemHypothesis, ImpactAssessment, SolutionMapping
from app.services.confidence_engine import ConfidenceEngine
from app.services.scoring_engine import ScoringEngine

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

app.dependency_overrides[get_db] = override_get_db
client = TestClient(app)

@pytest.fixture(autouse=True)
def setup_db():
    app.dependency_overrides[get_db] = override_get_db
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)
    app.dependency_overrides.pop(get_db, None)

def test_confidence_engine_decay_and_clamping():
    now = datetime.now(timezone.utc)
    # Stale evidence (> 90 days)
    old_ev = [
        EvidenceRecord(
            id="ev1",
            workspace_id="ws1",
            business_id="b1",
            signal_type="DOM_SIGNAL",
            raw_observation="no form",
            feature_presence_state="NOT_OBSERVED",
            classification_reason="stale scan",
            confidence=0.8,
            created_at=now - timedelta(days=120)
        )
    ]
    conf_old = ConfidenceEngine.calculate_confidence(old_ev)
    assert 0.0 <= conf_old <= 0.5

    # Fresh diverse evidence (DOM, REVIEW, INFRA)
    fresh_diverse = [
        EvidenceRecord(
            id="e1", workspace_id="ws1", business_id="b1",
            signal_type="DOM_ELEMENT", raw_observation="booking button missing",
            feature_presence_state="NOT_OBSERVED", classification_reason="verified",
            confidence=0.9, created_at=now
        ),
        EvidenceRecord(
            id="e2", workspace_id="ws1", business_id="b1",
            signal_type="REVIEW_SENTIMENT", raw_observation="slow response",
            feature_presence_state="PRESENT", classification_reason="verified",
            confidence=0.9, created_at=now
        ),
        EvidenceRecord(
            id="e3", workspace_id="ws1", business_id="b1",
            signal_type="INFRA_PROBE", raw_observation="no api",
            feature_presence_state="NOT_OBSERVED", classification_reason="verified",
            confidence=0.9, created_at=now
        )
    ]
    conf_fresh = ConfidenceEngine.calculate_confidence(fresh_diverse)
    assert conf_fresh >= 0.7
    assert conf_fresh <= 1.0

def test_s6_cap_rule_invariant():
    """
    Test S6 Cap Rule Invariant:
    If S1 < 40 or S2 < 40, S6 is strictly capped at 30.
    A bad website cannot reward a high opportunity score without operational activity & validated problems!
    """
    db = TestingSessionLocal()
    org = Organization(id="org1", name="Cap Org", slug="cap-org")
    ws = Workspace(id="ws1", organization_id="org1", name="Cap WS", slug="cap-ws")
    db.add_all([org, ws])
    db.commit()

    # Business without website and without online booking -> gap points = 100
    biz = MasterBusinessRecord(
        id="biz1",
        workspace_id="ws1",
        name="No Impact Plumbing",
        normalized_name="no impact plumbing",
        category="PLUMBING",
        source_provider="LOCAL_DIRECTORY",
        canonical_domain=None,
        website_url=None,
        primary_email="contact@plumbing.local",
        phone="555-1234",
        raw_metadata={}
    )
    db.add(biz)
    db.commit()

    # No validated hypotheses -> S1 = 0, S2 = 0
    eval_res = ScoringEngine.evaluate_business(db, "ws1", "biz1")
    assert eval_res["dimensions"]["S1_problem_severity"] == 0
    assert eval_res["dimensions"]["S2_financial_impact"] == 0
    # S6 raw was 100 (50 for missing website + 0 evidence), but capped at 30
    assert eval_res["dimensions"]["S6_adoption_gap"] <= 30
    assert eval_res["dimensions"]["S6_capped"] is True
    assert eval_res["score_semantics"] == "PRIORITIZATION_HEURISTIC"
    assert eval_res["outreach_eligible"] is False
    db.close()

def test_scoring_api_with_validated_hypotheses_and_tenant_isolation():
    db = TestingSessionLocal()
    org = Organization(id="org1", name="Tenant Org", slug="tenant-org")
    ws = Workspace(id="ws1", organization_id="org1", name="Tenant WS", slug="tenant-ws")
    ws_other = Workspace(id="ws2", organization_id="org1", name="Other WS", slug="other-ws")
    user = User(
        id="u1",
        email="operator@test.com",
        hashed_password=get_password_hash("ValidPassword123!"),
        full_name="Operator"
    )
    db.add_all([org, ws, ws_other, user])
    db.commit()

    m1 = Membership(id="m1", user_id="u1", organization_id="org1", workspace_id="ws1", role="WorkspaceAdmin")
    db.add(m1)
    db.commit()

    biz = MasterBusinessRecord(
        id="biz_validated",
        workspace_id="ws1",
        name="High Pain Dental Clinic",
        normalized_name="high pain dental clinic",
        category="DENTAL",
        source_provider="LOCAL_DIRECTORY",
        canonical_domain="highpaindental.com",
        website_url="https://highpaindental.com",
        primary_email="admin@highpaindental.com",
        phone="555-9876",
        raw_metadata={"reviews": [{"rating": 1}, {"rating": 2}, {"rating": 1}]}
    )
    ev1 = EvidenceRecord(
        id="ev_val1",
        workspace_id="ws1",
        business_id="biz_validated",
        signal_type="DOM_ELEMENT",
        raw_observation="No online scheduling form found",
        feature_presence_state="NOT_OBSERVED",
        classification_reason="Confirmed absence",
        confidence=0.9
    )
    ev2 = EvidenceRecord(
        id="ev_val2",
        workspace_id="ws1",
        business_id="biz_validated",
        signal_type="REVIEW_SENTIMENT",
        raw_observation="Patients complain about busy phone lines",
        feature_presence_state="PRESENT",
        classification_reason="Review sentiment",
        confidence=0.85
    )
    db.add_all([biz, ev1, ev2])
    db.commit()

    # Add validated hypotheses with impact
    hyp1 = ProblemHypothesis(
        id="hyp1",
        workspace_id="ws1",
        business_id="biz_validated",
        category="MISSED_CALLS_AFTER_HOURS",
        title="Missed Calls",
        description="Losing off-hours patients",
        status="VALIDATED",
        confidence=0.85,
        supporting_evidence_ids=[]
    )
    hyp2 = ProblemHypothesis(
        id="hyp2",
        workspace_id="ws1",
        business_id="biz_validated",
        category="MANUAL_SCHEDULING_BOTTLENECK",
        title="Manual Scheduling",
        description="Staff overwhelmed",
        status="VALIDATED",
        confidence=0.80,
        supporting_evidence_ids=[]
    )
    db.add_all([hyp1, hyp2])
    db.commit()

    imp1 = ImpactAssessment(
        id="imp1",
        workspace_id="ws1",
        hypothesis_id="hyp1",
        estimated_impact_low=Decimal("10000.00"),
        estimated_impact_high=Decimal("20000.00"),
        estimated_impact_midpoint=Decimal("15000.00"),
        impact_currency="USD",
        observed_financial_data={},
        business_specific_measurements={},
        industry_benchmark_assumptions={"benchmark_conversion": 0.15},
        model_assumptions={"ticket_size": 200}
    )
    sol1 = SolutionMapping(
        id="sol1",
        workspace_id="ws1",
        hypothesis_id="hyp1",
        solution_type="AI_RECEPTIONIST",
        title="AI Receptionist",
        capability_summary="24/7 call answering",
        feasibility_score=0.9
    )
    db.add_all([imp1, sol1])
    db.commit()

    token = create_access_token(
        user_id="u1",
        organization_id="org1",
        active_workspace_id="ws1",
        role="WorkspaceAdmin"
    )

    # Cross-tenant score evaluation check: using another organization token where user has no membership
    other_token = create_access_token(
        user_id="u1",
        organization_id="org_other",
        active_workspace_id="ws2",
        role="WorkspaceAdmin"
    )
    res_forbidden = client.post(
        "/api/v1/opportunities/biz_validated/score",
        headers={"Authorization": f"Bearer {other_token}"}
    )
    # Returns 403 because u1 doesn't have membership in org_other
    assert res_forbidden.status_code == 403

    # Authorized score evaluation
    res = client.post(
        "/api/v1/opportunities/biz_validated/score",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 200
    data = res.json()
    assert data["business_id"] == "biz_validated"
    assert data["opportunity_score"] > 0
    assert data["validated_hypotheses_count"] == 2
    assert data["score_semantics"] == "PRIORITIZATION_HEURISTIC"
    assert "dimensions" in data
    assert data["dimensions"]["S1_problem_severity"] > 40
    assert data["dimensions"]["S2_financial_impact"] > 40
    db.close()
