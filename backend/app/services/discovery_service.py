import logging
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session

from app.services.discovery_providers.base import BusinessDiscoveryProvider, DiscoveredBusiness
from app.services.discovery_providers.openstreetmap import OpenStreetMapDiscoveryProvider
from app.services.discovery_providers.local_directory import LocalDirectoryDiscoveryProvider
from app.services.business_identity_service import BusinessIdentityService
from app.models.business import MasterBusinessRecord

logger = logging.getLogger(__name__)


class DiscoveryService:
    """
    Provider-Agnostic Discovery Orchestration Service.
    Coordinates searches across external and synthetic discovery providers,
    enforcing bounded limits, provider provenance, rate-limit compliance,
    and deterministic deduplication into canonical business records.
    """

    def __init__(self, providers: Optional[List[BusinessDiscoveryProvider]] = None):
        self.providers = providers or [
            OpenStreetMapDiscoveryProvider(),
            LocalDirectoryDiscoveryProvider()
        ]

    def search_and_register(
        self,
        db: Session,
        workspace_id: str,
        query: str,
        location: str,
        category: str,
        limit: int = 20
    ) -> Dict[str, Any]:
        """
        Executes targeted discovery search, parses provider provenance,
        and registers canonical businesses via BusinessIdentityService.
        """
        bounded_limit = max(1, min(limit, 50))
        all_discovered: List[DiscoveredBusiness] = []
        errors: List[str] = []

        for provider in self.providers:
            try:
                results = provider.search(
                    query=query,
                    location=location,
                    category=category,
                    limit=bounded_limit
                )
                all_discovered.extend(results)
            except Exception as e:
                logger.warning("Discovery provider %s failed: %s", provider.provider_name, e)
                errors.append(f"{provider.provider_name}: {str(e)}")

        created_businesses: List[MasterBusinessRecord] = []
        exact_duplicates: int = 0
        possible_duplicates: int = 0

        for candidate in all_discovered:
            biz, status = BusinessIdentityService.resolve_or_create_business(
                db=db,
                workspace_id=workspace_id,
                candidate=candidate
            )
            if status == "CREATED":
                created_businesses.append(biz)
            elif status == "EXACT_DUPLICATE":
                exact_duplicates += 1
            elif status == "POSSIBLE_DUPLICATE":
                possible_duplicates += 1
                created_businesses.append(biz)

        def _classify_origin(b: MasterBusinessRecord):
            prov = (b.source_provider or "").upper()
            domain = (b.canonical_domain or "").lower()
            url = (b.website_url or "").lower()
            prov_id = (b.source_provider_id or "").lower()
            if (
                prov in ["MANUAL", "SEED", "SYNTHETIC", "DEMO", "TEST_FIXTURE"]
                or "example.com" in domain
                or "example.com" in url
                or "demo" in prov_id
            ):
                return True, "DEMO_FIXTURE"
            return False, "REAL_DISCOVERY"

        out_businesses = []
        for b in created_businesses:
            is_demo, origin = _classify_origin(b)
            out_businesses.append({
                "id": b.id,
                "name": b.name,
                "website_url": b.website_url,
                "canonical_domain": b.canonical_domain,
                "phone": b.phone,
                "primary_email": b.primary_email,
                "city": b.city,
                "category": b.category,
                "source_provider": b.source_provider,
                "identity_confidence": b.identity_confidence,
                "possible_duplicate_of_id": b.possible_duplicate_of_id,
                "is_demo_fixture": is_demo,
                "data_origin": origin
            })

        return {
            "query": query,
            "location": location,
            "category": category,
            "total_discovered_candidates": len(all_discovered),
            "newly_registered": len(created_businesses),
            "exact_duplicates_skipped": exact_duplicates,
            "possible_duplicates_flagged": possible_duplicates,
            "errors": errors,
            "businesses": out_businesses
        }
