from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from sqlalchemy import select
from typing import Dict, Any, List, Optional
from pydantic import BaseModel

from app.core.database import get_db
from app.core.dependencies import get_current_context, require_role, AuthContext
from app.services.scoring_engine import ScoringEngine
from app.models.opportunity import ProblemHypothesis, SolutionMapping, ImpactAssessment

router = APIRouter(prefix="/opportunities", tags=["Opportunities & Scoring"])


class SolutionSummary(BaseModel):
    id: str
    solution_type: str
    title: str
    capability_summary: str
    feasibility_score: float
    operator_approved: bool


class ImpactSummary(BaseModel):
    impact_currency: str
    estimated_impact_low: float
    estimated_impact_high: float
    estimated_impact_midpoint: float


class OpportunityItem(BaseModel):
    id: str
    business_id: str
    category: str
    title: str
    description: str
    status: str
    confidence: float
    supporting_evidence_ids: List[str] = []
    impact: Optional[ImpactSummary] = None
    solutions: List[SolutionSummary] = []


@router.get("", response_model=List[OpportunityItem])
def list_workspace_opportunities(
    category: Optional[str] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_role(["OrgAdmin", "WorkspaceAdmin", "Operator", "Viewer"]))
):
    query = select(ProblemHypothesis).where(ProblemHypothesis.workspace_id == ctx.active_workspace_id)
    if category:
        query = query.where(ProblemHypothesis.category == category)
    if status_filter:
        query = query.where(ProblemHypothesis.status == status_filter)
    query = query.offset(skip).limit(limit)
    items = db.execute(query).scalars().all()
    return items


@router.post("/{business_id}/score", response_model=Dict[str, Any])
def score_business_opportunity(
    business_id: str,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_role(["OrgAdmin", "WorkspaceAdmin", "Operator"]))
):
    try:
        result = ScoringEngine.evaluate_business(
            db=db,
            workspace_id=ctx.active_workspace_id,
            business_id=business_id
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@router.get("/{business_id}", response_model=Dict[str, Any])
def get_business_opportunity(
    business_id: str,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_role(["OrgAdmin", "WorkspaceAdmin", "Operator", "Viewer"]))
):
    try:
        result = ScoringEngine.evaluate_business(
            db=db,
            workspace_id=ctx.active_workspace_id,
            business_id=business_id
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
