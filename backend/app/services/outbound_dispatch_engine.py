import uuid
from datetime import datetime, timezone, date
from decimal import Decimal
from typing import List, Optional, Tuple, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.core.config import settings
from app.models.tenancy import Workspace, get_utc_now, SystemAuditEvent
from app.models.business import MasterBusinessRecord, ContactRecord
from app.models.campaign import Campaign, CampaignVersion, ApprovedSalesClaimRecord
from app.models.outreach import SenderAccount, SenderDailyMetric, OutboundMessage
from app.services.claim_governance import ClaimGovernanceService
from app.services.scoring_engine import ScoringEngine
from app.services.sender_readiness import SenderReadinessService
from app.services.policy_enforcer import FourTierPolicyEnforcer
from app.services.cost_guardrail import CostGuardrailService
from app.services.jurisdiction_engine import JurisdictionEngine
from app.services.suppression_service import SuppressionService

class OutboundDispatchEngine:
    """
    Authoritative 16-Gate Outbound Enforcement Pipeline.
    Strictly evaluates Gates 1 through 16 in exact frozen architectural sequence.
    Any gate failure immediately halts dispatch and prevents side-effect transmission.
    """

    @classmethod
    def evaluate_all_16_gates(
        cls,
        db: Session,
        workspace_id: str,
        campaign_version_id: str,
        sender_account_id: str,
        recipient_email: str,
        recipient_country: str,
        business_id: Optional[str],
        required_claim_ids: List[str],
        thread_state: str = "AUTONOMOUS",
        projected_daily_burn: Decimal = Decimal("10.00"),
        contact_verification_tier: str = "CONFIRMED_VERIFIED"
    ) -> Tuple[bool, Optional[str], int, Dict[str, Any]]:
        """
        Evaluates Gates 1 to 16.
        Returns: (passed: bool, failure_reason: Optional[str], gates_passed_count: int, gate_details: Dict)
        """
        gate_details = {}

        # Fetch Workspace & Organization
        ws = db.execute(select(Workspace).where(Workspace.id == workspace_id)).scalars().first()
        if not ws:
            return False, "GATE 0: Workspace not found.", 0, {}
        organization_id = ws.organization_id

        # Fetch CampaignVersion
        c_ver = db.execute(
            select(CampaignVersion).where(
                CampaignVersion.id == campaign_version_id,
                CampaignVersion.workspace_id == workspace_id
            )
        ).scalars().first()
        if not c_ver:
            return False, "GATE 1 VIOLATION: CampaignVersion not found.", 0, {}

        # ---------------------------------------------------------------------
        # GATE 1: CampaignVersion is in OPERATOR_APPROVED status
        # ---------------------------------------------------------------------
        if c_ver.status != "OPERATOR_APPROVED":
            return False, f"GATE 1 VIOLATION: CampaignVersion status is '{c_ver.status}', not OPERATOR_APPROVED.", 0, gate_details
        gate_details["gate_1"] = "OPERATOR_APPROVED"

        # ---------------------------------------------------------------------
        # GATE 2: ApprovedSalesClaim exists for all generated assertions
        # GATE 3: Every required ApprovedSalesClaim is OPERATOR_APPROVED
        # GATE 4: Approved claim canonical content hash remains valid and untampered
        # ---------------------------------------------------------------------
        claims_ok, claims_err = ClaimGovernanceService.verify_outbound_claim_gates(
            db, workspace_id, campaign_version_id, required_claim_ids
        )
        if not claims_ok:
            # Determine which gate failed based on prefix
            passed_so_far = 1
            if "GATE 2" in claims_err:
                passed_so_far = 1
            elif "GATE 3" in claims_err:
                passed_so_far = 2
            elif "GATE 4" in claims_err:
                passed_so_far = 3
            return False, claims_err, passed_so_far, gate_details

        gate_details["gate_2"] = "ALL_CLAIMS_EXIST"
        gate_details["gate_3"] = "ALL_CLAIMS_OPERATOR_APPROVED"
        gate_details["gate_4"] = "ALL_CONTENT_HASHES_VALID"

        # ---------------------------------------------------------------------
        # GATE 5: Lead satisfies all outreach eligibility criteria
        # ---------------------------------------------------------------------
        if business_id:
            try:
                score_card = ScoringEngine.evaluate_business(db, workspace_id, business_id)
                if not score_card["outreach_eligible"]:
                    return False, f"GATE 5 VIOLATION: Lead '{business_id}' does not satisfy outreach eligibility (Score: {score_card['opportunity_score']}, Validated: {score_card['validated_hypotheses_count']}).", 4, gate_details
                gate_details["gate_5"] = f"ELIGIBLE (Score: {score_card['opportunity_score']})"
            except Exception as e:
                return False, f"GATE 5 VIOLATION: Could not verify lead eligibility: {str(e)}", 4, gate_details
        else:
            gate_details["gate_5"] = "PASSED_MANUAL_LEAD"

        # ---------------------------------------------------------------------
        # GATE 6: Contact verification tier is fresh and permitted (Authoritative DB Check)
        # ---------------------------------------------------------------------
        from app.core.security import canonicalize_email
        c_email = canonicalize_email(recipient_email)
        contact_stmt = select(ContactRecord).where(
            ContactRecord.workspace_id == workspace_id,
            ContactRecord.canonical_email == c_email
        )
        if business_id:
            contact_stmt = contact_stmt.where(ContactRecord.business_id == business_id)
        
        contact = db.execute(contact_stmt).scalars().first()
        if not contact:
            return False, f"GATE 6 VIOLATION: No ContactRecord found in database for recipient '{recipient_email}' in workspace '{workspace_id}'. Autonomous outreach requires a persisted, verified contact.", 5, gate_details

        permitted_tiers = ["CONFIRMED_VERIFIED", "PROVIDER_VERIFIED"]
        if contact.verification_tier not in permitted_tiers:
            return False, f"GATE 6 VIOLATION: Contact verification tier '{contact.verification_tier}' is not permitted for autonomous outreach (requires CONFIRMED_VERIFIED or PROVIDER_VERIFIED).", 5, gate_details

        # Check verification freshness (max 90 days)
        if contact.freshness_timestamp:
            now_dt = get_utc_now()
            ts = contact.freshness_timestamp
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if (now_dt - ts).days > 90:
                return False, f"GATE 6 VIOLATION: Contact verification has expired (> 90 days stale, timestamp={contact.freshness_timestamp.isoformat()}). Re-verification required.", 5, gate_details

        gate_details["gate_6"] = f"DB_VERIFIED ({contact.verification_tier})"

        # ---------------------------------------------------------------------
        # GATE 7: Assigned SenderAccount is READY_FOR_OUTREACH
        # ---------------------------------------------------------------------
        sender = db.execute(
            select(SenderAccount).where(
                SenderAccount.id == sender_account_id,
                SenderAccount.workspace_id == workspace_id
            )
        ).scalars().first()
        s_ok, s_err = SenderReadinessService.verify_gate_7(sender)
        if not s_ok:
            return False, s_err, 6, gate_details
        gate_details["gate_7"] = "READY_FOR_OUTREACH"

        # ---------------------------------------------------------------------
        # GATES 8, 9, 10, 11: Four-Tier Policy Enforcement
        # ---------------------------------------------------------------------
        policy_ok, policy_err, policy_det = FourTierPolicyEnforcer.evaluate_gates_8_to_11(
            db, organization_id, workspace_id, c_ver.campaign_id, sender_account_id
        )
        if not policy_ok:
            passed_so_far = 7
            if "GATE 8" in policy_err:
                passed_so_far = 7
            elif "GATE 9" in policy_err:
                passed_so_far = 8
            elif "GATE 10" in policy_err:
                passed_so_far = 9
            elif "GATE 11" in policy_err:
                passed_so_far = 10
            return False, policy_err, passed_so_far, gate_details

        gate_details["gate_8"] = "ORG_POLICY_ALLOWED"
        gate_details["gate_9"] = "WORKSPACE_POLICY_ALLOWED"
        gate_details["gate_10"] = "CAMPAIGN_POLICY_ALLOWED"
        gate_details["gate_11"] = f"SENDER_POLICY_ALLOWED (Remaining: {policy_det.get('most_restrictive_remaining')})"

        # ---------------------------------------------------------------------
        # CAPACITY POLICY INTEGRATION: Workspace-scoped daily outreach capacity
        # ---------------------------------------------------------------------
        from app.services.capacity_service import CapacityService
        cap_decision = CapacityService.check_capacity(
            db=db,
            workspace_id=workspace_id,
            operation_type="OUTREACH",
            requested_units=1,
            is_queueable=True
        )
        if not cap_decision.is_allowed:
            return False, f"CAPACITY_POLICY_VIOLATION: {cap_decision.reason}", 10, gate_details
        gate_details["capacity_policy"] = f"ALLOWED (Remaining: {cap_decision.remaining_capacity})"

        # ---------------------------------------------------------------------
        # GATE 12: JurisdictionPolicy compliance decision evaluates to ALLOWED
        # ---------------------------------------------------------------------
        j_ok, j_err, j_det = JurisdictionEngine.evaluate_gate_12(db, recipient_country)
        if not j_ok:
            return False, j_err, 11, gate_details
        gate_details["gate_12"] = f"ALLOWED ({j_det.get('lawful_basis')})"

        # ---------------------------------------------------------------------
        # GATE 13: Cost/budget guardrails and historical burn-rate rules permit sending
        # ---------------------------------------------------------------------
        c_ok, c_err, c_det = CostGuardrailService.evaluate_gate_13(
            db, workspace_id, c_ver.campaign_id, projected_daily_burn
        )
        if not c_ok:
            return False, c_err, 12, gate_details
        gate_details["gate_13"] = f"BUDGET_APPROVED ({c_det.get('status')})"

        # ---------------------------------------------------------------------
        # GATE 14: Atomic suppression gate passes immediately before send
        # ---------------------------------------------------------------------
        is_suppressed, supp_reason = SuppressionService.check_suppression_gate_14(
            db=db,
            organization_id=organization_id,
            workspace_id=workspace_id,
            recipient_email=recipient_email
        )
        if is_suppressed:
            return False, f"GATE 14 VIOLATION: Recipient '{recipient_email}' is suppressed: {supp_reason}.", 13, gate_details
        gate_details["gate_14"] = "NOT_SUPPRESSED"

        # ---------------------------------------------------------------------
        # GATE 15: Global emergency kill switch is OFF
        # ---------------------------------------------------------------------
        if settings.GLOBAL_EMERGENCY_KILL_SWITCH:
            return False, "GATE 15 VIOLATION: Global emergency kill switch is ON. All outbound dispatches halted.", 14, gate_details
        gate_details["gate_15"] = "KILL_SWITCH_OFF"

        # ---------------------------------------------------------------------
        # GATE 16: Thread is NOT in HUMAN_REVIEW_REQUIRED or HUMAN_TAKEOVER
        # ---------------------------------------------------------------------
        if thread_state in ["HUMAN_REVIEW_REQUIRED", "HUMAN_TAKEOVER"]:
            return False, f"GATE 16 VIOLATION: Thread is in state '{thread_state}'. Autonomous outbound transmission prohibited.", 15, gate_details
        gate_details["gate_16"] = "THREAD_AUTONOMOUS"

        return True, None, 16, gate_details

    @classmethod
    def dispatch_outbound_message(
        cls,
        db: Session,
        workspace_id: str,
        campaign_version_id: str,
        sender_account_id: str,
        recipient_email: str,
        recipient_country: str,
        business_id: Optional[str],
        subject: str,
        body_text: str,
        body_html: Optional[str] = None,
        required_claim_ids: List[str] = None,
        side_effect_idempotency_key: str = "",
        thread_state: str = "AUTONOMOUS",
        projected_daily_burn: Decimal = Decimal("10.00"),
        contact_verification_tier: str = "CONFIRMED_VERIFIED"
    ) -> OutboundMessage:
        """
        Executes outbound dispatch transaction.
        Enforces idempotency check prior to side-effect execution.
        Evaluates all 16 gates in authoritative order.
        """
        # Idempotency check
        existing = db.execute(
            select(OutboundMessage).where(
                OutboundMessage.side_effect_idempotency_key == side_effect_idempotency_key
            )
        ).scalars().first()
        if existing:
            # 1. Recovery invariant: If the message was already transmitted over the wire,
            # ensure that capacity accounting was not lost due to an earlier crash/failure.
            if existing.status == "TRANSMITTED":
                from app.services.capacity_service import CapacityService
                CapacityService.record_transmitted_usage_if_unaccounted(db, existing)
                return existing

            # 2. Terminal states: Do not re-evaluate suppressed or blocked messages
            if existing.status in ["SUPPRESSED", "BLOCKED"]:
                return existing

            # 3. Backpressure resumption: If the message was held in QUEUED_BACKPRESSURE,
            # re-evaluate through authoritative gates in case capacity has become available.
            if existing.status == "QUEUED_BACKPRESSURE":
                return cls.resume_backpressure_message(
                    db=db,
                    message_id=existing.id,
                    projected_daily_burn=projected_daily_burn,
                    contact_verification_tier=contact_verification_tier
                )

            return existing

        msg = OutboundMessage(
            workspace_id=workspace_id,
            campaign_version_id=campaign_version_id,
            sender_account_id=sender_account_id,
            recipient_email=recipient_email.lower().strip(),
            recipient_country=recipient_country,
            business_id=business_id,
            subject=subject,
            body_text=body_text,
            body_html=body_html,
            side_effect_idempotency_key=side_effect_idempotency_key,
            thread_state=thread_state,
            status="QUEUED"
        )
        db.add(msg)
        db.commit()
        db.refresh(msg)

        passed, reason, gates_passed_count, details = cls.evaluate_all_16_gates(
            db=db,
            workspace_id=workspace_id,
            campaign_version_id=campaign_version_id,
            sender_account_id=sender_account_id,
            recipient_email=recipient_email,
            recipient_country=recipient_country,
            business_id=business_id,
            required_claim_ids=required_claim_ids,
            thread_state=thread_state,
            projected_daily_burn=projected_daily_burn,
            contact_verification_tier=contact_verification_tier
        )

        msg.gate_passed_count = gates_passed_count

        if not passed:
            msg.gate_failure_reason = reason
            if gates_passed_count == 13 and "GATE 14" in reason:
                # Gate 14 suppression match -> terminal SUPPRESSED state
                msg.status = "SUPPRESSED"
            elif "CAPACITY_POLICY_VIOLATION" in reason:
                msg.status = "QUEUED_BACKPRESSURE"
            else:
                msg.status = "BLOCKED"
            db.commit()
            db.refresh(msg)
            return msg

        # All 16 Gates Passed!
        msg.status = "GATES_PASSED"
        db.commit()

        # Gate 14 Concurrency Safety: Atomic Re-Check Immediately Prior to Side-Effect Transmission
        ws = db.execute(select(Workspace).where(Workspace.id == workspace_id)).scalars().first()
        is_suppressed, supp_reason = SuppressionService.check_suppression_gate_14(
            db=db,
            organization_id=ws.organization_id,
            workspace_id=workspace_id,
            recipient_email=recipient_email
        )
        if is_suppressed:
            msg.status = "SUPPRESSED"
            msg.gate_passed_count = 13
            msg.gate_failure_reason = f"GATE 14 VIOLATION (Pre-Transmission Race Check): Recipient '{recipient_email}' was suppressed prior to wire transmission: {supp_reason}."
            db.commit()
            db.refresh(msg)
            return msg

        return cls._execute_wire_transmission(db, msg, ws.organization_id)

    @classmethod
    def _execute_wire_transmission(
        cls,
        db: Session,
        msg: OutboundMessage,
        organization_id: str
    ) -> OutboundMessage:
        """
        Executes wire transmission with real/mock provider enforcement,
        captures provider Message-ID, updates sender metrics, and guarantees
        atomic durable capacity accounting with recovery flags.
        """
        workspace_id = msg.workspace_id
        sender_account_id = msg.sender_account_id
        recipient_email = msg.recipient_email
        campaign_version_id = msg.campaign_version_id
        side_effect_idempotency_key = msg.side_effect_idempotency_key

        # Side-Effect Execution: Generate RFC 8058 One-Click token and headers
        token = SuppressionService.generate_unsubscribe_token(
            db=db,
            organization_id=organization_id,
            workspace_id=workspace_id,
            email=recipient_email
        )

        # Inject footer links
        unsub_url = f"https://outreach.test/api/v1/unsubscribe?token={token}"
        msg.body_text = f"{msg.body_text}\n\nUnsubscribe: {unsub_url}"
        if msg.body_html:
            msg.body_html = f"{msg.body_html}<br><br><p style='font-size:12px;'><a href='{unsub_url}'>Unsubscribe</a></p>"

        # Fetch sender account and resolve real provider transport
        sender = db.execute(
            select(SenderAccount).where(SenderAccount.id == sender_account_id)
        ).scalars().first()
        if not sender:
            msg.status = "BLOCKED"
            msg.gate_failure_reason = "Sender account no longer exists at transmission time."
            db.commit()
            db.refresh(msg)
            return msg

        # Explicit Credential-Source Resolution Model
        import json
        from app.core.security import decrypt_credential
        from app.services.email_transports.factory import get_email_transport

        credentials = {}
        raw_cred = (sender.encrypted_credentials or "").strip()
        provider_type = (sender.provider_type or "SMTP").upper()
        is_production = getattr(settings, "ENVIRONMENT", "development") == "production"

        # 1. Enforce production safety invariant for mock/sandbox provider types
        if provider_type in ["MOCK", "SANDBOX"]:
            if is_production:
                msg.status = "BLOCKED"
                msg.gate_failure_reason = "REAL_TRANSPORT_NOT_CONFIGURED: Mock/sandbox transport provider is strictly forbidden in production environment."
                db.commit()
                db.refresh(msg)
                return msg

        # 2. Resolve credentials according to explicit credential-source model
        if raw_cred.startswith("v1:"):
            dec = decrypt_credential(raw_cred)
            try:
                credentials = json.loads(dec)
            except Exception:
                credentials = {"api_key": dec, "smtp_password": dec}
        elif raw_cred in ["env_managed", "ENV_MANAGED", "env", "environment", ""]:
            credentials = {}
        else:
            if is_production:
                msg.status = "BLOCKED"
                msg.gate_failure_reason = "SECURITY_VIOLATION: In production, sender credentials must be envelope-encrypted (v1:) or 'env_managed'. Arbitrary unencrypted credentials are forbidden."
                db.commit()
                db.refresh(msg)
                return msg
            else:
                credentials = {"api_key": raw_cred, "smtp_password": raw_cred}

        # 3. Handle SMTP specific configuration and environment loading
        if provider_type == "SMTP":
            if "smtp_host" not in credentials and settings.SMTP_HOST:
                credentials["smtp_host"] = settings.SMTP_HOST
            if "smtp_port" not in credentials and settings.SMTP_PORT:
                credentials["smtp_port"] = settings.SMTP_PORT
            if "smtp_user" not in credentials and settings.SMTP_USERNAME:
                credentials["smtp_user"] = settings.SMTP_USERNAME
            if "smtp_password" not in credentials and settings.SMTP_PASSWORD:
                credentials["smtp_password"] = settings.SMTP_PASSWORD
            if "use_tls" not in credentials and hasattr(settings, "SMTP_USE_TLS"):
                credentials["use_tls"] = settings.SMTP_USE_TLS

            if credentials.get("is_sandbox") is True:
                if is_production:
                    msg.status = "BLOCKED"
                    msg.gate_failure_reason = "REAL_TRANSPORT_NOT_CONFIGURED: Sandbox mode is strictly forbidden in production environment."
                    db.commit()
                    db.refresh(msg)
                    return msg
            else:
                credentials["is_sandbox"] = False

            if not credentials.get("is_sandbox"):
                smtp_pwd = credentials.get("smtp_password")
                smtp_hst = credentials.get("smtp_host")
                if not smtp_pwd or not smtp_hst:
                    msg.status = "BLOCKED"
                    msg.gate_failure_reason = "REAL_TRANSPORT_NOT_CONFIGURED: Missing SMTP host or password for real provider transmission."
                    db.commit()
                    db.refresh(msg)
                    return msg

        try:
            transport = get_email_transport(sender.provider_type)
        except ValueError as val_err:
            msg.status = "BLOCKED"
            msg.gate_failure_reason = f"REAL_TRANSPORT_NOT_CONFIGURED: {str(val_err)}"
            db.commit()
            db.refresh(msg)
            return msg

        rfc_headers = {
            "List-Unsubscribe": f"<{unsub_url}&action=one-click>, <mailto:unsubscribe@outreach.test?subject=unsub:{token}>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
            "X-Workspace-ID": workspace_id,
            "X-Campaign-Version": campaign_version_id
        }

        delivery_result = transport.send_email(
            from_email=sender.email,
            from_name=sender.display_name,
            to_email=recipient_email,
            subject=msg.subject,
            body_text=msg.body_text,
            body_html=msg.body_html,
            headers=rfc_headers,
            credentials=credentials,
            idempotency_key=side_effect_idempotency_key
        )

        msg.transport_mode = delivery_result.transport_mode

        if is_production and delivery_result.transport_mode != "REAL":
            msg.status = "BLOCKED"
            msg.gate_failure_reason = "SECURITY_VIOLATION: Mock or simulated delivery cannot mark message as TRANSMITTED in production environment."
            db.commit()
            db.refresh(msg)
            return msg

        if not delivery_result.success:
            msg.status = "DELIVERY_FAILED" if not delivery_result.retryable else "DELIVERY_STATE_UNKNOWN"
            msg.gate_failure_reason = f"Provider Delivery Error: {delivery_result.error_message}"
            db.commit()
            db.refresh(msg)
            return msg

        if not delivery_result.provider_message_id or not delivery_result.provider_message_id.strip():
            msg.status = "DELIVERY_FAILED"
            msg.gate_failure_reason = "Provider Delivery Error: Missing or empty provider message ID returned by transport."
            db.commit()
            db.refresh(msg)
            return msg

        msg.provider_message_id = delivery_result.provider_message_id
        msg.status = "TRANSMITTED"
        msg.sent_at = get_utc_now()

        # Increment sender daily metric
        metric = db.execute(
            select(SenderDailyMetric).where(
                SenderDailyMetric.sender_account_id == sender.id,
                SenderDailyMetric.metric_date == date.today()
            )
        ).scalars().first()
        if not metric:
            metric = SenderDailyMetric(
                workspace_id=workspace_id,
                sender_account_id=sender.id,
                metric_date=date.today(),
                sent_count=1
            )
            db.add(metric)
        else:
            metric.sent_count += 1

        # Atomically increment workspace durable capacity counter
        from app.services.capacity_service import CapacityService
        CapacityService.increment_usage_atomic(
            db=db,
            workspace_id=workspace_id,
            operation_type="OUTREACH",
            units=1,
            force_post_transmission=True
        )
        msg.capacity_accounted = True

        db.commit()
        db.refresh(msg)
        return msg

    @classmethod
    def resume_backpressure_message(
        cls,
        db: Session,
        message_id: str,
        projected_daily_burn: Decimal = Decimal("10.00"),
        contact_verification_tier: str = "CONFIRMED_VERIFIED"
    ) -> OutboundMessage:
        """
        Re-evaluates a QUEUED_BACKPRESSURE message when capacity may have become available.
        Strictly enforces all 16 authoritative gates (including Gate 14 suppression and Gate 15 kill switch).
        """
        msg = db.execute(
            select(OutboundMessage).where(OutboundMessage.id == message_id)
        ).scalars().first()
        if not msg:
            raise ValueError(f"Message '{message_id}' not found.")

        if msg.status != "QUEUED_BACKPRESSURE":
            return msg

        # Re-evaluate all 16 gates from scratch
        passed, reason, gates_passed_count, details = cls.evaluate_all_16_gates(
            db=db,
            workspace_id=msg.workspace_id,
            campaign_version_id=msg.campaign_version_id,
            sender_account_id=msg.sender_account_id,
            recipient_email=msg.recipient_email,
            recipient_country=msg.recipient_country or "US",
            business_id=msg.business_id,
            required_claim_ids=[],
            thread_state=msg.thread_state,
            projected_daily_burn=projected_daily_burn,
            contact_verification_tier=contact_verification_tier
        )

        msg.gate_passed_count = gates_passed_count

        if not passed:
            msg.gate_failure_reason = reason
            if gates_passed_count == 13 and "GATE 14" in reason:
                msg.status = "SUPPRESSED"
            elif "CAPACITY_POLICY_VIOLATION" in reason:
                msg.status = "QUEUED_BACKPRESSURE"
            else:
                msg.status = "BLOCKED"
            db.commit()
            db.refresh(msg)
            return msg

        # All 16 Gates Passed!
        msg.status = "GATES_PASSED"
        db.commit()

        # Gate 14 Concurrency Safety: Atomic Re-Check Immediately Prior to Side-Effect Transmission
        ws = db.execute(select(Workspace).where(Workspace.id == msg.workspace_id)).scalars().first()
        is_suppressed, supp_reason = SuppressionService.check_suppression_gate_14(
            db=db,
            organization_id=ws.organization_id,
            workspace_id=msg.workspace_id,
            recipient_email=msg.recipient_email
        )
        if is_suppressed:
            msg.status = "SUPPRESSED"
            msg.gate_passed_count = 13
            msg.gate_failure_reason = f"GATE 14 VIOLATION (Pre-Transmission Race Check): Recipient '{msg.recipient_email}' was suppressed prior to wire transmission: {supp_reason}."
            db.commit()
            db.refresh(msg)
            return msg

        # Transmit via provider wire
        return cls._execute_wire_transmission(db, msg, ws.organization_id)

    @classmethod
    def process_backpressure_queue(
        cls,
        db: Session,
        workspace_id: str,
        limit: int = 50
    ) -> Dict[str, Any]:
        """
        Drains QUEUED_BACKPRESSURE messages in FIFO order when capacity becomes available.
        Checks capacity before processing each message. If exhausted, halts cleanly without busy-looping.
        Guarantees that every message passes all 16 gates, Gate 14 suppression, and Gate 15 kill switch.
        """
        from app.services.capacity_service import CapacityService

        # Check current capacity before processing
        cap = CapacityService.check_capacity(db, workspace_id, "OUTREACH")
        if not cap.is_allowed:
            return {
                "processed": 0,
                "transmitted": 0,
                "still_queued": 0,
                "reason": f"No capacity available ({cap.reason})"
            }

        pending = db.execute(
            select(OutboundMessage).where(
                OutboundMessage.workspace_id == workspace_id,
                OutboundMessage.status == "QUEUED_BACKPRESSURE"
            ).order_by(OutboundMessage.created_at.asc()).limit(limit)
        ).scalars().all()

        processed = 0
        transmitted = 0

        for msg in pending:
            # Check capacity before each dispatch to avoid exceeding limits
            cap = CapacityService.check_capacity(db, workspace_id, "OUTREACH")
            if not cap.is_allowed:
                break

            res = cls.resume_backpressure_message(db, msg.id)
            processed += 1
            if res.status == "TRANSMITTED":
                transmitted += 1

        remaining_queued = db.execute(
            select(OutboundMessage).where(
                OutboundMessage.workspace_id == workspace_id,
                OutboundMessage.status == "QUEUED_BACKPRESSURE"
            )
        ).scalars().all()

        return {
            "processed": processed,
            "transmitted": transmitted,
            "still_queued": len(remaining_queued)
        }
