import logging
from datetime import datetime, timezone, date
from decimal import Decimal
from typing import Tuple, Optional
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.core.config import settings
from app.models.ai_audit import AIWorkspaceDailyCost
from app.services.llm.openai_provider import OPENAI_PRICING, DEFAULT_PRICING

logger = logging.getLogger(__name__)


class CostGuardrail:
    """
    Deterministic budget guardrails and usage accounting for LLM invocations.
    """

    @classmethod
    def estimate_token_count(cls, text: str) -> int:
        """Rough token approximation: 1 token ~= 4 characters / 0.75 words."""
        if not text:
            return 0
        return max(1, int(len(text) / 3.8))

    @classmethod
    def estimate_cost(cls, prompt_tokens: int, completion_tokens: int, model: str) -> float:
        rates = OPENAI_PRICING.get(model, DEFAULT_PRICING)
        cost = (prompt_tokens * rates["prompt"]) + (completion_tokens * rates["completion"])
        return round(cost, 6)

    @classmethod
    def check_budget_preflight(
        cls,
        db: Session,
        workspace_id: str,
        estimated_input_tokens: int,
        estimated_output_tokens: int,
        model: str
    ) -> Tuple[bool, Optional[str]]:
        """
        Pre-execution guardrail check.
        Returns (is_allowed, rejection_reason).
        """
        # 1. Enforce input token threshold
        if estimated_input_tokens > settings.LLM_MAX_INPUT_TOKENS:
            reason = (
                f"Input tokens ({estimated_input_tokens}) exceed maximum limit "
                f"({settings.LLM_MAX_INPUT_TOKENS})"
            )
            logger.warning("CostGuardrail rejected preflight for workspace %s: %s", workspace_id, reason)
            return False, reason

        # 2. Enforce output token threshold
        if estimated_output_tokens > settings.LLM_MAX_OUTPUT_TOKENS:
            reason = (
                f"Requested output tokens ({estimated_output_tokens}) exceed limit "
                f"({settings.LLM_MAX_OUTPUT_TOKENS})"
            )
            logger.warning("CostGuardrail rejected preflight for workspace %s: %s", workspace_id, reason)
            return False, reason

        # 3. Enforce per-run cost ceiling
        estimated_run_cost = cls.estimate_cost(estimated_input_tokens, estimated_output_tokens, model)
        if estimated_run_cost > settings.LLM_MAX_COST_PER_RUN:
            reason = (
                f"Estimated run cost (${estimated_run_cost:.4f}) exceeds single-run limit "
                f"(${settings.LLM_MAX_COST_PER_RUN:.2f})"
            )
            logger.warning("CostGuardrail rejected preflight for workspace %s: %s", workspace_id, reason)
            return False, reason

        # 4. Check workspace daily budget
        today = datetime.now(timezone.utc).date()
        daily_record = db.execute(
            select(AIWorkspaceDailyCost).where(
                AIWorkspaceDailyCost.workspace_id == workspace_id,
                AIWorkspaceDailyCost.usage_date == today
            )
        ).scalar_one_or_none()

        current_daily_cost = float(daily_record.total_cost_usd) if daily_record else 0.0
        if current_daily_cost + estimated_run_cost > settings.LLM_DAILY_COST_LIMIT:
            reason = (
                f"Workspace daily AI budget exceeded. Current: ${current_daily_cost:.2f}, "
                f"Estimated run: ${estimated_run_cost:.4f}, Limit: ${settings.LLM_DAILY_COST_LIMIT:.2f}"
            )
            logger.warning("CostGuardrail rejected preflight for workspace %s: %s", workspace_id, reason)
            return False, reason

        return True, None

    @classmethod
    def record_usage_postflight(
        cls,
        db: Session,
        workspace_id: str,
        prompt_tokens: int,
        completion_tokens: int,
        cost_usd: float
    ) -> AIWorkspaceDailyCost:
        """
        Record actual usage and update workspace daily aggregate.
        """
        today = datetime.now(timezone.utc).date()
        daily_record = db.execute(
            select(AIWorkspaceDailyCost).where(
                AIWorkspaceDailyCost.workspace_id == workspace_id,
                AIWorkspaceDailyCost.usage_date == today
            )
        ).scalar_one_or_none()

        if daily_record is None:
            daily_record = AIWorkspaceDailyCost(
                workspace_id=workspace_id,
                usage_date=today,
                total_tokens=prompt_tokens + completion_tokens,
                total_cost_usd=Decimal(str(round(cost_usd, 4))),
                run_count=1,
            )
            db.add(daily_record)
        else:
            daily_record.total_tokens += (prompt_tokens + completion_tokens)
            daily_record.total_cost_usd = Decimal(str(float(daily_record.total_cost_usd) + cost_usd))
            daily_record.run_count += 1
            daily_record.updated_at = datetime.now(timezone.utc)

        db.commit()
        db.refresh(daily_record)
        return daily_record
