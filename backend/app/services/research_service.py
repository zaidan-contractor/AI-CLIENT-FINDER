import logging
from typing import Dict, Any, Optional, List, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.models.research import ResearchRun, EvidenceRecord
from app.models.business import MasterBusinessRecord
from app.services.crawler_engine import CrawlerEngine
from app.models.tenancy import get_utc_now

logger = logging.getLogger(__name__)


class ResearchService:
    """
    Durable ResearchRun Lifecycle Orchestrator.
    Manages the state transitions (QUEUED -> RUNNING -> COMPLETED/FAILED/PARTIAL)
    of research operations and enforces idempotency.
    """

    @classmethod
    def get_or_create_research_run(
        cls,
        db: Session,
        workspace_id: str,
        business_id: str,
        trigger_type: str = "MANUAL",
        requested_scope: Optional[List[str]] = None,
        idempotency_key: Optional[str] = None
    ) -> Tuple[ResearchRun, bool]:
        """
        Retrieves existing run if idempotency_key is provided and matched,
        or initializes a new durable ResearchRun in QUEUED status.
        Returns (research_run, is_new).
        """
        if idempotency_key:
            existing = db.execute(
                select(ResearchRun).where(
                    ResearchRun.workspace_id == workspace_id,
                    ResearchRun.idempotency_key == idempotency_key
                )
            ).scalars().first()
            if existing:
                return existing, False

        run = ResearchRun(
            workspace_id=workspace_id,
            business_id=business_id,
            status="QUEUED",
            trigger_type=trigger_type,
            requested_scope=requested_scope or ["WEB_DOM", "INFRA_PROBE", "REVIEWS"],
            idempotency_key=idempotency_key
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        return run, True

    @classmethod
    def execute_research_run(
        cls,
        db: Session,
        research_run_id: str,
        mock_html: Optional[str] = None,
        mock_status_code: int = 200,
        mock_is_accessible: bool = True,
        mock_reviews: Optional[List[Dict[str, Any]]] = None
    ) -> ResearchRun:
        """
        Executes an initialized ResearchRun:
        Transitions status: RUNNING -> COMPLETED (or FAILED / PARTIAL).
        Collects bounded evidence and updates metrics.
        """
        run = db.execute(
            select(ResearchRun).where(ResearchRun.id == research_run_id)
        ).scalars().first()

        if not run:
            raise ValueError(f"ResearchRun '{research_run_id}' not found.")

        # If already completed or failed, return as idempotent response
        if run.status in ["COMPLETED", "FAILED", "CANCELLED"]:
            return run

        run.status = "RUNNING"
        run.started_at = get_utc_now()
        db.commit()

        crawler = CrawlerEngine()
        try:
            records = crawler.run_research_pipeline(
                db=db,
                workspace_id=run.workspace_id,
                business_id=run.business_id,
                research_run_id=run.id,
                mock_html=mock_html,
                mock_status_code=mock_status_code,
                mock_is_accessible=mock_is_accessible,
                mock_reviews=mock_reviews
            )

            run.evidence_count = len(records)
            run.status = "COMPLETED"
            run.completed_at = get_utc_now()
            db.commit()
            db.refresh(run)
            return run

        except Exception as e:
            logger.error("ResearchRun %s execution error: %s", research_run_id, e, exc_info=True)
            run.status = "FAILED"
            run.error_message = str(e)
            run.completed_at = get_utc_now()
            db.commit()
            db.refresh(run)
            return run
