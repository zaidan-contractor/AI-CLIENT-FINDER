import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.core.database import Base, get_db
from app.core.security import get_password_hash, create_access_token
from app.models.tenancy import Organization, Workspace, User, Membership
from app.models.business import MasterBusinessRecord
from app.models.outreach import OutboundMessage
from app.models.inbound import InboundMessage
from app.models.learning import RecommendationRecord
from app.services.learning_service import LearningService

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
def setup_analytics_data():
    db = TestingSessionLocal()
    org = Organization(id="org_l", name="Learning Org", slug="learn-org", is_active=True)
    ws = Workspace(id="ws_l", organization_id="org_l", name="Learn WS", slug="learn-ws", is_active=True)
    user = User(
        id="u_learn",
        email="operator@learn.test",
        hashed_password=get_password_hash("Secret123!"),
        full_name="Lead Analyst"
    )
    db.add_all([org, ws, user])
    db.commit()

    m = Membership(id="m_l", user_id="u_learn", organization_id="org_l", workspace_id="ws_l", role="WorkspaceAdmin")
    db.add(m)
    db.commit()

    # Seed 5 businesses
    for i in range(5):
        db.add(MasterBusinessRecord(
            id=f"biz_l_{i}",
            workspace_id="ws_l",
            name=f"Clinic {i}",
            normalized_name=f"clinic {i}",
            category="HEALTHCARE",
            source_provider="LOCAL_DIRECTORY"
        ))

    # Seed 10 outbound messages (8 TRANSMITTED, 1 SUPPRESSED, 1 BLOCKED)
    for i in range(8):
        db.add(OutboundMessage(
            id=f"msg_out_{i}",
            workspace_id="ws_l",
            campaign_version_id="cv_1",
            sender_account_id="s_1",
            recipient_email=f"client{i}@clinic.test",
            subject="Diagnostic",
            body_text="Body",
            status="TRANSMITTED",
            side_effect_idempotency_key=f"idemp_out_{i}"
        ))
    db.add(OutboundMessage(
        id="msg_out_supp",
        workspace_id="ws_l",
        campaign_version_id="cv_1",
        sender_account_id="s_1",
        recipient_email="suppressed@clinic.test",
        subject="Diagnostic",
        body_text="Body",
        status="SUPPRESSED",
        side_effect_idempotency_key="idemp_out_supp"
    ))
    db.add(OutboundMessage(
        id="msg_out_block",
        workspace_id="ws_l",
        campaign_version_id="cv_1",
        sender_account_id="s_1",
        recipient_email="blocked@clinic.test",
        subject="Diagnostic",
        body_text="Body",
        status="BLOCKED",
        side_effect_idempotency_key="idemp_out_block"
    ))

    # Seed 2 inbound messages (1 HOT_DEAL, 1 OPT_OUT)
    db.add(InboundMessage(
        id="inb_hot",
        workspace_id="ws_l",
        sender_email="client1@clinic.test",
        recipient_email="outreach@learn.test",
        subject="Re: Diagnostic",
        body_text="Let's book a demo call",
        classified_intent="HOT_DEAL",
        thread_state="HUMAN_TAKEOVER"
    ))
    db.add(InboundMessage(
        id="inb_opt",
        workspace_id="ws_l",
        sender_email="client2@clinic.test",
        recipient_email="outreach@learn.test",
        subject="Re: Diagnostic",
        body_text="Please stop emailing me",
        classified_intent="OPT_OUT",
        thread_state="OPT_OUT_SUPPRESSED"
    ))

    db.commit()
    db.close()
    return {"org_id": "org_l", "workspace_id": "ws_l", "user_id": "u_learn"}

def test_workspace_performance_analysis(setup_analytics_data):
    db = TestingSessionLocal()
    ws_id = setup_analytics_data["workspace_id"]

    perf = LearningService.analyze_workspace_performance(db, ws_id)
    assert perf["businesses_discovered"] == 5
    assert perf["outbound"]["transmitted"] == 8
    assert perf["outbound"]["suppressed_gate14"] == 1
    assert perf["outbound"]["blocked_by_gates"] == 1
    assert perf["inbound"]["total_replies"] == 2
    assert perf["inbound"]["hot_deals"] == 1
    assert perf["inbound"]["opt_outs"] == 1
    # 2 replies / 8 sent = 25% reply rate
    assert perf["conversion_metrics"]["reply_rate_pct"] == 25.0
    # 1 hot deal / 8 sent = 12.5%
    assert perf["conversion_metrics"]["hot_deal_conversion_pct"] == 12.5
    # 1 opt out / 8 sent = 12.5%
    assert perf["conversion_metrics"]["opt_out_rate_pct"] == 12.5
    db.close()

def test_recommendation_generation_and_zero_silent_mutations(setup_analytics_data):
    db = TestingSessionLocal()
    ws_id = setup_analytics_data["workspace_id"]

    recs = LearningService.generate_recommendations(db, ws_id)
    assert len(recs) >= 1

    # Invariant: All recommendations are PROPOSED and do NOT silently modify live campaigns
    for r in recs:
        assert r.status == "PROPOSED"
        assert r.applied_at is None
        assert r.applied_by_user_id is None

    # Operator explicitly accepts recommendation
    rec = recs[0]
    updated = LearningService.update_recommendation_status(
        db=db,
        workspace_id=ws_id,
        recommendation_id=rec.id,
        new_status="ACCEPTED",
        operator_user_id=setup_analytics_data["user_id"]
    )
    assert updated.status == "ACCEPTED"
    assert updated.applied_by_user_id == setup_analytics_data["user_id"]
    assert updated.applied_at is not None
    db.close()

def test_analytics_and_recommendations_api(setup_analytics_data):
    token = create_access_token(
        user_id=setup_analytics_data["user_id"],
        organization_id=setup_analytics_data["org_id"],
        active_workspace_id=setup_analytics_data["workspace_id"],
        role="WorkspaceAdmin"
    )
    headers = {"Authorization": f"Bearer {token}"}

    # 1. GET /api/v1/analytics/workspace
    res = client.get("/api/v1/analytics/workspace", headers=headers)
    assert res.status_code == 200
    analytics_data = res.json()
    assert analytics_data["businesses_discovered"] == 5
    assert "conversion_metrics" in analytics_data

    # 2. POST /api/v1/recommendations/generate
    res_gen = client.post("/api/v1/recommendations/generate", headers=headers)
    assert res_gen.status_code == 200
    gen_data = res_gen.json()
    assert len(gen_data) >= 1
    rec_id = gen_data[0]["id"]
    assert gen_data[0]["status"] == "PROPOSED"

    # 3. POST /api/v1/recommendations/{id}/action (Accept)
    res_act = client.post(f"/api/v1/recommendations/{rec_id}/action", json={"action": "ACCEPTED"}, headers=headers)
    assert res_act.status_code == 200
    act_data = res_act.json()
    assert act_data["status"] == "ACCEPTED"
    assert act_data["applied_by_user_id"] == setup_analytics_data["user_id"]

    # 4. GET /api/v1/recommendations
    res_list = client.get("/api/v1/recommendations", headers=headers)
    assert res_list.status_code == 200
    list_data = res_list.json()
    assert any(r["id"] == rec_id for r in list_data)
