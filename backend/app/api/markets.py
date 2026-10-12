from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.core.database import get_db
from app.core.dependencies import get_current_context, AuthContext
from app.models.business import MarketTarget
from app.services.market_engine import MarketEngine

router = APIRouter(prefix="/markets", tags=["Markets & Discovery"])

class MarketTargetCreate(BaseModel):
    query_text: str
    location: str
    target_category: str

class MarketTargetResponse(BaseModel):
    id: str
    workspace_id: str
    query_text: str
    location: str
    target_category: str
    status: str
    discovered_count: int

@router.post("", response_model=MarketTargetResponse)
def create_market_target(
    req: MarketTargetCreate,
    ctx: AuthContext = Depends(get_current_context),
    db: Session = Depends(get_db)
):
    target = MarketTarget(
        workspace_id=ctx.active_workspace_id,
        query_text=req.query_text,
        location=req.location,
        target_category=req.target_category
    )
    db.add(target)
    db.commit()
    db.refresh(target)
    return target

@router.get("", response_model=List[MarketTargetResponse])
def list_market_targets(
    ctx: AuthContext = Depends(get_current_context),
    db: Session = Depends(get_db)
):
    targets = db.execute(
        select(MarketTarget).where(MarketTarget.workspace_id == ctx.active_workspace_id)
    ).scalars().all()
    return targets

@router.post("/{target_id}/run")
def run_discovery(
    target_id: str,
    ctx: AuthContext = Depends(get_current_context),
    db: Session = Depends(get_db)
):
    engine = MarketEngine()
    try:
        result = engine.execute_discovery(
            db=db,
            workspace_id=ctx.active_workspace_id,
            target_id=target_id
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
