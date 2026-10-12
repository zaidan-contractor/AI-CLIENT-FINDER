from abc import ABC, abstractmethod
from typing import List, Optional, Dict, Any
from pydantic import BaseModel

class DiscoveredBusiness(BaseModel):
    name: str
    website_url: Optional[str] = None
    phone: Optional[str] = None
    primary_email: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    postal_code: Optional[str] = None
    category: str
    source_provider: str
    source_provider_id: Optional[str] = None
    raw_metadata: Dict[str, Any] = {}

class BusinessDiscoveryProvider(ABC):
    @property
    @abstractmethod
    def provider_name(self) -> str:
        pass

    @abstractmethod
    def search(
        self,
        query: str,
        location: str,
        category: str,
        limit: int = 50
    ) -> List[DiscoveredBusiness]:
        """Executes targeted discovery search against external/local provider."""
        pass
