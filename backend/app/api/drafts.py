"""
Email Draft human review API endpoints.

Supports: read, edit, approve, reject, regenerate.

CRITICAL SAFETY RULES:
- AI cannot approve its own output (enforced: approver_user_id must be a real DB user).
- Approve endpoint only accepts OPERATOR+ roles.
- Approved draft ≠ send authorization. 16-gate pipeline is unchanged.
- All actions are workspace-scoped and IDOR-protected.
- No "Send Email" endpoint exists in this router.
"""
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import select, desc

from app.core.database import get_db
from app.core.dependencies import get_current_context, require_role, AuthContext
from app.models.lead import EmailDraft, EmailDraftVersion, CampaignLead
from app.services.email_draft_service import EmailDraftService, CampaignLeadService
from app.services.llm.factory import get_llm_provider

router = APIRouter(prefix="/drafts", tags=["Email Draft Review"])


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------
class EditDraftRequest(BaseModel):
    new_subject: Optional[str] = None
    new_body: Optional[str] = None


class RejectDraftRequest(BaseModel):
    reason: Optional[str] = None


# ---------------------------------------------------------------------------
# GET /drafts/{id}
# ---------------------------------------------------------------------------
@router.get("/{draft_id}")
def get_draft(
    draft_id: str,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_role(["OrgAdmin", "WorkspaceAdmin", "Operator", "Auditor"]))
):
    """Get a single email draft with full version history."""
    draft = db.execute(
        select(EmailDraft).where(
            EmailDraft.id == draft_id,
            EmailDraft.workspace_id == ctx.active_workspace_id
        )
    ).scalars().first()
    if not draft:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Email draft not found.")

    versions = db.execute(
        select(EmailDraftVersion).where(
            EmailDraftVersion.draft_id == draft_id,
            EmailDraftVersion.workspace_id == ctx.active_workspace_id
        ).order_by(EmailDraftVersion.version_number)
    ).scalars().all()

    return {
        "id": draft.id,
        "workspace_id": draft.workspace_id,
        "business_id": draft.business_id,
        "lead_qualification_id": draft.lead_qualification_id,
        "opportunity_id": draft.opportunity_id,
        "contact_id": draft.contact_id,
        "subject": draft.subject,
        "body": draft.body,
        "personalization_points": draft.personalization_points,
        "supporting_evidence_ids": draft.supporting_evidence_ids,
        "model_name": draft.model_name,
        "provider": draft.provider,
        "prompt_version": draft.prompt_version,
        "prompt_tokens": draft.prompt_tokens,
        "completion_tokens": draft.completion_tokens,
        "estimated_cost_usd": str(draft.estimated_cost_usd),
        "ai_audit_ledger_id": draft.ai_audit_ledger_id,
        "status": draft.status,
        "approved_by_user_id": draft.approved_by_user_id,
        "approved_at": draft.approved_at.isoformat() if draft.approved_at else None,
        "rejection_reason": draft.rejection_reason,
        "version_count": draft.version_count,
        "versions": [
            {
                "version_number": v.version_number,
                "subject": v.subject,
                "body": v.body,
                "content_hash": v.content_hash,
                "previous_hash": v.previous_hash,
                "changed_fields": v.changed_fields,
                "editor_user_id": v.editor_user_id,
                "editor_type": v.editor_type,
                "approval_state_snapshot": v.approval_state_snapshot,
                "created_at": v.created_at.isoformat() if v.created_at else None,
            }
            for v in versions
        ],
        "created_at": draft.created_at.isoformat() if draft.created_at else None,
        "updated_at": draft.updated_at.isoformat() if draft.updated_at else None,
    }


# ---------------------------------------------------------------------------
# PATCH /drafts/{id} — human edit
# ---------------------------------------------------------------------------
@router.patch("/{draft_id}")
def edit_draft(
    draft_id: str,
    req: EditDraftRequest,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_role(["OrgAdmin", "WorkspaceAdmin", "Operator"]))
):
    """
    Apply human edits to a draft.
    Creates an immutable version snapshot preserving the original AI output.
    Auditor role cannot edit drafts.
    """
    try:
        draft = EmailDraftService.edit_draft(
            db=db,
            workspace_id=ctx.active_workspace_id,
            draft_id=draft_id,
            editor_user_id=ctx.user_id,
            new_subject=req.new_subject,
            new_body=req.new_body
        )
        return {"id": draft.id, "status": draft.status, "version_count": draft.version_count,
                "subject": draft.subject}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


# ---------------------------------------------------------------------------
# POST /drafts/{id}/approve — human approval (AI cannot self-approve)
# ---------------------------------------------------------------------------
@router.post("/{draft_id}/approve")
def approve_draft(
    draft_id: str,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_role(["OrgAdmin", "WorkspaceAdmin", "Operator"]))
):
    """
    Explicit human operator approval of an email draft.

    CRITICAL INVARIANT: AI cannot self-approve.
    Auditor role cannot approve.
    Approved draft ≠ send authorization.
    The existing 16-gate OutboundDispatchEngine remains the sole send authority.
    """
    try:
        draft = EmailDraftService.approve_draft(
            db=db,
            workspace_id=ctx.active_workspace_id,
            draft_id=draft_id,
            approver_user_id=ctx.user_id
        )
        return {
            "id": draft.id,
            "status": draft.status,
            "approved_by_user_id": draft.approved_by_user_id,
            "approved_at": draft.approved_at.isoformat() if draft.approved_at else None,
            "note": "Draft approved. APPROVED_FOR_OUTBOUND status does NOT authorize transmission. "
                    "All 16 outbound gates must still be passed for any actual send."
        }
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


# ---------------------------------------------------------------------------
# POST /drafts/{id}/reject
# ---------------------------------------------------------------------------
@router.post("/{draft_id}/reject")
def reject_draft(
    draft_id: str,
    req: Optional[RejectDraftRequest] = None,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_role(["OrgAdmin", "WorkspaceAdmin", "Operator"]))
):
    """Reject a draft. Must regenerate before outbound consideration."""
    r = req or RejectDraftRequest()
    try:
        draft = EmailDraftService.reject_draft(
            db=db,
            workspace_id=ctx.active_workspace_id,
            draft_id=draft_id,
            rejector_user_id=ctx.user_id,
            reason=r.reason
        )
        return {"id": draft.id, "status": draft.status, "rejection_reason": draft.rejection_reason}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


# ---------------------------------------------------------------------------
# POST /drafts/{id}/regenerate
# ---------------------------------------------------------------------------
@router.post("/{draft_id}/regenerate")
def regenerate_draft(
    draft_id: str,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_role(["OrgAdmin", "WorkspaceAdmin", "Operator"]))
):
    """
    Supersede the existing draft and generate a fresh AI draft.
    Old draft preserved in version history.
    """
    try:
        from app.services.capacity_service import CapacityService
        policy = CapacityService.get_or_create_policy(db, ctx.active_workspace_id)
        floor = policy.min_quality_floor if policy and policy.min_quality_floor else "TIER_1_ADVANCED"
        provider = get_llm_provider(required_quality_floor=floor)
        new_draft = EmailDraftService.regenerate_draft(
            db=db,
            workspace_id=ctx.active_workspace_id,
            draft_id=draft_id,
            requester_user_id=ctx.user_id,
            provider=provider
        )
        return {
            "id": new_draft.id,
            "status": new_draft.status,
            "subject": new_draft.subject,
            "body": new_draft.body,
            "version_count": new_draft.version_count,
            "message": "New draft generated. Previous draft superseded and preserved in history."
        }
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
