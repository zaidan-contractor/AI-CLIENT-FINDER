import re
from urllib.parse import urlparse
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.models.business import MasterBusinessRecord, ContactRecord, MarketTarget
from app.services.discovery_providers.base import BusinessDiscoveryProvider, DiscoveredBusiness
from app.services.discovery_providers.openstreetmap import OpenStreetMapDiscoveryProvider
from app.services.discovery_providers.local_directory import LocalDirectoryDiscoveryProvider
from app.core.security import canonicalize_email

def canonicalize_domain(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    clean = url.strip().lower()
    if not clean.startswith("http://") and not clean.startswith("https://"):
        clean = "https://" + clean
    try:
        parsed = urlparse(clean)
        netloc = parsed.netloc or parsed.path
        # Remove port if present
        if ":" in netloc:
            netloc = netloc.split(":", 1)[0]
        # Remove www.
        if netloc.startswith("www."):
            netloc = netloc[4:]
        return netloc.strip().rstrip("/")
    except Exception:
        return None

def normalize_business_name(name: str) -> str:
    cleaned = re.sub(r"[^\w\s]", "", name.lower())
    return " ".join(cleaned.split())

class MarketEngine:
    def __init__(self, providers: Optional[List[BusinessDiscoveryProvider]] = None):
        self.providers = providers or [
            OpenStreetMapDiscoveryProvider(),
            LocalDirectoryDiscoveryProvider()
        ]

    def execute_discovery(
        self,
        db: Session,
        workspace_id: str,
        target_id: str,
        limit_per_provider: int = 20
    ) -> Dict[str, Any]:
        """
        Runs multi-provider discovery pipeline for a MarketTarget,
        deduplicates within the workspace, and persists master business & contact records.
        """
        target = db.execute(
            select(MarketTarget).where(MarketTarget.id == target_id, MarketTarget.workspace_id == workspace_id)
        ).scalars().first()

        if not target:
            raise ValueError("Target not found.")

        target.status = "RUNNING"
        db.commit()

        discovered_raw: List[DiscoveredBusiness] = []
        for provider in self.providers:
            try:
                results = provider.search(
                    query=target.query_text,
                    location=target.location,
                    category=target.target_category,
                    limit=limit_per_provider
                )
                discovered_raw.extend(results)
            except Exception as e:
                # Resilient: continue with next provider
                continue

        created_count = 0
        duplicate_count = 0

        for item in discovered_raw:
            c_domain = canonicalize_domain(item.website_url)
            norm_name = normalize_business_name(item.name)

            # Check duplication by domain or normalized name within workspace
            existing = None
            if c_domain:
                existing = db.execute(
                    select(MasterBusinessRecord).where(
                        MasterBusinessRecord.workspace_id == workspace_id,
                        MasterBusinessRecord.canonical_domain == c_domain
                    )
                ).scalars().first()

            if not existing and norm_name:
                existing = db.execute(
                    select(MasterBusinessRecord).where(
                        MasterBusinessRecord.workspace_id == workspace_id,
                        MasterBusinessRecord.normalized_name == norm_name
                    )
                ).scalars().first()

            if existing:
                duplicate_count += 1
                continue

            # Create Master Business Record
            biz = MasterBusinessRecord(
                workspace_id=workspace_id,
                name=item.name,
                normalized_name=norm_name,
                website_url=item.website_url,
                canonical_domain=c_domain,
                phone=item.phone,
                primary_email=item.primary_email,
                address=item.address,
                city=item.city,
                state=item.state,
                country=item.country,
                postal_code=item.postal_code,
                category=item.category,
                source_provider=item.source_provider,
                source_provider_id=item.source_provider_id,
                raw_metadata=item.raw_metadata
            )
            db.add(biz)
            db.flush()

            # Create initial Contact Record if email present
            if item.primary_email:
                c_email = canonicalize_email(item.primary_email)
                contact = ContactRecord(
                    workspace_id=workspace_id,
                    business_id=biz.id,
                    email=item.primary_email,
                    canonical_email=c_email,
                    role_title="General Contact",
                    verification_tier="UNVERIFIED"
                )
                db.add(contact)

            created_count += 1

        target.status = "COMPLETED"
        target.discovered_count += created_count
        db.commit()

        return {
            "target_id": target_id,
            "status": "COMPLETED",
            "newly_discovered": created_count,
            "duplicates_skipped": duplicate_count,
            "total_workspace_businesses": db.execute(
                select(MasterBusinessRecord).where(MasterBusinessRecord.workspace_id == workspace_id)
            ).scalars().all().__len__()
        }
