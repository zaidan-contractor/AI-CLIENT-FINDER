import re
from typing import Optional, Tuple, Dict, Any, List
from urllib.parse import urlparse
from sqlalchemy.orm import Session
from sqlalchemy import select, or_, and_

from app.models.business import MasterBusinessRecord, ContactRecord
from app.services.discovery_providers.base import DiscoveredBusiness
from app.services.entity_classifier import EntityClassifier
from app.core.security import canonicalize_email


class BusinessIdentityService:
    """
    Production-grade Canonical Business Identity and Deduplication Service.
    Enforces multi-signal deterministic deduplication within the workspace tenant.
    Never silently merges or drops ambiguous matches; flags them as POSSIBLE_DUPLICATE.
    """
    PUBLIC_DIRECTORY_DOMAINS = {
        "playo.co", "hudle.in", "justdial.com", "facebook.com", "instagram.com",
        "google.com", "maps.google.com", "yelp.com", "tripadvisor.com",
        "sulekha.com", "indiamart.com", "yellowpages.com"
    }

    @staticmethod
    def canonicalize_domain(url: Optional[str]) -> Optional[str]:
        if not url:
            return None
        clean = url.strip().lower()
        if not clean.startswith("http://") and not clean.startswith("https://"):
            clean = "https://" + clean
        try:
            parsed = urlparse(clean)
            netloc = parsed.netloc or parsed.path
            if ":" in netloc:
                netloc = netloc.split(":", 1)[0]
            if netloc.startswith("www."):
                netloc = netloc[4:]
            res = netloc.strip().rstrip("/")
            return res if "." in res else None
        except Exception:
            return None

    @staticmethod
    def normalize_business_name(name: str) -> str:
        if not name:
            return ""
        # Strip common legal suffixes and punctuation for identity matching
        cleaned = re.sub(r"[^\w\s]", "", name.lower())
        words = cleaned.split()
        stopwords = {"inc", "llc", "ltd", "corp", "co", "gmbh", "pvt", "sa"}
        filtered = [w for w in words if w not in stopwords]
        return " ".join(filtered) if filtered else " ".join(words)

    @staticmethod
    def normalize_phone(phone: Optional[str]) -> Optional[str]:
        if not phone:
            return None
        # Remove whitespace, hyphens, parens
        stripped = phone.strip()
        has_plus = stripped.startswith("+")
        digits = re.sub(r"\D", "", stripped)
        if not digits or len(digits) < 7:
            return None
        return f"+{digits}" if has_plus else digits

    @staticmethod
    def normalize_address(address: Optional[str]) -> Optional[str]:
        if not address:
            return None
        cleaned = re.sub(r"[^\w\s]", " ", address.lower())
        return " ".join(cleaned.split())

    @classmethod
    def resolve_or_create_business(
        cls,
        db: Session,
        workspace_id: str,
        candidate: DiscoveredBusiness
    ) -> Tuple[MasterBusinessRecord, str]:
        """
        Deduplicates candidate against existing businesses in workspace.
        Returns (business_record, resolution_status).
        Resolution statuses:
          - "CREATED": Brand new business record.
          - "EXACT_DUPLICATE": Matched via high-confidence signal (provider ID, domain, phone).
          - "POSSIBLE_DUPLICATE": Ambiguous match recorded with possible_duplicate_of_id.
        """
        c_domain = cls.canonicalize_domain(candidate.website_url)
        norm_name = cls.normalize_business_name(candidate.name)
        norm_phone = cls.normalize_phone(candidate.phone)
        norm_address = cls.normalize_address(candidate.address)

        # 1. Check Stable Provider ID match (Confidence: 1.0)
        if candidate.source_provider and candidate.source_provider_id:
            existing_provider = db.execute(
                select(MasterBusinessRecord).where(
                    MasterBusinessRecord.workspace_id == workspace_id,
                    MasterBusinessRecord.source_provider == candidate.source_provider,
                    MasterBusinessRecord.source_provider_id == candidate.source_provider_id
                )
            ).scalars().first()
            if existing_provider:
                return existing_provider, "EXACT_DUPLICATE"

        # 2. Check Canonical Domain match (Confidence: 0.95)
        # Directory platforms (Playo, Justdial, Hudle, etc.) must not merge unrelated businesses
        if c_domain and c_domain not in cls.PUBLIC_DIRECTORY_DOMAINS:
            existing_domain = db.execute(
                select(MasterBusinessRecord).where(
                    MasterBusinessRecord.workspace_id == workspace_id,
                    MasterBusinessRecord.canonical_domain == c_domain
                )
            ).scalars().first()
            if existing_domain:
                return existing_domain, "EXACT_DUPLICATE"
        elif c_domain and c_domain in cls.PUBLIC_DIRECTORY_DOMAINS and candidate.website_url:
            existing_url = db.execute(
                select(MasterBusinessRecord).where(
                    MasterBusinessRecord.workspace_id == workspace_id,
                    MasterBusinessRecord.website_url == candidate.website_url
                )
            ).scalars().first()
            if existing_url:
                return existing_url, "EXACT_DUPLICATE"

        # 3. Check Normalized Phone match (Confidence: 0.90)
        if norm_phone:
            existing_phone = db.execute(
                select(MasterBusinessRecord).where(
                    MasterBusinessRecord.workspace_id == workspace_id,
                    MasterBusinessRecord.normalized_phone == norm_phone
                )
            ).scalars().first()
            if existing_phone:
                return existing_phone, "EXACT_DUPLICATE"

        # 4. Check Strong Name + City/Address match (Confidence: 0.85)
        if norm_name and candidate.city:
            existing_name_city = db.execute(
                select(MasterBusinessRecord).where(
                    MasterBusinessRecord.workspace_id == workspace_id,
                    MasterBusinessRecord.normalized_name == norm_name,
                    MasterBusinessRecord.city.ilike(candidate.city)
                )
            ).scalars().first()
            if existing_name_city:
                return existing_name_city, "EXACT_DUPLICATE"

        # 5. Check Ambiguous Name Match without City (Confidence: 0.65 -> POSSIBLE_DUPLICATE)
        possible_dup_id = None
        identity_conf = 1.0
        resolution_status = "CREATED"

        if norm_name:
            ambiguous_match = db.execute(
                select(MasterBusinessRecord).where(
                    MasterBusinessRecord.workspace_id == workspace_id,
                    MasterBusinessRecord.normalized_name == norm_name
                )
            ).scalars().first()
            if ambiguous_match:
                possible_dup_id = ambiguous_match.id
                identity_conf = 0.65
                resolution_status = "POSSIBLE_DUPLICATE"

        # Classify entity and sales eligibility
        raw_meta = dict(candidate.raw_metadata or {})
        ent_classification = EntityClassifier.classify_entity(
            name=candidate.name,
            category=candidate.category,
            raw_metadata=raw_meta,
            source_provider=candidate.source_provider
        )
        raw_meta["entity_classification_reasons"] = ent_classification.reasons
        raw_meta["entity_supporting_signals"] = ent_classification.supporting_signals

        # Create new MasterBusinessRecord
        biz = MasterBusinessRecord(
            workspace_id=workspace_id,
            name=candidate.name,
            normalized_name=norm_name,
            website_url=candidate.website_url,
            canonical_domain=c_domain,
            phone=candidate.phone,
            normalized_phone=norm_phone,
            primary_email=candidate.primary_email,
            address=candidate.address,
            city=candidate.city,
            state=candidate.state,
            country=candidate.country or "US",
            postal_code=candidate.postal_code,
            category=candidate.category,
            source_provider=candidate.source_provider,
            source_provider_id=candidate.source_provider_id,
            possible_duplicate_of_id=possible_dup_id,
            identity_confidence=identity_conf,
            entity_type=ent_classification.entity_type,
            entity_type_confidence=ent_classification.confidence,
            sales_eligibility_state=ent_classification.sales_eligibility_state,
            raw_metadata=raw_meta
        )
        db.add(biz)
        db.flush()

        # Create initial Contact Record if primary_email exists
        if candidate.primary_email:
            cls.create_or_update_contact(
                db=db,
                workspace_id=workspace_id,
                business_id=biz.id,
                email=candidate.primary_email,
                role_title="General Contact"
            )

        db.commit()
        db.refresh(biz)
        return biz, resolution_status

    @classmethod
    def create_or_update_contact(
        cls,
        db: Session,
        workspace_id: str,
        business_id: str,
        email: str,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        role_title: Optional[str] = None,
        verification_tier: str = "UNVERIFIED",
        metadata: Optional[Dict[str, Any]] = None
    ) -> ContactRecord:
        c_email = canonicalize_email(email)
        contact = db.execute(
            select(ContactRecord).where(
                ContactRecord.workspace_id == workspace_id,
                ContactRecord.business_id == business_id,
                ContactRecord.canonical_email == c_email
            )
        ).scalars().first()

        if not contact:
            contact = ContactRecord(
                workspace_id=workspace_id,
                business_id=business_id,
                email=email,
                canonical_email=c_email,
                first_name=first_name,
                last_name=last_name,
                role_title=role_title or "General Contact",
                verification_tier=verification_tier,
                verification_metadata=metadata or {}
            )
            db.add(contact)
        else:
            if first_name and not contact.first_name:
                contact.first_name = first_name
            if last_name and not contact.last_name:
                contact.last_name = last_name
            if role_title and not contact.role_title:
                contact.role_title = role_title
            if metadata:
                merged = dict(contact.verification_metadata or {})
                merged.update(metadata)
                contact.verification_metadata = merged

        db.flush()
        return contact
