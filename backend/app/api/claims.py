from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.core.database import get_db
from app.core.dependencies import get_current_context, require_role, AuthContext
from app.models.campaign import ApprovedSalesClaimRecord
from app.services.claim_governance import ClaimGovernanceService

router = APIRouter(prefix="/claims", tags=["Sales Claims & Governance"])

class CreateClaimRequest(BaseModel):
    campaign_version_id: str
    hypothesis_id: str
    claim_text: str
    evidence_ids: List[str] = []

class MutateClaimRequest(BaseModel):
    new_claim_text: str

class ClaimResponse(BaseModel):
    id: str
    workspace_id: str
    campaign_version_id: str
    hypothesis_id: str
    claim_text: str
    claim_version: int
    evidence_ids: List[str]
    content_hash: str
    approval_status: str
    approved_by_user_id: Optional[str]
    approved_at: Optional[Any]
    integrity_valid: bool

@router.post("", response_model=ClaimResponse)
def create_claim(
    req: CreateClaimRequest,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_role(["OrgAdmin", "WorkspaceAdmin", "Operator"]))
):
    try:
        claim = ClaimGovernanceService.create_claim(
            db=db,
            workspace_id=ctx.active_workspace_id,
            campaign_version_id=req.campaign_version_id,
            hypothesis_id=req.hypothesis_id,
            claim_text=req.claim_text,
            evidence_ids=req.evidence_ids
        )
        return ClaimResponse(
            id=claim.id,
            workspace_id=claim.workspace_id,
            campaign_version_id=claim.campaign_version_id,
            hypothesis_id=claim.hypothesis_id,
            claim_text=claim.claim_text,
            claim_version=claim.claim_version,
            evidence_ids=claim.evidence_ids or [],
            content_hash=claim.content_hash,
            approval_status=claim.approval_status,
            approved_by_user_id=claim.approved_by_user_id,
            approved_at=claim.approved_at,
            integrity_valid=claim.verify_integrity()
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

@router.post("/{claim_id}/approve", response_model=ClaimResponse)
def approve_claim(
    claim_id: str,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_role(["OrgAdmin", "WorkspaceAdmin", "Operator"]))
):
    """
    Human Operator Approval Gate for Sales Claims (Gate 3).
    Cryptographically verifies hash integrity before promoting status to OPERATOR_APPROVED.
    """
    try:
        claim = ClaimGovernanceService.approve_claim(
            db=db,
            workspace_id=ctx.active_workspace_id,
            claim_id=claim_id,
            operator_user_id=ctx.user.id
        )
        return ClaimResponse(
            id=claim.id,
            workspace_id=claim.workspace_id,
            campaign_version_id=claim.campaign_version_id,
            hypothesis_id=claim.hypothesis_id,
            claim_text=claim.claim_text,
            claim_version=claim.claim_version,
            evidence_ids=claim.evidence_ids or [],
            content_hash=claim.content_hash,
            approval_status=claim.approval_status,
            approved_by_user_id=claim.approved_by_user_id,
            approved_at=claim.approved_at,
            integrity_valid=claim.verify_integrity()
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

@router.post("/{claim_id}/mutate", response_model=ClaimResponse)
def mutate_claim(
    claim_id: str,
    req: MutateClaimRequest,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_role(["OrgAdmin", "WorkspaceAdmin", "Operator"]))
):
    """
    Mutate claim text:
    Supersedes prior claim, creates new claim version with new content hash in PROPOSED status.
    Requires fresh operator approval!
    """
    try:
        new_claim = ClaimGovernanceService.mutate_claim_text(
            db=db,
            workspace_id=ctx.active_workspace_id,
            claim_id=claim_id,
            new_claim_text=req.new_claim_text
        )
        return ClaimResponse(
            id=new_claim.id,
            workspace_id=new_claim.workspace_id,
            campaign_version_id=new_claim.campaign_version_id,
            hypothesis_id=new_claim.hypothesis_id,
            claim_text=new_claim.claim_text,
            claim_version=new_claim.claim_version,
            evidence_ids=new_claim.evidence_ids or [],
            content_hash=new_claim.content_hash,
            approval_status=new_claim.approval_status,
            approved_by_user_id=new_claim.approved_by_user_id,
            approved_at=new_claim.approved_at,
            integrity_valid=new_claim.verify_integrity()
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

@router.get("", response_model=List[ClaimResponse])
def list_claims(
    campaign_version_id: Optional[str] = None,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_role(["OrgAdmin", "WorkspaceAdmin", "Operator", "Auditor"]))
):
    query = select(ApprovedSalesClaimRecord).where(
        ApprovedSalesClaimRecord.workspace_id == ctx.active_workspace_id
    )
    if campaign_version_id:
        query = query.where(ApprovedSalesClaimRecord.campaign_version_id == campaign_version_id)

    claims = db.execute(query).scalars().all()
    return [
        ClaimResponse(
            id=c.id,
            workspace_id=c.workspace_id,
            campaign_version_id=c.campaign_version_id,
            hypothesis_id=c.hypothesis_id,
            claim_text=c.claim_text,
            claim_version=c.claim_version,
            evidence_ids=c.evidence_ids or [],
            content_hash=c.content_hash,
            approval_status=c.approval_status,
            approved_by_user_id=c.approved_by_user_id,
            approved_at=c.approved_at,
            integrity_valid=c.verify_integrity()
        )
        for c in claims
    ]
