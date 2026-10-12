from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional
from pydantic import BaseModel

class ContactVerificationResult(BaseModel):
    email: str
    verification_tier: str  # "CONFIRMED_VERIFIED", "PROVIDER_VERIFIED", "UNVERIFIED", "FAILED_VERIFICATION"
    is_deliverable: bool
    confidence: float
    mx_records: List[str] = []
    details: Dict[str, Any] = {}

class BaseContactVerifier(ABC):
    """Abstract interface for contact verification providers."""
    @abstractmethod
    def verify_email(self, email: str, credentials: Optional[Dict[str, Any]] = None) -> ContactVerificationResult:
        pass
