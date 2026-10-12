import os
import sys
import uuid
import time
from decimal import Decimal
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.tenancy import Workspace, get_utc_now
from app.models.business import MasterBusinessRecord, ContactRecord
from app.models.opportunity import ProblemHypothesis, ImpactAssessment, SolutionMapping
from app.models.campaign import CampaignVersion, ApprovedSalesClaimRecord
from app.models.outreach import SenderAccount, OutboundMessage
from app.core.queue import JobQueueManager, OutboundJobPayload
from rq import SimpleWorker, Queue
import redis

# STRICT SAFETY INVARIANT: Only this exact mailbox may receive the diagnostic message
MANDATORY_TEST_MAILBOX = "opportunity.platform.test@gmail.com"

def execute_single_diagnostic_transmission():
    print("=" * 80)
    print("EXECUTING CONTROLLED SINGLE DIAGNOSTIC WIRE TRANSMISSION")
    print("=" * 80)

    # 1. Safety Pin Enforcement
    assert MANDATORY_TEST_MAILBOX == "opportunity.platform.test@gmail.com", "Safety pin violated!"

    db = SessionLocal()
    try:
        ws = db.query(Workspace).filter_by(id="ws_diagnostic_test").first()
        assert ws is not None, "Preflight fixtures not found! Run run_diagnostic_email_preflight.py first."
        sender = db.query(SenderAccount).filter_by(id="sender_gmail_diagnostic").first()
        biz = db.query(MasterBusinessRecord).filter_by(id="biz_diagnostic_target").first()
        cnt = db.query(ContactRecord).filter_by(id="cnt_diagnostic_target").first()
        ver = db.query(CampaignVersion).filter_by(workspace_id=ws.id, status="OPERATOR_APPROVED").first()
        claim = db.query(ApprovedSalesClaimRecord).filter_by(workspace_id=ws.id, approval_status="OPERATOR_APPROVED").first()

        # Enforce that recipient matches safety pin exactly
        assert cnt.email == MANDATORY_TEST_MAILBOX, "Contact record does not match safety mailbox!"

        subject = "Opportunity Platform Controlled Diagnostic Wire Verification"
        body_text = (
            "Diagnostic Wire Verification Notice\n\n"
            "This is an automated single diagnostic test message sent through the complete "
            "production pipeline to verify external SMTP connectivity with Google servers.\n\n"
            "Pipeline Path Verified:\n"
            "1. API Enqueue Interface\n"
            "2. Redis / RQ Queue ('outbound_dispatch')\n"
            "3. Outbound Worker Process\n"
            "4. 16 Monotonic Runtime Gates (All Evaluated & Passed)\n"
            "5. Gate 14 Pre-Transmission Suppression Re-Check\n"
            "6. Real SMTPEmailTransport (smtp.gmail.com:587 STARTTLS)\n"
            "7. RFC 8058 One-Click List-Unsubscribe Header Injection\n"
            "8. Delivery State Persisted in PostgreSQL Database\n\n"
            f"Timestamp: {get_utc_now().isoformat()}\n"
            f"Environment: {settings.ENVIRONMENT}\n"
        )
        body_html = f"<pre style='font-family: monospace;'>{body_text}</pre>"
        idempotency_key = f"diag_wire_{uuid.uuid4().hex[:12]}"
        job_id = f"job_diag_{uuid.uuid4().hex[:12]}"

        # 2. Enqueue outbound job to Redis
        r_conn = redis.from_url(settings.REDIS_URL)
        q = Queue("outbound_dispatch", connection=r_conn)
        initial_q_len = len(q)

        payload = OutboundJobPayload(
            job_id=job_id,
            workspace_id=ws.id,
            organization_id=ws.organization_id,
            campaign_version_id=str(ver.id),
            sender_account_id=sender.id,
            recipient_email=MANDATORY_TEST_MAILBOX,
            recipient_country="US",
            business_id=biz.id,
            subject=subject,
            body_text=body_text,
            body_html=body_html,
            required_claim_ids=[str(claim.id)],
            side_effect_idempotency_key=idempotency_key
        )

        enqueue_res = JobQueueManager.enqueue_outbound_dispatch(payload)
        print(f"[STEP 1] Enqueued job to Redis RQ: {enqueue_res}")
        assert enqueue_res["mode"] == "REDIS_RQ"
        assert enqueue_res["status"] == "QUEUED"
        assert len(q) == initial_q_len + 1

        # 3. Worker Dequeue & 16-Gate Execution via Real SMTP
        print("[STEP 2] Running RQ Worker to dequeue and transmit via smtp.gmail.com:587...")
        worker = SimpleWorker([q], connection=r_conn)
        worker.work(burst=True)

        # 4. Verify persisted state in PostgreSQL
        db.expire_all()
        saved_msg = db.query(OutboundMessage).filter_by(side_effect_idempotency_key=idempotency_key).first()
        assert saved_msg is not None, "OutboundMessage record was not created in PostgreSQL!"
        print(f"[STEP 3] Persisted Message Status: {saved_msg.status}")
        print(f"[STEP 4] 16 Gates Evaluation: {saved_msg.gate_passed_count}/16 Gates Passed")
        print(f"[STEP 5] Provider Message ID: {saved_msg.provider_message_id}")
        print(f"[STEP 6] Sent At: {saved_msg.sent_at}")

        assert saved_msg.status == "TRANSMITTED", f"Expected TRANSMITTED, got {saved_msg.status}: {saved_msg.gate_failure_reason}"
        assert saved_msg.gate_passed_count == 16, f"Expected 16 gates passed, got {saved_msg.gate_passed_count}"
        assert saved_msg.provider_message_id is not None, "Provider Message ID was not captured!"
        assert saved_msg.transport_mode == "REAL", f"Expected transport_mode REAL, got {saved_msg.transport_mode}"

        print("=" * 80)
        print("DIAGNOSTIC TRANSMISSION COMPLETED SUCCESSFULLY!")
        print(f"Target Recipient:      {MANDATORY_TEST_MAILBOX}")
        print(f"Sender:                {sender.email}")
        print(f"Queue / Job ID:        {job_id}")
        print(f"OutboundMessage ID:    {saved_msg.id}")
        print(f"Provider Message ID:   {saved_msg.provider_message_id}")
        print(f"Transport Mode:        {saved_msg.transport_mode}")
        print(f"Database Final Status: {saved_msg.status}")
        print(f"Sent At:               {saved_msg.sent_at}")
        print(f"Second Transmission:   NONE (Single transmission hard-stopped)")
        print("=" * 80)
        return True

    finally:
        db.close()

if __name__ == "__main__":
    success = execute_single_diagnostic_transmission()
    sys.exit(0 if success else 1)
