import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.main import app
from app.models.tenancy import Organization, Workspace, User, Membership
from app.models.business import MasterBusinessRecord
from app.models.research import EvidenceRecord
from app.core.security import get_password_hash
from app.services.crawler_engine import CrawlerEngine
from app.services.signal_detectors.dom_detector import DOMDetector

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
def research_fixture(db_session):
    org = Organization(name="Research Tenant", slug="res-tenant")
    db_session.add(org)
    db_session.flush()

    ws = Workspace(name="Research Lab", slug="res-lab", organization_id=org.id)
    db_session.add(ws)
    db_session.flush()

    user = User(
        email="analyst@research.com",
        hashed_password=get_password_hash("ResearchPass123!"),
        full_name="Research Analyst"
    )
    db_session.add(user)
    db_session.flush()

    mem = Membership(user_id=user.id, organization_id=org.id, workspace_id=ws.id, role="Operator")
    db_session.add(mem)

    biz = MasterBusinessRecord(
        workspace_id=ws.id,
        name="Sunset Smiles Dental",
        normalized_name="sunset smiles dental",
        website_url="https://sunsetsmiles.example.com",
        canonical_domain="sunsetsmiles.example.com",
        phone="+1-555-0988",
        primary_email="contact@sunsetsmiles.example.com",
        category="healthcare",
        source_provider="OPENSTREETMAP",
        raw_metadata={
            "reviews": [
                {"rating": 2, "text": "Great dentist but they never answered the phone when I had an emergency after hours."},
                {"rating": 1, "text": "Called after 5 and left 3 voicemails, no one picks up or calls back."}
            ]
        }
    )
    db_session.add(biz)
    db_session.commit()

    return {
        "org": org,
        "ws": ws,
        "user": user,
        "biz": biz
    }

def test_1_booking_widget_present():
    detector = DOMDetector()
    html = """<html><body>
        <h1>Book an Appointment</h1>
        <iframe src="https://calendly.com/sunsetsmiles/consultation"></iframe>
    </body></html>"""
    results = detector.analyze_html(html)
    booking_item = next(r for r in results if r["feature"] == "online_booking")
    assert booking_item["presence_state"] == "PRESENT"
    assert booking_item["confidence"] == 1.0

def test_2_inaccessibility_not_absence():
    """Inaccessibility != Absence: A blocked page (HTTP 403/500/timeout) yields UNKNOWN, never ABSENCE_CONFIRMED."""
    detector = DOMDetector()
    # Simulated 403 Forbidden / Cloudflare challenge
    results = detector.analyze_html(None, status_code=403, is_accessible=False)
    booking_item = next(r for r in results if r["feature"] == "online_booking")
    assert booking_item["presence_state"] == "UNKNOWN"
    assert "Inaccessibility != Absence" in booking_item["classification_reason"]

def test_3_explicit_absence_confirmed():
    """Explicit textual denial yields ABSENCE_CONFIRMED."""
    detector = DOMDetector()
    html = """<html><body>
        <h1>Appointments</h1>
        <p>Please note: We do not offer online booking at this clinic. All scheduling is handled at reception.</p>
    </body></html>"""
    results = detector.analyze_html(html)
    booking_item = next(r for r in results if r["feature"] == "online_booking")
    assert booking_item["presence_state"] == "ABSENCE_CONFIRMED"
    assert "Explicit denial text observed" in booking_item["raw_observation"]

def test_4_offline_signal_does_not_prove_online_absence():
    """Positive offline signal (e.g. 'Call us to book') yields NOT_OBSERVED, NOT ABSENCE_CONFIRMED."""
    detector = DOMDetector()
    html = """<html><body>
        <h1>Welcome</h1>
        <p>To schedule, please call us to book an appointment! Walk-ins welcome.</p>
    </body></html>"""
    results = detector.analyze_html(html)
    booking_item = next(r for r in results if r["feature"] == "online_booking")
    assert booking_item["presence_state"] == "NOT_OBSERVED"
    assert "Offline signal does NOT prove online absence" in booking_item["classification_reason"]

def test_5_no_website_yields_absence_confirmed(db_session, research_fixture):
    ws_id = research_fixture["ws"].id
    # Create business without website
    biz_no_web = MasterBusinessRecord(
        workspace_id=ws_id,
        name="Offline Barber Shop",
        normalized_name="offline barber shop",
        website_url=None,
        category="barbershop",
        source_provider="MANUAL"
    )
    db_session.add(biz_no_web)
    db_session.commit()

    crawler = CrawlerEngine()
    records = crawler.run_research_pipeline(db_session, ws_id, biz_no_web.id)

    states = {r.signal_type: r.feature_presence_state for r in records}
    assert any(r.feature_presence_state == "NOT_OBSERVED" for r in records)
    assert not all(r.feature_presence_state == "ABSENCE_CONFIRMED" for r in records)

def test_6_crawler_review_nlp_extraction(db_session, research_fixture):
    crawler = CrawlerEngine()
    records = crawler.run_research_pipeline(
        db_session,
        research_fixture["ws"].id,
        research_fixture["biz"].id
    )

    review_records = [r for r in records if r.signal_type == "REVIEW_TEXT"]
    assert len(review_records) >= 2
    observations = " ".join([r.raw_observation for r in review_records])
    assert "never answered" in observations or "called after 5" in observations

def test_7_research_api_flow(client, db_session, research_fixture):
    login_resp = client.post("/api/v1/auth/login", json={
        "email": "analyst@research.com",
        "password": "ResearchPass123!"
    })
    token = login_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    biz_id = research_fixture["biz"].id

    # 1. Run Research
    resp = client.post("/api/v1/research/run", json={
        "business_id": biz_id
    }, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "COMPLETED"
    assert resp.json()["evidence_count"] > 0

    # 2. Query Evidence
    ev_resp = client.get(f"/api/v1/research/{biz_id}/evidence", headers=headers)
    assert ev_resp.status_code == 200
    evidence = ev_resp.json()
    assert len(evidence) >= 4
    signals = [e["signal_type"] for e in evidence]
    assert "DOM_SELECTOR" in signals
    assert "INFRA_PROBE" in signals
