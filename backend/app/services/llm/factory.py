import os
import logging
from typing import Optional, List

from app.core.config import settings
from app.services.llm.base import LLMProvider
from app.services.llm.openai_provider import OpenAIProvider
from app.services.llm.gemini_provider import GeminiProvider
from app.services.llm.groq_provider import GroqProvider
from app.services.llm.rule_based_fallback import RuleBasedFallbackProvider

logger = logging.getLogger(__name__)

_LLM_PROVIDER_OVERRIDE: Optional[LLMProvider] = None

QUALITY_TIER_RANK = {
    "TIER_1_ADVANCED": 3,
    "TIER_2_STANDARD": 2,
    "FALLBACK_HEURISTIC": 1,
    "UNKNOWN": 0,
}


def set_llm_provider_override(provider: Optional[LLMProvider]) -> None:
    """Sets a global LLM provider override for unit testing and offline simulation."""
    global _LLM_PROVIDER_OVERRIDE
    _LLM_PROVIDER_OVERRIDE = provider


def set_llm_client_override(provider: Optional[LLMProvider]) -> None:
    """Backward compatibility helper."""
    set_llm_provider_override(provider)


def build_provider_instance(name: str) -> Optional[LLMProvider]:
    """
    Constructs a provider instance by name using environment/settings configuration.
    Returns None if the provider name is unknown or missing required API keys.
    """
    p_name = name.lower().strip()

    if p_name == "openai":
        api_key = settings.OPENAI_API_KEY or os.getenv("OPENAI_API_KEY") or settings.LLM_API_KEY or os.getenv("LLM_API_KEY")
        if api_key and api_key.strip():
            base_url = settings.OPENAI_BASE_URL or os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
            model = settings.OPENAI_MODEL or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
            return OpenAIProvider(
                api_key=api_key.strip(),
                base_url=base_url,
                model=model,
                timeout_seconds=settings.LLM_TIMEOUT_SECONDS,
                max_retries=settings.LLM_MAX_RETRIES,
                max_input_tokens=settings.LLM_MAX_INPUT_TOKENS,
                max_output_tokens=settings.LLM_MAX_OUTPUT_TOKENS,
            )
        return OpenAIProvider(api_key=None)

    if p_name == "gemini":
        api_key = settings.GEMINI_API_KEY or os.getenv("GEMINI_API_KEY")
        if api_key and api_key.strip():
            base_url = settings.GEMINI_BASE_URL or os.getenv("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai")
            model = settings.GEMINI_MODEL or os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
            return GeminiProvider(
                api_key=api_key.strip(),
                base_url=base_url,
                model=model,
                timeout_seconds=settings.LLM_TIMEOUT_SECONDS,
                max_retries=settings.LLM_MAX_RETRIES,
                max_input_tokens=settings.LLM_MAX_INPUT_TOKENS,
                max_output_tokens=settings.LLM_MAX_OUTPUT_TOKENS,
            )
        return GeminiProvider(api_key=None, model=settings.GEMINI_MODEL or "gemini-3.8-flash")

    if p_name == "groq":
        api_key = settings.GROQ_API_KEY or os.getenv("GROQ_API_KEY")
        if api_key and api_key.strip():
            base_url = settings.GROQ_BASE_URL or os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
            model = settings.GROQ_MODEL or os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
            return GroqProvider(
                api_key=api_key.strip(),
                base_url=base_url,
                model=model,
                timeout_seconds=settings.LLM_TIMEOUT_SECONDS,
                max_retries=settings.LLM_MAX_RETRIES,
                max_input_tokens=settings.LLM_MAX_INPUT_TOKENS,
                max_output_tokens=settings.LLM_MAX_OUTPUT_TOKENS,
            )
        return GroqProvider(api_key=None, model=settings.GROQ_MODEL or "openai/gpt-oss-120b")

    if p_name == "rule_based":
        return RuleBasedFallbackProvider()

    return None


def get_llm_provider(required_quality_floor: Optional[str] = None) -> LLMProvider:
    """
    Configuration-driven factory returning the active LLMProvider.

    Quality-Preserving Failover Invariant:
    - If primary provider is unavailable (e.g. QUOTA_EXHAUSTED, ERROR, unconfigured, DEPRECATED_MODEL, INVALID_MODEL):
      An alternate provider is selected ONLY IF it satisfies required_quality_floor
      and is configured with credentials and available.
    - Downgrading to FALLBACK_HEURISTIC when the workspace requires TIER_1_ADVANCED
      is strictly forbidden.
    """
    global _LLM_PROVIDER_OVERRIDE
    if _LLM_PROVIDER_OVERRIDE is not None:
        return _LLM_PROVIDER_OVERRIDE

    unacceptable_states = {"QUOTA_EXHAUSTED", "ERROR", "UNAVAILABLE", "DEPRECATED_MODEL", "INVALID_MODEL"}

    primary_name = (settings.LLM_PROVIDER or os.getenv("LLM_PROVIDER", "openai")).lower().strip()
    primary_provider = build_provider_instance(primary_name)

    # Check if primary provider is healthy and meets quality floor
    if primary_provider is not None:
        constraints = primary_provider.get_operational_constraints()
        provider_tier = constraints.quality_tier or "UNKNOWN"
        is_viable = (
            constraints.provider_availability == "AVAILABLE"
            and constraints.model_availability == "AVAILABLE"
        )
        meets_floor = True
        if required_quality_floor:
            p_rank = QUALITY_TIER_RANK.get(provider_tier, 0)
            f_rank = QUALITY_TIER_RANK.get(required_quality_floor, 3)
            meets_floor = (p_rank >= f_rank)

        # Has API key check
        has_key = bool(getattr(primary_provider, "_api_key", None))
        if isinstance(primary_provider, RuleBasedFallbackProvider):
            has_key = True

        if is_viable and meets_floor and has_key:
            return primary_provider

    # Primary is not viable or lacks credentials. Evaluate quality-preserving failovers.
    candidates: List[str] = []
    if settings.LLM_FALLBACK_PROVIDER:
        candidates.append(settings.LLM_FALLBACK_PROVIDER.lower().strip())
    for name in ["gemini", "groq", "openai"]:
        if name != primary_name and name not in candidates:
            candidates.append(name)

    floor_to_enforce = required_quality_floor or "TIER_1_ADVANCED"
    min_rank = QUALITY_TIER_RANK.get(floor_to_enforce, 3)

    if min_rank <= QUALITY_TIER_RANK["FALLBACK_HEURISTIC"] and "rule_based" not in candidates:
        candidates.append("rule_based")

    for cand_name in candidates:
        cand_provider = build_provider_instance(cand_name)
        if cand_provider is None:
            continue

        cand_key = getattr(cand_provider, "_api_key", None)
        if isinstance(cand_provider, RuleBasedFallbackProvider):
            cand_key = "rule_based_offline"
        if not cand_key:
            continue

        c_constraints = cand_provider.get_operational_constraints()
        c_tier = c_constraints.quality_tier or "UNKNOWN"
        c_rank = QUALITY_TIER_RANK.get(c_tier, 0)

        # Strictly enforce: alternate provider MUST meet or exceed the required quality floor
        if c_rank < min_rank:
            continue

        # Strictly enforce: alternate provider MUST be AVAILABLE and not in error/quota exhausted/deprecated states
        if c_constraints.provider_availability != "AVAILABLE" or c_constraints.model_availability != "AVAILABLE":
            continue

        logger.info(
            "Quality-preserving failover: Primary provider '%s' unavailable. "
            "Selecting alternate provider '%s' (tier=%s, model=%s).",
            primary_name, cand_name, c_tier, cand_provider.get_model_metadata().get("model")
        )
        return cand_provider

    # No alternate provider met the quality floor and availability requirements.
    # When a higher quality floor is strictly requested (e.g. TIER_1_ADVANCED):
    # Return the primary provider (even if unavailable) so that CapacityService
    # truthfully records PROVIDER_UNAVAILABLE or QUALITY_FLOOR_VIOLATION.
    # NEVER silently downgrade to RuleBasedFallbackProvider when TIER_1_ADVANCED is required!
    if required_quality_floor and min_rank > QUALITY_TIER_RANK["FALLBACK_HEURISTIC"]:
        if primary_provider is not None:
            return primary_provider
        return OpenAIProvider(api_key=None)

    # When NO quality floor is demanded or floor allows FALLBACK_HEURISTIC:
    # safe offline fallback to RuleBasedFallbackProvider:
    return RuleBasedFallbackProvider()


# Backward compatibility alias
get_llm_client = get_llm_provider
