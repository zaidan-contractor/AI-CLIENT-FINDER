import time
import logging
from typing import List, Optional
import httpx

from app.services.discovery_providers.base import BusinessDiscoveryProvider, DiscoveredBusiness

logger = logging.getLogger(__name__)

class OpenStreetMapDiscoveryProvider(BusinessDiscoveryProvider):
    """
    Real OpenStreetMap / Nominatim Discovery Provider.
    Executes bounded HTTP API requests against Nominatim with proper User-Agent,
    rate-limit compliance, and error resilience.
    """
    _last_request_time = 0.0

    @property
    def provider_name(self) -> str:
        return "OPENSTREETMAP"

    def search(
        self,
        query: str,
        location: str,
        category: str,
        limit: int = 50,
        allow_fallback: bool = False
    ) -> List[DiscoveredBusiness]:
        results: List[DiscoveredBusiness] = []
        seen_ids = set()

        loc_clean = location.split(",")[0].strip()
        search_terms = [
            f"{query} in {location}".strip(),
            f"{query} {loc_clean}".strip(),
            f"{query.rstrip('s')} {loc_clean}".strip(),
            f"futsal {loc_clean}".strip() if "futsal" in query.lower() else None,
            f"sports arena {loc_clean}".strip() if "arena" in query.lower() else None,
            f"sports complex {loc_clean}".strip()
        ]
        queries_to_try = []
        for st in search_terms:
            if st and st not in queries_to_try:
                queries_to_try.append(st)

        url = "https://nominatim.openstreetmap.org/search"
        headers = {
            "User-Agent": "OpportunityPlatform/1.0 (compliance@platform.test)",
            "Accept": "application/json"
        }

        for st in queries_to_try:
            if len(results) >= limit:
                break

            now = time.time()
            elapsed = now - self.__class__._last_request_time
            if elapsed < 1.0:
                time.sleep(1.0 - elapsed)
            self.__class__._last_request_time = time.time()

            params = {
                "q": st,
                "format": "json",
                "addressdetails": 1,
                "extratags": 1,
                "limit": min(limit - len(results), 50)
            }

            try:
                with httpx.Client(timeout=6.0) as client:
                    resp = client.get(url, params=params, headers=headers)
                    if resp.is_success:
                        items = resp.json()
                        if isinstance(items, list):
                            for item in items:
                                pid = str(item.get("osm_id", item.get("place_id", "")))
                                if not pid or pid in seen_ids:
                                    continue
                                seen_ids.add(pid)

                                extratags = item.get("extratags") or {}
                                address = item.get("address") or {}

                                name = item.get("name") or item.get("display_name", "").split(",")[0]
                                website = extratags.get("website") or extratags.get("contact:website")
                                phone = extratags.get("phone") or extratags.get("contact:phone")
                                email = extratags.get("email") or extratags.get("contact:email")
                                city = address.get("city") or address.get("town") or address.get("village") or address.get("county") or loc_clean
                                country = address.get("country_code", "in").upper()

                                results.append(
                                    DiscoveredBusiness(
                                        name=name,
                                        website_url=website,
                                        phone=phone,
                                        primary_email=email,
                                        address=item.get("display_name"),
                                        city=city,
                                        country=country,
                                        category=category,
                                        source_provider=self.provider_name,
                                        source_provider_id=pid,
                                        raw_metadata={
                                            "osm_type": item.get("osm_type"),
                                            "lat": item.get("lat"),
                                            "lon": item.get("lon"),
                                            "category": item.get("category"),
                                            "extratags": extratags,
                                            "matched_query": st
                                        }
                                    )
                                )
                                if len(results) >= limit:
                                    break
            except Exception as e:
                logger.warning("OSM Nominatim request failed for '%s': %s", st, e)

        if results:
            return results

        if allow_fallback:
            simulated_entities = [
                {
                    "name": f"{location.title()} Dental Care",
                    "website_url": f"https://www.{location.lower().replace(' ', '')}dental.example.com",
                    "phone": "+1-555-0199",
                    "primary_email": f"appointments@{location.lower().replace(' ', '')}dental.example.com",
                    "address": f"120 Main Street, {location.title()}",
                    "city": location.title(),
                    "country": "US",
                    "category": category,
                    "provider_id": "osm-node-101"
                },
                {
                    "name": f"Elite {category.title()} Studio",
                    "website_url": f"https://www.elite{category.lower().replace(' ', '')}.example.com",
                    "phone": "+1-555-0245",
                    "primary_email": f"contact@elite{category.lower().replace(' ', '')}.example.com",
                    "address": f"450 Commercial Way, {location.title()}",
                    "city": location.title(),
                    "country": "US",
                    "category": category,
                    "provider_id": "osm-node-102"
                }
            ]
            for item in simulated_entities[:limit]:
                results.append(
                    DiscoveredBusiness(
                        name=item["name"],
                        website_url=item["website_url"],
                        phone=item["phone"],
                        primary_email=item["primary_email"],
                        address=item["address"],
                        city=item["city"],
                        country=item["country"],
                        category=item["category"],
                        source_provider=self.provider_name,
                        source_provider_id=item["provider_id"],
                        raw_metadata={"fallback": True, "query": query, "location": location}
                    )
                )

        return results
