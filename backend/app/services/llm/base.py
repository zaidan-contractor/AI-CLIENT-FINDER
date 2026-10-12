import logging
from abc import ABC, abstractmethod
from enum import Enum
from typing import Type, TypeVar, Generic, Dict, Any, Optional, List
from pydantic import BaseModel

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class LLMExecutionStatus(str, Enum):
    LLM_SUCCESS = "LLM_SUCCESS"
    LLM_FALLBACK = "LLM_FALLBACK"
    LLM_FAILED = "LLM_FAILED"
    LLM_BLOCKED = "LLM_BLOCKED"


class LLMErrorClassification(str, Enum):
    RETRYABLE = "RETRYABLE"
    NON_RETRYABLE = "NON_RETRYABLE"


class LLMUsageMetadata(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0
    is_cost_estimated: bool = False
    model_name: str = "unknown"
    provider_name: str = "unknown"
    latency_ms: float = 0.0


class ProviderOperationalConstraints(BaseModel):
    """
    Known operational constraints and quality tier of an LLM provider.
    Unknown constraints are strictly None / UNKNOWN rather than invented.
    """
    provider_name: Optional[str] = None
    requests_per_minute: Optional[int] = None
    requests_per_day: Optional[int] = None
    input_tokens_per_minute: Optional[int] = None
    max_input_tokens: Optional[int] = None
    max_output_tokens: Optional[int] = None
    provider_availability: str = "AVAILABLE"  # AVAILABLE, DEGRADED, UNAVAILABLE, UNKNOWN
    model_availability: str = "AVAILABLE"     # AVAILABLE, DEGRADED, UNAVAILABLE, UNKNOWN
    quality_tier: str = "TIER_1_ADVANCED"     # TIER_1_ADVANCED, TIER_2_STANDARD, FALLBACK_HEURISTIC, UNKNOWN

    @property
    def is_available(self) -> bool:
        return self.provider_availability == "AVAILABLE" and self.model_availability == "AVAILABLE"


class LLMStructuredResult(Generic[T]):
    def __init__(
        self,
        parsed: Optional[T],
        raw_output: str,
        status: LLMExecutionStatus,
        metadata: LLMUsageMetadata,
        error_message: Optional[str] = None
    ):
        self.parsed = parsed
        self.raw_output = raw_output
        self.status = status
        self.metadata = metadata
        self.error_message = error_message

    @property
    def is_fallback(self) -> bool:
        return self.status == LLMExecutionStatus.LLM_FALLBACK

    @property
    def is_success(self) -> bool:
        return self.status == LLMExecutionStatus.LLM_SUCCESS


class LLMTextResult:
    def __init__(
        self,
        text: Optional[str],
        status: LLMExecutionStatus,
        metadata: LLMUsageMetadata,
        error_message: Optional[str] = None
    ):
        self.text = text
        self.status = status
        self.metadata = metadata
        self.error_message = error_message

    @property
    def is_fallback(self) -> bool:
        return self.status == LLMExecutionStatus.LLM_FALLBACK

    @property
    def is_success(self) -> bool:
        return self.status == LLMExecutionStatus.LLM_SUCCESS


class LLMProvider(ABC):
    """
    Provider-agnostic LLM interface.
    Decoupled from specific vendor SDKs.
    CRITICAL INVARIANT: LLM OUTPUT != APPLICATION AUTHORIZATION.
    The LLM may only propose structured data/actions. Execution is deterministic.
    """

    @classmethod
    def sanitize_untrusted_input(cls, text: str) -> str:
        """Backward-compatible helper defanging untrusted inputs."""
        from app.services.llm.guardrails import PromptInjectionGuardrail
        return PromptInjectionGuardrail.defang_text(text)

    @abstractmethod
    def generate_structured(
        self,
        messages: List[Dict[str, str]],
        response_schema: Type[T],
        temperature: float = 0.1,
        max_tokens: Optional[int] = None
    ) -> LLMStructuredResult[T]:
        """Generate structured output adhering to response_schema."""
        pass

    @abstractmethod
    def generate_text(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None
    ) -> LLMTextResult:
        """Generate plain text output."""
        pass

    @abstractmethod
    def get_model_metadata(self) -> Dict[str, Any]:
        """Return provider and model metadata."""
        pass

    def get_operational_constraints(self) -> "ProviderOperationalConstraints":
        """
        Return operational constraints and quality tier where supported.
        Defaults to unknown limits rather than invented values.
        """
        return ProviderOperationalConstraints(
            provider_availability="UNKNOWN",
            model_availability="UNKNOWN",
            quality_tier="UNKNOWN"
        )

    @abstractmethod
    def estimate_cost(self, prompt_tokens: int, completion_tokens: int, model: Optional[str] = None) -> float:
        """Estimate cost in USD based on standard rates."""
        pass

    @abstractmethod
    def classify_error(self, e: Exception) -> LLMErrorClassification:
        """Classify exception as RETRYABLE or NON_RETRYABLE."""
        pass

    @abstractmethod
    def normalize_error(self, e: Exception) -> str:
        """Sanitize error for safe logging and reporting with ZERO secret leakage."""
        pass


# Backward compatibility alias
BaseLLMClient = LLMProvider
