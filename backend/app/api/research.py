from typing import List, Optional, Any
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.core.database import get_db
from app.core.dependencies import get_current_context, AuthContext
from app.models.research import EvidenceRecord, ResearchRun
from app.services.crawler_engine import CrawlerEngine
from app.services.research_service import ResearchService

router = APIRouter(prefix="/research", tags=["Research & Evidence"])


class RunResearchRequest(BaseModel):
    business_id: str
    mock_html: Optional[str] = None
    mock_status_code: int = 200
    mock_is_accessible: bool = True


class EvidenceResponse(BaseModel):
    id: str
    business_id: str
    signal_type: str
    source_url: Optional[str] = None
    raw_observation: str
    feature_presence_state: str
    confidence: float
    classification_reason: str


class ResearchRunStatusResponse(BaseModel):
    id: str
    workspace_id: str
    business_id: str
    status: str
    trigger_type: str
    evidence_count: int
    error_message: Optional[str] = None
    started_at: Optional[Any] = None
    completed_at: Optional[Any] = None


@router.post("/run")
def run_research(
    req: RunResearchRequest,
    ctx: AuthContext = Depends(get_current_context),
    db: Session = Depends(get_db)
):
    crawler = CrawlerEngine()
    try:
        records = crawler.run_research_pipeline(
            db=db,
            workspace_id=ctx.active_workspace_id,
            business_id=req.business_id,
            mock_html=req.mock_html,
            mock_status_code=req.mock_status_code,
            mock_is_accessible=req.mock_is_accessible
        )
        return {
            "business_id": req.business_id,
            "status": "COMPLETED",
            "evidence_count": len(records),
            "evidence": [
                {
                    "id": r.id,
                    "signal_type": r.signal_type,
                    "presence_state": r.feature_presence_state,
                    "confidence": r.confidence,
                    "observation": r.raw_observation,
                    "reason": r.classification_reason
                }
                for r in records
            ]
        }
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@router.get("/runs/{run_id}", response_model=ResearchRunStatusResponse)
def get_research_run_status(
    run_id: str,
    ctx: AuthContext = Depends(get_current_context),
    db: Session = Depends(get_db)
):
    run = db.execute(
        select(ResearchRun).where(
            ResearchRun.id == run_id,
            ResearchRun.workspace_id == ctx.active_workspace_id
        )
    ).scalars().first()
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ResearchRun not found.")
    return run


@router.get("/{business_id}/evidence", response_model=List[EvidenceResponse])
def get_business_evidence(
    business_id: str,
    ctx: AuthContext = Depends(get_current_context),
    db: Session = Depends(get_db)
):
    records = db.execute(
        select(EvidenceRecord).where(
            EvidenceRecord.business_id == business_id,
            EvidenceRecord.workspace_id == ctx.active_workspace_id
        )
    ).scalars().all()
    return records
