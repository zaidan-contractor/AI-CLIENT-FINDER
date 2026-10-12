from typing import List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.core.database import get_db
from app.core.dependencies import get_current_context, require_role, AuthContext
from app.models.tenancy import Organization, OrgSuppressionRecord, ComplianceSuppressionLedger
from app.schemas.auth import OrganizationResponse

router = APIRouter(prefix="/organizations", tags=["Organizations"])

@router.get("/current", response_model=OrganizationResponse)
def get_current_organization(
    ctx: AuthContext = Depends(get_current_context),
    db: Session = Depends(get_db)
):
    org = db.execute(
        select(Organization).where(Organization.id == ctx.organization_id)
    ).scalars().first()
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found."
        )
    return org

@router.get("/suppressions")
def get_org_suppressions(
    ctx: AuthContext = Depends(require_role(["SuperAdmin", "OrgAdmin", "Auditor"])),
    db: Session = Depends(get_db)
):
    """
    Returns organization-level suppression count and ledger statistics.
    Never exposes raw unencrypted compliance records unnecessarily.
    """
    org_records = db.execute(
        select(OrgSuppressionRecord).where(
            OrgSuppressionRecord.organization_id == ctx.organization_id
        )
    ).scalars().all()

    ledger_count = db.execute(
        select(ComplianceSuppressionLedger).where(
            ComplianceSuppressionLedger.organization_id == ctx.organization_id
        )
    ).scalars().all()

    return {
        "organization_id": ctx.organization_id,
        "operational_suppression_count": len(org_records),
        "compliance_ledger_count": len(ledger_count),
        "records": [
            {
                "id": r.id,
                "email_canonical": r.email_canonical,
                "reason": r.reason,
                "source_channel": r.source_channel,
                "created_at": r.created_at
            }
            for r in org_records
        ]
    }
