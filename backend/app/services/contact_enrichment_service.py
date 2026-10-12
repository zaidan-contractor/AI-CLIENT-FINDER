import re
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.models.business import MasterBusinessRecord, ContactRecord
from app.services.contact_verification.dns_mx_verifier import DNSMXContactVerifier
from app.core.security import canonicalize_email
from app.models.tenancy import get_utc_now

logger = logging.getLogger(__name__)

EMAIL_REGEX = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")


class ContactEnrichmentService:
    """
    Automated Contact Discovery and Authoritative Verification Pipeline.
    Extracts contact signals from evidence and crawl payloads,
    verifies domain reachability via DNS MX verification,
    and updates ContactRecords with freshness timestamps and verification tiers.
    """

    @classmethod
    def extract_contacts_from_html(cls, html: str) -> List[str]:
        if not html:
            return []
        found = EMAIL_REGEX.findall(html)
        # Filter out common false positives (e.g. image extensions, example assets)
        clean_emails = set()
        ignore_exts = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".js", ".css"}
        for raw in found:
            low = raw.lower()
            if any(low.endswith(ext) for ext in ignore_exts):
                continue
            if len(low) > 100:
                continue
            clean_emails.add(low)
        return list(clean_emails)

    @classmethod
    def enrich_and_verify_contacts(
        cls,
        db: Session,
        workspace_id: str,
        business_id: str,
        extracted_emails: List[str],
        source: str = "CRAWL_DISCOVERY"
    ) -> List[ContactRecord]:
        """
        Processes discovered emails, verifies them against DNS MX hosts,
        and persists them into ContactRecord with authoritative verification tiers.
        """
        biz = db.execute(
            select(MasterBusinessRecord).where(
                MasterBusinessRecord.id == business_id,
                MasterBusinessRecord.workspace_id == workspace_id
            )
        ).scalars().first()

        if not biz:
            return []

        verifier = DNSMXContactVerifier()
        records: List[ContactRecord] = []

        for raw_email in extracted_emails:
            c_email = canonicalize_email(raw_email)
            # Verify MX reachability
            v_res = verifier.verify_email(c_email)

            contact = db.execute(
                select(ContactRecord).where(
                    ContactRecord.workspace_id == workspace_id,
                    ContactRecord.business_id == business_id,
                    ContactRecord.canonical_email == c_email
                )
            ).scalars().first()

            now_dt = get_utc_now()
            meta = {
                "source": source,
                "confidence": v_res.confidence,
                "is_deliverable": v_res.is_deliverable,
                "mx_records": v_res.mx_records,
                "verification_details": v_res.details
            }

            if not contact:
                contact = ContactRecord(
                    workspace_id=workspace_id,
                    business_id=business_id,
                    email=raw_email,
                    canonical_email=c_email,
                    role_title="Discovered Inbound Contact",
                    verification_tier=v_res.verification_tier,
                    freshness_timestamp=now_dt if v_res.is_deliverable else None,
                    verification_metadata=meta
                )
                db.add(contact)
            else:
                # Update tier and freshness
                contact.verification_tier = v_res.verification_tier
                if v_res.is_deliverable:
                    contact.freshness_timestamp = now_dt
                merged_meta = dict(contact.verification_metadata or {})
                merged_meta.update(meta)
                contact.verification_metadata = merged_meta

            db.flush()
            records.append(contact)

        db.commit()
        return records
