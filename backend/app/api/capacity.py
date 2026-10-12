from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import select, desc

from app.core.database import get_db
from app.core.dependencies import get_current_context, require_role, AuthContext
from app.models.capacity import CapacityPolicy, CapacityDecisionAudit
from app.services.capacity_service import CapacityService
from app.services.llm.factory import get_llm_provider

router = APIRouter(prefix="/capacity", tags=["Capacity Policy & Quotas"])


class CapacityPolicyUpdateRequest(BaseModel):
    discovery_limit_per_day: Optional[int] = Field(None, ge=0)
    research_limit_per_day: Optional[int] = Field(None, ge=0)
    llm_operations_limit_per_day: Optional[int] = Field(None, ge=0)
    outreach_limit_per_day: Optional[int] = Field(None, ge=0)
    sender_limit_per_day: Optional[int] = Field(None, ge=0)
    campaign_limit_per_day: Optional[int] = Field(None, ge=0)
    min_quality_floor: Optional[str] = None
    timezone: Optional[str] = None
    is_active: Optional[bool] = None


@router.get("/status")
def get_capacity_status(
    auth_ctx: AuthContext = Depends(get_current_context),
    db: Session = Depends(get_db)
):
    """
    Returns real-time capacity limits, daily consumption, remaining capacity,
    and provider operational status for the active workspace.
    """
    workspace_id = auth_ctx.active_workspace_id
    summary = CapacityService.get_workspace_capacity_summary(db, workspace_id)

    # Attach operational constraints of active LLM provider respecting workspace quality floor
    policy = CapacityService.get_or_create_policy(db, workspace_id)
    quality_floor = policy.min_quality_floor or "TIER_1_ADVANCED"
    provider = get_llm_provider(required_quality_floor=quality_floor)
    constraints = provider.get_operational_constraints()
    meta = provider.get_model_metadata()

    from app.services.llm.provider_status_tracker import ProviderStatusTracker
    from app.core.config import settings

    all_providers: Dict[str, Any] = {}
    for p_name, m_name, has_cred in [
        ("OPENAI", getattr(settings, "OPENAI_MODEL", "gpt-4o-mini") or "gpt-4o-mini", bool(getattr(settings, "OPENAI_API_KEY", None))),
        ("GROQ", getattr(settings, "GROQ_MODEL", "openai/gpt-oss-120b") or "openai/gpt-oss-120b", bool(getattr(settings, "GROQ_API_KEY", None))),
        ("GEMINI", getattr(settings, "GEMINI_MODEL", "gemini-3.8-flash") or "gemini-3.8-flash", bool(getattr(settings, "GEMINI_API_KEY", None))),
    ]:
        p_state, p_details = ProviderStatusTracker.get_provider_state(p_name, m_name, has_credentials=has_cred)
        all_providers[p_name] = {
            "provider": p_name,
            "model": m_name,
            "state": p_state.value if hasattr(p_state, "value") else str(p_state),
            "is_usable": p_details.get("is_usable", False),
            "status_code": p_details.get("status_code"),
            "error_message": p_details.get("error_message"),
        }

    # Workspace-level LLM operations quota check
    llm_quota = summary.get("metrics", {}).get("llm_operations", {})
    workspace_exhausted = (llm_quota.get("remaining", 1) <= 0) if llm_quota else False

    summary["provider_status"] = {
        "provider": meta.get("provider", "UNKNOWN"),
        "provider_name": meta.get("provider", "UNKNOWN"),
        "model": meta.get("model", "UNKNOWN"),
        "quality_tier": constraints.quality_tier,
        "is_available": constraints.is_available,
        "state": constraints.provider_availability,
        "provider_availability": constraints.provider_availability,
        "model_availability": constraints.model_availability,
        "max_input_tokens": constraints.max_input_tokens,
        "max_output_tokens": constraints.max_output_tokens,
        "requests_per_minute": constraints.requests_per_minute,  # None if unknown
    }
    summary["all_providers"] = all_providers
    summary["workspace_exhausted"] = workspace_exhausted
    summary["quotas"] = summary["metrics"]
    summary["provider"] = summary["provider_status"]
    summary["policy"] = {
        "min_quality_floor": summary["min_quality_floor"],
        "timezone": summary["timezone"],
        "is_active": summary["is_active"]
    }

    return summary


@router.put("/policy")
def update_capacity_policy(
    payload: CapacityPolicyUpdateRequest,
    auth_ctx: AuthContext = Depends(require_role(["SuperAdmin", "OrgAdmin", "WorkspaceAdmin"])),
    db: Session = Depends(get_db)
):
    """
    Updates workspace-level capacity limits, quality floor, and timezone.
    Restricted to WorkspaceAdmin, OrgAdmin, and SuperAdmin.
    """
    workspace_id = auth_ctx.active_workspace_id
    policy = CapacityService.get_or_create_policy(db, workspace_id)

    if payload.discovery_limit_per_day is not None:
        policy.discovery_limit_per_day = payload.discovery_limit_per_day
    if payload.research_limit_per_day is not None:
        policy.research_limit_per_day = payload.research_limit_per_day
    if payload.llm_operations_limit_per_day is not None:
        policy.llm_operations_limit_per_day = payload.llm_operations_limit_per_day
    if payload.outreach_limit_per_day is not None:
        policy.outreach_limit_per_day = payload.outreach_limit_per_day
    if payload.sender_limit_per_day is not None:
        policy.sender_limit_per_day = payload.sender_limit_per_day
    if payload.campaign_limit_per_day is not None:
        policy.campaign_limit_per_day = payload.campaign_limit_per_day
    if payload.min_quality_floor is not None:
        policy.min_quality_floor = payload.min_quality_floor
    if payload.timezone is not None:
        policy.timezone = payload.timezone
    if payload.is_active is not None:
        policy.is_active = payload.is_active

    db.commit()
    db.refresh(policy)

    return CapacityService.get_workspace_capacity_summary(db, workspace_id)


@router.get("/decisions")
def get_capacity_decisions(
    limit: int = 50,
    auth_ctx: AuthContext = Depends(get_current_context),
    db: Session = Depends(get_db)
):
    """
    Returns immutable audit records of capacity and quality decisions for the workspace.
    Zero secrets or credentials exposed.
    """
    workspace_id = auth_ctx.active_workspace_id
    decisions = db.execute(
        select(CapacityDecisionAudit)
        .where(CapacityDecisionAudit.workspace_id == workspace_id)
        .order_by(desc(CapacityDecisionAudit.created_at))
        .limit(limit)
    ).scalars().all()

    return [
        {
            "id": d.id,
            "operation_type": d.operation_type,
            "decision": d.decision,
            "reason": d.reason,
            "remaining_capacity": d.remaining_capacity,
            "requested_provider": d.requested_provider,
            "requested_model": d.requested_model,
            "job_id": d.job_id,
            "created_at": d.created_at.isoformat() if d.created_at else None,
        }
        for d in decisions
    ]


@router.post("/drain-backpressure")
def drain_backpressure_queue(
    limit: int = 50,
    auth_ctx: AuthContext = Depends(require_role(["SuperAdmin", "OrgAdmin", "WorkspaceAdmin"])),
    db: Session = Depends(get_db)
):
    """
    Triggers deterministic draining of QUEUED_BACKPRESSURE messages in FIFO order.
    Halts immediately if daily outreach capacity is exhausted.
    Requires WorkspaceAdmin, OrgAdmin, or SuperAdmin.
    """
    from app.services.outbound_dispatch_engine import OutboundDispatchEngine
    workspace_id = auth_ctx.active_workspace_id
    result = OutboundDispatchEngine.process_backpressure_queue(db, workspace_id, limit=limit)
    return result

