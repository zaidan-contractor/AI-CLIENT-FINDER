import logging
import os
import sys
from typing import Dict, Any, Optional

from app.core.database import SessionLocal
from app.services.research_service import ResearchService

logger = logging.getLogger(__name__)


def process_research_job_task(job_data: Dict[str, Any], db_session: Any = None) -> Dict[str, Any]:
    """
    Authoritative Background Worker Task for Research Execution.
    Executes in worker process (RQ or synchronous runner).
    Transitions ResearchRun status to RUNNING -> COMPLETED/FAILED,
    collects bounded evidence, and triggers contact enrichment.
    """
    db = db_session if db_session is not None else SessionLocal()
    should_close = db_session is None
    research_run_id = job_data.get("research_run_id")

    try:
        run = ResearchService.execute_research_run(
            db=db,
            research_run_id=research_run_id,
            mock_html=job_data.get("mock_html"),
            mock_status_code=job_data.get("mock_status_code", 200),
            mock_is_accessible=job_data.get("mock_is_accessible", True),
            mock_reviews=job_data.get("mock_reviews")
        )
        return {
            "success": run.status == "COMPLETED",
            "research_run_id": run.id,
            "status": run.status,
            "evidence_count": run.evidence_count,
            "error_message": run.error_message
        }
    except Exception as e:
        logger.error("Research worker task exception for run %s: %s", research_run_id, e, exc_info=True)
        return {
            "success": False,
            "research_run_id": research_run_id,
            "status": "WORKER_ERROR",
            "error": str(e)
        }
    finally:
        if should_close:
            db.close()


if __name__ == "__main__":
    from app.core.config import settings
    import redis
    from rq import Queue, Worker, SimpleWorker

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    logger.info("Starting Research Worker connecting to Redis at %s...", settings.REDIS_URL)

    try:
        conn = redis.from_url(settings.REDIS_URL)
        conn.ping()
        logger.info("Connected to Redis server successfully.")
    except Exception as exc:
        logger.critical("Failed to connect to Redis: %s", exc)
        sys.exit(1)

    listen_queues = ["research_jobs"]
    worker_cls = SimpleWorker if (not hasattr(os, "fork") or sys.platform == "win32") else Worker
    worker = worker_cls(listen_queues, connection=conn)
    logger.info("Worker %s listening on queues %s using %s", worker.name, listen_queues, worker_cls.__name__)
    worker.work(with_scheduler=True)
