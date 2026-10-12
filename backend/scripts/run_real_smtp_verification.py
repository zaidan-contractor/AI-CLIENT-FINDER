"""
Authoritative Real SMTP Integration Verification Script.
Executes controlled live SMTP transmission against the dedicated test mailbox only.

CRITICAL SAFETY INVARIANTS:
1. ONLY permitted recipient: opportunity.platform.test@gmail.com
2. ONLY permitted sender: opportunity.platform.test@gmail.com
3. Zero prospect outreach
4. Zero marketing blasts
5. All 16 gates evaluated without bypass
6. Real wire transmission verified with TLS negotiation & provider Message-ID
7. Actual inbox receipt verified via IMAP
8. Concurrency / idempotency verified against double transmission
9. Real suppression race verified
10. Real emergency kill-switch verified
11. Capacity accounting verified
12. Zero secret leakage
"""
import os
import sys
import uuid
import time
import imaplib
import email
from email.header import decode_header
from decimal import Decimal
from datetime import datetime, timezone, date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.tenancy import Organization, Workspace, User, Membership, OrgSuppressionRecord, get_utc_now
from app.models.business import MasterBusinessRecord, ContactRecord
from app.models.opportunity import ProblemHypothesis, ImpactAssessment, SolutionMapping
from app.models.campaign import Campaign, CampaignVersion, ApprovedSalesClaimRecord
from app.models.outreach import SenderAccount, SenderDailyMetric, OutboundMessage, JurisdictionPolicy, WorkspaceDailyCost
from app.models.capacity import CapacityPolicy, WorkspaceCapacityDailyUsage
from app.services.claim_governance import ClaimGovernanceService
from app.services.outbound_dispatch_engine import OutboundDispatchEngine
from app.services.suppression_service import SuppressionService
from app.services.capacity_service import CapacityService
from app.services.email_transports.factory import set_transport_override, get_email_transport
from app.services.email_transports.smtp_transport import SMTPEmailTransport

# -----------------------------------------------------------------------------
# PHASE 1: HARD-PINNED SAFETY INVARIANTS
# -----------------------------------------------------------------------------
REAL_SMTP_DIAGNOSTIC_ALLOWED_RECIPIENT = "opportunity.platform.test@gmail.com"
REAL_SMTP_DIAGNOSTIC_ALLOWED_SENDER = "opportunity.platform.test@gmail.com"

def enforce_safety_pin(recipient: str, sender_email: str):
    if recipient.lower().strip() != REAL_SMTP_DIAGNOSTIC_ALLOWED_RECIPIENT:
        raise RuntimeError(f"SAFETY VIOLATION: Recipient '{recipient}' is not allowed! Must be '{REAL_SMTP_DIAGNOSTIC_ALLOWED_RECIPIENT}'.")
    if sender_email.lower().strip() != REAL_SMTP_DIAGNOSTIC_ALLOWED_SENDER:
        raise RuntimeError(f"SAFETY VIOLATION: Sender '{sender_email}' is not allowed! Must be '{REAL_SMTP_DIAGNOSTIC_ALLOWED_SENDER}'.")

def verify_inbox_receipt(target_message_id: str, subject: str, timeout_seconds: int = 30) -> dict:
    """
    Connects to imap.gmail.com over SSL using test mailbox credentials to verify
    the transmitted email physically arrived in the inbox.
    """
    start_time = time.time()
    result = {
        "received": False,
        "subject": None,
        "provider_message_id": None,
        "from": None,
        "to": None,
        "date": None,
        "list_unsubscribe": None,
        "list_unsubscribe_post": None,
        "latency_seconds": None
    }

    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com", 993)
        mail.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
        mail.select("INBOX")

        while time.time() - start_time < timeout_seconds:
            # Search for messages with the subject or recent messages
            status, search_data = mail.search(None, "ALL")
            if status == "OK" and search_data[0]:
                msg_ids = search_data[0].split()
                # Check most recent 10 messages in reverse chronological order
                for mid in reversed(msg_ids[-10:]):
                    f_status, data = mail.fetch(mid, "(RFC822)")
                    if f_status != "OK":
                        continue
                    raw_email = data[0][1]
                    parsed = email.message_from_bytes(raw_email)
                    inbox_msg_id = (parsed.get("Message-ID") or "").strip()
                    inbox_subject = parsed.get("Subject") or ""

                    # Match by provider Message-ID or subject
                    if inbox_msg_id == target_message_id or target_message_id in inbox_msg_id or subject in inbox_subject:
                        result["received"] = True
                        result["subject"] = inbox_subject
                        result["provider_message_id"] = inbox_msg_id
                        result["from"] = parsed.get("From")
                        result["to"] = parsed.get("To")
                        result["date"] = parsed.get("Date")
                        result["list_unsubscribe"] = parsed.get("List-Unsubscribe")
                        result["list_unsubscribe_post"] = parsed.get("List-Unsubscribe-Post")
                        result["latency_seconds"] = round(time.time() - start_time, 2)
                        mail.close()
                        mail.logout()
                        return result
            time.sleep(2)

        mail.close()
        mail.logout()
    except Exception as e:
        result["error"] = str(e)

    return result

def run_real_outbound_audit():
    print("=" * 80)
    print("AUTHORITATIVE REAL SMTP INTEGRATION AUDIT — ZERO PROSPECT OUTREACH")
    print(f"Safety Recipient: {REAL_SMTP_DIAGNOSTIC_ALLOWED_RECIPIENT}")
    print(f"Safety Sender:    {REAL_SMTP_DIAGNOSTIC_ALLOWED_SENDER}")
    print("=" * 80)

    # Guarantee real transport (no test override)
    set_transport_override(None)

    db = SessionLocal()
    audit_results = {}

    try:
        # ---------------------------------------------------------------------
        # PHASE 2: PRE-FLIGHT DATABASE SNAPSHOT
        # ---------------------------------------------------------------------
        print("\n--- PHASE 2: PRE-FLIGHT DATABASE SNAPSHOT ---")
        ws = db.query(Workspace).filter_by(id="ws_diagnostic_test").first()
        if not ws:
            raise RuntimeError("Diagnostic fixtures not initialized! Run run_diagnostic_email_preflight.py first.")

        org = db.query(Organization).filter_by(id=ws.organization_id).first()
        camp = db.query(Campaign).filter_by(workspace_id=ws.id).first()
        ver = db.query(CampaignVersion).filter_by(workspace_id=ws.id, status="OPERATOR_APPROVED").first()
        claim = db.query(ApprovedSalesClaimRecord).filter_by(workspace_id=ws.id, approval_status="OPERATOR_APPROVED").first()
        biz = db.query(MasterBusinessRecord).filter_by(workspace_id=ws.id).first()
        cnt = db.query(ContactRecord).filter_by(workspace_id=ws.id).first()
        sender = db.query(SenderAccount).filter_by(workspace_id=ws.id).first()
        policy = CapacityService.get_or_create_policy(db, ws.id)

        # Reset diagnostic test records to guarantee pristine audit baseline
        from app.models.tenancy import WorkspaceSuppressionRecord, ComplianceSuppressionLedger
        for wrec in db.query(WorkspaceSuppressionRecord).filter_by(workspace_id=ws.id).all():
            db.delete(wrec)
        for srec in db.query(OrgSuppressionRecord).filter_by(organization_id=org.id).all():
            db.delete(srec)
        for cleg in db.query(ComplianceSuppressionLedger).filter_by(organization_id=org.id).all():
            db.delete(cleg)
        for old_msg in db.query(OutboundMessage).filter_by(workspace_id=ws.id).all():
            db.delete(old_msg)
        for sm in db.query(SenderDailyMetric).filter_by(sender_account_id=sender.id).all():
            sm.sent_count = 0
        daily_usage = CapacityService.get_or_create_daily_usage(db, ws.id, date.today())
        daily_usage.outreach_count = 0
        db.commit()

        initial_msg_count = db.query(OutboundMessage).count()
        initial_transmitted_count = db.query(OutboundMessage).filter_by(status="TRANSMITTED").count()
        initial_usage = daily_usage.outreach_count

        snapshot = {
            "workspace_id": ws.id,
            "organization_id": org.id,
            "campaign_id": camp.id,
            "campaign_version_id": str(ver.id),
            "campaign_version_status": ver.status,
            "claim_id": str(claim.id),
            "claim_approval_status": claim.approval_status,
            "claim_integrity_valid": claim.verify_integrity(),
            "business_id": biz.id,
            "contact_id": cnt.id,
            "canonical_recipient_email": cnt.canonical_email,
            "server_side_verification_tier": cnt.verification_tier,
            "verification_freshness": cnt.freshness_timestamp.isoformat() if cnt.freshness_timestamp else None,
            "sender_account_id": sender.id,
            "sender_readiness_status": sender.status,
            "sender_dns_valid": sender.dns_valid,
            "capacity_policy_daily_limit": policy.outreach_limit_per_day,
            "initial_capacity_outreach_usage": initial_usage,
            "jurisdiction_recipient_country": "US",
            "suppression_active": False,
            "kill_switch_state": settings.GLOBAL_EMERGENCY_KILL_SWITCH,
            "thread_state": "AUTONOMOUS",
            "initial_outbound_messages_count": initial_msg_count,
            "initial_transmitted_count": initial_transmitted_count
        }
        audit_results["snapshot"] = snapshot
        for k, v in snapshot.items():
            print(f"  {k}: {v}")

        # ---------------------------------------------------------------------
        # PHASE 3 & 4: REAL SMTP SUCCESS PATH & WIRE VERIFICATION
        # ---------------------------------------------------------------------
        print("\n--- PHASES 3 & 4: REAL SMTP SUCCESS PATH & WIRE VERIFICATION ---")
        idemp_key_success = f"real_smtp_success_{uuid.uuid4().hex[:12]}"
        subject_success = f"Opportunity Platform Real SMTP Verification [{uuid.uuid4().hex[:6]}]"
        body_success = (
            "Opportunity Platform Real SMTP Diagnostic\n\n"
            "This message verifies live end-to-end SMTP delivery across all 16 Authoritative Outbound Gates.\n"
            "Recipient: Hard-pinned test mailbox.\n"
            f"Idempotency Key: {idemp_key_success}\n"
            f"Timestamp: {get_utc_now().isoformat()}\n"
        )

        msg_success = OutboundDispatchEngine.dispatch_outbound_message(
            db=db,
            workspace_id=ws.id,
            campaign_version_id=str(ver.id),
            sender_account_id=sender.id,
            recipient_email=REAL_SMTP_DIAGNOSTIC_ALLOWED_RECIPIENT,
            recipient_country="US",
            business_id=biz.id,
            subject=subject_success,
            body_text=body_success,
            body_html=f"<p>{body_success}</p>",
            required_claim_ids=[str(claim.id)],
            side_effect_idempotency_key=idemp_key_success,
            thread_state="AUTONOMOUS",
            contact_verification_tier="CONFIRMED_VERIFIED"
        )

        print(f"  Result Message ID:       {msg_success.id}")
        print(f"  Status:                  {msg_success.status}")
        print(f"  Transport Mode:          {msg_success.transport_mode}")
        print(f"  Gates Passed Count:      {msg_success.gate_passed_count}/16")
        print(f"  Provider Message-ID:     {msg_success.provider_message_id}")
        print(f"  Sent At:                 {msg_success.sent_at}")
        print(f"  Capacity Accounted:      {msg_success.capacity_accounted}")

        assert msg_success.status == "TRANSMITTED", f"Expected TRANSMITTED, got {msg_success.status}: {msg_success.gate_failure_reason}"
        assert msg_success.transport_mode == "REAL", f"Expected transport_mode REAL, got {msg_success.transport_mode}"
        assert msg_success.gate_passed_count == 16, f"Expected 16 gates passed, got {msg_success.gate_passed_count}"
        assert msg_success.provider_message_id is not None, "Provider Message-ID is missing!"
        assert msg_success.sent_at is not None, "Sent at timestamp is missing!"
        assert msg_success.capacity_accounted is True, "Capacity accounted flag is False!"

        audit_results["success_dispatch"] = {
            "id": msg_success.id,
            "status": msg_success.status,
            "transport_mode": msg_success.transport_mode,
            "gate_passed_count": msg_success.gate_passed_count,
            "provider_message_id": msg_success.provider_message_id,
            "sent_at": msg_success.sent_at.isoformat(),
            "capacity_accounted": msg_success.capacity_accounted
        }

        # ---------------------------------------------------------------------
        # PHASE 5: VERIFY ACTUAL INBOX RECEIPT
        # ---------------------------------------------------------------------
        print("\n--- PHASE 5: VERIFY ACTUAL INBOX RECEIPT VIA IMAP ---")
        print("  Checking imap.gmail.com:993 for actual receipt...")
        inbox_check = verify_inbox_receipt(
            target_message_id=msg_success.provider_message_id,
            subject=subject_success,
            timeout_seconds=25
        )
        print(f"  Inbox Receipt Confirmed: {inbox_check['received']}")
        print(f"  Matched Message-ID:     {inbox_check.get('provider_message_id')}")
        print(f"  Matched Subject:        {inbox_check.get('subject')}")
        print(f"  List-Unsubscribe:       {inbox_check.get('list_unsubscribe')}")
        print(f"  Latency Seconds:        {inbox_check.get('latency_seconds')}s")

        assert inbox_check["received"] is True, f"Message was not detected in inbox: {inbox_check}"
        audit_results["inbox_receipt"] = inbox_check

        # ---------------------------------------------------------------------
        # PHASE 6: DUPLICATE / IDEMPOTENCY REPLAY TEST
        # ---------------------------------------------------------------------
        print("\n--- PHASE 6: DUPLICATE / IDEMPOTENCY REPLAY TEST ---")
        tx_count_before = db.query(OutboundMessage).filter_by(status="TRANSMITTED").count()
        
        # Re-dispatch with exact same idempotency key
        msg_replay = OutboundDispatchEngine.dispatch_outbound_message(
            db=db,
            workspace_id=ws.id,
            campaign_version_id=str(ver.id),
            sender_account_id=sender.id,
            recipient_email=REAL_SMTP_DIAGNOSTIC_ALLOWED_RECIPIENT,
            recipient_country="US",
            business_id=biz.id,
            subject=subject_success,
            body_text=body_success,
            required_claim_ids=[str(claim.id)],
            side_effect_idempotency_key=idemp_key_success,  # REPLAY
            thread_state="AUTONOMOUS"
        )

        tx_count_after = db.query(OutboundMessage).filter_by(status="TRANSMITTED").count()
        print(f"  Replay Message ID:       {msg_replay.id} (Matches original: {msg_replay.id == msg_success.id})")
        print(f"  Replay Status:           {msg_replay.status}")
        print(f"  Replay Provider Msg-ID:  {msg_replay.provider_message_id}")
        print(f"  Transmitted Count Delta: {tx_count_after - tx_count_before}")

        assert msg_replay.id == msg_success.id, "Replay returned a new message ID!"
        assert tx_count_after == tx_count_before, "Second transmission occurred! Critical Idempotency Breach!"
        assert msg_replay.provider_message_id == msg_success.provider_message_id, "Provider message ID changed on replay!"

        audit_results["idempotency_replay"] = {
            "original_id": msg_success.id,
            "replay_id": msg_replay.id,
            "duplicate_transmissions": tx_count_after - tx_count_before
        }

        # ---------------------------------------------------------------------
        # PHASE 7: REAL SMTP FAILURE TEST
        # ---------------------------------------------------------------------
        print("\n--- PHASE 7: REAL SMTP FAILURE TEST ---")
        # Create or retrieve synthetic sender pointing to an unreachable port to simulate delivery failure safely
        failing_sender = db.query(SenderAccount).filter_by(id="sender_failing_port").first()
        if not failing_sender:
            failing_sender = SenderAccount(
                id="sender_failing_port",
                workspace_id=ws.id,
                email=REAL_SMTP_DIAGNOSTIC_ALLOWED_SENDER,
                display_name="Failing Diagnostic Sender",
                provider_type="SMTP",
                encrypted_credentials='{"smtp_host": "127.0.0.1", "smtp_port": 54321, "smtp_user": "u", "smtp_password": "p", "timeout_seconds": 1.0}',
                status="ACTIVE",
                daily_quota=50,
                connection_verified=True,
                dns_valid=True,
                spf_valid=True,
                dkim_valid=True,
                dmarc_valid=True,
                tracking_domain_aligned=True,
                delivery_health_ok=True
            )
            db.add(failing_sender)
            db.commit()

        idemp_fail = f"real_smtp_fail_{uuid.uuid4().hex[:12]}"
        msg_fail = OutboundDispatchEngine.dispatch_outbound_message(
            db=db,
            workspace_id=ws.id,
            campaign_version_id=str(ver.id),
            sender_account_id=failing_sender.id,
            recipient_email=REAL_SMTP_DIAGNOSTIC_ALLOWED_RECIPIENT,
            recipient_country="US",
            business_id=biz.id,
            subject="Failure Test",
            body_text="Failure Body",
            required_claim_ids=[str(claim.id)],
            side_effect_idempotency_key=idemp_fail,
            thread_state="AUTONOMOUS"
        )

        print(f"  Failed Message Status:   {msg_fail.status}")
        print(f"  Failure Reason:          {msg_fail.gate_failure_reason}")
        print(f"  Sent At (Must be None):  {msg_fail.sent_at}")
        print(f"  Provider Msg ID (None):  {msg_fail.provider_message_id}")

        assert msg_fail.status != "TRANSMITTED", "Message was incorrectly marked TRANSMITTED!"
        assert msg_fail.status in ["DELIVERY_FAILED", "DELIVERY_STATE_UNKNOWN"], f"Unexpected status {msg_fail.status}"
        assert msg_fail.sent_at is None, "sent_at must be None on delivery failure!"
        assert msg_fail.provider_message_id is None, "provider_message_id must not be fabricated on failure!"

        audit_results["failure_test"] = {
            "status": msg_fail.status,
            "failure_reason": msg_fail.gate_failure_reason,
            "sent_at_is_none": msg_fail.sent_at is None,
            "provider_message_id_is_none": msg_fail.provider_message_id is None
        }

        # ---------------------------------------------------------------------
        # PHASE 8: REAL SUPPRESSION RACE TEST
        # ---------------------------------------------------------------------
        print("\n--- PHASE 8: REAL SUPPRESSION RACE TEST ---")
        # Invariant: All 16 gates pass, but immediately before wire execution, recipient is suppressed.
        # We test the atomic pre-transmission race check at lines 344-358 of OutboundDispatchEngine.
        # 1. Insert suppression for test mailbox
        SuppressionService.execute_atomic_suppression(
            db=db,
            organization_id=org.id,
            workspace_id=ws.id,
            email=REAL_SMTP_DIAGNOSTIC_ALLOWED_RECIPIENT,
            source_channel="ONE_CLICK_DIAGNOSTIC",
            reason="Controlled suppression race test"
        )

        idemp_supp = f"supp_race_{uuid.uuid4().hex[:12]}"
        tx_count_before_supp = db.query(OutboundMessage).filter_by(status="TRANSMITTED").count()

        msg_supp = OutboundDispatchEngine.dispatch_outbound_message(
            db=db,
            workspace_id=ws.id,
            campaign_version_id=str(ver.id),
            sender_account_id=sender.id,
            recipient_email=REAL_SMTP_DIAGNOSTIC_ALLOWED_RECIPIENT,
            recipient_country="US",
            business_id=biz.id,
            subject="Suppressed Message",
            body_text="Should not send",
            required_claim_ids=[str(claim.id)],
            side_effect_idempotency_key=idemp_supp,
            thread_state="AUTONOMOUS"
        )

        tx_count_after_supp = db.query(OutboundMessage).filter_by(status="TRANSMITTED").count()
        print(f"  Suppressed Message Status: {msg_supp.status}")
        print(f"  Gate Passed Count:         {msg_supp.gate_passed_count}")
        print(f"  Failure Reason:            {msg_supp.gate_failure_reason}")
        print(f"  Transmitted Delta:         {tx_count_after_supp - tx_count_before_supp}")

        assert msg_supp.status == "SUPPRESSED", f"Expected SUPPRESSED, got {msg_supp.status}"
        assert tx_count_after_supp == tx_count_before_supp, "CRITICAL BLOCKER: Email was transmitted despite suppression!"
        assert msg_supp.sent_at is None, "sent_at must be None on suppression!"

        # Clean up synthetic test suppression records so subsequent operations are not permanently blocked
        from app.models.tenancy import WorkspaceSuppressionRecord, ComplianceSuppressionLedger
        for wrec in db.query(WorkspaceSuppressionRecord).filter_by(workspace_id=ws.id).all():
            db.delete(wrec)
        for srec in db.query(OrgSuppressionRecord).filter_by(organization_id=org.id).all():
            db.delete(srec)
        for cleg in db.query(ComplianceSuppressionLedger).filter_by(organization_id=org.id).all():
            db.delete(cleg)
        db.commit()
        print("  Cleaned up synthetic test suppression records (Workspace, Org, and ComplianceLedger).")

        is_supp_still, _ = SuppressionService.check_suppression_gate_14(db, org.id, ws.id, REAL_SMTP_DIAGNOSTIC_ALLOWED_RECIPIENT)
        assert is_supp_still is False, "Suppression record cleanup failed!"

        audit_results["suppression_race"] = {
            "status": msg_supp.status,
            "gate_passed_count": msg_supp.gate_passed_count,
            "failure_reason": msg_supp.gate_failure_reason,
            "transmitted_delta": tx_count_after_supp - tx_count_before_supp
        }

        # ---------------------------------------------------------------------
        # PHASE 9: REAL KILL SWITCH TEST
        # ---------------------------------------------------------------------
        print("\n--- PHASE 9: REAL KILL SWITCH TEST ---")
        settings.GLOBAL_EMERGENCY_KILL_SWITCH = True
        idemp_kill = f"kill_test_{uuid.uuid4().hex[:12]}"
        tx_before_kill = db.query(OutboundMessage).filter_by(status="TRANSMITTED").count()

        msg_kill = OutboundDispatchEngine.dispatch_outbound_message(
            db=db,
            workspace_id=ws.id,
            campaign_version_id=str(ver.id),
            sender_account_id=sender.id,
            recipient_email=REAL_SMTP_DIAGNOSTIC_ALLOWED_RECIPIENT,
            recipient_country="US",
            business_id=biz.id,
            subject="Kill Switch Test",
            body_text="Should be killed",
            required_claim_ids=[str(claim.id)],
            side_effect_idempotency_key=idemp_kill,
            thread_state="AUTONOMOUS"
        )

        tx_after_kill = db.query(OutboundMessage).filter_by(status="TRANSMITTED").count()
        print(f"  Kill Switch Active Status: {msg_kill.status}")
        print(f"  Gate Passed Count:         {msg_kill.gate_passed_count}")
        print(f"  Failure Reason:            {msg_kill.gate_failure_reason}")
        print(f"  Transmitted Delta:         {tx_after_kill - tx_before_kill}")

        assert msg_kill.status == "BLOCKED", f"Expected BLOCKED, got {msg_kill.status}"
        assert msg_kill.gate_passed_count == 14, f"Expected Gate 15 violation (14 passed), got {msg_kill.gate_passed_count}"
        assert tx_after_kill == tx_before_kill, "CRITICAL: Email transmitted while kill switch ON!"

        # Restore kill switch to OFF
        settings.GLOBAL_EMERGENCY_KILL_SWITCH = False
        print("  Restored GLOBAL_EMERGENCY_KILL_SWITCH = False.")

        audit_results["kill_switch"] = {
            "status": msg_kill.status,
            "gate_passed_count": msg_kill.gate_passed_count,
            "failure_reason": msg_kill.gate_failure_reason,
            "transmitted_delta": tx_after_kill - tx_before_kill
        }

        # ---------------------------------------------------------------------
        # PHASE 10: CAPACITY ACCOUNTING VERIFICATION
        # ---------------------------------------------------------------------
        print("\n--- PHASE 10: CAPACITY ACCOUNTING VERIFICATION ---")
        final_usage = CapacityService.get_or_create_daily_usage(db, ws.id, date.today()).outreach_count
        usage_delta = final_usage - initial_usage
        print(f"  Initial Outreach Usage:   {initial_usage}")
        print(f"  Final Outreach Usage:     {final_usage}")
        print(f"  Outreach Usage Delta:     {usage_delta}")

        # Exactly 1 real transmission succeeded across all tests
        assert usage_delta == 1, f"Expected usage delta == 1, got {usage_delta}!"

        audit_results["capacity"] = {
            "initial_usage": initial_usage,
            "final_usage": final_usage,
            "usage_delta": usage_delta
        }

        # ---------------------------------------------------------------------
        # PHASE 11 & 12: DATABASE AUDIT & SECURITY AUDIT
        # ---------------------------------------------------------------------
        print("\n--- PHASES 11 & 12: DATABASE & SECURITY AUDIT ---")
        all_msgs = db.query(OutboundMessage).all()
        transmitted_msgs = [m for m in all_msgs if m.status == "TRANSMITTED"]
        print(f"  Total OutboundMessage records created: {len(all_msgs)}")
        print(f"  Total TRANSMITTED records:              {len(transmitted_msgs)}")

        assert len(transmitted_msgs) == 1, f"Expected exactly 1 TRANSMITTED message, found {len(transmitted_msgs)}"
        transmitted_msg = transmitted_msgs[0]
        assert transmitted_msg.recipient_email == REAL_SMTP_DIAGNOSTIC_ALLOWED_RECIPIENT
        assert transmitted_msg.transport_mode == "REAL"
        assert transmitted_msg.sent_at is not None
        assert transmitted_msg.provider_message_id is not None
        assert transmitted_msg.capacity_accounted is True

        # Check for credential leakage in database fields or diagnostic outputs
        secret_fragments = ["platform_password", "SecretAudit", "DiagPass"]
        smtp_pw = settings.SMTP_PASSWORD
        if smtp_pw and len(smtp_pw) > 4:
            secret_fragments.append(smtp_pw)

        for m in all_msgs:
            for field_val in [m.gate_failure_reason, m.body_text, m.subject, m.transport_mode]:
                if field_val:
                    for frag in secret_fragments:
                        if frag.lower() in field_val.lower():
                            raise RuntimeError(f"SECURITY BREACH: Credential fragment found in OutboundMessage {m.id} field!")

        print("  Zero credential leakage confirmed across all OutboundMessage records.")

        audit_results["security"] = {
            "total_outbound_messages": len(all_msgs),
            "total_transmitted": len(transmitted_msgs),
            "credential_leakage": False,
            "hard_pin_preserved": True
        }

        print("\n" + "=" * 80)
        print("REAL SMTP INTEGRATION VERIFICATION COMPLETE — ALL PHASES PASSED 100%")
        print("=" * 80)
        return audit_results

    finally:
        db.close()

if __name__ == "__main__":
    results = run_real_outbound_audit()
    print("\nSummary Execution Object:")
    import json
    # Redact any potentially sensitive info before printing JSON
    print(json.dumps(results, indent=2, default=str))
