import logging
from datetime import datetime, timezone, date
from enum import Enum
from typing import Tuple, Dict, Any, Optional
from zoneinfo import ZoneInfo
from sqlalchemy.orm import Session
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.models.capacity import CapacityPolicy, WorkspaceCapacityDailyUsage, CapacityDecisionAudit
from app.services.llm.base import LLMProvider, ProviderOperationalConstraints

logger = logging.getLogger(__name__)

QUALITY_TIER_RANK = {
    "TIER_1_ADVANCED": 3,
    "TIER_2_STANDARD": 2,
    "FALLBACK_HEURISTIC": 1,
    "UNKNOWN": 0,
}

OPERATION_FIELD_MAP = {
    "DISCOVERY": ("discovery_count", "discovery_limit_per_day"),
    "RESEARCH": ("research_count", "research_limit_per_day"),
    "LLM_OPERATION": ("llm_operations_count", "llm_operations_limit_per_day"),
    "LLM_OPERATIONS": ("llm_operations_count", "llm_operations_limit_per_day"),
    "LLM_INFERENCE": ("llm_operations_count", "llm_operations_limit_per_day"),
    "AI_DRAFT_GENERATION": ("llm_operations_count", "llm_operations_limit_per_day"),
    "EMAIL_DRAFT": ("llm_operations_count", "llm_operations_limit_per_day"),
    "OUTREACH": ("outreach_count", "outreach_limit_per_day"),
    "SENDER": ("sender_usage_count", "sender_limit_per_day"),
    "CAMPAIGN": ("campaign_usage_count", "campaign_limit_per_day"),
}


class CapacityDecision(str, Enum):
    ALLOW = "ALLOW"
    QUEUE = "QUEUE"
    REJECT = "REJECT"


class CapacityDecisionResult:
    def __init__(
        self,
        decision: CapacityDecision,
        operation_type: str,
        remaining_capacity: int,
        limit: int,
        used: int,
        reason: str,
        audit_id: Optional[str] = None
    ):
        self.decision = decision
        self.operation_type = operation_type
        self.remaining_capacity = remaining_capacity
        self.limit = limit
        self.used = used
        self.reason = reason
        self.audit_id = audit_id

    @property
    def is_allowed(self) -> bool:
        return self.decision == CapacityDecision.ALLOW

    @property
    def is_queued(self) -> bool:
        return self.decision == CapacityDecision.QUEUE

    @property
    def is_rejected(self) -> bool:
        return self.decision == CapacityDecision.REJECT


class CapacityService:
    """
    First-class capacity policy decision engine, durable usage accounting, and quality floor guard.
    Guarantees:
    - Never downgrade AI quality merely to hit a daily target.
    - Concurrency-safe durable usage counters using row-level locking.
    - Timezone-aware calendar resets per workspace.
    - Full observability through CapacityDecisionAudit.
    """

    @classmethod
    def get_or_create_policy(cls, db: Session, workspace_id: str) -> CapacityPolicy:
        """Retrieve existing CapacityPolicy or initialize with safe defaults."""
        policy = db.execute(
            select(CapacityPolicy).where(CapacityPolicy.workspace_id == workspace_id)
        ).scalar_one_or_none()

        if not policy:
            policy = CapacityPolicy(
                workspace_id=workspace_id,
                discovery_limit_per_day=50,
                research_limit_per_day=100,
                llm_operations_limit_per_day=200,
                outreach_limit_per_day=50,
                sender_limit_per_day=30,
                campaign_limit_per_day=10,
                min_quality_floor="TIER_1_ADVANCED",
                timezone="UTC",
                is_active=True,
            )
            db.add(policy)
            try:
                db.commit()
                db.refresh(policy)
            except IntegrityError:
                db.rollback()
                policy = db.execute(
                    select(CapacityPolicy).where(CapacityPolicy.workspace_id == workspace_id)
                ).scalar_one()

        return policy

    @classmethod
    def get_current_workspace_date(cls, policy: CapacityPolicy) -> date:
        """Calculate the local calendar date for the workspace based on its configured timezone."""
        try:
            tz = ZoneInfo(policy.timezone)
        except Exception:
            logger.warning("Invalid timezone '%s' on workspace policy. Defaulting to UTC.", policy.timezone)
            tz = timezone.utc
        return datetime.now(tz).date()

    @classmethod
    def get_or_create_daily_usage(
        cls, db: Session, workspace_id: str, usage_date: date
    ) -> WorkspaceCapacityDailyUsage:
        """Retrieve or initialize today's durable usage record."""
        usage = db.execute(
            select(WorkspaceCapacityDailyUsage).where(
                WorkspaceCapacityDailyUsage.workspace_id == workspace_id,
                WorkspaceCapacityDailyUsage.usage_date == usage_date
            )
        ).scalar_one_or_none()

        if not usage:
            usage = WorkspaceCapacityDailyUsage(
                workspace_id=workspace_id,
                usage_date=usage_date,
                discovery_count=0,
                research_count=0,
                llm_operations_count=0,
                outreach_count=0,
                sender_usage_count=0,
                campaign_usage_count=0,
            )
            db.add(usage)
            try:
                db.commit()
                db.refresh(usage)
            except IntegrityError:
                db.rollback()
                usage = db.execute(
                    select(WorkspaceCapacityDailyUsage).where(
                        WorkspaceCapacityDailyUsage.workspace_id == workspace_id,
                        WorkspaceCapacityDailyUsage.usage_date == usage_date
                    )
                ).scalar_one()

        return usage

    @classmethod
    def evaluate_quality_floor(
        cls, policy: CapacityPolicy, provider: Optional[LLMProvider]
    ) -> Tuple[bool, str]:
        """
        Enforce quality floor. Never allow silent downgrade of AI quality.
        Returns (is_compliant, reason).
        """
        if not provider:
            return True, "No provider evaluation requested."

        constraints = provider.get_operational_constraints()
        provider_tier = constraints.quality_tier or "UNKNOWN"
        min_floor = policy.min_quality_floor or "TIER_1_ADVANCED"

        provider_rank = QUALITY_TIER_RANK.get(provider_tier, 0)
        floor_rank = QUALITY_TIER_RANK.get(min_floor, 3)

        if provider_rank < floor_rank:
            reason = (
                f"QUALITY_FLOOR_VIOLATION: Provider quality tier '{provider_tier}' (rank {provider_rank}) "
                f"is below required workspace quality floor '{min_floor}' (rank {floor_rank}). "
                f"Downgrading quality is strictly prohibited by platform policy."
            )
            return False, reason

        return True, "Quality floor satisfied."

    @classmethod
    def check_capacity(
        cls,
        db: Session,
        workspace_id: str,
        operation_type: str,
        requested_units: int = 1,
        provider: Optional[LLMProvider] = None,
        job_id: Optional[str] = None,
        is_queueable: bool = True
    ) -> CapacityDecisionResult:
        """
        Deterministic pre-execution capacity and quality floor decision.
        Returns CapacityDecisionResult (ALLOW, QUEUE, REJECT).
        """
        policy = cls.get_or_create_policy(db, workspace_id)

        if not policy.is_active:
            audit = cls._record_audit(
                db, workspace_id, operation_type, provider, CapacityDecision.REJECT,
                "POLICY_INACTIVE: Workspace CapacityPolicy is deactivated.", 0, job_id
            )
            return CapacityDecisionResult(
                CapacityDecision.REJECT, operation_type, 0, 0, 0,
                "POLICY_INACTIVE: Workspace CapacityPolicy is deactivated.", audit.id
            )

        # 1. Provider Availability & Quality Floor Check (Strict Quality Standards First)
        if provider:
            constraints = provider.get_operational_constraints()

            # Handle explicit unavailability, quota exhaustion, deprecated or invalid models
            unacceptable = ("UNAVAILABLE", "QUOTA_EXHAUSTED", "ERROR", "DEPRECATED_MODEL", "INVALID_MODEL")
            if constraints.provider_availability in unacceptable or constraints.model_availability in unacceptable:
                status_flag = constraints.provider_availability if constraints.provider_availability in unacceptable else constraints.model_availability
                reason = f"PROVIDER_UNAVAILABLE: Provider '{constraints.provider_name or 'UNKNOWN'}' or requested model is {status_flag}."
                decision = CapacityDecision.QUEUE if is_queueable else CapacityDecision.REJECT
                audit = cls._record_audit(db, workspace_id, operation_type, provider, decision, reason, 0, job_id)
                return CapacityDecisionResult(decision, operation_type, 0, 0, 0, reason, audit.id)

            # Evaluate quality floor (Never downgrade AI quality merely to hit daily target)
            quality_ok, quality_reason = cls.evaluate_quality_floor(policy, provider)
            if not quality_ok:
                decision = CapacityDecision.QUEUE if is_queueable else CapacityDecision.REJECT
                audit = cls._record_audit(
                    db, workspace_id, operation_type, provider, decision, quality_reason, 0, job_id
                )
                return CapacityDecisionResult(
                    decision, operation_type, 0, 0, 0, quality_reason, audit.id
                )

        # 2. Daily Limit & Usage Evaluation
        op_key = operation_type.upper()
        if op_key not in OPERATION_FIELD_MAP:
            reason = f"UNKNOWN_OPERATION_TYPE: '{operation_type}' is not recognized."
            audit = cls._record_audit(db, workspace_id, operation_type, provider, CapacityDecision.REJECT, reason, 0, job_id)
            return CapacityDecisionResult(CapacityDecision.REJECT, operation_type, 0, 0, 0, reason, audit.id)

        count_attr, limit_attr = OPERATION_FIELD_MAP[op_key]
        limit = getattr(policy, limit_attr)

        current_date = cls.get_current_workspace_date(policy)
        usage = cls.get_or_create_daily_usage(db, workspace_id, current_date)
        current_count = getattr(usage, count_attr)

        remaining = max(0, limit - current_count)

        if current_count + requested_units > limit:
            reason = (
                f"DAILY_CAPACITY_EXCEEDED: Workspace reached limit for {op_key} "
                f"({current_count}/{limit} used today in timezone {policy.timezone})."
            )
            decision = CapacityDecision.QUEUE if is_queueable else CapacityDecision.REJECT
            audit = cls._record_audit(db, workspace_id, operation_type, provider, decision, reason, remaining, job_id)
            return CapacityDecisionResult(decision, operation_type, remaining, limit, current_count, reason, audit.id)

        # 3. Capacity Allowed
        reason = f"CAPACITY_ALLOWED: {remaining} units remaining for {op_key}."
        audit = cls._record_audit(db, workspace_id, operation_type, provider, CapacityDecision.ALLOW, reason, remaining, job_id)
        return CapacityDecisionResult(CapacityDecision.ALLOW, operation_type, remaining, limit, current_count, reason, audit.id)

    @classmethod
    def increment_usage_atomic(
        cls,
        db: Session,
        workspace_id: str,
        operation_type: str,
        units: int = 1,
        force_post_transmission: bool = False
    ) -> Tuple[bool, int, int]:
        """
        Concurrency-safe atomic increment of usage counter using PostgreSQL row locks.
        Guarantees two workers cannot both acquire the last unit of capacity.
        If force_post_transmission=True, ensures real wire transmissions already executed
        are recorded in durable accounting even if limits changed concurrently.
        Returns (success, current_count, remaining_capacity).
        """
        op_key = operation_type.upper()
        if op_key not in OPERATION_FIELD_MAP:
            return False, 0, 0

        count_attr, limit_attr = OPERATION_FIELD_MAP[op_key]
        policy = cls.get_or_create_policy(db, workspace_id)
        limit = getattr(policy, limit_attr)
        current_date = cls.get_current_workspace_date(policy)

        # Use with_for_update() row lock to guarantee mutual exclusion across concurrent workers
        usage = db.execute(
            select(WorkspaceCapacityDailyUsage).where(
                WorkspaceCapacityDailyUsage.workspace_id == workspace_id,
                WorkspaceCapacityDailyUsage.usage_date == current_date
            ).with_for_update()
        ).scalar_one_or_none()

        if not usage:
            # Row doesn't exist yet: initialize it
            cls.get_or_create_daily_usage(db, workspace_id, current_date)
            usage = db.execute(
                select(WorkspaceCapacityDailyUsage).where(
                    WorkspaceCapacityDailyUsage.workspace_id == workspace_id,
                    WorkspaceCapacityDailyUsage.usage_date == current_date
                ).with_for_update()
            ).scalar_one()

        current_count = getattr(usage, count_attr)
        if current_count + units > limit and not force_post_transmission:
            db.commit()
            return False, current_count, max(0, limit - current_count)

        # Perform update while holding the lock
        new_count = current_count + units
        setattr(usage, count_attr, new_count)
        usage.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(usage)

        remaining = max(0, limit - new_count)
        return True, new_count, remaining

    @classmethod
    def record_transmitted_usage_if_unaccounted(
        cls,
        db: Session,
        message: Any
    ) -> bool:
        """
        Idempotent recovery mechanism: If a message was already transmitted over the wire
        but capacity accounting was not recorded (e.g. post-transmission crash/disconnect),
        safely increment the durable usage counter and mark capacity_accounted = True.
        Guarantees that:
        - Real transmissions are never double-counted.
        - Real transmissions are never silently lost from capacity limits.
        """
        if getattr(message, "status", None) != "TRANSMITTED":
            return False
        if getattr(message, "capacity_accounted", False):
            return True  # Already accounted, do not double-increment

        cls.increment_usage_atomic(
            db=db,
            workspace_id=message.workspace_id,
            operation_type="OUTREACH",
            units=1,
            force_post_transmission=True
        )
        message.capacity_accounted = True
        db.commit()
        db.refresh(message)
        return True

    @classmethod
    def get_workspace_capacity_summary(cls, db: Session, workspace_id: str) -> Dict[str, Any]:
        """Return comprehensive capacity and usage breakdown for the workspace."""
        policy = cls.get_or_create_policy(db, workspace_id)
        current_date = cls.get_current_workspace_date(policy)
        usage = cls.get_or_create_daily_usage(db, workspace_id, current_date)

        summary = {
            "workspace_id": workspace_id,
            "timezone": policy.timezone,
            "usage_date": str(current_date),
            "min_quality_floor": policy.min_quality_floor,
            "is_active": policy.is_active,
            "outreach_limit_per_day": policy.outreach_limit_per_day,
            "llm_operations_limit_per_day": policy.llm_operations_limit_per_day,
            "discovery_limit_per_day": policy.discovery_limit_per_day,
            "research_limit_per_day": policy.research_limit_per_day,
            "sender_limit_per_day": policy.sender_limit_per_day,
            "campaign_limit_per_day": policy.campaign_limit_per_day,
            "policy": {
                "discovery_limit_per_day": policy.discovery_limit_per_day,
                "research_limit_per_day": policy.research_limit_per_day,
                "llm_operations_limit_per_day": policy.llm_operations_limit_per_day,
                "outreach_limit_per_day": policy.outreach_limit_per_day,
                "sender_limit_per_day": policy.sender_limit_per_day,
                "campaign_limit_per_day": policy.campaign_limit_per_day,
                "min_quality_floor": policy.min_quality_floor,
                "timezone": policy.timezone,
                "is_active": policy.is_active,
            },
            "metrics": {}
        }

        for op, (count_field, limit_field) in OPERATION_FIELD_MAP.items():
            used = getattr(usage, count_field)
            limit = getattr(policy, limit_field)
            remaining = max(0, limit - used)
            pct = round((used / limit * 100), 1) if limit > 0 else 100.0
            summary["metrics"][op.lower()] = {
                "used": used,
                "limit": limit,
                "remaining": remaining,
                "utilization_pct": min(100.0, pct)
            }

        summary["quotas"] = summary["metrics"]
        return summary

    @classmethod
    def _record_audit(
        cls,
        db: Session,
        workspace_id: str,
        operation_type: str,
        provider: Optional[LLMProvider],
        decision: CapacityDecision,
        reason: str,
        remaining_capacity: int,
        job_id: Optional[str]
    ) -> CapacityDecisionAudit:
        provider_name = provider.get_model_metadata().get("provider") if provider else None
        model_name = provider.get_model_metadata().get("model") if provider else None

        audit = CapacityDecisionAudit(
            workspace_id=workspace_id,
            operation_type=operation_type,
            requested_provider=provider_name,
            requested_model=model_name,
            decision=decision.value,
            reason=reason,
            remaining_capacity=remaining_capacity,
            job_id=job_id,
        )
        db.add(audit)
        db.commit()
        db.refresh(audit)
        return audit
