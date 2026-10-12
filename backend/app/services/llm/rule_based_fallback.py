import json
import logging
from typing import Type, TypeVar, Any, Dict, Optional, List
from pydantic import BaseModel

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


class RuleBasedFallbackProvider(LLMProvider):
    """
    Deterministic rule-based fallback provider.
    CRITICAL INVARIANT:
    - Sets status to LLM_FALLBACK.
    - NEVER pretends an LLM call occurred or masquerades as high-confidence AI.
    - Assigns conservative confidence and explicit uncertainty flags.
    - Operates completely offline with zero network requests and $0 cost.
    """

    def __init__(self, model_name: str = "rule_based_fallback_v1"):
        self.model_name = model_name

    def get_model_metadata(self) -> Dict[str, Any]:
        return {
            "provider": "RULE_BASED",
            "model": self.model_name,
            "is_fallback": True,
            "offline": True,
        }

    def get_operational_constraints(self) -> ProviderOperationalConstraints:
        return ProviderOperationalConstraints(
            provider_name="RULE_BASED_FALLBACK",
            requests_per_minute=None,
            requests_per_day=None,
            input_tokens_per_minute=None,
            max_input_tokens=8000,
            max_output_tokens=2000,
            provider_availability="AVAILABLE",
            model_availability="AVAILABLE",
            quality_tier="FALLBACK_HEURISTIC"
        )

    def estimate_cost(self, prompt_tokens: int, completion_tokens: int, model: Optional[str] = None) -> float:
        return 0.0

    def classify_error(self, e: Exception) -> LLMErrorClassification:
        return LLMErrorClassification.NON_RETRYABLE

    def normalize_error(self, e: Exception) -> str:
        return f"RuleBasedFallbackError: {str(e)[:200]}"

    def generate_structured(
        self,
        messages: List[Dict[str, str]],
        response_schema: Type[T],
        temperature: float = 0.0,
        max_tokens: Optional[int] = None
    ) -> LLMStructuredResult[T]:
        # Extract user content for heuristic keyword matching
        user_text = ""
        for msg in messages:
            if msg.get("role") == "user":
                user_text += msg.get("content", "") + " "

        user_lower = user_text.lower()
        fields = response_schema.model_fields
        sample_dict: Dict[str, Any] = {}

        # Heuristic generation targeting known schema structures
        schema_name = response_schema.__name__

        if schema_name == "BusinessResearchAnalysis":
            sample_dict = {
                "observed_business_facts": ["Website accessible and business operational"],
                "evidence_references": ["heuristic_fallback_evidence_001"],
                "digital_gaps": ["Unverified digital footprint due to fallback mode"],
                "operational_gaps": [],
                "customer_experience_gaps": [],
                "confidence": 0.35,  # Deliberately conservative
                "uncertainty": 0.70, # High uncertainty flag
                "recommended_solution_categories": ["DIGITAL_PRESENCE_AUDIT"],
                "reasoning": "Fallback heuristic analysis executed because live LLM provider was unconfigured or unavailable."
            }
        elif schema_name == "OpportunityAnalysis":
            sample_dict = {
                "problem": "Unverified operational bottlenecks detected in fallback mode",
                "evidence": ["heuristic_evidence_snapshot"],
                "affected_workflow": "General operations",
                "potential_solution": "Baseline digital assessment",
                "solution_type": "CATALOGUE_SOLUTION",
                "expected_business_impact": "Initial qualification required before estimate",
                "confidence": 0.40,
                "assumptions": ["Assumes target business matches standard profile"],
                "missing_information": ["Complete business telemetry unavailable in fallback"]
            }
        elif schema_name == "OutreachPersonalization":
            sample_dict = {
                "personalization_facts": ["Publicly listed operational entity"],
                "evidence_references": ["public_listing_index"],
                "proposed_value_proposition": "Exploration of process optimization and workflow automation",
                "email_angle": "Professional baseline introduction",
                "uncertainty_flags": ["FALLBACK_GENERATED_REQUIRES_HUMAN_REVIEW"]
            }
        elif schema_name == "InboundReplyClassification":
            # Keyword heuristic for inbound reply
            if any(w in user_lower for w in ["unsubscribe", "stop", "remove", "do not contact", "opt out"]):
                intent = "OPT_OUT"
                confidence = 0.95
                human_escalation = False
            elif any(w in user_lower for w in ["interested", "pricing", "cost", "demo", "call", "schedule"]):
                intent = "HOT_DEAL"
                confidence = 0.65
                human_escalation = True
            elif any(w in user_lower for w in ["not interested", "no thanks", "busy", "later"]):
                intent = "OBJECTION"
                confidence = 0.70
                human_escalation = False
            elif any(w in user_lower for w in ["api", "integration", "how does", "technical", "security"]):
                intent = "TECHNICAL_INQUIRY"
                confidence = 0.70
                human_escalation = False
            else:
                intent = "GENERAL"
                confidence = 0.45
                human_escalation = True

            sample_dict = {
                "intent_category": intent,
                "confidence": confidence,
                "requested_action": "Opt-out" if intent == "OPT_OUT" else None,
                "objections": ["Expressed disinterest"] if intent == "OBJECTION" else [],
                "human_escalation_required": human_escalation,
                "proposed_response": None
            }
        else:
            # Generic fallback for any arbitrary BaseModel
            for field_name, field_info in fields.items():
                if field_info.annotation == str:
                    sample_dict[field_name] = f"Deterministic fallback for {field_name}"
                elif field_info.annotation == float:
                    sample_dict[field_name] = 0.40
                elif field_info.annotation == int:
                    sample_dict[field_name] = 1
                elif field_info.annotation == bool:
                    sample_dict[field_name] = False
                elif getattr(field_info.annotation, "__origin__", None) == list:
                    sample_dict[field_name] = []
                elif getattr(field_info.annotation, "__origin__", None) == dict:
                    sample_dict[field_name] = {}
                else:
                    sample_dict[field_name] = None

        try:
            parsed = response_schema.model_validate(sample_dict)
        except Exception:
            parsed = response_schema.model_construct(**sample_dict)

        raw_json = json.dumps(sample_dict)
        words_in = sum(len(m.get("content", "").split()) for m in messages)
        words_out = len(raw_json.split())

        return LLMStructuredResult(
            parsed=parsed,
            raw_output=raw_json,
            status=LLMExecutionStatus.LLM_FALLBACK,
            metadata=LLMUsageMetadata(
                prompt_tokens=words_in,
                completion_tokens=words_out,
                total_tokens=words_in + words_out,
                estimated_cost_usd=0.0,
                is_cost_estimated=True,
                model_name=self.model_name,
                provider_name="RULE_BASED",
                latency_ms=1.0
            ),
        )

    def generate_text(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None
    ) -> LLMTextResult:
        words_in = sum(len(m.get("content", "").split()) for m in messages)
        fallback_text = "Rule-based fallback response. Review required before action."
        words_out = len(fallback_text.split())

        return LLMTextResult(
            text=fallback_text,
            status=LLMExecutionStatus.LLM_FALLBACK,
            metadata=LLMUsageMetadata(
                prompt_tokens=words_in,
                completion_tokens=words_out,
                total_tokens=words_in + words_out,
                estimated_cost_usd=0.0,
                is_cost_estimated=True,
                model_name=self.model_name,
                provider_name="RULE_BASED",
                latency_ms=1.0
            )
        )


# Backward compatibility alias
RuleBasedLLMClient = RuleBasedFallbackProvider
