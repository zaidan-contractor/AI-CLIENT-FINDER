from decimal import Decimal
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.core.database import get_db
from app.core.dependencies import get_current_context, AuthContext
from app.models.opportunity import ProblemHypothesis, ImpactAssessment, SolutionMapping
from app.services.hypothesis_engine import HypothesisEngine
from app.core.currency import is_valid_currency_code

router = APIRouter(prefix="/hypotheses", tags=["Problem Hypotheses & Impact"])

class GenerateHypothesesRequest(BaseModel):
    business_id: str
    currency: str = "USD"

class SolutionResponse(BaseModel):
    id: str
    solution_type: str
    title: str
    capability_summary: str
    feasibility_score: float
    operator_approved: bool

class ImpactResponse(BaseModel):
    impact_currency: str
    estimated_impact_low: float
    estimated_impact_high: float
    estimated_impact_midpoint: float
    business_specific_measurements: Dict[str, Any]
    industry_benchmark_assumptions: Dict[str, Any]

class HypothesisResponse(BaseModel):
    id: str
    business_id: str
    category: str
    title: str
    description: str
    status: str
    confidence: float
    refutation_reason: Optional[str] = None
    impact: Optional[ImpactResponse] = None
    solutions: List[SolutionResponse] = []

@router.post("/generate", response_model=List[HypothesisResponse])
def generate_hypotheses(
    req: GenerateHypothesesRequest,
    ctx: AuthContext = Depends(get_current_context),
    db: Session = Depends(get_db)
):
    if not is_valid_currency_code(req.currency, db):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid ISO-4217 currency code: '{req.currency}'."
        )

    engine = HypothesisEngine()
    hypotheses = engine.generate_and_evaluate_hypotheses(
        db=db,
        workspace_id=ctx.active_workspace_id,
        business_id=req.business_id,
        currency=req.currency
    )
    return hypotheses

@router.get("/{business_id}", response_model=List[HypothesisResponse])
def list_hypotheses(
    business_id: str,
    ctx: AuthContext = Depends(get_current_context),
    db: Session = Depends(get_db)
):
    hypotheses = db.execute(
        select(ProblemHypothesis).where(
            ProblemHypothesis.business_id == business_id,
            ProblemHypothesis.workspace_id == ctx.active_workspace_id
        )
    ).scalars().all()
    return hypotheses
