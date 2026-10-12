import logging
from typing import Optional, Dict, Any, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.models.tenancy import (
    WorkspaceSuppressionRecord,
    OrgSuppressionRecord,
    ComplianceSuppressionLedger,
    SystemAuditEvent
)
from app.core.security import (
    canonicalize_email,
    hash_suppression_target,
    verify_suppression_match
)
from app.core.config import settings

logger = logging.getLogger(__name__)

class SuppressionService:
    @staticmethod
    def is_suppressed(db: Session, organization_id: str, workspace_id: str, email: str) -> bool:
        """
        Gate 14 Pre-Send Suppression Evaluation:
        Checks:
        1. Workspace operational suppression records
        2. Organization operational suppression records
        3. Compliance suppression ledger (permanent HMAC key-versioned opt-outs)
        """
        canonical = canonicalize_email(email)

        # 1. Check workspace operational suppression
        ws_match = db.execute(
            select(WorkspaceSuppressionRecord).where(
                WorkspaceSuppressionRecord.workspace_id == workspace_id,
                WorkspaceSuppressionRecord.email_canonical == canonical
            )
        ).scalars().first()
        if ws_match:
            return True

        # 2. Check organization operational suppression
        org_match = db.execute(
            select(OrgSuppressionRecord).where(
                OrgSuppressionRecord.organization_id == organization_id,
                OrgSuppressionRecord.email_canonical == canonical
            )
        ).scalars().first()
        if org_match:
            return True

        # 3. Check permanent cryptographic compliance ledger
        active_hash, active_version, _ = hash_suppression_target(canonical, settings.SUPPRESSION_ACTIVE_KEY_VERSION)
        ledger_active_match = db.execute(
            select(ComplianceSuppressionLedger).where(
                ComplianceSuppressionLedger.organization_id == organization_id,
                ComplianceSuppressionLedger.suppression_hash == active_hash,
                ComplianceSuppressionLedger.hash_key_version == active_version
            )
        ).scalars().first()
        if ledger_active_match:
            return True

        # Check historical key version 1 if currently on version 2
        if settings.SUPPRESSION_ACTIVE_KEY_VERSION != 1:
            h1_hash, h1_version, _ = hash_suppression_target(canonical, 1)
            ledger_h1_match = db.execute(
                select(ComplianceSuppressionLedger).where(
                    ComplianceSuppressionLedger.organization_id == organization_id,
                    ComplianceSuppressionLedger.suppression_hash == h1_hash,
                    ComplianceSuppressionLedger.hash_key_version == h1_version
                )
            ).scalars().first()
            if ledger_h1_match:
                return True

        return False

    @classmethod
    def check_suppression_gate_14(
        cls, db: Session, organization_id: str, workspace_id: str, recipient_email: str
    ) -> Tuple[bool, Optional[str]]:
        suppressed = cls.is_suppressed(db, organization_id, workspace_id, recipient_email)
        if suppressed:
            return True, "Suppression active in operational or permanent compliance ledger"
        return False, None

    @classmethod
    def generate_unsubscribe_token(
        cls, db: Session, organization_id: str, workspace_id: str, email: str
    ) -> str:
        from datetime import timedelta, datetime, timezone
        from app.core.security import generate_unsubscribe_token as gen_token
        from app.models.tenancy import UnsubscribeToken

        token_str = gen_token()
        canon_hash, _, _ = hash_suppression_target(canonicalize_email(email))
        token_obj = UnsubscribeToken(
            token=token_str,
            organization_id=organization_id,
            workspace_id=workspace_id,
            email_canonical_hash=canon_hash,
            raw_email_for_delivery=email,
            expires_at=datetime.now(timezone.utc) + timedelta(days=30)
        )
        db.add(token_obj)
        db.commit()
        return token_str

    @staticmethod
    def execute_atomic_suppression(
        db: Session,
        organization_id: str,
        workspace_id: str,
        email: str,
        source_channel: str,
        reason: str,
        actor_id: str = "SYSTEM",
        actor_type: str = "SYSTEM",
        commit: bool = True
    ) -> Dict[str, Any]:
        """
        Authoritative Shared Atomic Suppression Engine:
        Converges both browser confirmation and RFC 8058 one-click flows.
        Atomically writes:
        1. workspace.suppression_records
        2. org_suppression_records
        3. compliance_suppression_ledger (HMAC key-versioned, permanent)
        4. Immutable SystemAuditEvent (zero plaintext email / PII)
        """
        canonical = canonicalize_email(email)
        hash_val, key_ver, algo = hash_suppression_target(canonical)

        # 1. Workspace operational suppression record
        existing_ws = db.execute(
            select(WorkspaceSuppressionRecord).where(
                WorkspaceSuppressionRecord.workspace_id == workspace_id,
                WorkspaceSuppressionRecord.email_canonical == canonical
            )
        ).scalars().first()
        if not existing_ws:
            ws_rec = WorkspaceSuppressionRecord(
                workspace_id=workspace_id,
                email_canonical=canonical,
                reason=reason,
                source_channel=source_channel
            )
            db.add(ws_rec)

        # 2. Organization operational suppression record
        existing_org = db.execute(
            select(OrgSuppressionRecord).where(
                OrgSuppressionRecord.organization_id == organization_id,
                OrgSuppressionRecord.email_canonical == canonical
            )
        ).scalars().first()
        if not existing_org:
            org_rec = OrgSuppressionRecord(
                organization_id=organization_id,
                email_canonical=canonical,
                reason=reason,
                source_channel=source_channel
            )
            db.add(org_rec)

        # 3. Permanent compliance suppression ledger (HMAC key-versioned)
        existing_ledger = db.execute(
            select(ComplianceSuppressionLedger).where(
                ComplianceSuppressionLedger.organization_id == organization_id,
                ComplianceSuppressionLedger.suppression_hash == hash_val,
                ComplianceSuppressionLedger.hash_key_version == key_ver
            )
        ).scalars().first()
        if not existing_ledger:
            ledger_rec = ComplianceSuppressionLedger(
                organization_id=organization_id,
                suppression_hash=hash_val,
                hash_key_version=key_ver,
                algorithm=algo
            )
            db.add(ledger_rec)

        # 4. Immutable Audit Event (ZERO PLAINTEXT PII)
        audit_event = SystemAuditEvent(
            organization_id=organization_id,
            workspace_id=workspace_id,
            event_type="SUPPRESSION_COMMITTED",
            actor_id=actor_id,
            actor_type=actor_type,
            payload={
                "suppression_hash": hash_val,
                "hash_key_version": key_ver,
                "source_channel": source_channel,
                "reason": reason,
                "algorithm": algo
            }
        )
        db.add(audit_event)

        if commit:
            db.commit()

        return {
            "status": "SUPPRESSED",
            "organization_id": organization_id,
            "workspace_id": workspace_id,
            "suppression_hash": hash_val,
            "source_channel": source_channel
        }
