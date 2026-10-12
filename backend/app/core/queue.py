import json
import logging
import uuid
from typing import Dict, Any, Optional
from pydantic import BaseModel

from app.core.config import settings

logger = logging.getLogger(__name__)

class OutboundJobPayload(BaseModel):
    job_id: str
    workspace_id: str
    organization_id: str
    campaign_version_id: str
    sender_account_id: str
    recipient_email: str
    recipient_country: str = "US"
    business_id: Optional[str] = None
    subject: str
    body_text: str
    body_html: Optional[str] = None
    required_claim_ids: list = []
    side_effect_idempotency_key: str
    thread_state: str = "AUTONOMOUS"
    projected_daily_burn: str = "10.00"

class JobQueueManager:
    """
    Production Redis/RQ Queue & Worker Boundary with fallback to synchronous/in-process execution.
    Preserves tenant context safely without putting credentials into job queues.
    """
    _redis_conn = None
    _rq_queue = None

    @classmethod
    def _get_redis_queue(cls):
        if cls._rq_queue is not None:
            return cls._rq_queue

        if settings.QUEUE_MODE == "sync":
            return None

        try:
            import redis
            from rq import Queue
            conn = redis.from_url(settings.REDIS_URL, socket_timeout=2)
            conn.ping()
            cls._redis_conn = conn
            cls._rq_queue = Queue("outbound_dispatch", connection=conn)
            logger.info("Connected to Redis RQ queue 'outbound_dispatch'.")
            return cls._rq_queue
        except Exception as e:
            logger.warning("Redis not available or connection failed (%s). Falling back to in-process execution.", e)
            return None

    @classmethod
    def enqueue_outbound_dispatch(cls, payload: OutboundJobPayload, db_session: Any = None) -> Dict[str, Any]:
        """
        Enqueues job to Redis RQ if available, otherwise executes synchronously.
        Guarantees that job payload contains no plaintext or decrypted secrets.
        """
        queue = cls._get_redis_queue()

        if queue and db_session is None:
            try:
                from app.workers.outbound_worker import process_outbound_job_task
                job = queue.enqueue(
                    process_outbound_job_task,
                    payload.model_dump(),
                    job_id=payload.job_id,
                    result_ttl=86400,
                    failure_ttl=86400
                )
                return {
                    "job_id": job.id,
                    "status": "QUEUED",
                    "mode": "REDIS_RQ",
                    "queue": "outbound_dispatch"
                }
            except Exception as ex:
                logger.error("Failed to enqueue into Redis RQ: %s. Processing in-process.", ex)

        # In-process / Synchronous fallback (e.g. during test suite or dev without Redis)
        from app.workers.outbound_worker import process_outbound_job_task
        result = process_outbound_job_task(payload.model_dump(), db_session=db_session)
        return {
            "job_id": payload.job_id,
            "status": result.get("status", "COMPLETED"),
            "mode": "SYNCHRONOUS_DEV",
            "result": result
        }
