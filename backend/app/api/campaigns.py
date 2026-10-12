from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import select, func

from app.core.database import get_db
from app.core.dependencies import get_current_context, require_role, AuthContext
from app.models.campaign import Campaign, CampaignVersion
from app.models.lead import CampaignLead
from app.services.claim_governance import ClaimGovernanceService
from app.services.email_draft_service import CampaignLeadService

router = APIRouter(prefix="/campaigns", tags=["Campaigns & Versions"])

class CreateCampaignRequest(BaseModel):
    name: str
    description: Optional[str] = None

class CreateVersionRequest(BaseModel):
    sequence_steps: List[Dict[str, Any]] = []

class CampaignResponse(BaseModel):
    id: str
    workspace_id: str
    name: str
    description: Optional[str]
    status: str

class VersionResponse(BaseModel):
    id: str
    workspace_id: str
    campaign_id: str
    version_number: int
    status: str
    approval_hash: Optional[str]
    approved_by_user_id: Optional[str]
    approved_at: Optional[Any]
    sequence_steps: List[Dict[str, Any]]

@router.post("", response_model=CampaignResponse)
def create_campaign(
    req: CreateCampaignRequest,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_role(["OrgAdmin", "WorkspaceAdmin", "Operator"]))
):
    campaign = ClaimGovernanceService.create_campaign(
        db=db,
        workspace_id=ctx.active_workspace_id,
        name=req.name,
        description=req.description
    )
    return CampaignResponse(
        id=campaign.id,
        workspace_id=campaign.workspace_id,
        name=campaign.name,
        description=campaign.description,
        status=campaign.status
    )

@router.get("", response_model=List[CampaignResponse])
def list_campaigns(
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_role(["OrgAdmin", "WorkspaceAdmin", "Operator", "Auditor"]))
):
    campaigns = db.execute(
        select(Campaign).where(Campaign.workspace_id == ctx.active_workspace_id)
    ).scalars().all()
    return [
        CampaignResponse(
            id=c.id,
            workspace_id=c.workspace_id,
            name=c.name,
            description=c.description,
            status=c.status
        )
        for c in campaigns
    ]

@router.post("/{campaign_id}/versions", response_model=VersionResponse)
def create_campaign_version(
    campaign_id: str,
    req: CreateVersionRequest,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_role(["OrgAdmin", "WorkspaceAdmin", "Operator"]))
):
    try:
        ver = ClaimGovernanceService.create_campaign_version(
            db=db,
            workspace_id=ctx.active_workspace_id,
            campaign_id=campaign_id,
            sequence_steps=req.sequence_steps
        )
        return VersionResponse(
            id=ver.id,
            workspace_id=ver.workspace_id,
            campaign_id=ver.campaign_id,
            version_number=ver.version_number,
            status=ver.status,
            approval_hash=ver.approval_hash,
            approved_by_user_id=ver.approved_by_user_id,
            approved_at=ver.approved_at,
            sequence_steps=ver.sequence_steps
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

@router.post("/{campaign_id}/versions/{version_id}/approve", response_model=VersionResponse)
def approve_campaign_version(
    campaign_id: str,
    version_id: str,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_role(["OrgAdmin", "WorkspaceAdmin", "Operator"]))
):
    """
    Human Operator Approval Gate (Gate 1).
    Validates that all attached claims are OPERATOR_APPROVED and untampered, then commits approval.
    """
    try:
        ver = ClaimGovernanceService.approve_campaign_version(
            db=db,
            workspace_id=ctx.active_workspace_id,
            campaign_version_id=version_id,
            operator_user_id=ctx.user.id
        )
        return VersionResponse(
            id=ver.id,
            workspace_id=ver.workspace_id,
            campaign_id=ver.campaign_id,
            version_number=ver.version_number,
            status=ver.status,
            approval_hash=ver.approval_hash,
            approved_by_user_id=ver.approved_by_user_id,
            approved_at=ver.approved_at,
            sequence_steps=ver.sequence_steps
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

@router.get("/{campaign_id}/versions", response_model=List[VersionResponse])
def list_campaign_versions(
    campaign_id: str,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_role(["OrgAdmin", "WorkspaceAdmin", "Operator", "Auditor"]))
):
    versions = db.execute(
        select(CampaignVersion).where(
            CampaignVersion.campaign_id == campaign_id,
            CampaignVersion.workspace_id == ctx.active_workspace_id
        ).order_by(CampaignVersion.version_number.desc())
    ).scalars().all()
    return [
        VersionResponse(
            id=v.id,
            workspace_id=v.workspace_id,
            campaign_id=v.campaign_id,
            version_number=v.version_number,
            status=v.status,
            approval_hash=v.approval_hash,
            approved_by_user_id=v.approved_by_user_id,
            approved_at=v.approved_at,
            sequence_steps=v.sequence_steps
        )
        for v in versions
    ]


@router.get('/{campaign_id}')
def get_campaign_detail(
    campaign_id: str,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_role(['OrgAdmin', 'WorkspaceAdmin', 'Operator', 'Auditor']))
):
    campaign = db.execute(
        select(Campaign).where(
            Campaign.id == campaign_id,
            Campaign.workspace_id == ctx.active_workspace_id
        )
    ).scalars().first()
    if not campaign:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Campaign not found.')
    enrolled_count = db.execute(
        select(func.count(CampaignLead.id)).where(
            CampaignLead.campaign_id == campaign_id,
            CampaignLead.workspace_id == ctx.active_workspace_id,
            CampaignLead.status == 'ENROLLED'
        )
    ).scalar() or 0
    leads_list = db.execute(
        select(CampaignLead).where(
            CampaignLead.campaign_id == campaign_id,
            CampaignLead.workspace_id == ctx.active_workspace_id
        )
    ).scalars().all()
    return {
        'id': campaign.id,
        'workspace_id': campaign.workspace_id,
        'name': campaign.name,
        'description': campaign.description,
        'status': campaign.status,
        'enrolled_lead_count': enrolled_count,
        'leads': [
            {
                'id': cl.id,
                'lead_qualification_id': cl.lead_qualification_id,
                'email_draft_id': cl.email_draft_id,
                'status': cl.status,
                'created_at': cl.created_at.isoformat() if cl.created_at else None,
            }
            for cl in leads_list
        ],
    }


class EnrollLeadRequest(BaseModel):
    lead_qualification_id: str
    email_draft_id: str


@router.post('/{campaign_id}/leads')
def enroll_lead_in_campaign(
    campaign_id: str,
    req: EnrollLeadRequest,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_role(['OrgAdmin', 'WorkspaceAdmin', 'Operator']))
):
    try:
        cl = CampaignLeadService.add_lead(
            db=db,
            workspace_id=ctx.active_workspace_id,
            campaign_id=campaign_id,
            lead_qualification_id=req.lead_qualification_id,
            email_draft_id=req.email_draft_id,
            operator_user_id=ctx.user_id
        )
        return {
            'id': cl.id,
            'status': cl.status,
            'campaign_id': cl.campaign_id,
            'lead_qualification_id': cl.lead_qualification_id,
            'email_draft_id': cl.email_draft_id,
        }
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.delete('/{campaign_id}/leads/{campaign_lead_id}')
def remove_lead_from_campaign(
    campaign_id: str,
    campaign_lead_id: str,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_role(['OrgAdmin', 'WorkspaceAdmin', 'Operator']))
):
    try:
        cl = CampaignLeadService.remove_lead(
            db=db,
            workspace_id=ctx.active_workspace_id,
            campaign_lead_id=campaign_lead_id,
            operator_user_id=ctx.user_id
        )
        return {'id': cl.id, 'status': cl.status}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
