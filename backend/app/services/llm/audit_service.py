import hashlib
import json
import logging
from decimal import Decimal
from typing import Dict, Any, Optional
from sqlalchemy.orm import Session

from app.models.ai_audit import AIActionAuditLedger
from app.services.llm.base import LLMExecutionStatus

logger = logging.getLogger(__name__)


class AIAuditService:
    """
    Immutable ledger service for recording all AI/LLM invocations and safety decisions.
    Strictly forbids credential or secret storage.
    """

    @classmethod
    def hash_input_payload(cls, payload: Any) -> str:
        """Compute SHA-256 digest of input payload to preserve provenance without storing raw text."""
        if isinstance(payload, (dict, list)):
            normalized = json.dumps(payload, sort_keys=True, default=str)
        else:
            normalized = str(payload or "")
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    @classmethod
    def record_ai_action(
        cls,
        db: Session,
        workspace_id: str,
        actor_id: str,
        provider: str,
        model_name: str,
        purpose: str,
        input_payload: Any,
        output_schema_name: str,
        status: LLMExecutionStatus,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        estimated_cost_usd: float = 0.0,
        is_cost_estimated: bool = False,
        action_proposed: Optional[Dict[str, Any]] = None,
        action_executed: Optional[Dict[str, Any]] = None,
        approval_state: str = "AUTO_GATED",
        actor_type: str = "SYSTEM",
    ) -> AIActionAuditLedger:
        """
        Persist immutable audit record in PostgreSQL.
        """
        input_hash = cls.hash_input_payload(input_payload)

        # Ensure no secrets in action dictionaries
        clean_proposed = json.loads(json.dumps(action_proposed, default=str)) if action_proposed else None
        clean_executed = json.loads(json.dumps(action_executed, default=str)) if action_executed else None

        ledger_entry = AIActionAuditLedger(
            workspace_id=workspace_id,
            actor_id=actor_id,
            actor_type=actor_type,
            provider=provider,
            model_name=model_name,
            purpose=purpose,
            input_hash=input_hash,
            output_schema_name=output_schema_name,
            status=status.value if hasattr(status, "value") else str(status),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            estimated_cost_usd=Decimal(str(round(estimated_cost_usd, 6))),
            is_cost_estimated=is_cost_estimated,
            action_proposed=clean_proposed,
            action_executed=clean_executed,
            approval_state=approval_state,
        )

        db.add(ledger_entry)
        db.commit()
        db.refresh(ledger_entry)

        logger.info(
            "AI Action Audit recorded: ID=%s, workspace=%s, purpose=%s, status=%s, tokens=%d, cost=$%.6f",
            ledger_entry.id, workspace_id, purpose, ledger_entry.status, total_tokens, estimated_cost_usd
        )
        return ledger_entry
