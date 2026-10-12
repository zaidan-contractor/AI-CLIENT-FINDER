import re
from typing import List, Dict, Any, Tuple, Optional
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.models.tenancy import Workspace, SystemAuditEvent
from app.models.inbound import InboundMessage
from app.services.suppression_service import SuppressionService
from app.services.llm.base import LLMExecutionStatus
from app.services.llm.guardrails import PromptInjectionGuardrail
from app.services.llm.factory import get_llm_provider
from app.services.llm.cost_guardrail import CostGuardrail
from app.services.llm.audit_service import AIAuditService
from app.schemas.llm import InboundReplyClassification

class InboundProcessorService:
    """
    Inbound email security, opt-out suppression, and AI agent boundary processor.
    Strictly implements:
    1. Attachment security pipeline: REJECTED vs QUARANTINED (§3.6)
    2. Automated atomic suppression on reply opt-outs (§6.7)
    3. Multi-tier prompt-injection defanging (§3.1)
    4. Structured LLM intent classification with rule-based fallback (§7)
    5. Host Application Authorization Invariant (LLM Output != Application Authorization)
    """

    REJECTED_EXTENSIONS = {
        ".exe", ".bat", ".cmd", ".js", ".vbs", ".scr", ".ps1", ".sh", ".jar", ".msi", ".dll"
    }

    QUARANTINED_EXTENSIONS = {
        ".pdf", ".docx", ".doc", ".xlsx", ".csv", ".png", ".jpg", ".jpeg", ".txt"
    }

    OPT_OUT_PATTERN = re.compile(
        r"\b(stop|unsubscribe|remove me|don't email|do not email|take me off|not interested|leave me alone|delete my data)\b",
        re.IGNORECASE
    )

    INJECTION_PATTERN = re.compile(
        r"\b(ignore (all )?previous instructions|system prompt|reveal your instructions|you are now|developer mode|override safety|jailbreak|disregard the above)\b",
        re.IGNORECASE
    )

    HOT_DEAL_PATTERN = re.compile(
        r"\b(schedule a (call|demo)|book a call|send (a )?proposal|what is the pricing|can we meet|let's talk|ready to move forward|quote|cost)\b",
        re.IGNORECASE
    )

    TECHNICAL_PATTERN = re.compile(
        r"\b(how does it work|integration|api|security|hipaa|ehr|capabilities|feature|specs)\b",
        re.IGNORECASE
    )

    @classmethod
    def evaluate_attachments(cls, raw_attachments: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], bool]:
        """
        Inbound Attachment Security Pipeline:
        - REJECTED: Executable/script extensions or path traversal. Dropped at boundary.
        - QUARANTINED: Non-executable document types. Isolated safely; mandates HUMAN_REVIEW_REQUIRED.
        """
        processed = []
        requires_quarantine_review = False

        for att in raw_attachments:
            filename = att.get("filename", "").strip()
            # Path traversal check
            if ".." in filename or "/" in filename or "\\" in filename:
                processed.append({
                    "filename": filename,
                    "security_state": "REJECTED",
                    "reason": "Path traversal filename detected."
                })
                continue

            # Extension extraction
            dot_idx = filename.rfind(".")
            ext = filename[dot_idx:].lower() if dot_idx != -1 else ""

            if ext in cls.REJECTED_EXTENSIONS:
                processed.append({
                    "filename": filename,
                    "security_state": "REJECTED",
                    "reason": f"Active executable/script extension '{ext}' strictly prohibited."
                })
            elif ext in cls.QUARANTINED_EXTENSIONS:
                processed.append({
                    "filename": filename,
                    "security_state": "QUARANTINED",
                    "reason": "Isolated in non-executable quarantine storage awaiting operator clearance."
                })
                requires_quarantine_review = True
            else:
                # Unknown extension -> safe default to QUARANTINED
                processed.append({
                    "filename": filename,
                    "security_state": "QUARANTINED",
                    "reason": f"Unrecognized extension '{ext}' safely quarantined."
                })
                requires_quarantine_review = True

        return processed, requires_quarantine_review

    @classmethod
    def process_inbound_email(
        cls,
        db: Session,
        workspace_id: str,
        sender_email: str,
        recipient_email: str,
        subject: str,
        body_text: str,
        raw_headers: Optional[Dict[str, Any]] = None,
        attachments: Optional[List[Dict[str, Any]]] = None
    ) -> InboundMessage:
        ws = db.execute(select(Workspace).where(Workspace.id == workspace_id)).scalars().first()
        if not ws:
            raise ValueError("Workspace not found.")
        organization_id = ws.organization_id

        # 1. Attachment security evaluation
        att_results, att_quarantined = cls.evaluate_attachments(attachments or [])

        # 2. Text Normalization
        clean_text = body_text.strip()

        # 3. Security Check: Prompt Injection (Regex + Guardrail)
        is_injection_detected, matched_patterns = PromptInjectionGuardrail.detect_injection_attempt(clean_text)
        if cls.INJECTION_PATTERN.search(clean_text) or is_injection_detected:
            intent = "PROMPT_INJECTION_ATTEMPT"
            thread_state = "HUMAN_REVIEW_REQUIRED"
            escalation_reason = "Adversarial prompt injection attempt detected and defanged."
            proposed_reply = None

        # 4. Opt-Out Intent Detection -> Immediate Deterministic Atomic Suppression
        elif cls.OPT_OUT_PATTERN.search(clean_text):
            intent = "OPT_OUT"
            thread_state = "OPT_OUT_SUPPRESSED"
            escalation_reason = "Recipient explicitly requested opt-out via reply."
            proposed_reply = None

            # Execute shared atomic suppression across Workspace, Org, and Permanent HMAC Ledger
            SuppressionService.execute_atomic_suppression(
                db=db,
                organization_id=organization_id,
                workspace_id=workspace_id,
                email=sender_email,
                source_channel="REPLY_OPT_OUT",
                reason="Inbound reply text contained explicit opt-out keywords",
                actor_id="INBOUND_PARSER",
                actor_type="SYSTEM"
            )

        # 5. LLM Structured Classification (with deterministic fallback)
        else:
            provider = get_llm_provider()
            model_name = provider.get_model_metadata().get("model", "unknown")

            untrusted_context = {
                "sender_email": sender_email,
                "subject": subject,
                "email_body": clean_text
            }

            approx_input = CostGuardrail.estimate_token_count(str(untrusted_context)) + 200
            approx_output = 300

            is_allowed, _ = CostGuardrail.check_budget_preflight(
                db=db,
                workspace_id=workspace_id,
                estimated_input_tokens=approx_input,
                estimated_output_tokens=approx_output,
                model=model_name
            )

            llm_result = None
            if is_allowed:
                system_instruction = (
                    "You are an enterprise email triage assistant. Classify the inbound prospect reply into "
                    "one of: OPT_OUT, HOT_DEAL, TECHNICAL_INQUIRY, OBJECTION, GENERAL. "
                    "Flag human_escalation_required if the prospect requires high-touch or customized response."
                )
                platform_task = "Analyze the inbound reply and classify its commercial intent."

                messages = PromptInjectionGuardrail.build_secure_messages(
                    system_instructions=system_instruction,
                    platform_task=platform_task,
                    untrusted_inputs=untrusted_context
                )

                llm_result = provider.generate_structured(
                    messages=messages,
                    response_schema=InboundReplyClassification,
                    temperature=0.0
                )

            if llm_result and llm_result.parsed:
                parsed = llm_result.parsed
                intent = parsed.intent_category
                proposed_reply = parsed.proposed_response

                if intent == "OPT_OUT":
                    thread_state = "OPT_OUT_SUPPRESSED"
                    escalation_reason = "Recipient requested opt-out (LLM classified)."
                    proposed_reply = None
                    SuppressionService.execute_atomic_suppression(
                        db=db,
                        organization_id=organization_id,
                        workspace_id=workspace_id,
                        email=sender_email,
                        source_channel="REPLY_OPT_OUT",
                        reason="Inbound reply text classified as opt-out by LLM",
                        actor_id="INBOUND_LLM_CLASSIFIER",
                        actor_type="SYSTEM"
                    )
                elif intent == "HOT_DEAL":
                    thread_state = "HUMAN_TAKEOVER"
                    escalation_reason = "High buying intent classified by AI. Escalated for human closing."
                elif parsed.human_escalation_required:
                    thread_state = "HUMAN_REVIEW_REQUIRED"
                    escalation_reason = "Complex inquiry or objections require operator review."
                else:
                    thread_state = "AUTONOMOUS"
                    escalation_reason = None

                # Audit record
                AIAuditService.record_ai_action(
                    db=db,
                    workspace_id=workspace_id,
                    actor_id="INBOUND_TRIAGE",
                    provider=llm_result.metadata.provider_name,
                    model_name=llm_result.metadata.model_name,
                    purpose="INBOUND_REPLY_CLASSIFICATION",
                    input_payload=untrusted_context,
                    output_schema_name="InboundReplyClassification",
                    status=llm_result.status,
                    prompt_tokens=llm_result.metadata.prompt_tokens,
                    completion_tokens=llm_result.metadata.completion_tokens,
                    total_tokens=llm_result.metadata.total_tokens,
                    estimated_cost_usd=llm_result.metadata.estimated_cost_usd,
                    is_cost_estimated=llm_result.metadata.is_cost_estimated,
                    action_proposed=parsed.model_dump(),
                    action_executed={"intent": intent, "thread_state": thread_state},
                    approval_state="AUTO_GATED"
                )

                if llm_result.metadata.total_tokens > 0:
                    CostGuardrail.record_usage_postflight(
                        db=db,
                        workspace_id=workspace_id,
                        prompt_tokens=llm_result.metadata.prompt_tokens,
                        completion_tokens=llm_result.metadata.completion_tokens,
                        cost_usd=llm_result.metadata.estimated_cost_usd
                    )
            else:
                # Rule-based fallback if LLM is blocked, unconfigured, or fails
                if cls.HOT_DEAL_PATTERN.search(clean_text):
                    intent = "HOT_DEAL"
                    thread_state = "HUMAN_TAKEOVER"
                    escalation_reason = "High buying intent detected (fallback heuristic). Escalated for closing."
                    proposed_reply = "Thank you for your interest. An enterprise solutions specialist will follow up."
                elif cls.TECHNICAL_PATTERN.search(clean_text):
                    intent = "TECHNICAL_INQUIRY"
                    thread_state = "AUTONOMOUS"
                    escalation_reason = None
                    proposed_reply = "Thank you for reaching out. Our solution directly integrates via secure webhooks and REST APIs."
                else:
                    intent = "GENERAL"
                    thread_state = "AUTONOMOUS"
                    escalation_reason = None
                    proposed_reply = "Thank you for your message. We appreciate your feedback."

        # Attachment quarantine override
        if att_quarantined:
            thread_state = "HUMAN_REVIEW_REQUIRED"
            escalation_reason = (escalation_reason + " | " if escalation_reason else "") + "Inbound attachments quarantined awaiting operator review."

        inbound_msg = InboundMessage(
            workspace_id=workspace_id,
            sender_email=sender_email.lower().strip(),
            recipient_email=recipient_email.lower().strip(),
            subject=subject,
            body_text=clean_text,
            raw_headers=raw_headers or {},
            attachments_metadata=att_results,
            classified_intent=intent,
            thread_state=thread_state,
            escalation_reason=escalation_reason,
            proposed_ai_reply=proposed_reply,
            ai_reply_authorized_by_operator=False
        )
        db.add(inbound_msg)
        db.commit()
        db.refresh(inbound_msg)
        return inbound_msg
