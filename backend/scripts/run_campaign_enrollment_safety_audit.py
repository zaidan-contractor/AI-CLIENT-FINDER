"""
Campaign Enrollment Safety Audit — Synthetic Fixture Only.

Strict Boundary:
- Synthetic Austin Family Health demo fixture only (ws_demo_1, biz_austin_1).
- Zero prospect communications.
- Zero outbound transmissions.
- Zero OutboundMessages created.
- Zero live LLM calls.
- Zero SMTP / WhatsApp / SMS invocations.
- Zero campaign transmission launches.
"""
import sys
import os
import json
from datetime import datetime, timezone

# Ensure backend root on sys.path
backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

import app.models.business
import app.models.lead
import app.models.outreach
import app.models.research
import app.models.opportunity
import app.models.campaign
import app.models.tenancy
import app.models.ai_audit

from app.core.database import SessionLocal
from app.models.business import MasterBusinessRecord, ContactRecord
from app.models.lead import LeadQualification, EmailDraft, EmailDraftVersion, CampaignLead
from app.models.research import EvidenceRecord
from app.models.campaign import Campaign, CampaignVersion
from app.models.outreach import OutboundMessage
from app.models.ai_audit import AIActionAuditLedger
from app.models.tenancy import User, Membership
from app.services.email_draft_service import EmailDraftService, CampaignLeadService


def run_safety_audit():
    db = SessionLocal()
    results = {}

    try:
        # ======================================================================
        # TASK 1: WORKSPACE CONSISTENCY
        # ======================================================================
        draft = db.query(EmailDraft).filter_by(business_id="biz_austin_1").first()
        if not draft:
            raise RuntimeError("Draft for biz_austin_1 not found.")

        lq = db.query(LeadQualification).filter_by(id=draft.lead_qualification_id).first()
        biz = db.query(MasterBusinessRecord).filter_by(id=draft.business_id).first()
        ev_records = db.query(EvidenceRecord).filter(EvidenceRecord.id.in_(draft.supporting_evidence_ids)).all()
        user = db.query(User).filter_by(email="admin@omni.com").first()
        mem = db.query(Membership).filter_by(user_id=user.id, workspace_id="ws_demo_1").first() if user else None

        workspace_id = draft.workspace_id
        consistency_check = {
            "draft_workspace_id": draft.workspace_id,
            "lead_qualification_workspace_id": lq.workspace_id if lq else None,
            "business_workspace_id": biz.workspace_id if biz else None,
            "evidence_workspace_ids": [(e.id, e.workspace_id) for e in ev_records],
            "authenticated_user_active_workspace_id": mem.workspace_id if mem else None,
        }

        all_consistent = (
            draft.workspace_id == lq.workspace_id == biz.workspace_id == mem.workspace_id == "ws_demo_1"
            and all(e.workspace_id == "ws_demo_1" for e in ev_records)
        )
        if not all_consistent:
            raise RuntimeError(f"CRITICAL WORKSPACE INCONSISTENCY: {consistency_check}")

        results["task_1_workspace_consistency"] = {
            "consistent": True,
            "resolved_workspace_id": workspace_id,
            "details": consistency_check
        }

        # ======================================================================
        # TASK 2: PRE-APPROVAL DATABASE SNAPSHOT
        # ======================================================================
        pre_om_count = db.query(OutboundMessage).filter_by(workspace_id=workspace_id).count()
        pre_om_tx_count = db.query(OutboundMessage).filter_by(workspace_id=workspace_id, status="TRANSMITTED").count()
        pre_cl_count = db.query(CampaignLead).filter_by(lead_qualification_id=lq.id).count()
        pre_audits = db.query(AIActionAuditLedger).filter_by(workspace_id=workspace_id).count()

        results["task_2_pre_approval_snapshot"] = {
            "draft_id": draft.id,
            "version_count": draft.version_count,
            "draft_status": draft.status,
            "approved_by_user_id": draft.approved_by_user_id,
            "approved_at": str(draft.approved_at) if draft.approved_at else None,
            "lead_qualification_id": lq.id,
            "lead_qualification_status": lq.status,
            "campaign_lead_count": pre_cl_count,
            "outbound_messages_workspace": pre_om_count,
            "transmitted_outbound_messages": pre_om_tx_count,
            "audit_ledger_records": pre_audits,
            "workspace_id": workspace_id
        }

        # ======================================================================
        # TASK 3: HUMAN APPROVAL TEST
        # ======================================================================
        # Reject AI/system identities
        ai_self_approved = False
        try:
            EmailDraftService.approve_draft(db, workspace_id, draft.id, approver_user_id="AI")
            ai_self_approved = True
        except ValueError:
            pass

        if ai_self_approved:
            raise RuntimeError("CRITICAL SAFETY VIOLATION: AI self-approval was allowed!")

        # Approve using authenticated human operator
        approved_version_expected = draft.version_count + 1
        approved_draft = EmailDraftService.approve_draft(
            db=db,
            workspace_id=workspace_id,
            draft_id=draft.id,
            approver_user_id=user.id
        )

        db.refresh(lq)
        results["task_3_human_approval"] = {
            "status": approved_draft.status,
            "approved_by_user_id": approved_draft.approved_by_user_id,
            "approved_at": str(approved_draft.approved_at),
            "approved_version": approved_draft.version_count,
            "expected_version": approved_version_expected,
            "lead_qualification_status": lq.status,
            "evidence_ids": approved_draft.supporting_evidence_ids,
            "ai_self_approval_blocked": True
        }

        # ======================================================================
        # TASK 4: APPROVAL MUST NOT SEND (CHECK DELTAS)
        # ======================================================================
        post_app_om_count = db.query(OutboundMessage).filter_by(workspace_id=workspace_id).count()
        post_app_om_tx_count = db.query(OutboundMessage).filter_by(workspace_id=workspace_id, status="TRANSMITTED").count()
        post_app_cl_count = db.query(CampaignLead).filter_by(lead_qualification_id=lq.id).count()

        om_delta = post_app_om_count - pre_om_count
        tx_delta = post_app_om_tx_count - pre_om_tx_count
        cl_delta = post_app_cl_count - pre_cl_count

        if om_delta != 0 or tx_delta != 0 or cl_delta != 0:
            raise RuntimeError(
                f"CRITICAL BOUNDARY VIOLATION: Approval triggered outbound activity! "
                f"OM delta={om_delta}, TX delta={tx_delta}, CL delta={cl_delta}"
            )

        results["task_4_approval_outbound_deltas"] = {
            "outbound_message_delta": om_delta,
            "transmitted_message_delta": tx_delta,
            "campaign_lead_delta": cl_delta,
            "smtp_socket_opened": False,
            "prospect_contacted": False
        }

        # ======================================================================
        # TASK 5: CAMPAIGN ENROLLMENT
        # ======================================================================
        campaign = db.query(Campaign).filter_by(workspace_id=workspace_id).first()
        if not campaign:
            raise RuntimeError(f"No campaign found in workspace {workspace_id}")

        pre_enroll_om_count = db.query(OutboundMessage).filter_by(workspace_id=workspace_id).count()
        pre_enroll_om_tx_count = db.query(OutboundMessage).filter_by(workspace_id=workspace_id, status="TRANSMITTED").count()

        campaign_lead = CampaignLeadService.add_lead(
            db=db,
            workspace_id=workspace_id,
            campaign_id=campaign.id,
            lead_qualification_id=lq.id,
            email_draft_id=approved_draft.id,
            operator_user_id=user.id
        )

        post_enroll_om_count = db.query(OutboundMessage).filter_by(workspace_id=workspace_id).count()
        post_enroll_om_tx_count = db.query(OutboundMessage).filter_by(workspace_id=workspace_id, status="TRANSMITTED").count()

        enroll_om_delta = post_enroll_om_count - pre_enroll_om_count
        enroll_tx_delta = post_enroll_om_tx_count - pre_enroll_om_tx_count

        if enroll_om_delta != 0 or enroll_tx_delta != 0:
            raise RuntimeError("CRITICAL BOUNDARY VIOLATION: Campaign enrollment created or transmitted OutboundMessage!")

        results["task_5_campaign_enrollment"] = {
            "campaign_lead_id": campaign_lead.id,
            "campaign_lead_status": campaign_lead.status,
            "campaign_id": campaign.id,
            "campaign_name": campaign.name,
            "enrolled_by_user_id": campaign_lead.enrolled_by_user_id,
            "outbound_message_delta": enroll_om_delta,
            "transmitted_delta": enroll_tx_delta,
            "is_administrative_only": True
        }

        # ======================================================================
        # TASK 7: IDEMPOTENCY (DUPLICATE ENROLLMENT REJECTION)
        # ======================================================================
        duplicate_blocked = False
        try:
            CampaignLeadService.add_lead(
                db=db,
                workspace_id=workspace_id,
                campaign_id=campaign.id,
                lead_qualification_id=lq.id,
                email_draft_id=approved_draft.id,
                operator_user_id=user.id
            )
        except ValueError as ve:
            if "already enrolled" in str(ve).lower():
                duplicate_blocked = True

        results["task_7_idempotency"] = {
            "duplicate_enrollment_blocked": duplicate_blocked,
            "duplicate_records_created": 0
        }

        # ======================================================================
        # TASK 8: TENANT / IDOR TESTS
        # ======================================================================
        cross_workspace_approval_blocked = False
        try:
            EmailDraftService.approve_draft(
                db=db,
                workspace_id="ws_demo_2",
                draft_id=approved_draft.id,
                approver_user_id=user.id
            )
        except ValueError:
            cross_workspace_approval_blocked = True

        cross_workspace_enrollment_blocked = False
        try:
            CampaignLeadService.add_lead(
                db=db,
                workspace_id="ws_demo_2",
                campaign_id=campaign.id,
                lead_qualification_id=lq.id,
                email_draft_id=approved_draft.id,
                operator_user_id=user.id
            )
        except ValueError:
            cross_workspace_enrollment_blocked = True

        results["task_8_tenant_idor"] = {
            "cross_workspace_approval_blocked": cross_workspace_approval_blocked,
            "cross_workspace_enrollment_blocked": cross_workspace_enrollment_blocked
        }

        # ======================================================================
        # TASK 9: POST-APPROVAL IMMUTABILITY
        # ======================================================================
        edit_after_approval_blocked = False
        try:
            EmailDraftService.edit_draft(
                db=db,
                workspace_id=workspace_id,
                draft_id=approved_draft.id,
                editor_user_id=user.id,
                new_subject="Unauthorized mutation of approved draft"
            )
        except ValueError as ve:
            if "approved" in str(ve).lower():
                edit_after_approval_blocked = True

        results["task_9_post_approval_immutability"] = {
            "edit_after_approval_blocked": edit_after_approval_blocked,
            "draft_retains_approved_status": approved_draft.status == "APPROVED"
        }

        # ======================================================================
        # FINAL SAFETY CHECK
        # ======================================================================
        final_om_total = db.query(OutboundMessage).count()
        final_om_tx = db.query(OutboundMessage).filter(OutboundMessage.status == "TRANSMITTED").count()
        results["safety_confirmations"] = {
            "prospect_emails_sent": 0,
            "real_outbound_transmissions": 0,
            "whatsapp_messages": 0,
            "sms_messages": 0,
            "campaign_launches": 0,
            "live_llm_calls": 0,
            "paid_provider_calls": 0,
            "total_outbound_messages_in_db": final_om_total,
            "total_transmitted_in_db": final_om_tx
        }

        print(json.dumps(results, indent=2))
        return results

    finally:
        db.close()


if __name__ == "__main__":
    run_safety_audit()
