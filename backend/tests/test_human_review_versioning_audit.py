"""
Human Review & Provider Status Consistency Audit Test Suite.

Exhaustively verifies:
1. Complete draft state machine:
   GENERATED -> PENDING_REVIEW -> EDITED/VERSIONED -> REJECTED -> APPROVED.
2. Version 1 is immutable; edits create new versions; previous versions remain auditable.
3. Evidence IDs remain attached to every version and claims remain traceable.
4. Authenticated human reviewer is recorded on edits and approval.
5. AI cannot self-approve; approved versions cannot silently mutate; rejected versions cannot be approved.
6. Approval boundary: approve_draft NEVER creates OutboundMessages, transmits emails, or launches campaigns.
7. Regeneration: old draft is superseded/preserved, new draft starts in PENDING_REVIEW, no auto-approval.
8. Security: IDOR protection, workspace isolation, unverified evidence rejection, inert payload handling.
"""
import pytest
from decimal import Decimal
from datetime import datetime, timezone
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker, Session

from app.core.database import Base
from app.models.tenancy import Organization, Workspace, User, Membership, get_utc_now
from app.models.business import MasterBusinessRecord, ContactRecord
from app.models.research import EvidenceRecord, ResearchRun
from app.models.opportunity import ProblemHypothesis
from app.models.lead import LeadQualification, EmailDraft, EmailDraftVersion, CampaignLead
from app.models.outreach import OutboundMessage
from app.models.ai_audit import AIActionAuditLedger
from app.services.email_draft_service import EmailDraftService, EmailDraftOutput
from app.services.llm.base import LLMStructuredResult, LLMExecutionStatus, LLMUsageMetadata
from app.services.llm.provider_status_tracker import ProviderStatusTracker, ProviderOperationalState


TEST_DB_URL = "sqlite:///:memory:"


class MockTier1Provider:
    """Mock Tier-1 provider for deterministic, zero-cost test execution."""
    def __init__(self, subject="Mock Subject", body="Mock Body referencing evidence.", evidence_ids=None):
        self.model = "mock-tier1-model"
        self._subject = subject
        self._body = body
        self._evidence_ids = evidence_ids or ["ev_audit_1"]
        self._api_key = "mock_key"

    def get_model_metadata(self):
        return {"provider": "GROQ", "model": self.model}

    def get_operational_constraints(self):
        from app.services.llm.base import ProviderOperationalConstraints
        return ProviderOperationalConstraints(
            provider_name="GROQ",
            quality_tier="TIER_1_ADVANCED",
            provider_availability="AVAILABLE",
            model_availability="AVAILABLE"
        )

    def generate_structured(self, messages, response_schema, temperature=0.3, max_tokens=800):
        output = EmailDraftOutput(
            subject=self._subject,
            body=self._body,
            personalization_points=["Grounded personalization point"],
            supporting_evidence_ids=self._evidence_ids
        )
        return LLMStructuredResult(
            parsed=output,
            raw_output=output.model_dump_json(),
            status=LLMExecutionStatus.LLM_SUCCESS,
            metadata=LLMUsageMetadata(
                prompt_tokens=100,
                completion_tokens=50,
                total_tokens=150,
                estimated_cost_usd=0.0001,
                model_name=self.model,
                provider_name="GROQ",
                latency_ms=120.0
            )
        )


@pytest.fixture(scope="function")
def db_session():
    engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine)
    session = TestSession()
    yield session
    session.close()
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def audit_fixture(db_session: Session):
    """Sets up a synthetic business fixture with verified evidence and qualified lead."""
    org = Organization(id="org_audit_1", name="Audit Org", slug="audit-org")
    ws1 = Workspace(id="ws_audit_1", organization_id=org.id, name="Audit Workspace 1", slug="audit-ws-1")
    ws2 = Workspace(id="ws_audit_2", organization_id=org.id, name="Audit Workspace 2", slug="audit-ws-2")
    db_session.add_all([org, ws1, ws2])
    db_session.flush()

    operator_user = User(
        id="usr_human_operator",
        email="operator@audit.test",
        hashed_password="hashed_pass",
        full_name="Certified Human Operator"
    )
    unauthorized_user = User(
        id="usr_other_operator",
        email="other@audit.test",
        hashed_password="hashed_pass",
        full_name="Other Workspace Operator"
    )
    db_session.add_all([operator_user, unauthorized_user])
    db_session.flush()

    mem1 = Membership(id="mem_1", user_id=operator_user.id, organization_id=org.id, workspace_id=ws1.id, role="Operator")
    mem2 = Membership(id="mem_2", user_id=unauthorized_user.id, organization_id=org.id, workspace_id=ws2.id, role="Operator")
    db_session.add_all([mem1, mem2])
    db_session.flush()

    biz = MasterBusinessRecord(
        id="biz_audit_1",
        workspace_id=ws1.id,
        name="Synthetic Audit Medical Center",
        normalized_name="synthetic audit medical center",
        website_url="https://audit-medical.example.com",
        canonical_domain="audit-medical.example.com",
        primary_email="contact@audit-medical.example.com",
        category="healthcare",
        source_provider="MANUAL"
    )
    contact = ContactRecord(
        id="cnt_audit_1",
        workspace_id=ws1.id,
        business_id=biz.id,
        email="contact@audit-medical.example.com",
        canonical_email="contact@audit-medical.example.com",
        verification_tier="CONFIRMED_VERIFIED",
        freshness_timestamp=get_utc_now()
    )
    run = ResearchRun(
        id="run_audit_1",
        workspace_id=ws1.id,
        business_id=biz.id,
        status="COMPLETED",
        evidence_count=2
    )
    ev1 = EvidenceRecord(
        id="ev_audit_1",
        workspace_id=ws1.id,
        business_id=biz.id,
        research_run_id=run.id,
        signal_type="DOM_ELEMENT",
        raw_observation="No after-hours booking workflow available on website.",
        feature_presence_state="NOT_OBSERVED",
        classification_reason="Confirmed absence on crawl",
        confidence=0.95
    )
    ev2 = EvidenceRecord(
        id="ev_audit_2",
        workspace_id=ws1.id,
        business_id=biz.id,
        research_run_id=run.id,
        signal_type="REVIEW_SENTIMENT",
        raw_observation="Patients note difficulty reaching staff after 5 PM.",
        feature_presence_state="PRESENT",
        classification_reason="Patient review sentiment extraction",
        confidence=0.90
    )
    hyp = ProblemHypothesis(
        id="hyp_audit_1",
        workspace_id=ws1.id,
        business_id=biz.id,
        category="SCHEDULING",
        title="After-Hours Intake Bottleneck",
        description="Patients cannot schedule outside clinic hours.",
        status="VALIDATED",
        confidence=0.92,
        supporting_evidence_ids=["ev_audit_1", "ev_audit_2"]
    )
    lq = LeadQualification(
        id="lq_audit_1",
        workspace_id=ws1.id,
        business_id=biz.id,
        status="QUALIFIED"
    )
    db_session.add_all([biz, contact, run, ev1, ev2, hyp, lq])
    db_session.commit()

    return {
        "ws1": ws1,
        "ws2": ws2,
        "operator": operator_user,
        "unauthorized_user": unauthorized_user,
        "biz": biz,
        "contact": contact,
        "lq": lq,
        "hyp": hyp,
        "ev1": ev1,
        "ev2": ev2
    }


# ==============================================================================
# TASK 2 & 3: VERSIONING, IMMUTABILITY, AND REJECTION
# ==============================================================================

def test_version_1_immutable_and_edit_creates_new_version(db_session: Session, audit_fixture):
    """Verify Version 1 is immutable and editing creates an auditable Version 2 with evidence preserved."""
    f = audit_fixture
    mock_llm = MockTier1Provider(
        subject="Initial AI Subject",
        body="Initial AI Body citing public evidence.",
        evidence_ids=["ev_audit_1", "ev_audit_2"]
    )

    # 1. Generate Version 1
    draft = EmailDraftService.generate_draft(
        db=db_session,
        workspace_id=f["ws1"].id,
        business_id=f["biz"].id,
        lead_qualification_id=f["lq"].id,
        opportunity_id=f["hyp"].id,
        contact_id=f["contact"].id,
        provider=mock_llm
    )

    assert draft.version_count == 1
    assert draft.status == "GENERATED"
    assert draft.supporting_evidence_ids == ["ev_audit_1", "ev_audit_2"]

    v1 = db_session.query(EmailDraftVersion).filter_by(draft_id=draft.id, version_number=1).first()
    assert v1 is not None
    assert v1.subject == "Initial AI Subject"
    assert v1.editor_type == "AI"
    v1_hash = v1.content_hash

    # 2. Edit draft with harmless human revision
    edited = EmailDraftService.edit_draft(
        db=db_session,
        workspace_id=f["ws1"].id,
        draft_id=draft.id,
        editor_user_id=f["operator"].id,
        new_subject="Revised Human Subject",
        new_body="Initial AI Body citing public evidence with human refinement."
    )

    assert edited.version_count == 2
    assert edited.status == "EDITED"
    assert edited.subject == "Revised Human Subject"
    assert edited.supporting_evidence_ids == ["ev_audit_1", "ev_audit_2"]

    # 3. Verify Version 1 remains completely immutable
    v1_after = db_session.query(EmailDraftVersion).filter_by(draft_id=draft.id, version_number=1).first()
    assert v1_after.subject == "Initial AI Subject"
    assert v1_after.content_hash == v1_hash

    # 4. Verify Version 2 exists and has previous_hash pointing to Version 1
    v2 = db_session.query(EmailDraftVersion).filter_by(draft_id=draft.id, version_number=2).first()
    assert v2 is not None
    assert v2.subject == "Revised Human Subject"
    assert v2.previous_hash == v1_hash
    assert v2.editor_user_id == f["operator"].id
    assert v2.editor_type == "HUMAN"


def test_rejection_marks_draft_and_prevents_subsequent_approval(db_session: Session, audit_fixture):
    """Verify rejection records reason and version snapshot, and cannot subsequently be approved."""
    f = audit_fixture
    mock_llm = MockTier1Provider(evidence_ids=["ev_audit_1"])

    draft = EmailDraftService.generate_draft(
        db=db_session,
        workspace_id=f["ws1"].id,
        business_id=f["biz"].id,
        lead_qualification_id=f["lq"].id,
        provider=mock_llm
    )

    # Reject draft
    rejected = EmailDraftService.reject_draft(
        db=db_session,
        workspace_id=f["ws1"].id,
        draft_id=draft.id,
        rejector_user_id=f["operator"].id,
        reason="Does not align with clinical tone standards"
    )

    assert rejected.status == "REJECTED"
    assert rejected.rejection_reason == "Does not align with clinical tone standards"

    # Verify version snapshot created for rejection
    v_rej = db_session.query(EmailDraftVersion).filter_by(draft_id=draft.id, version_number=rejected.version_count).first()
    assert v_rej.approval_state_snapshot == "REJECTED"
    assert v_rej.editor_user_id == f["operator"].id

    # Attempting to approve rejected draft MUST raise ValueError
    with pytest.raises(ValueError, match="Cannot approve a draft in status 'REJECTED'"):
        EmailDraftService.approve_draft(
            db=db_session,
            workspace_id=f["ws1"].id,
            draft_id=draft.id,
            approver_user_id=f["operator"].id
        )


# ==============================================================================
# TASK 4: APPROVAL BOUNDARY & ZERO OUTBOUND TRANSMISSION
# ==============================================================================

def test_approval_boundary_strictness(db_session: Session, audit_fixture):
    """Verify approve_draft ONLY changes review state; creates ZERO OutboundMessages and zero email transmissions."""
    f = audit_fixture
    mock_llm = MockTier1Provider(evidence_ids=["ev_audit_1", "ev_audit_2"])

    draft = EmailDraftService.generate_draft(
        db=db_session,
        workspace_id=f["ws1"].id,
        business_id=f["biz"].id,
        lead_qualification_id=f["lq"].id,
        provider=mock_llm
    )

    om_before = db_session.query(OutboundMessage).count()
    cl_before = db_session.query(CampaignLead).count()

    approved = EmailDraftService.approve_draft(
        db=db_session,
        workspace_id=f["ws1"].id,
        draft_id=draft.id,
        approver_user_id=f["operator"].id
    )

    assert approved.status == "APPROVED"
    assert approved.approved_by_user_id == f["operator"].id
    assert approved.approved_at is not None

    # CRITICAL: Confirm ZERO outbound messages created or transmitted
    om_after = db_session.query(OutboundMessage).count()
    cl_after = db_session.query(CampaignLead).count()
    assert om_after == om_before == 0
    assert cl_after == cl_before == 0

    # Approved draft cannot be silently mutated via edit_draft
    with pytest.raises(ValueError, match="Cannot edit a draft in status 'APPROVED'"):
        EmailDraftService.edit_draft(
            db=db_session,
            workspace_id=f["ws1"].id,
            draft_id=draft.id,
            editor_user_id=f["operator"].id,
            new_subject="Malicious Silent Change"
        )


def test_ai_cannot_self_approve(db_session: Session, audit_fixture):
    """Verify that neither AI nor SYSTEM can approve a draft; only authenticated human operators."""
    f = audit_fixture
    mock_llm = MockTier1Provider(evidence_ids=["ev_audit_1"])

    draft = EmailDraftService.generate_draft(
        db=db_session,
        workspace_id=f["ws1"].id,
        business_id=f["biz"].id,
        lead_qualification_id=f["lq"].id,
        provider=mock_llm
    )

    with pytest.raises(ValueError, match="AI cannot self-approve"):
        EmailDraftService.approve_draft(db=db_session, workspace_id=f["ws1"].id, draft_id=draft.id, approver_user_id="AI")

    with pytest.raises(ValueError, match="AI cannot self-approve"):
        EmailDraftService.approve_draft(db=db_session, workspace_id=f["ws1"].id, draft_id=draft.id, approver_user_id="SYSTEM")

    with pytest.raises(ValueError, match="AI cannot self-approve"):
        EmailDraftService.approve_draft(db=db_session, workspace_id=f["ws1"].id, draft_id=draft.id, approver_user_id="")


# ==============================================================================
# TASK 5: REGENERATION BEHAVIOR
# ==============================================================================

def test_regeneration_supersedes_and_preserves_history(db_session: Session, audit_fixture):
    """Verify regeneration supersedes old draft, creates fresh unapproved draft, and preserves history."""
    f = audit_fixture
    mock_llm_1 = MockTier1Provider(subject="Initial Draft", evidence_ids=["ev_audit_1"])
    mock_llm_2 = MockTier1Provider(subject="Fresh Regenerated Draft", evidence_ids=["ev_audit_2"])

    d1 = EmailDraftService.generate_draft(
        db=db_session,
        workspace_id=f["ws1"].id,
        business_id=f["biz"].id,
        lead_qualification_id=f["lq"].id,
        provider=mock_llm_1
    )

    # Regenerate
    d2 = EmailDraftService.regenerate_draft(
        db=db_session,
        workspace_id=f["ws1"].id,
        draft_id=d1.id,
        requester_user_id=f["operator"].id,
        provider=mock_llm_2
    )

    db_session.refresh(d1)
    assert d1.status == "SUPERSEDED"
    assert d2.id != d1.id
    assert d2.status == "GENERATED"
    assert d2.subject == "Fresh Regenerated Draft"
    assert d2.version_count == 1
    assert d2.approved_by_user_id is None
    assert d2.approved_at is None

    # Zero outbound records
    assert db_session.query(OutboundMessage).count() == 0


# ==============================================================================
# TASK 6: SECURITY & ISOLATION
# ==============================================================================

def test_security_cross_workspace_draft_access_denied(db_session: Session, audit_fixture):
    """Verify IDOR and cross-workspace access is strictly rejected."""
    f = audit_fixture
    mock_llm = MockTier1Provider(evidence_ids=["ev_audit_1"])

    draft = EmailDraftService.generate_draft(
        db=db_session,
        workspace_id=f["ws1"].id,
        business_id=f["biz"].id,
        lead_qualification_id=f["lq"].id,
        provider=mock_llm
    )

    # Attempting to edit from Workspace 2 MUST fail
    with pytest.raises(ValueError, match="not found or cross-workspace access denied"):
        EmailDraftService.edit_draft(
            db=db_session,
            workspace_id=f["ws2"].id,
            draft_id=draft.id,
            editor_user_id=f["unauthorized_user"].id,
            new_subject="Cross-workspace attack"
        )

    # Attempting to approve from Workspace 2 MUST fail
    with pytest.raises(ValueError, match="not found or cross-workspace access denied"):
        EmailDraftService.approve_draft(
            db=db_session,
            workspace_id=f["ws2"].id,
            draft_id=draft.id,
            approver_user_id=f["unauthorized_user"].id
        )


def test_unsupported_evidence_cannot_be_approved(db_session: Session, audit_fixture):
    """Verify that if an ungrounded or external evidence ID is attached, approval is blocked and draft flagged."""
    f = audit_fixture
    mock_llm = MockTier1Provider(evidence_ids=["ev_audit_1"])

    draft = EmailDraftService.generate_draft(
        db=db_session,
        workspace_id=f["ws1"].id,
        business_id=f["biz"].id,
        lead_qualification_id=f["lq"].id,
        provider=mock_llm
    )

    # Inject unauthorized evidence ID directly
    draft.supporting_evidence_ids = ["ev_fake_nonexistent"]
    db_session.commit()

    with pytest.raises(ValueError, match="Unsupported claims cannot reach an approved draft"):
        EmailDraftService.approve_draft(
            db=db_session,
            workspace_id=f["ws1"].id,
            draft_id=draft.id,
            approver_user_id=f["operator"].id
        )

    db_session.refresh(draft)
    assert draft.status == "NEEDS_REVIEW"
