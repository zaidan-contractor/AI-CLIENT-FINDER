import json
import logging
from typing import Type, TypeVar
import httpx
from pydantic import BaseModel

from app.services.llm.base import BaseLLMClient, LLMStructuredResult, LLMUsageMetadata

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

class OpenAILikeLLMClient(BaseLLMClient):
    """
    Real OpenAI/Groq/Ollama/Gemini-compatible HTTP LLM provider.
    Enforces strict timeouts, structured output validation, and cost tracking.
    """
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        model_name: str = "gpt-4o-mini"
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model_name = model_name

    def generate_structured(
        self,
        system_instruction: str,
        user_prompt: str,
        response_schema: Type[T],
        temperature: float = 0.1,
        max_tokens: int = 1500
    ) -> LLMStructuredResult[T]:
        sanitized_user = self.sanitize_untrusted_input(user_prompt)
        sanitized_system = self.sanitize_untrusted_input(system_instruction)

        # Append schema requirement
        schema_json = json.dumps(response_schema.model_json_schema())
        augmented_system = (
            f"{sanitized_system}\n\n"
            f"You MUST reply ONLY with a valid JSON object strictly matching this schema:\n"
            f"{schema_json}\n"
            f"Do not include markdown code block backticks. Output valid raw JSON only."
        )

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": augmented_system},
                {"role": "user", "content": sanitized_user}
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"}
        }

        try:
            with httpx.Client(timeout=15.0) as client:
                resp = client.post(f"{self.base_url}/chat/completions", headers=headers, json=payload)
                if resp.is_success:
                    res_data = resp.json()
                    raw_content = res_data["choices"][0]["message"]["content"]
                    usage = res_data.get("usage", {})
                    p_tok = usage.get("prompt_tokens", 0)
                    c_tok = usage.get("completion_tokens", 0)
                    cost = (p_tok * 0.00015 / 1000) + (c_tok * 0.0006 / 1000)

                    parsed_obj = response_schema.model_validate_json(raw_content)
                    return LLMStructuredResult(
                        parsed=parsed_obj,
                        raw_output=raw_content,
                        metadata=LLMUsageMetadata(
                            prompt_tokens=p_tok,
                            completion_tokens=c_tok,
                            total_tokens=p_tok + c_tok,
                            estimated_cost_usd=cost,
                            model_name=self.model_name,
                            version_provenance=res_data.get("model", self.model_name)
                        ),
                        is_fallback=False
                    )
                else:
                    logger.error("LLM Provider HTTP error [%s]: %s", resp.status_code, resp.text[:200])
                    raise RuntimeError(f"LLM Provider API returned status {resp.status_code}")
        except Exception as e:
            logger.warning("LLM API call failed (%s). Falling back to rule-based generation.", e)
            from app.services.llm.rule_based_fallback import RuleBasedLLMClient
            return RuleBasedLLMClient().generate_structured(
                system_instruction=system_instruction,
                user_prompt=user_prompt,
                response_schema=response_schema
            )
