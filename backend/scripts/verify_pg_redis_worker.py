import os
import sys
import uuid
import time
from decimal import Decimal
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import settings
from app.core.database import SessionLocal, Base, engine
from app.models.tenancy import Organization, Workspace, User, Membership, get_utc_now
from app.models.business import MasterBusinessRecord, ContactRecord
from app.models.research import EvidenceRecord
from app.models.opportunity import ProblemHypothesis, ImpactAssessment, SolutionMapping
from app.models.campaign import Campaign, CampaignVersion, ApprovedSalesClaimRecord
from app.models.outreach import SenderAccount, OutboundMessage
from app.services.claim_governance import ClaimGovernanceService
from app.services.email_transports.mock_transport import MockEmailTransport
from app.services.email_transports.factory import set_transport_override
from app.core.queue import JobQueueManager, OutboundJobPayload
from app.core.security import get_password_hash, encrypt_credential
from rq import SimpleWorker, Queue
import redis

def run_integration_verification():
    print("================================================================================")
    print("STARTING END-TO-END POSTGRESQL + REDIS + WORKER VERIFICATION")
    print("================================================================================")

    # 1. Connect to PostgreSQL
    db = SessionLocal()
    print("[1] PostgreSQL Database connection established.")

    # 2. Connect to Redis
    r_conn = redis.from_url(settings.REDIS_URL)
    r_conn.ping()
    print("[2] Redis connection verified via ping.")

    # Clean test queue if needed
    q = Queue("outbound_dispatch", connection=r_conn)
    q.empty()

    # 3. Create test records in PostgreSQL
    unique_suffix = uuid.uuid4().hex[:8]
    org_id = f"org_test_{unique_suffix}"
    ws_id = f"ws_test_{unique_suffix}"
    user_id = f"user_test_{unique_suffix}"

    try:
        org = Organization(id=org_id, name="Verify Org", slug=f"verify-org-{unique_suffix}")
        ws = Workspace(id=ws_id, name="Verify WS", slug=f"verify-ws-{unique_suffix}", organization_id=org_id)
        user = User(id=user_id, email=f"op_{unique_suffix}@test.local", hashed_password=get_password_hash("Pass123!"), full_name="Op Test")
        mem = Membership(id=f"mem_{unique_suffix}", user_id=user_id, organization_id=org_id, workspace_id=ws_id, role="Operator")

        biz = MasterBusinessRecord(
            id=f"biz_{unique_suffix}",
            workspace_id=ws_id,
            name="Verify Medical Clinic",
            normalized_name="verify medical clinic",
            category="HEALTHCARE",
            source_provider="MANUAL",
            website_url="https://verifymed.test",
            primary_email=f"clinic_{unique_suffix}@verifymed.test"
        )
        cnt = ContactRecord(
            id=f"cnt_{unique_suffix}",
            workspace_id=ws_id,
            business_id=biz.id,
            email=f"contact_{unique_suffix}@verifymed.test",
            canonical_email=f"contact_{unique_suffix}@verifymed.test",
            verification_tier="CONFIRMED_VERIFIED",
            freshness_timestamp=get_utc_now()
        )
        sender = SenderAccount(
            id=f"sender_{unique_suffix}",
            workspace_id=ws_id,
            email=f"outreach_{unique_suffix}@testsender.local",
            display_name="Diagnostic Sender",
            provider_type="MOCK",
            encrypted_credentials=encrypt_credential("mock_key"),
            status="ACTIVE",
            daily_quota=100,
            connection_verified=True,
            dns_valid=True,
            spf_valid=True,
            dkim_valid=True,
            dmarc_valid=True,
            tracking_domain_aligned=True,
            delivery_health_ok=True,
            bounce_rate_pct=0.0,
            spam_complaint_pct=0.0
        )
        ev = EvidenceRecord(
            id=f"ev_{unique_suffix}",
            workspace_id=ws_id,
            business_id=biz.id,
            signal_type="DOM_ELEMENT",
            raw_observation="No digital patient forms",
            feature_presence_state="NOT_OBSERVED",
            classification_reason="Confirmed absence",
            confidence=0.9
        )
        ev2 = EvidenceRecord(
            id=f"ev2_{unique_suffix}",
            workspace_id=ws_id,
            business_id=biz.id,
            signal_type="REVIEW_SENTIMENT",
            raw_observation="Patients complain about manual intake bottlenecks",
            feature_presence_state="PRESENT",
            classification_reason="Review sentiment analysis",
            confidence=0.9
        )
        hyp = ProblemHypothesis(
            id=f"hyp_{unique_suffix}",
            workspace_id=ws_id,
            business_id=biz.id,
            category="MANUAL_SCHEDULING_BOTTLENECK",
            title="Manual Intake Bottleneck",
            description="Paper intake causes 25-minute wait times for patients.",
            status="VALIDATED",
            confidence=0.9,
            supporting_evidence_ids=[f"ev_{unique_suffix}", f"ev2_{unique_suffix}"]
        )
        imp = ImpactAssessment(
            id=f"imp_{unique_suffix}",
            workspace_id=ws_id,
            hypothesis_id=hyp.id,
            estimated_impact_low=Decimal("12000"),
            estimated_impact_high=Decimal("24000"),
            estimated_impact_midpoint=Decimal("18000"),
            impact_currency="USD"
        )
        sol = SolutionMapping(
            id=f"sol_{unique_suffix}",
            workspace_id=ws_id,
            hypothesis_id=hyp.id,
            solution_type="AI_RECEPTIONIST",
            title="Automated Intake Portal",
            capability_summary="Online intake",
            feasibility_score=0.9
        )
        db.add_all([org, user])
        db.commit()

        db.add(ws)
        db.commit()

        db.add(mem)
        db.commit()

        db.add(biz)
        db.commit()

        db.add_all([cnt, sender, ev, ev2, hyp])
        db.commit()

        db.add_all([imp, sol])
        db.commit()

        camp = ClaimGovernanceService.create_campaign(db, ws_id, "End-to-End Verification Campaign")
        ver = ClaimGovernanceService.create_campaign_version(db, ws_id, camp.id, [{"step": 1}])
        claim = ClaimGovernanceService.create_claim(db, ws_id, ver.id, hyp.id, "Validated intake audit.", [ev.id])
        ClaimGovernanceService.approve_claim(db, ws_id, claim.id, user_id)
        ClaimGovernanceService.approve_campaign_version(db, ws_id, ver.id, user_id)

        print("[3] Successfully seeded valid Organization, Workspace, Campaign, and Claims in PostgreSQL.")

        # 4. Set Mock Email Transport so no real emails are sent during verification
        mock_transport = MockEmailTransport()
        set_transport_override(mock_transport)

        # 5. Enqueue Outbound Job to Redis
        job_id = f"job_e2e_{unique_suffix}"
        idempotency_key = f"idemp_e2e_{unique_suffix}"
        payload = OutboundJobPayload(
            job_id=job_id,
            workspace_id=ws_id,
            organization_id=org_id,
            campaign_version_id=str(ver.id),
            sender_account_id=sender.id,
            recipient_email=cnt.email,
            recipient_country="US",
            business_id=biz.id,
            subject="Diagnostic Verification Notice",
            body_text="Automated verification dispatch test.",
            required_claim_ids=[str(claim.id)],
            side_effect_idempotency_key=idempotency_key
        )

        enqueue_res = JobQueueManager.enqueue_outbound_dispatch(payload)
        print(f"[4] Enqueued outbound job to Redis RQ: {enqueue_res}")
        assert enqueue_res["mode"] == "REDIS_RQ"
        assert enqueue_res["status"] == "QUEUED"
        assert len(q) == 1

        # 6. Run Worker to process from Redis
        print("[5] Running Worker process to dequeue and execute job from Redis...")
        worker = SimpleWorker([q], connection=r_conn)
        worker.work(burst=True)

        # 7. Check message in PostgreSQL
        db_check = SessionLocal()
        saved_msg = db_check.query(OutboundMessage).filter_by(side_effect_idempotency_key=idempotency_key).first()
        assert saved_msg is not None, "Message must be saved in PostgreSQL"
        print(f"[6] Message status in PostgreSQL: {saved_msg.status} (Gates passed: {saved_msg.gate_passed_count}/16)")
        assert saved_msg.status == "TRANSMITTED", f"Expected TRANSMITTED, got {saved_msg.status}"
        assert saved_msg.gate_passed_count == 16, f"Expected 16 gates passed, got {saved_msg.gate_passed_count}"
        assert saved_msg.provider_message_id is not None

        # 8. Check Mock Transport delivery
        assert len(mock_transport.sent_messages) == 1
        sent = mock_transport.sent_messages[0]
        assert sent["to_email"] == cnt.email
        assert "List-Unsubscribe" in sent["headers"]
        print("[7] Provider transport transmission verified; RFC 8058 headers present.")

        print("================================================================================")
        print("ALL VERIFICATION CHECKS PASSED SUCCESSFULLY!")
        print("PostgreSQL persistence + Redis task queue + Worker execution + 16 Gates VERIFIED.")
        print("================================================================================")
        return True

    finally:
        set_transport_override(None)
        db.close()

if __name__ == "__main__":
    success = run_integration_verification()
    sys.exit(0 if success else 1)
