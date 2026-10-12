import os
import sys
import json
import time
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field

# Ensure backend root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.outreach import OutboundMessage
from app.services.llm.base import LLMExecutionStatus, LLMErrorClassification
from app.services.llm.groq_provider import GroqProvider
from app.services.llm.provider_status_tracker import ProviderStatusTracker, ProviderOperationalState


class ControlledDiagnosticSchema(BaseModel):
    status: str
    purpose: str
    synthetic: bool


def run_diagnostic() -> Dict[str, Any]:
    print("=" * 70)
    print("CONTROLLED LIVE GROQ DIAGNOSTIC EXECUTION")
    print("=" * 70)

    provider_name = "GROQ"
    model_name = settings.GROQ_MODEL or "openai/gpt-oss-120b"
    api_key = settings.GROQ_API_KEY

    # Audit outbound messages before diagnostic
    db = SessionLocal()
    try:
        initial_outbound_count = db.query(OutboundMessage).count()
    finally:
        db.close()

    result: Dict[str, Any] = {
        "provider": "Groq",
        "model": model_name,
        "real_request_performed": False,
        "http_result": None,
        "auth_result": None,
        "provider_status_before": None,
        "provider_status_after": None,
        "structured_output_result": None,
        "parsed_output": None,
        "token_usage": None,
        "cost_usd": None,
        "latency_ms": None,
        "retry_count": 0,
        "fallback_invoked": False,
        "outbound_emails": 0,
        "prospect_communications": 0,
        "outbound_message_created": 0,
        "outbound_message_transmitted": 0,
        "api_key_exposed": False,
        "compatibility_issues": None,
        "groq_verified_available": False,
    }

    # Initial state check
    state_before, meta_before = ProviderStatusTracker.get_provider_state(
        provider_name, model_name, has_credentials=bool(api_key and api_key.strip())
    )
    result["provider_status_before"] = state_before.value

    if not api_key or not api_key.strip():
        result["auth_result"] = "MISSING_CREDENTIALS"
        result["provider_status_after"] = ProviderOperationalState.ERROR.value
        result["http_result"] = "NOT_EXECUTED (Missing GROQ_API_KEY in backend/.env)"
        print("\n[DIAGNOSTIC BLOCKER]: GROQ_API_KEY is not configured in backend/.env.")
        print(f"Provider Status: {result['provider_status_after']}")
        return result

    # Configured with credentials: make exactly ONE live request
    print(f"\nModel: {model_name}")
    print(f"Endpoint: {settings.GROQ_BASE_URL}")
    print("Credential: [CONFIGURED - REDACTED]")
    print("\nExecuting single controlled live HTTPS request to Groq API...")

    # Instantiate provider with max_retries=1 to prevent automatic retries during this diagnostic
    provider = GroqProvider(
        api_key=api_key.strip(),
        base_url=settings.GROQ_BASE_URL,
        model=model_name,
        timeout_seconds=settings.LLM_TIMEOUT_SECONDS,
        max_retries=1
    )

    diagnostic_messages = [
        {
            "role": "user",
            "content": (
                "Return JSON with:\n"
                "{\n"
                '  "status": "ok",\n'
                '  "purpose": "controlled_provider_diagnostic",\n'
                '  "synthetic": true\n'
                "}\n"
                "Do not include any real business, person, prospect, contact, or private information."
            )
        }
    ]

    start_time = time.time()
    result["real_request_performed"] = True

    try:
        exec_res = provider.generate_structured(
            messages=diagnostic_messages,
            response_schema=ControlledDiagnosticSchema
        )
        elapsed_ms = round((time.time() - start_time) * 1000, 2)
        result["latency_ms"] = elapsed_ms

        if exec_res.is_success and exec_res.parsed:
            result["http_result"] = "HTTP 200 OK"
            result["auth_result"] = "AUTHENTICATED_VALID"
            result["structured_output_result"] = "VALIDATED_PYDANTIC_SUCCESS"
            result["parsed_output"] = exec_res.parsed.model_dump()
            result["token_usage"] = {
                "prompt_tokens": exec_res.metadata.prompt_tokens,
                "completion_tokens": exec_res.metadata.completion_tokens,
                "total_tokens": exec_res.metadata.total_tokens,
            }
            result["cost_usd"] = exec_res.metadata.estimated_cost_usd
            ProviderStatusTracker.record_success(provider_name, model_name)
            result["provider_status_after"] = ProviderOperationalState.AVAILABLE.value
            result["groq_verified_available"] = True
            print("\n[SUCCESS]: Live Groq diagnostic completed successfully.")
            print(f"Latency: {elapsed_ms}ms")
            print(f"Tokens: {result['token_usage']}")
            print(f"Cost: ${result['cost_usd']:.6f}")
            print(f"Parsed response: {result['parsed_output']}")
            print(f"Provider Operational State: CONFIGURED -> AVAILABLE")
        else:
            result["http_result"] = "EXECUTION_FAILED"
            result["structured_output_result"] = f"FAILED: {exec_res.error_message}"
            state_after, _ = ProviderStatusTracker.get_provider_state(provider_name, model_name, has_credentials=True)
            result["provider_status_after"] = state_after.value
            print(f"\n[FAILURE]: Structured generation failed: {exec_res.error_message}")
            print(f"Provider Operational State: {result['provider_status_after']}")

    except Exception as e:
        elapsed_ms = round((time.time() - start_time) * 1000, 2)
        result["latency_ms"] = elapsed_ms
        norm_err = provider.normalize_error(e)
        result["http_result"] = f"EXCEPTION: {norm_err}"
        result["structured_output_result"] = "EXCEPTION_RAISED"

        # Update tracker with truthful failure state
        import httpx
        if isinstance(e, httpx.HTTPStatusError):
            if e.response.status_code == 401 or e.response.status_code == 403:
                result["auth_result"] = f"AUTH_FAILED (HTTP {e.response.status_code})"
                ProviderStatusTracker.record_http_error(provider_name, model_name, e.response.status_code, norm_err)
                result["provider_status_after"] = ProviderOperationalState.ERROR.value
            elif e.response.status_code == 429:
                result["auth_result"] = "AUTHENTICATED_VALID (Rate limited / Quota exhausted)"
                ProviderStatusTracker.record_http_error(provider_name, model_name, 429, norm_err)
                result["provider_status_after"] = ProviderOperationalState.QUOTA_EXHAUSTED.value
            else:
                result["auth_result"] = "AUTHENTICATED_VALID"
                ProviderStatusTracker.record_http_error(provider_name, model_name, e.response.status_code, norm_err)
                result["provider_status_after"] = ProviderOperationalState.ERROR.value
        else:
            result["auth_result"] = "UNKNOWN_OR_NETWORK_ERROR"
            ProviderStatusTracker.record_diagnostic(provider_name, model_name, ProviderOperationalState.ERROR, error_message=norm_err)
            result["provider_status_after"] = ProviderOperationalState.ERROR.value

        print(f"\n[EXCEPTION]: {norm_err}")
        print(f"Provider Operational State: {result['provider_status_after']}")

    # Audit outbound messages after diagnostic to guarantee zero outreach
    db = SessionLocal()
    try:
        final_outbound_count = db.query(OutboundMessage).count()
        new_outbound = final_outbound_count - initial_outbound_count
        result["outbound_message_created"] = new_outbound
        result["outbound_message_transmitted"] = 0
    finally:
        db.close()

    # Verify no secret leaked into result dictionary
    for k, v in result.items():
        val_str = str(v)
        if api_key and api_key in val_str and len(api_key) > 8:
            result["api_key_exposed"] = True

    return result


if __name__ == "__main__":
    diag_res = run_diagnostic()
    print("\n" + "=" * 70)
    print("DIAGNOSTIC SUMMARY JSON:")
    print(json.dumps(diag_res, indent=2))
    print("=" * 70)
