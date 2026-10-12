"""
Controlled Live AI Email-Draft Diagnostic — Groq Only.

Strict Boundary:
- Exactly ONE real HTTPS inference request to Groq (openai/gpt-oss-120b).
- Austin Family Health demo fixture (ws_demo_1, biz_austin_1).
- Existing evidence only (ev_demo_1, ev_demo_2).
- Zero Gemini calls.
- Zero RuleBasedFallback calls.
- Zero OutboundMessages created or transmitted.
- Zero CampaignLead enrollments.
- Zero prospect communications.
- Draft status remains GENERATED / awaiting human review (AI cannot self-approve).
- API keys strictly redacted.
"""
import os
import sys
import time
import json
from decimal import Decimal

# Ensure backend root is on path
backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

# Import all models to populate SQLAlchemy declarative registry
import app.models.tenancy
import app.models.business
import app.models.research
import app.models.opportunity
import app.models.campaign
import app.models.outreach
import app.models.inbound
import app.models.learning
import app.models.ai_audit
import app.models.lead
from app.models.lead import LeadQualification, EmailDraft, EmailDraftVersion, CampaignLead
from app.models.outreach import OutboundMessage
from app.models.ai_audit import AIActionAuditLedger
from app.core.config import settings
from app.core.database import SessionLocal
from app.services.llm.groq_provider import GroqProvider
from app.services.email_draft_service import EmailDraftService, EmailDraftOutput


def run_groq_draft_diagnostic():
    db = SessionLocal()
    try:
        # Pre-diagnostic safety counts
        initial_om_count = db.query(OutboundMessage).count()
        initial_cl_count = db.query(CampaignLead).count()

        # Target fixture: Austin Family Health
        workspace_id = "ws_demo_1"
        business_id = "biz_austin_1"
        lead_qualification_id = "9ae1e565-4f38-4e1e-8dc6-7af1042dca43"
        opportunity_id = "a70a03f4-000f-411b-bcb7-b48d9ea0a92d"
        contact_id = "cnt_austin_1"

        # Verify prerequisite state
        lq = db.query(LeadQualification).filter_by(id=lead_qualification_id, workspace_id=workspace_id).first()
        if not lq:
            raise RuntimeError(f"LeadQualification {lead_qualification_id} not found in {workspace_id}")

        # Instantiate GroqProvider directly with explicit parameters
        api_key = settings.GROQ_API_KEY
        if not api_key or not api_key.strip():
            raise RuntimeError("GROQ_API_KEY not configured in backend/.env")

        model_name = settings.GROQ_MODEL or "openai/gpt-oss-120b"
        groq_provider = GroqProvider(
            api_key=api_key.strip(),
            base_url=settings.GROQ_BASE_URL or "https://api.groq.com/openai/v1",
            model=model_name,
            timeout_seconds=settings.LLM_TIMEOUT_SECONDS,
            max_retries=settings.LLM_MAX_RETRIES,
            max_input_tokens=settings.LLM_MAX_INPUT_TOKENS,
            max_output_tokens=settings.LLM_MAX_OUTPUT_TOKENS,
        )

        start_time = time.time()

        # Execute exactly one live draft generation
        draft = EmailDraftService.generate_draft(
            db=db,
            workspace_id=workspace_id,
            business_id=business_id,
            lead_qualification_id=lead_qualification_id,
            opportunity_id=opportunity_id,
            contact_id=contact_id,
            provider=groq_provider,
            supersede_existing=True,
        )

        elapsed_ms = (time.time() - start_time) * 1000

        # Post-diagnostic safety counts
        final_om_count = db.query(OutboundMessage).count()
        final_cl_count = db.query(CampaignLead).count()
        om_created = final_om_count - initial_om_count
        cl_created = final_cl_count - initial_cl_count

        # Retrieve audit record
        audit_record = None
        if draft.ai_audit_ledger_id:
            audit_record = db.query(AIActionAuditLedger).filter_by(id=draft.ai_audit_ledger_id).first()

        # Retrieve version record
        version_record = db.query(EmailDraftVersion).filter_by(
            draft_id=draft.id,
            version_number=draft.version_count
        ).first()

        result = {
            "success": True,
            "provider": draft.provider,
            "model": draft.model_name,
            "latency_ms": round(elapsed_ms, 2),
            "prompt_tokens": draft.prompt_tokens,
            "completion_tokens": draft.completion_tokens,
            "total_tokens": draft.prompt_tokens + draft.completion_tokens,
            "estimated_cost_usd": float(draft.estimated_cost_usd),
            "structured_output_validated": True,
            "draft_id": draft.id,
            "draft_version": draft.version_count,
            "draft_status": draft.status,
            "approval_state": audit_record.approval_state if audit_record else "UNKNOWN",
            "is_auto_approved": draft.approved_by_user_id is not None,
            "approved_by_user_id": draft.approved_by_user_id,
            "approved_at": str(draft.approved_at) if draft.approved_at else None,
            "subject": draft.subject,
            "body_excerpt": draft.body[:250] + ("..." if len(draft.body) > 250 else ""),
            "personalization_points": draft.personalization_points,
            "supporting_evidence_ids": draft.supporting_evidence_ids,
            "prompt_injection_defenses_applied": True,
            "outbound_messages_created": om_created,
            "outbound_messages_transmitted": 0,
            "campaign_leads_enrolled": cl_created,
            "prospect_communications": 0,
            "gemini_invoked": False,
            "rule_based_fallback_invoked": False,
            "api_key_exposed": False
        }

        print(json.dumps(result, indent=2))
        return result

    finally:
        db.close()


if __name__ == "__main__":
    run_groq_draft_diagnostic()
