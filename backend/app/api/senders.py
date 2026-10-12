from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.core.database import get_db
from app.core.dependencies import get_current_context, require_role, AuthContext
from app.core.security import encrypt_credential
from app.models.outreach import SenderAccount
from app.services.sender_readiness import SenderReadinessService

router = APIRouter(prefix="/senders", tags=["Sender Accounts & Readiness"])

class CreateSenderRequest(BaseModel):
    email: str
    display_name: str
    provider_type: str = "SMTP"  # "SMTP", "SENDGRID", "RESEND"
    credentials_secret: str  # Encrypted before storage via envelope encryption
    daily_quota: int = 50
    warmup_max_daily: int = 20

class SenderResponse(BaseModel):
    id: str
    workspace_id: str
    email: str
    display_name: str
    provider_type: str
    status: str
    daily_quota: int
    warmup_day: int
    warmup_max_daily: int
    readiness_state: str
    is_ready_for_outreach: bool

@router.post("", response_model=SenderResponse)
def register_sender(
    req: CreateSenderRequest,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_role(["OrgAdmin", "WorkspaceAdmin"]))
):
    # Envelope encryption of credentials
    encrypted_cred = encrypt_credential(req.credentials_secret)

    # Live DNS Infrastructure Verification (SPF, DKIM, DMARC)
    from app.services.dns_verifier import DNSVerifierService
    dns_res = DNSVerifierService.verify_sender_infrastructure(req.email)

    sender = SenderAccount(
        workspace_id=ctx.active_workspace_id,
        email=req.email.lower().strip(),
        display_name=req.display_name,
        provider_type=req.provider_type,
        encrypted_credentials=encrypted_cred,
        status="WARMING_UP",
        daily_quota=req.daily_quota,
        warmup_max_daily=req.warmup_max_daily,
        connection_verified=True,
        dns_valid=dns_res["all_dns_valid"],
        spf_valid=dns_res["spf"]["valid"],
        dkim_valid=dns_res["dkim"]["valid"],
        dmarc_valid=dns_res["dmarc"]["valid"],
        tracking_domain_aligned=True,
        delivery_health_ok=True
    )
    db.add(sender)
    db.commit()
    db.refresh(sender)

    eval_res = SenderReadinessService.evaluate_account(sender)

    return SenderResponse(
        id=sender.id,
        workspace_id=sender.workspace_id,
        email=sender.email,
        display_name=sender.display_name,
        provider_type=sender.provider_type,
        status=sender.status,
        daily_quota=sender.daily_quota,
        warmup_day=sender.warmup_day,
        warmup_max_daily=sender.warmup_max_daily,
        readiness_state=eval_res["readiness_state"],
        is_ready_for_outreach=eval_res["is_ready_for_outreach"]
    )

@router.get("", response_model=List[SenderResponse])
def list_senders(
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_role(["OrgAdmin", "WorkspaceAdmin", "Operator", "Auditor"]))
):
    senders = db.execute(
        select(SenderAccount).where(SenderAccount.workspace_id == ctx.active_workspace_id)
    ).scalars().all()

    out = []
    for s in senders:
        eval_res = SenderReadinessService.evaluate_account(s)
        out.append(
            SenderResponse(
                id=s.id,
                workspace_id=s.workspace_id,
                email=s.email,
                display_name=s.display_name,
                provider_type=s.provider_type,
                status=s.status,
                daily_quota=s.daily_quota,
                warmup_day=s.warmup_day,
                warmup_max_daily=s.warmup_max_daily,
                readiness_state=eval_res["readiness_state"],
                is_ready_for_outreach=eval_res["is_ready_for_outreach"]
            )
        )
    return out

@router.get("/{sender_id}/readiness", response_model=Dict[str, Any])
def get_sender_readiness(
    sender_id: str,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_role(["OrgAdmin", "WorkspaceAdmin", "Operator", "Auditor"]))
):
    sender = db.execute(
        select(SenderAccount).where(
            SenderAccount.id == sender_id,
            SenderAccount.workspace_id == ctx.active_workspace_id
        )
    ).scalars().first()
    if not sender:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sender not found.")

    return SenderReadinessService.evaluate_account(sender)

@router.post("/{sender_id}/verify-dns", response_model=Dict[str, Any])
def recheck_sender_dns(
    sender_id: str,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_role(["OrgAdmin", "WorkspaceAdmin", "Operator"]))
):
    """
    Triggers live DNS re-verification (SPF, DKIM, DMARC) for a sender domain.
    """
    sender = db.execute(
        select(SenderAccount).where(
            SenderAccount.id == sender_id,
            SenderAccount.workspace_id == ctx.active_workspace_id
        )
    ).scalars().first()
    if not sender:
        raise HTTPException(status_code=404, detail="Sender account not found.")

    from app.services.dns_verifier import DNSVerifierService
    dns_res = DNSVerifierService.verify_sender_infrastructure(sender.email)
    sender.dns_valid = dns_res["all_dns_valid"]
    sender.spf_valid = dns_res["spf"]["valid"]
    sender.dkim_valid = dns_res["dkim"]["valid"]
    sender.dmarc_valid = dns_res["dmarc"]["valid"]
    db.commit()
    db.refresh(sender)

    eval_res = SenderReadinessService.evaluate_account(sender)
    return {
        "dns_verification": dns_res,
        "readiness_state": eval_res["readiness_state"],
        "is_ready_for_outreach": eval_res["is_ready_for_outreach"]
    }
