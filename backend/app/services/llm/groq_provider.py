import json
import logging
import re
import time
from typing import Type, TypeVar, Dict, Any, Optional, List
import httpx
from pydantic import BaseModel, ValidationError

from app.services.llm.base import (
    LLMProvider,
    LLMStructuredResult,
    LLMTextResult,
    LLMUsageMetadata,
    LLMExecutionStatus,
    LLMErrorClassification,
    ProviderOperationalConstraints,
)

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# Groq pricing in USD per token
GROQ_PRICING: Dict[str, Dict[str, float]] = {
    "openai/gpt-oss-120b": {"prompt": 0.15 / 1_000_000, "completion": 0.60 / 1_000_000},
    "llama-3.3-70b-versatile": {"prompt": 0.59 / 1_000_000, "completion": 0.79 / 1_000_000},
    "llama-3.1-70b-versatile": {"prompt": 0.59 / 1_000_000, "completion": 0.79 / 1_000_000},
    "mixtral-8x7b-32768": {"prompt": 0.24 / 1_000_000, "completion": 0.24 / 1_000_000},
}
DEFAULT_GROQ_PRICING = {"prompt": 0.15 / 1_000_000, "completion": 0.60 / 1_000_000}


class GroqProvider(LLMProvider):
    """
    Production-grade Groq Tier-1 LLM provider.
    Connects to Groq's high-throughput OpenAI-compatible endpoint.

    Guarantees:
    - Tier-1 Advanced quality floor (120B/70B frontier instruction models)
    - Zero secret leakage in logs, exceptions, or audit metadata
    - Bounded exponential backoff retries for transient errors
    - Deterministic error classification and ProviderStatusTracker integration
    - Token accounting and cost estimation
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = "https://api.groq.com/openai/v1",
        model: str = "openai/gpt-oss-120b",
        timeout_seconds: float = 30.0,
        max_retries: int = 3,
        max_input_tokens: int = 8000,
        max_output_tokens: int = 2000,
        http_client: Optional[httpx.Client] = None,
    ):
        self._api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.max_input_tokens = max_input_tokens
        self.max_output_tokens = max_output_tokens
        self._client = http_client

    def __repr__(self) -> str:
        masked_key = "REDACTED" if self._api_key else "NONE"
        return f"<GroqProvider model={self.model} base_url={self.base_url} api_key={masked_key}>"

    def get_model_metadata(self) -> Dict[str, Any]:
        return {
            "provider": "GROQ",
            "model": self.model,
            "base_url": self.base_url,
            "has_api_key": bool(self._api_key),
            "timeout_seconds": self.timeout_seconds,
            "max_retries": self.max_retries,
            "max_input_tokens": self.max_input_tokens,
            "max_output_tokens": self.max_output_tokens,
        }

    def get_operational_constraints(self) -> ProviderOperationalConstraints:
        from app.services.llm.provider_status_tracker import ProviderStatusTracker
        state, _ = ProviderStatusTracker.get_provider_state("GROQ", self.model, has_credentials=bool(self._api_key))
        return ProviderOperationalConstraints(
            provider_name="GROQ",
            requests_per_minute=None,  # Rate limits are plan/tier-dependent and UNKNOWN unless reported
            requests_per_day=None,
            input_tokens_per_minute=None,
            max_input_tokens=self.max_input_tokens,
            max_output_tokens=self.max_output_tokens,
            provider_availability=state.value,
            model_availability=state.value,
            quality_tier="TIER_1_ADVANCED"
        )

    def estimate_cost(self, prompt_tokens: int, completion_tokens: int, model: Optional[str] = None) -> float:
        m = model or self.model
        rates = GROQ_PRICING.get(m, DEFAULT_GROQ_PRICING)
        cost = (prompt_tokens * rates["prompt"]) + (completion_tokens * rates["completion"])
        return round(cost, 6)

    def classify_error(self, e: Exception) -> LLMErrorClassification:
        if isinstance(e, (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError)):
            return LLMErrorClassification.RETRYABLE

        if isinstance(e, httpx.HTTPStatusError):
            status = e.response.status_code
            if status in (429, 500, 502, 503, 504):
                return LLMErrorClassification.RETRYABLE
            return LLMErrorClassification.NON_RETRYABLE

        if isinstance(e, ValidationError):
            return LLMErrorClassification.NON_RETRYABLE

        return LLMErrorClassification.NON_RETRYABLE

    def normalize_error(self, e: Exception) -> str:
        """Sanitize error messages to eliminate credentials, headers, and tokens."""
        raw_msg = str(e)
        if isinstance(e, httpx.HTTPStatusError):
            try:
                raw_msg = f"{raw_msg} | Body: {e.response.text}"
            except Exception:
                pass

        sanitized = re.sub(r"Bearer\s+[a-zA-Z0-9_\-\.]+", "Bearer [REDACTED]", raw_msg)
        sanitized = re.sub(r"gsk_[a-zA-Z0-9_\-\.]+", "gsk_[REDACTED]", sanitized)
        sanitized = re.sub(r"sk-[a-zA-Z0-9_\-\.]+", "sk-[REDACTED]", sanitized)
        sanitized = re.sub(r"key=[a-zA-Z0-9_\-\.]+", "key=[REDACTED]", sanitized)

        if isinstance(e, httpx.HTTPStatusError):
            return f"GroqProviderHTTPError(status_code={e.response.status_code}, detail={sanitized[:250]})"
        if isinstance(e, httpx.TimeoutException):
            return f"GroqProviderTimeoutError(timeout_seconds={self.timeout_seconds})"
        return f"{type(e).__name__}: {sanitized[:200]}"

    def _execute_with_retry(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute request with bounded retries and exponential backoff.
        Raises normalized exception on terminal failure.
        """
        if not self._api_key:
            raise ValueError("GROQ_API_KEY is not configured")

        from app.services.llm.provider_status_tracker import ProviderStatusTracker, ProviderOperationalState

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        url = f"{self.base_url}/chat/completions"
        last_exception: Optional[Exception] = None

        for attempt in range(1, self.max_retries + 1):
            try:
                if self._client:
                    resp = self._client.post(url, headers=headers, json=payload, timeout=self.timeout_seconds)
                    resp.raise_for_status()
                    data = resp.json()
                    ProviderStatusTracker.record_success("GROQ", self.model)
                    return data
                else:
                    with httpx.Client(timeout=self.timeout_seconds) as client:
                        resp = client.post(url, headers=headers, json=payload)
                        resp.raise_for_status()
                        data = resp.json()
                        ProviderStatusTracker.record_success("GROQ", self.model)
                        return data

            except Exception as e:
                last_exception = e
                classification = self.classify_error(e)
                norm_msg = self.normalize_error(e)

                if classification == LLMErrorClassification.RETRYABLE and attempt < self.max_retries:
                    backoff = min(0.5 * (2 ** (attempt - 1)), 8.0)
                    logger.warning(
                        "Groq call transient failure on attempt %d/%d (%s). Retrying in %.2fs...",
                        attempt, self.max_retries, norm_msg, backoff
                    )
                    time.sleep(backoff)
                else:
                    logger.error("Groq call terminal failure on attempt %d/%d: %s", attempt, self.max_retries, norm_msg)
                    if isinstance(e, httpx.HTTPStatusError):
                        ProviderStatusTracker.record_http_error("GROQ", self.model, e.response.status_code, norm_msg)
                    elif isinstance(e, (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError)):
                        ProviderStatusTracker.record_diagnostic(
                            "GROQ", self.model, ProviderOperationalState.ERROR, error_message=norm_msg
                        )
                    else:
                        ProviderStatusTracker.record_diagnostic(
                            "GROQ", self.model, ProviderOperationalState.ERROR, error_message=norm_msg
                        )
                    raise

        if last_exception:
            raise last_exception
        raise RuntimeError("Unreachable retry state")

    def generate_structured(
        self,
        messages: List[Dict[str, str]],
        response_schema: Type[T],
        temperature: float = 0.1,
        max_tokens: Optional[int] = None
    ) -> LLMStructuredResult[T]:
        start_time = time.time()
        schema_json = json.dumps(response_schema.model_json_schema())

        augmented_messages = []
        for msg in messages:
            if msg.get("role") == "system":
                aug_content = (
                    f"{msg.get('content', '')}\n\n"
                    f"OUTPUT REQUIREMENT: You MUST output a JSON object adhering strictly to this JSON Schema:\n"
                    f"{schema_json}\n"
                    f"Do NOT include markdown code fences or extra text. Output valid raw JSON only."
                )
                augmented_messages.append({"role": "system", "content": aug_content})
            else:
                augmented_messages.append(msg)

        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": augmented_messages,
            "temperature": temperature,
            "max_tokens": max_tokens or self.max_output_tokens,
            "response_format": {"type": "json_object"},
        }

        try:
            res_data = self._execute_with_retry(payload)
            latency_ms = (time.time() - start_time) * 1000

            raw_content = res_data["choices"][0]["message"]["content"]
            usage = res_data.get("usage", {})
            prompt_tokens = usage.get("prompt_tokens", 0)
            completion_tokens = usage.get("completion_tokens", 0)
            total_tokens = usage.get("total_tokens", prompt_tokens + completion_tokens)
            is_estimated = ("prompt_tokens" not in usage)

            cost = self.estimate_cost(prompt_tokens, completion_tokens, self.model)
            parsed = response_schema.model_validate_json(raw_content)

            return LLMStructuredResult(
                parsed=parsed,
                raw_output=raw_content,
                status=LLMExecutionStatus.LLM_SUCCESS,
                metadata=LLMUsageMetadata(
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    estimated_cost_usd=cost,
                    is_cost_estimated=is_estimated,
                    model_name=self.model,
                    provider_name="GROQ",
                    latency_ms=round(latency_ms, 2)
                )
            )

        except ValidationError as ve:
            logger.error("Groq output failed schema validation: %s", ve)
            norm_err = f"SchemaValidationError: {str(ve)[:200]}"
            return LLMStructuredResult(
                parsed=None,
                raw_output=locals().get("raw_content", ""),
                status=LLMExecutionStatus.LLM_FAILED,
                metadata=LLMUsageMetadata(
                    model_name=self.model,
                    provider_name="GROQ",
                    latency_ms=round((time.time() - start_time) * 1000, 2)
                ),
                error_message=norm_err
            )

        except Exception as e:
            norm_err = self.normalize_error(e)
            logger.error("Groq structured generation failed: %s", norm_err)
            return LLMStructuredResult(
                parsed=None,
                raw_output="",
                status=LLMExecutionStatus.LLM_FAILED,
                metadata=LLMUsageMetadata(
                    model_name=self.model,
                    provider_name="GROQ",
                    latency_ms=round((time.time() - start_time) * 1000, 2)
                ),
                error_message=norm_err
            )

    def generate_text(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None
    ) -> LLMTextResult:
        start_time = time.time()
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens or self.max_output_tokens,
        }

        try:
            res_data = self._execute_with_retry(payload)
            latency_ms = (time.time() - start_time) * 1000

            text_content = res_data["choices"][0]["message"]["content"]
            usage = res_data.get("usage", {})
            prompt_tokens = usage.get("prompt_tokens", 0)
            completion_tokens = usage.get("completion_tokens", 0)
            total_tokens = usage.get("total_tokens", prompt_tokens + completion_tokens)
            is_estimated = ("prompt_tokens" not in usage)
            cost = self.estimate_cost(prompt_tokens, completion_tokens, self.model)

            return LLMTextResult(
                text=text_content,
                status=LLMExecutionStatus.LLM_SUCCESS,
                metadata=LLMUsageMetadata(
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    estimated_cost_usd=cost,
                    is_cost_estimated=is_estimated,
                    model_name=self.model,
                    provider_name="GROQ",
                    latency_ms=round(latency_ms, 2)
                )
            )

        except Exception as e:
            norm_err = self.normalize_error(e)
            logger.error("Groq text generation failed: %s", norm_err)
            return LLMTextResult(
                text=None,
                status=LLMExecutionStatus.LLM_FAILED,
                metadata=LLMUsageMetadata(
                    model_name=self.model,
                    provider_name="GROQ",
                    latency_ms=round((time.time() - start_time) * 1000, 2)
                ),
                error_message=norm_err
            )
