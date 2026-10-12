from abc import ABC, abstractmethod
from typing import Optional, Dict, Any
from pydantic import BaseModel

class EmailDeliveryResult(BaseModel):
    success: bool
    provider_message_id: Optional[str] = None
    error_message: Optional[str] = None
    retryable: bool = False
    transport_mode: str = "REAL"  # "REAL" or "MOCK"
    details: Dict[str, Any] = {}

class BaseEmailTransport(ABC):
    """
    Provider-agnostic email transport interface.
    The actual provider transmission must occur ONLY after all 16 gates pass.
    Never exposes decrypted credentials to logs or audit outputs.
    """
    @abstractmethod
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
        """
        Transmits outbound email via provider.
        Must return EmailDeliveryResult with real or deterministic provider_message_id.
        """
        pass
