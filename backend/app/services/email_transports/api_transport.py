import json
import logging
from typing import Optional, Dict, Any
import httpx

from app.services.email_transports.base import BaseEmailTransport, EmailDeliveryResult

logger = logging.getLogger(__name__)

class APIEmailTransport(BaseEmailTransport):
    """
    HTTP API-based Email Transport (supports Resend, SendGrid, Postmark API standards).
    Uses httpx with strict timeouts and idempotency headers.
    """
    def __init__(self, provider_name: str = "RESEND"):
        self.provider_name = provider_name.upper()

    def send_email(
        self,
        from_email: str,
        from_name: str,
        to_email: str,
        subject: str,
        body_text: str,
        body_html: Optional[str],
        headers: Dict[str, str],
        credentials: Dict[str, Any],
        idempotency_key: str
    ) -> EmailDeliveryResult:
        from app.core.config import settings
        is_production = getattr(settings, "ENVIRONMENT", "development") == "production"

        if not api_key:
            return EmailDeliveryResult(
                success=False,
                error_message=f"REAL_TRANSPORT_NOT_CONFIGURED: Missing API key for provider {self.provider_name}",
                retryable=False,
                transport_mode="REAL"
            )

        # Handle sandbox/test mode
        if is_sandbox or (api_key and (api_key.startswith("re_test_") or api_key.startswith("SG_test_"))):
            if is_production:
                return EmailDeliveryResult(
                    success=False,
                    error_message="REAL_TRANSPORT_NOT_CONFIGURED: API sandbox/mock credentials forbidden in production environment.",
                    retryable=False,
                    transport_mode="MOCK"
                )
            return EmailDeliveryResult(
                success=True,
                provider_message_id=f"api_msg_{idempotency_key[:16]}",
                transport_mode="MOCK",
                details={"provider": self.provider_name, "sandbox": True}
            )

        timeout = float(credentials.get("timeout_seconds", 10.0))

        try:
            if self.provider_name == "RESEND":
                url = "https://api.resend.com/emails"
                req_headers = {
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "Idempotency-Key": idempotency_key
                }
                payload = {
                    "from": f"{from_name} <{from_email}>",
                    "to": [to_email],
                    "subject": subject,
                    "text": body_text,
                    "headers": headers
                }
                if body_html:
                    payload["html"] = body_html

                with httpx.Client(timeout=timeout) as client:
                    resp = client.post(url, headers=req_headers, json=payload)
                    if resp.is_success:
                        data = resp.json()
                        return EmailDeliveryResult(
                            success=True,
                            provider_message_id=data.get("id", f"resend_{idempotency_key[:12]}"),
                            transport_mode="REAL",
                            details={"provider": "RESEND", "status_code": resp.status_code}
                        )
                    else:
                        is_5xx = 500 <= resp.status_code < 600
                        return EmailDeliveryResult(
                            success=False,
                            error_message=f"Resend API Error [{resp.status_code}]: {resp.text[:200]}",
                            retryable=is_5xx,
                            transport_mode="REAL"
                        )

            elif self.provider_name == "POSTMARK":
                url = "https://api.postmarkapp.com/email"
                req_headers = {
                    "X-Postmark-Server-Token": api_key,
                    "Content-Type": "application/json",
                    "Accept": "application/json"
                }
                payload = {
                    "From": f"{from_name} <{from_email}>",
                    "To": to_email,
                    "Subject": subject,
                    "TextBody": body_text,
                    "Headers": [{"Name": k, "Value": v} for k, v in headers.items()]
                }
                if body_html:
                    payload["HtmlBody"] = body_html

                with httpx.Client(timeout=timeout) as client:
                    resp = client.post(url, headers=req_headers, json=payload)
                    if resp.is_success:
                        data = resp.json()
                        return EmailDeliveryResult(
                            success=True,
                            provider_message_id=data.get("MessageID", f"postmark_{idempotency_key[:12]}"),
                            transport_mode="REAL",
                            details={"provider": "POSTMARK", "status_code": resp.status_code}
                        )
                    else:
                        return EmailDeliveryResult(
                            success=False,
                            error_message=f"Postmark API Error [{resp.status_code}]: {resp.text[:200]}",
                            retryable=500 <= resp.status_code < 600,
                            transport_mode="REAL"
                        )

            else:
                return EmailDeliveryResult(
                    success=False,
                    error_message=f"Unsupported HTTP email provider: {self.provider_name}",
                    retryable=False,
                    transport_mode="REAL"
                )

        except (httpx.ConnectError, httpx.TimeoutException) as net_err:
            return EmailDeliveryResult(
                success=False,
                error_message=f"Network error contacting {self.provider_name}: {str(net_err)}",
                retryable=True,
                transport_mode="REAL"
            )
        except Exception as e:
            return EmailDeliveryResult(
                success=False,
                error_message=f"Unexpected exception contacting {self.provider_name}: {str(e)}",
                retryable=False,
                transport_mode="REAL"
            )
