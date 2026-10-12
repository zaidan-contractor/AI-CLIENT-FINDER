import logging
from decimal import Decimal
from typing import Dict, Any

from app.core.database import SessionLocal
from app.services.outbound_dispatch_engine import OutboundDispatchEngine

logger = logging.getLogger(__name__)

def process_outbound_job_task(job_data: Dict[str, Any], db_session: Any = None) -> Dict[str, Any]:
    """
    Authoritative Background Worker Task for Outbound Dispatch.
    Executes in worker process (RQ or synchronous runner).
    Opens isolated database transaction, evaluates 16-gate pipeline,
    and dispatches through the configured provider transport.
    """
    db = db_session if db_session is not None else SessionLocal()
    should_close = db_session is None
    try:
        msg = OutboundDispatchEngine.dispatch_outbound_message(
            db=db,
            workspace_id=job_data["workspace_id"],
            campaign_version_id=job_data["campaign_version_id"],
            sender_account_id=job_data["sender_account_id"],
            recipient_email=job_data["recipient_email"],
            recipient_country=job_data.get("recipient_country", "US"),
            business_id=job_data.get("business_id"),
            subject=job_data["subject"],
            body_text=job_data["body_text"],
            body_html=job_data.get("body_html"),
            required_claim_ids=job_data.get("required_claim_ids", []),
            side_effect_idempotency_key=job_data["side_effect_idempotency_key"],
            thread_state=job_data.get("thread_state", "AUTONOMOUS"),
            projected_daily_burn=Decimal(str(job_data.get("projected_daily_burn", "10.00")))
        )
        return {
            "success": msg.status == "TRANSMITTED",
            "message_id": msg.id,
            "status": msg.status,
            "provider_message_id": msg.provider_message_id,
            "transport_mode": getattr(msg, "transport_mode", None),
            "gate_passed_count": msg.gate_passed_count,
            "failure_reason": msg.gate_failure_reason
        }
    except Exception as e:
        logger.error("Outbound worker task exception for key %s: %s", job_data.get("side_effect_idempotency_key"), e, exc_info=True)
        return {
            "success": False,
            "status": "WORKER_ERROR",
            "error": str(e)
        }
    finally:
        if should_close:
            db.close()

if __name__ == "__main__":
    import sys
    import os
    from app.core.config import settings
    import redis
    from rq import Queue, Worker, SimpleWorker

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    logger.info("Starting Outbound Worker connecting to Redis at %s...", settings.REDIS_URL)

    try:
        conn = redis.from_url(settings.REDIS_URL)
        conn.ping()
        logger.info("Connected to Redis server successfully.")
    except Exception as exc:
        logger.critical("Failed to connect to Redis: %s", exc)
        sys.exit(1)

    listen_queues = ["outbound_dispatch"]
    # On Windows or environments without fork, use SimpleWorker
    worker_cls = SimpleWorker if (not hasattr(os, "fork") or sys.platform == "win32") else Worker
    worker = worker_cls(listen_queues, connection=conn)
    logger.info("Worker %s listening on queues %s using %s", worker.name, listen_queues, worker_cls.__name__)
    worker.work(with_scheduler=True)

