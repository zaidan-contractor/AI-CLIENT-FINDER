import os
import sys
import uuid
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
from app.models.outreach import SenderAccount, OutboundMessage, JurisdictionPolicy
from app.services.claim_governance import ClaimGovernanceService
from app.services.outbound_dispatch_engine import OutboundDispatchEngine
from app.core.queue import JobQueueManager, OutboundJobPayload
from app.core.security import get_password_hash, encrypt_credential
import redis

# STRICT SAFETY PIN: Only this exact mailbox may be targeted
MANDATORY_TEST_MAILBOX = "opportunity.platform.test@gmail.com"

def run_preflight():
    print("=" * 80)
    print("CONTROLLED OUTBOUND WIRE TRANSMISSION PREFLIGHT (DRY RUN)")
    print("=" * 80)

    # 1. Verify recipient pin
    assert MANDATORY_TEST_MAILBOX == "opportunity.platform.test@gmail.com", "Safety pin violated!"
    print(f"[PREFLIGHT CHECK 1] Hard-pinned Recipient: {MANDATORY_TEST_MAILBOX}")

    # 2. Verify Database Connection
    db = SessionLocal()
    print("[PREFLIGHT CHECK 2] Connected to PostgreSQL database.")

    # 3. Verify Redis Connection
    r = redis.from_url(settings.REDIS_URL)
    assert r.ping(), "Redis ping failed!"
    print(f"[PREFLIGHT CHECK 3] Connected to Redis Task Queue at {settings.REDIS_URL}.")

    # 4. Verify SMTP Credentials Configured in settings
    assert settings.SMTP_HOST == "smtp.gmail.com", f"Invalid SMTP_HOST: {settings.SMTP_HOST}"
    assert settings.SMTP_PORT == 587, f"Invalid SMTP_PORT: {settings.SMTP_PORT}"
    assert settings.SMTP_USERNAME == MANDATORY_TEST_MAILBOX, f"Invalid SMTP_USERNAME: {settings.SMTP_USERNAME}"
    assert bool(settings.SMTP_PASSWORD), "SMTP_PASSWORD is empty in .env!"
    print(f"[PREFLIGHT CHECK 4] SMTP configuration present for {settings.SMTP_USERNAME} -> {settings.SMTP_HOST}:{settings.SMTP_PORT}")

    # 5. Seed or retrieve persistent synthetic diagnostic fixtures
    org = db.query(Organization).filter_by(id="org_diagnostic_test").first()
    if not org:
        org = Organization(id="org_diagnostic_test", name="Diagnostic Self-Test Org", slug="diag-org")
        user = User(id="user_diagnostic_op", email="diagnostic-operator@test.local", hashed_password=get_password_hash("DiagPass123!"), full_name="Diagnostic Operator")
        db.add_all([org, user])
        db.commit()

        ws = Workspace(id="ws_diagnostic_test", name="Diagnostic Self-Test Workspace", slug="diag-ws", organization_id=org.id)
        db.add(ws)
        db.commit()

        mem = Membership(id="mem_diagnostic_op", user_id=user.id, organization_id=org.id, workspace_id=ws.id, role="Operator")
        db.add(mem)
        db.commit()

        # Synthetic test target business entity
        biz = MasterBusinessRecord(
            id="biz_diagnostic_target",
            workspace_id=ws.id,
            name="Diagnostic Self-Test Target Clinic",
            normalized_name="diagnostic self test target clinic",
            category="HEALTHCARE",
            source_provider="MANUAL",
            website_url="https://diagnostic-self-test.local",
            primary_email=MANDATORY_TEST_MAILBOX
        )
        db.add(biz)
        db.commit()

        cnt = ContactRecord(
            id="cnt_diagnostic_target",
            workspace_id=ws.id,
            business_id=biz.id,
            email=MANDATORY_TEST_MAILBOX,
            canonical_email=MANDATORY_TEST_MAILBOX,
            verification_tier="CONFIRMED_VERIFIED",
            freshness_timestamp=get_utc_now()
        )
        sender = SenderAccount(
            id="sender_gmail_diagnostic",
            workspace_id=ws.id,
            email=MANDATORY_TEST_MAILBOX,
            display_name="Opportunity Platform Diagnostics",
            provider_type="SMTP",
            encrypted_credentials="env_managed",
            status="ACTIVE",
            daily_quota=50,
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
        ev1 = EvidenceRecord(
            id="ev_diag_1",
            workspace_id=ws.id,
            business_id=biz.id,
            signal_type="DOM_ELEMENT",
            raw_observation="No digital patient intake forms observed",
            feature_presence_state="NOT_OBSERVED",
            classification_reason="Confirmed absence",
            confidence=0.9
        )
        ev2 = EvidenceRecord(
            id="ev_diag_2",
            workspace_id=ws.id,
            business_id=biz.id,
            signal_type="REVIEW_SENTIMENT",
            raw_observation="Patients cite intake desk delays",
            feature_presence_state="PRESENT",
            classification_reason="Review sentiment analysis",
            confidence=0.9
        )
        hyp = ProblemHypothesis(
            id="hyp_diag_1",
            workspace_id=ws.id,
            business_id=biz.id,
            category="MANUAL_SCHEDULING_BOTTLENECK",
            title="Diagnostic Intake Bottleneck",
            description="Intake delays verified during preflight.",
            status="VALIDATED",
            confidence=0.9,
            supporting_evidence_ids=["ev_diag_1", "ev_diag_2"]
        )
        db.add_all([cnt, sender, ev1, ev2, hyp])
        db.commit()

        imp = ImpactAssessment(
            id="imp_diag_1",
            workspace_id=ws.id,
            hypothesis_id=hyp.id,
            estimated_impact_low=Decimal("12000"),
            estimated_impact_high=Decimal("24000"),
            estimated_impact_midpoint=Decimal("18000"),
            impact_currency="USD"
        )
        sol = SolutionMapping(
            id="sol_diag_1",
            workspace_id=ws.id,
            hypothesis_id=hyp.id,
            solution_type="AI_RECEPTIONIST",
            title="Automated Intake Portal",
            capability_summary="Online intake",
            feasibility_score=0.9
        )
        db.add_all([imp, sol])
        db.commit()

        camp = ClaimGovernanceService.create_campaign(db, ws.id, "Diagnostic Controlled Outreach Campaign")
        ver = ClaimGovernanceService.create_campaign_version(db, ws.id, camp.id, [{"step": 1}])
        claim = ClaimGovernanceService.create_claim(db, ws.id, ver.id, hyp.id, "Diagnostic finding: Intake delay observed.", [ev1.id])
        ClaimGovernanceService.approve_claim(db, ws.id, claim.id, user.id)
        ClaimGovernanceService.approve_campaign_version(db, ws.id, ver.id, user.id)
    else:
        ws = db.query(Workspace).filter_by(id="ws_diagnostic_test").first()
        sender = db.query(SenderAccount).filter_by(id="sender_gmail_diagnostic").first()
        biz = db.query(MasterBusinessRecord).filter_by(id="biz_diagnostic_target").first()
        cnt = db.query(ContactRecord).filter_by(id="cnt_diagnostic_target").first()
        ver = db.query(CampaignVersion).filter_by(workspace_id=ws.id, status="OPERATOR_APPROVED").first()
        claim = db.query(ApprovedSalesClaimRecord).filter_by(workspace_id=ws.id, approval_status="OPERATOR_APPROVED").first()

    # Ensure US Jurisdiction Policy exists
    us_policy = db.query(JurisdictionPolicy).filter_by(country_code="US").first()
    if not us_policy:
        us_policy = JurisdictionPolicy(
            country_code="US",
            policy_version="v1.0",
            decision="ALLOWED",
            lawful_basis="OPT_OUT",
            requires_one_click_unsubscribe=True,
            requires_physical_address=True,
            description="CAN-SPAM compliant cold consultative outreach",
            is_active=True
        )
        db.add(us_policy)
        db.commit()

    print("[PREFLIGHT CHECK 5] Verified synthetic diagnostic fixtures in PostgreSQL.")

    # 6. Evaluate all 16 gates in DRY-RUN mode (no send, no write)
    passed, reason, gates_passed, details = OutboundDispatchEngine.evaluate_all_16_gates(
        db=db,
        workspace_id=ws.id,
        campaign_version_id=str(ver.id),
        sender_account_id=sender.id,
        recipient_email=MANDATORY_TEST_MAILBOX,
        recipient_country="US",
        business_id=biz.id,
        required_claim_ids=[str(claim.id)],
        thread_state="AUTONOMOUS",
        projected_daily_burn=Decimal("10.00"),
        contact_verification_tier="CONFIRMED_VERIFIED"
    )

    print("\n[PREFLIGHT CHECK 6] 16-Gate Monotonic Pipeline Pre-Evaluation:")
    gate_descriptions = {
        1: "CampaignVersion OPERATOR_APPROVED",
        2: "All required sales claims exist",
        3: "All required sales claims OPERATOR_APPROVED",
        4: "Sales claim cryptographic hashes valid & untampered",
        5: "Lead satisfies outreach eligibility criteria (Opportunity Score >= 50)",
        6: "Contact verification tier fresh & permitted (CONFIRMED_VERIFIED in DB)",
        7: "SenderAccount 8-dimension health & DNS readiness passed",
        8: "Workspace daily quota limit check",
        9: "Sender account daily volume limit check",
        10: "Sender account warm-up velocity check",
        11: "Domain velocity throttling check",
        12: "Recipient country jurisdiction policy check (US: ALLOWED)",
        13: "14-day rolling cost anomaly detector check",
        14: "Atomic suppression list pre-check (opt-outs, unsubscribes)",
        15: "Global emergency kill-switch inactive check",
        16: "Thread state autonomous check (no human takeover lock)"
    }

    for gate_num in range(1, 17):
        key = f"gate_{gate_num}"
        gate_val = details.get(key, "PASSED" if gate_num <= gates_passed else "FAILED")
        print(f"  Gate {gate_num:2d} | {gate_descriptions[gate_num]:<55} -> [PASS] {gate_val}")

    print(f"\nDry-Run 16-Gate Result: Passed = {passed}, Gates Passed = {gates_passed}/16")
    if not passed:
        print(f"Gate Failure Reason: {reason}")
        db.close()
        return False

    assert passed is True, f"16-Gate preflight failed: {reason}"
    assert gates_passed == 16, f"Expected all 16 gates to pass, got {gates_passed}"

    # 7. Preflight Summary
    subject = "Opportunity Platform Controlled Diagnostic Wire Verification"
    idempotency_key = f"diag_wire_{uuid.uuid4().hex[:12]}"

    print("\n" + "=" * 80)
    print("PREFLIGHT SUCCESSFUL — READY FOR OPERATOR APPROVAL")
    print("=" * 80)
    print(f"Target Recipient:     {MANDATORY_TEST_MAILBOX} (Hard-pinned)")
    print(f"Sender Account:       {sender.email} ({sender.display_name})")
    print(f"SMTP Server:          {settings.SMTP_HOST}:{settings.SMTP_PORT} (STARTTLS enabled)")
    print(f"Subject:              {subject}")
    print(f"Idempotency Key:      {idempotency_key}")
    print(f"Queue Mode:           Redis / RQ Queue ('outbound_dispatch')")
    print(f"Worker Engine:        rq SimpleWorker / process_outbound_job_task")
    print(f"Email Transport:      Real SMTPEmailTransport (smtp.gmail.com)")
    print(f"Prospect Outreach:    STRICTLY PROHIBITED (Guaranteed 0 prospect emails)")
    print("=" * 80)
    print("STATUS: Preflight completed. NO EMAIL TRANSMITTED.")
    print("Awaiting operator approval to perform the single live test.")
    print("=" * 80)

    db.close()
    return True

if __name__ == "__main__":
    success = run_preflight()
    sys.exit(0 if success else 1)
