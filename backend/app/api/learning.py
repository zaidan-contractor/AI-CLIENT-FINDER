from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.core.database import get_db
from app.core.dependencies import get_current_context, require_role, AuthContext
from app.models.learning import RecommendationRecord
from app.services.learning_service import LearningService

router = APIRouter(tags=["Learning Engine & Analytics"])

class RecommendationActionRequest(BaseModel):
    action: str  # "ACCEPTED" or "DISMISSED"

class RecommendationResponse(BaseModel):
    id: str
    workspace_id: str
    campaign_id: Optional[str] = None
    recommendation_type: str
    headline: str
    suggested_action: str
    rationale: str
    expected_benefit: Optional[str] = None
    status: str
    applied_by_user_id: Optional[str] = None
    applied_at: Optional[Any] = None


def _to_response(r: RecommendationRecord) -> RecommendationResponse:
    benefit = r.expected_benefit or ""
    return RecommendationResponse(
        id=r.id,
        workspace_id=r.workspace_id,
        campaign_id=r.campaign_id,
        recommendation_type=r.recommendation_type,
        headline=r.headline,
        suggested_action=r.suggested_action,
        rationale=benefit,
        expected_benefit=benefit,
        status=r.status,
        applied_by_user_id=r.applied_by_user_id,
        applied_at=r.applied_at
    )


# -----------------------------------------------------------------------------
# Workspace Analytics (Canonical: /learning/analytics/workspace & Alias: /analytics/workspace)
# -----------------------------------------------------------------------------
@router.get("/learning/analytics/workspace", response_model=Dict[str, Any])
@router.get("/analytics/workspace", response_model=Dict[str, Any])
def get_workspace_analytics(
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_role(["OrgAdmin", "WorkspaceAdmin", "Operator", "Auditor"]))
):
    return LearningService.analyze_workspace_performance(db, ctx.active_workspace_id)


# -----------------------------------------------------------------------------
# Recommendations Listing (Canonical: /learning/recommendations & Alias: /recommendations)
# -----------------------------------------------------------------------------
@router.get("/learning/recommendations", response_model=List[RecommendationResponse])
@router.get("/recommendations", response_model=List[RecommendationResponse])
def list_recommendations(
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_role(["OrgAdmin", "WorkspaceAdmin", "Operator", "Auditor"]))
):
    recs = db.execute(
        select(RecommendationRecord).where(RecommendationRecord.workspace_id == ctx.active_workspace_id)
        .order_by(RecommendationRecord.created_at.desc())
    ).scalars().all()

    return [_to_response(r) for r in recs]


# -----------------------------------------------------------------------------
# Recommendations Generation (Canonical: /learning/recommendations/generate & Alias: /recommendations/generate)
# -----------------------------------------------------------------------------
@router.post("/learning/recommendations/generate", response_model=List[RecommendationResponse])
@router.post("/recommendations/generate", response_model=List[RecommendationResponse])
def generate_recommendations(
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_role(["OrgAdmin", "WorkspaceAdmin", "Operator"]))
):
    """
    Triggers Recommendation-Only Pattern Synthesis.
    Zero silent mutations to active campaigns!
    """
    recs = LearningService.generate_recommendations(db, ctx.active_workspace_id)
    return [_to_response(r) for r in recs]


# -----------------------------------------------------------------------------
# Recommendation Action (Canonical: /learning/recommendations/{id}/action & Alias: /recommendations/{id}/action)
# -----------------------------------------------------------------------------
@router.post("/learning/recommendations/{recommendation_id}/action", response_model=RecommendationResponse)
@router.post("/recommendations/{recommendation_id}/action", response_model=RecommendationResponse)
def act_on_recommendation(
    recommendation_id: str,
    req: RecommendationActionRequest,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_role(["OrgAdmin", "WorkspaceAdmin", "Operator"]))
):
    if req.action not in ["ACCEPTED", "DISMISSED"]:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Action must be either 'ACCEPTED' or 'DISMISSED'"
        )

    try:
        rec = LearningService.update_recommendation_status(
            db=db,
            workspace_id=ctx.active_workspace_id,
            recommendation_id=recommendation_id,
            new_status=req.action,
            operator_user_id=ctx.user.id
        )
        return _to_response(rec)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
