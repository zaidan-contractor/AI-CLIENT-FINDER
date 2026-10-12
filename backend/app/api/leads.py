"""
Lead Qualification and Lead Dossier API endpoints.

All endpoints require authentication, workspace authorization, and RBAC.
Tenant isolation is strictly enforced — workspace_id always comes from the
authenticated context, never from the client request body.

OUTBOUND BOUNDARY:
  No endpoint in this router triggers email transmission.
  APPROVED_FOR_OUTBOUND is a data state only.
  The existing /api/v1/outbound/* endpoints and 16-gate engine remain authoritative.
"""
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import select, desc

from app.core.database import get_db
from app.core.dependencies import get_current_context, require_role, AuthContext
from app.models.lead import LeadQualification, EmailDraft
from app.services.lead_qualification_service import LeadQualificationService
from app.services.lead_dossier_service import LeadDossierService
from app.services.email_draft_service import EmailDraftService
from app.services.llm.factory import get_llm_provider

router = APIRouter(prefix="/leads", tags=["Lead Qualification & Dossier"])


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------
class QualifyLeadRequest(BaseModel):
    operator_notes: Optional[str] = None
    force_status: Optional[str] = None   # Only: DISQUALIFIED, NEEDS_REVIEW, APPROVED_FOR_DRAFT


class GenerateDraftRequest(BaseModel):
    opportunity_id: Optional[str] = None
    contact_id: Optional[str] = None
    supersede_existing: bool = False


class LeadQualificationResponse(BaseModel):
    id: str
    workspace_id: str
    business_id: str
    status: str
    evidence_count: int
    fact_evidence_count: int
    validated_hypothesis_count: int
    has_eligible_contact: bool
    contact_verification_tier: Optional[str] = None
    primary_contact_id: Optional[str] = None
    primary_opportunity_id: Optional[str] = None
    solution_fit_type: Optional[str] = None
    opportunity_score_snapshot: Optional[float] = None
    rationale: Optional[str] = None
    qualification_notes: Optional[str] = None
    disqualification_reason: Optional[str] = None
    qualified_at: Optional[Any] = None
    approved_for_draft_at: Optional[Any] = None
    approved_for_outbound_at: Optional[Any] = None
    created_at: Any
    updated_at: Any


class DraftSummaryResponse(BaseModel):
    id: str
    status: str
    subject: str
    provider: str
    model_name: str
    version_count: int
    approved_by_user_id: Optional[str] = None
    approved_at: Optional[Any] = None
    created_at: Any


def _serialize_lead(lq: LeadQualification) -> LeadQualificationResponse:
    return LeadQualificationResponse(
        id=lq.id,
        workspace_id=lq.workspace_id,
        business_id=lq.business_id,
        status=lq.status,
        evidence_count=lq.evidence_count,
        fact_evidence_count=lq.fact_evidence_count,
        validated_hypothesis_count=lq.validated_hypothesis_count,
        has_eligible_contact=lq.has_eligible_contact,
        contact_verification_tier=lq.contact_verification_tier,
        primary_contact_id=lq.primary_contact_id,
        primary_opportunity_id=lq.primary_opportunity_id,
        solution_fit_type=lq.solution_fit_type,
        opportunity_score_snapshot=lq.opportunity_score_snapshot,
        rationale=lq.rationale,
        qualification_notes=lq.qualification_notes,
        disqualification_reason=lq.disqualification_reason,
        qualified_at=lq.qualified_at,
        approved_for_draft_at=lq.approved_for_draft_at,
        approved_for_outbound_at=lq.approved_for_outbound_at,
        created_at=lq.created_at,
        updated_at=lq.updated_at,
    )


# ---------------------------------------------------------------------------
# GET /leads — list all lead qualifications in workspace
# ---------------------------------------------------------------------------
@router.get("", response_model=List[LeadQualificationResponse])
def list_leads(
    status_filter: Optional[str] = Query(None, alias="status"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_role(["OrgAdmin", "WorkspaceAdmin", "Operator", "Auditor"]))
):
    """List all lead qualifications in the authenticated workspace."""
    q = select(LeadQualification).where(
        LeadQualification.workspace_id == ctx.active_workspace_id
    )
    if status_filter:
        q = q.where(LeadQualification.status == status_filter)
    q = q.order_by(desc(LeadQualification.updated_at)).offset(skip).limit(limit)
    leads = db.execute(q).scalars().all()
    return [_serialize_lead(lq) for lq in leads]


# ---------------------------------------------------------------------------
# GET /leads/{id} — get single lead qualification
# ---------------------------------------------------------------------------
@router.get("/{lead_id}", response_model=LeadQualificationResponse)
def get_lead(
    lead_id: str,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_role(["OrgAdmin", "WorkspaceAdmin", "Operator", "Auditor"]))
):
    lq = db.execute(
        select(LeadQualification).where(
            LeadQualification.id == lead_id,
            LeadQualification.workspace_id == ctx.active_workspace_id
        )
    ).scalars().first()
    if not lq:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead qualification not found.")
    return _serialize_lead(lq)


# ---------------------------------------------------------------------------
# GET /leads/by-business/{business_id} — get lead qualification for a business
# ---------------------------------------------------------------------------
@router.get("/by-business/{business_id}", response_model=Optional[LeadQualificationResponse])
def get_lead_by_business(
    business_id: str,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_role(["OrgAdmin", "WorkspaceAdmin", "Operator", "Auditor"]))
):
    lq = db.execute(
        select(LeadQualification).where(
            LeadQualification.business_id == business_id,
            LeadQualification.workspace_id == ctx.active_workspace_id
        ).order_by(desc(LeadQualification.updated_at))
    ).scalars().first()
    if not lq:
        return None
    return _serialize_lead(lq)


# ---------------------------------------------------------------------------
# POST /leads/{business_id}/qualify — run qualification engine
# ---------------------------------------------------------------------------
@router.post("/business/{business_id}/qualify", response_model=LeadQualificationResponse)
def qualify_lead(
    business_id: str,
    req: Optional[QualifyLeadRequest] = None,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_role(["OrgAdmin", "WorkspaceAdmin", "Operator"]))
):
    """
    Server-side lead qualification for a business.

    Evaluates: research completeness, evidence depth, validated hypotheses,
    contact eligibility (server-side DB lookup), solution fit, and opportunity score.

    If `force_status` is provided, only DISQUALIFIED, NEEDS_REVIEW, or APPROVED_FOR_DRAFT
    are accepted as manual overrides by operators.

    DOES NOT authorize outbound. The 16-gate pipeline remains authoritative.
    """
    r = req or QualifyLeadRequest()
    try:
        lq = LeadQualificationService.qualify_lead(
            db=db,
            workspace_id=ctx.active_workspace_id,
            business_id=business_id,
            operator_user_id=ctx.user_id,
            operator_notes=r.operator_notes,
            force_status=r.force_status
        )
        return _serialize_lead(lq)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


# ---------------------------------------------------------------------------
# GET /leads/{id}/dossier — full aggregated dossier
# ---------------------------------------------------------------------------
@router.get("/{lead_id}/dossier", response_model=Dict[str, Any])
def get_lead_dossier(
    lead_id: str,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_role(["OrgAdmin", "WorkspaceAdmin", "Operator", "Auditor"]))
):
    """
    Returns the full Lead Dossier: business, research, evidence, opportunities,
    contacts, qualification state, and email draft(s).
    Does NOT expose secrets or internal credentials.
    """
    # Resolve business_id from lead qualification (with workspace isolation)
    lq = db.execute(
        select(LeadQualification).where(
            LeadQualification.id == lead_id,
            LeadQualification.workspace_id == ctx.active_workspace_id
        )
    ).scalars().first()
    if not lq:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead qualification not found.")

    try:
        dossier = LeadDossierService.build_dossier(
            db=db,
            workspace_id=ctx.active_workspace_id,
            business_id=lq.business_id
        )
        return dossier
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


# ---------------------------------------------------------------------------
# POST /leads/{id}/draft — generate AI email draft
# ---------------------------------------------------------------------------
@router.post("/{lead_id}/draft")
def generate_lead_draft(
    lead_id: str,
    req: Optional[GenerateDraftRequest] = None,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_role(["OrgAdmin", "WorkspaceAdmin", "Operator"]))
):
    """
    Generate an AI email draft for the lead.

    Uses deterministic rule-based provider (no live LLM calls unless configured).
    Capacity policy enforced. Evidence traceability validated.
    Logs to AIActionAuditLedger.

    Does NOT send any email. Does NOT bypass any gate.
    """
    lq = db.execute(
        select(LeadQualification).where(
            LeadQualification.id == lead_id,
            LeadQualification.workspace_id == ctx.active_workspace_id
        )
    ).scalars().first()
    if not lq:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead qualification not found.")

    r = req or GenerateDraftRequest()
    try:
        from app.services.capacity_service import CapacityService
        policy = CapacityService.get_or_create_policy(db, ctx.active_workspace_id)
        floor = policy.min_quality_floor if policy and policy.min_quality_floor else "TIER_1_ADVANCED"
        provider = get_llm_provider(required_quality_floor=floor)
        draft = EmailDraftService.generate_draft(
            db=db,
            workspace_id=ctx.active_workspace_id,
            business_id=lq.business_id,
            lead_qualification_id=lead_id,
            opportunity_id=r.opportunity_id,
            contact_id=r.contact_id,
            provider=provider,
            supersede_existing=r.supersede_existing
        )
        return {"id": draft.id, "status": draft.status, "subject": draft.subject,
                "body": draft.body, "version_count": draft.version_count,
                "provider": draft.provider, "model_name": draft.model_name,
                "supporting_evidence_ids": draft.supporting_evidence_ids,
                "personalization_points": draft.personalization_points,
                "created_at": draft.created_at.isoformat() if draft.created_at else None}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


# ---------------------------------------------------------------------------
# GET /leads/{id}/drafts — list all drafts for a lead
# ---------------------------------------------------------------------------
@router.get("/{lead_id}/drafts", response_model=List[DraftSummaryResponse])
def list_lead_drafts(
    lead_id: str,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_role(["OrgAdmin", "WorkspaceAdmin", "Operator", "Auditor"]))
):
    lq = db.execute(
        select(LeadQualification).where(
            LeadQualification.id == lead_id,
            LeadQualification.workspace_id == ctx.active_workspace_id
        )
    ).scalars().first()
    if not lq:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead qualification not found.")

    drafts = db.execute(
        select(EmailDraft).where(
            EmailDraft.lead_qualification_id == lead_id,
            EmailDraft.workspace_id == ctx.active_workspace_id
        ).order_by(desc(EmailDraft.created_at))
    ).scalars().all()
    return [
        DraftSummaryResponse(
            id=d.id,
            status=d.status,
            subject=d.subject,
            provider=d.provider,
            model_name=d.model_name,
            version_count=d.version_count,
            approved_by_user_id=d.approved_by_user_id,
            approved_at=d.approved_at,
            created_at=d.created_at
        )
        for d in drafts
    ]


# ---------------------------------------------------------------------------
# Also: qualify directly by lead_id (alias, used in tests)
# ---------------------------------------------------------------------------
@router.post("/{lead_id}/qualify", response_model=LeadQualificationResponse)
def qualify_lead_by_id(
    lead_id: str,
    req: Optional[QualifyLeadRequest] = None,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_role(["OrgAdmin", "WorkspaceAdmin", "Operator"]))
):
    """Re-run qualification engine on an existing LeadQualification record."""
    lq = db.execute(
        select(LeadQualification).where(
            LeadQualification.id == lead_id,
            LeadQualification.workspace_id == ctx.active_workspace_id
        )
    ).scalars().first()
    if not lq:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead qualification not found.")
    r = req or QualifyLeadRequest()
    try:
        updated = LeadQualificationService.qualify_lead(
            db=db,
            workspace_id=ctx.active_workspace_id,
            business_id=lq.business_id,
            operator_user_id=ctx.user_id,
            operator_notes=r.operator_notes,
            force_status=r.force_status
        )
        return _serialize_lead(updated)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
