import pytest
from decimal import Decimal
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.main import app
from app.models.tenancy import Organization, Workspace, User, Membership
from app.models.business import MasterBusinessRecord
from app.models.research import EvidenceRecord
from app.models.opportunity import ProblemHypothesis, ImpactAssessment, SolutionMapping
from app.core.security import get_password_hash
from app.core.currency import seed_currency_registry
from app.services.impact_calculator import ImpactCalculator
from app.services.hypothesis_engine import HypothesisEngine

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
    seed_currency_registry(session)
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
def opp_fixture(db_session):
    org = Organization(name="Opportunity Org", slug="opp-org")
    db_session.add(org)
    db_session.flush()

    ws = Workspace(name="Opportunity Lab", slug="opp-lab", organization_id=org.id)
    db_session.add(ws)
    db_session.flush()

    user = User(
        email="consultant@opp.com",
        hashed_password=get_password_hash("ConsultantPass123!"),
        full_name="Chief Consultant"
    )
    db_session.add(user)
    db_session.flush()

    mem = Membership(user_id=user.id, organization_id=org.id, workspace_id=ws.id, role="Operator")
    db_session.add(mem)

    biz = MasterBusinessRecord(
        workspace_id=ws.id,
        name="Oakland Family Dentistry",
        normalized_name="oakland family dentistry",
        website_url="https://oaklandfamilydentistry.example.com",
        category="healthcare",
        source_provider="MANUAL"
    )
    db_session.add(biz)
    db_session.commit()

    return {
        "org": org,
        "ws": ws,
        "user": user,
        "biz": biz
    }

def test_1_decimal_impact_precision_and_no_float():
    calc = ImpactCalculator.calculate_monetary_impact(
        currency="USD",
        n_lost_inquiries_monthly=Decimal("15"),
        v_ticket_avg=Decimal("150.00"),
        c_conversion_rate=Decimal("0.30"),
        h_wasted_hours_monthly=Decimal("10"),
        r_labor_hourly_rate=Decimal("25.00"),
        variance_percentage=Decimal("0.20")
    )
    assert isinstance(calc["estimated_impact_low"], Decimal)
    assert isinstance(calc["estimated_impact_high"], Decimal)
    assert isinstance(calc["estimated_impact_midpoint"], Decimal)
    # Monthly: (15 * 150 * 0.3) + (10 * 25) = 675 + 250 = 925
    # Annual: 925 * 12 = 11,100
    # Low: 11,100 * 0.8 = 8,880.00
    # High: 11,100 * 1.2 = 13,320.00
    # Midpoint: (8880 + 13320) / 2 = 11,100.00
    assert calc["estimated_impact_low"] == Decimal("8880.00")
    assert calc["estimated_impact_high"] == Decimal("13320.00")
    assert calc["estimated_impact_midpoint"] == Decimal("11100.00")

def test_2_currency_validation_no_silent_fallback():
    with pytest.raises(ValueError, match="Invalid or missing ISO-4217"):
        ImpactCalculator.calculate_monetary_impact(
            currency="INVALID_FAKE_CODE",
            n_lost_inquiries_monthly=Decimal("10"),
            v_ticket_avg=Decimal("100"),
            c_conversion_rate=Decimal("0.2")
        )

def test_3_midpoint_consistency():
    calc = ImpactCalculator.calculate_monetary_impact(
        currency="INR",
        n_lost_inquiries_monthly=Decimal("30"),
        v_ticket_avg=Decimal("2500.00"),
        c_conversion_rate=Decimal("0.25")
    )
    low = calc["estimated_impact_low"]
    high = calc["estimated_impact_high"]
    mid = calc["estimated_impact_midpoint"]
    assert low <= high
    assert mid == ((low + high) / Decimal("2"))

def test_4_hypothesis_generation_validated(db_session, opp_fixture):
    ws_id = opp_fixture["ws"].id
    biz_id = opp_fixture["biz"].id

    # Add absence confirmed evidence
    ev = EvidenceRecord(
        workspace_id=ws_id,
        business_id=biz_id,
        signal_type="DOM_SELECTOR",
        raw_observation="Explicit denial text observed: 'no online booking available'",
        feature_presence_state="ABSENCE_CONFIRMED",
        confidence=0.95,
        classification_reason="Explicit denial"
    )
    db_session.add(ev)
    db_session.commit()

    engine = HypothesisEngine()
    hypotheses = engine.generate_and_evaluate_hypotheses(db_session, ws_id, biz_id, currency="USD")

    assert len(hypotheses) >= 1
    booking_hyp = next(h for h in hypotheses if h.category == "BOOKING_FRICTION_AFTER_HOURS_LEAKAGE")
    assert booking_hyp.status == "VALIDATED"
    assert booking_hyp.confidence >= 0.70
    assert booking_hyp.impact is not None
    assert booking_hyp.impact.impact_currency == "USD"
    assert len(booking_hyp.solutions) >= 1
    assert booking_hyp.solutions[0].solution_type == "CATALOGUE_SOLUTION"

def test_5_direct_counter_evidence_refutes_hypothesis(db_session, opp_fixture):
    ws_id = opp_fixture["ws"].id
    biz_id = opp_fixture["biz"].id

    # Add counter-evidence: booking is PRESENT
    ev = EvidenceRecord(
        workspace_id=ws_id,
        business_id=biz_id,
        signal_type="DOM_SELECTOR",
        raw_observation="Booking engine detected: calendly.com online_booking",
        feature_presence_state="PRESENT",
        confidence=1.0,
        classification_reason="Active widget"
    )
    db_session.add(ev)
    db_session.commit()

    engine = HypothesisEngine()
    hypotheses = engine.generate_and_evaluate_hypotheses(db_session, ws_id, biz_id, currency="USD")

    booking_hyp = next(h for h in hypotheses if h.category == "BOOKING_FRICTION_AFTER_HOURS_LEAKAGE")
    assert booking_hyp.status == "REFUTED"
    assert "counter-evidence" in booking_hyp.refutation_reason

def test_6_inaccessibility_does_not_validate_hypothesis(db_session, opp_fixture):
    ws_id = opp_fixture["ws"].id
    biz_id = opp_fixture["biz"].id

    # Inaccessible page evidence
    ev = EvidenceRecord(
        workspace_id=ws_id,
        business_id=biz_id,
        signal_type="DOM_SELECTOR",
        raw_observation="Website inaccessible or returned HTTP 403",
        feature_presence_state="UNKNOWN",
        confidence=0.0,
        classification_reason="Inaccessibility != Absence"
    )
    db_session.add(ev)
    db_session.commit()

    engine = HypothesisEngine()
    hypotheses = engine.generate_and_evaluate_hypotheses(db_session, ws_id, biz_id, currency="USD")
    # Must NOT produce a validated booking hypothesis from unknown status
    validated_booking = [h for h in hypotheses if h.category == "BOOKING_FRICTION_AFTER_HOURS_LEAKAGE" and h.status == "VALIDATED"]
    assert len(validated_booking) == 0

def test_7_hypotheses_api_endpoint(client, db_session, opp_fixture):
    login_resp = client.post("/api/v1/auth/login", json={
        "email": "consultant@opp.com",
        "password": "ConsultantPass123!"
    })
    token = login_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    biz_id = opp_fixture["biz"].id

    # Add evidence
    ev = EvidenceRecord(
        workspace_id=opp_fixture["ws"].id,
        business_id=biz_id,
        signal_type="REVIEW_TEXT",
        raw_observation="Customer review complaint: 'Called after 5 and never answered'",
        feature_presence_state="PRESENT",
        confidence=0.85,
        classification_reason="Review complaints"
    )
    db_session.add(ev)
    db_session.commit()

    # Call Generate API
    resp = client.post("/api/v1/hypotheses/generate", json={
        "business_id": biz_id,
        "currency": "EUR"
    }, headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1
    call_hyp = next(h for h in data if h["category"] == "MISSED_CALLS_UNANSWERED_INQUIRIES")
    assert call_hyp["status"] == "VALIDATED"
    assert call_hyp["impact"]["impact_currency"] == "EUR"
    assert call_hyp["impact"]["estimated_impact_midpoint"] > 0
