import email.utils
from typing import Optional, Dict, Any, List
from app.services.email_transports.base import BaseEmailTransport, EmailDeliveryResult

class MockEmailTransport(BaseEmailTransport):
    """
    In-memory mock email transport for test harnesses.
    Captures transmitted messages and headers to allow assertions without network side effects.
    """
    def __init__(self):
        self.sent_messages: List[Dict[str, Any]] = []
        self.simulate_failure: bool = False
        self.simulate_retryable_failure: bool = False
        self.failure_reason: str = "Simulated delivery failure"

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
        if self.simulate_failure:
            return EmailDeliveryResult(
                success=False,
                error_message=self.failure_reason,
                retryable=self.simulate_retryable_failure,
                transport_mode="MOCK"
            )

        msg_id = f"msg_mock_{idempotency_key[:12]}"
        self.sent_messages.append({
            "from_email": from_email,
            "from_name": from_name,
            "to_email": to_email,
            "subject": subject,
            "body_text": body_text,
            "body_html": body_html,
            "headers": headers,
            "provider_message_id": msg_id,
            "idempotency_key": idempotency_key
        })

        return EmailDeliveryResult(
            success=True,
            provider_message_id=msg_id,
            transport_mode="MOCK",
            details={"transport": "MOCK", "queue_len": len(self.sent_messages)}
        )

    def reset(self):
        self.sent_messages.clear()
        self.simulate_failure = False
        self.simulate_retryable_failure = False
