from typing import Optional, Dict
from app.services.email_transports.base import BaseEmailTransport
from app.services.email_transports.smtp_transport import SMTPEmailTransport
from app.services.email_transports.api_transport import APIEmailTransport
from app.services.email_transports.mock_transport import MockEmailTransport

_OVERRIDE_TRANSPORT: Optional[BaseEmailTransport] = None

def set_transport_override(transport: Optional[BaseEmailTransport]):
    """Set global transport override for test environments."""
    if transport is not None:
        from app.core.config import settings
        if getattr(settings, "ENVIRONMENT", "development") == "production":
            if isinstance(transport, MockEmailTransport) or getattr(transport, "is_mock", False):
                raise ValueError("MOCK transport override is strictly forbidden in production environment.")
    global _OVERRIDE_TRANSPORT
    _OVERRIDE_TRANSPORT = transport

def get_email_transport(provider_type: str = "SMTP") -> BaseEmailTransport:
    """
    Factory resolving provider-specific email transport.
    Respects test overrides when active in non-production environments.
    """
    from app.core.config import settings
    is_production = getattr(settings, "ENVIRONMENT", "development") == "production"

    global _OVERRIDE_TRANSPORT
    if _OVERRIDE_TRANSPORT is not None:
        if is_production and (isinstance(_OVERRIDE_TRANSPORT, MockEmailTransport) or getattr(_OVERRIDE_TRANSPORT, "is_mock", False)):
            raise ValueError("MOCK transport override is strictly forbidden in production environment.")
        return _OVERRIDE_TRANSPORT

    p_type = (provider_type or "SMTP").upper()
    if p_type in ["MOCK", "SANDBOX"]:
        if is_production:
            raise ValueError("MOCK or SANDBOX email transport is strictly forbidden in production environment.")
        return MockEmailTransport()
    elif p_type == "SMTP":
        return SMTPEmailTransport()
    elif p_type in ["RESEND", "POSTMARK"]:
        return APIEmailTransport(provider_name=p_type)
    else:
        # Default to SMTP for standard compliance
        return SMTPEmailTransport()
