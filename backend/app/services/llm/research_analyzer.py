import logging
from typing import List, Dict, Any, Tuple, Optional
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.models.business import MasterBusinessRecord
from app.models.research import EvidenceRecord
from app.models.opportunity import ProblemHypothesis, SolutionMapping
from app.schemas.llm import BusinessResearchAnalysis, OpportunityAnalysis
from app.services.llm.base import LLMExecutionStatus, LLMStructuredResult
from app.services.llm.guardrails import PromptInjectionGuardrail
from app.services.llm.factory import get_llm_provider
from app.services.llm.cost_guardrail import CostGuardrail
from app.services.llm.audit_service import AIAuditService

logger = logging.getLogger(__name__)

APPROVED_SOLUTION_TAXONOMY = [
    "DIGITAL_PRESENCE_AUDIT",
    "ONLINE_ORDERING_SYSTEM",
    "REPUTATION_MANAGEMENT_AUTOMATION",
    "LOCAL_SEO_OPTIMIZATION",
    "SPEED_AND_PERFORMANCE_OPTIMIZATION",
    "ACCESSIBILITY_COMPLIANCE_SUITE",
    "CUSTOMER_ENGAGEMENT_CRM"
]


class ResearchAnalyzerService:
    """
    Integrates the LLM layer with the business research and opportunity pipeline.
    Enforces:
    - Multi-tenant workspace scoping on all database queries and context
    - Grounding of all factual claims strictly in collected EvidenceRecords
    - Multi-tier prompt injection defanging
    - Catalogue vs Bespoke solution boundary enforcement
    - Immutable AI action audit ledger recording
    - Deterministic cost guardrails
    """

    @classmethod
    def analyze_business_evidence(
        cls,
        db: Session,
        workspace_id: str,
        business_id: str,
        actor_id: str = "SYSTEM_RESEARCH_PIPELINE"
    ) -> Tuple[Optional[BusinessResearchAnalysis], LLMExecutionStatus, Optional[str]]:
        """
        Analyze verified evidence records for a discovered business.
        """
        # 1. Enforce strict workspace isolation
        biz = db.execute(
            select(MasterBusinessRecord).where(
                MasterBusinessRecord.id == business_id,
                MasterBusinessRecord.workspace_id == workspace_id
            )
        ).scalar_one_or_none()

        if not biz:
            raise ValueError(f"Business '{business_id}' not found in workspace '{workspace_id}'")

        # 2. Retrieve collected evidence
        evidence_records = db.execute(
            select(EvidenceRecord).where(
                EvidenceRecord.business_id == business_id,
                EvidenceRecord.workspace_id == workspace_id
            )
        ).scalars().all()

        # Format untrusted evidence payload
        untrusted_data: Dict[str, Any] = {
            "business_name": biz.name,
            "business_domain": biz.canonical_domain or "N/A",
            "business_category": biz.category or "N/A",
        }

        if not evidence_records:
            untrusted_data["evidence_notes"] = "No technical probe signals recorded."
        else:
            for i, ev in enumerate(evidence_records):
                untrusted_data[f"evidence_{ev.id}"] = (
                    f"[{ev.signal_type}] State: {ev.feature_presence_state}. "
                    f"Observation: {ev.raw_observation}. Source: {ev.source_url or 'direct_probe'}"
                )

        # 3. Budget preflight check
        approx_input_tokens = sum(CostGuardrail.estimate_token_count(str(v)) for v in untrusted_data.values()) + 300
        approx_output_tokens = 600

        provider = get_llm_provider()
        model_name = provider.get_model_metadata().get("model", "unknown")

        is_allowed, budget_rejection = CostGuardrail.check_budget_preflight(
            db=db,
            workspace_id=workspace_id,
            estimated_input_tokens=approx_input_tokens,
            estimated_output_tokens=approx_output_tokens,
            model=model_name
        )

        if not is_allowed:
            AIAuditService.record_ai_action(
                db=db,
                workspace_id=workspace_id,
                actor_id=actor_id,
                provider=provider.get_model_metadata().get("provider", "UNKNOWN"),
                model_name=model_name,
                purpose="BUSINESS_RESEARCH_ANALYSIS",
                input_payload=untrusted_data,
                output_schema_name="BusinessResearchAnalysis",
                status=LLMExecutionStatus.LLM_BLOCKED,
                action_proposed={"rejection": budget_rejection},
                action_executed={"action": "REQUEST_BLOCKED_BUDGET"},
                approval_state="REJECTED"
            )
            return None, LLMExecutionStatus.LLM_BLOCKED, budget_rejection

        # CapacityPolicy & Quality Floor Verification
        from app.services.capacity_service import CapacityService
        cap_res = CapacityService.check_capacity(
            db=db,
            workspace_id=workspace_id,
            operation_type="LLM_OPERATION",
            requested_units=1,
            provider=provider
        )
        if not cap_res.is_allowed:
            return None, LLMExecutionStatus.LLM_BLOCKED, f"CAPACITY_LIMIT_EXCEEDED: {cap_res.reason}"

        # 4. Construct secure messages with trust boundary
        system_instructions = (
            "You are an enterprise research analyst evaluating commercial business capabilities. "
            "You identify operational, digital, and customer experience gaps based ONLY on observed evidence. "
            "Never invent facts or cite uncollected evidence."
        )
        platform_task = (
            f"Analyze the technical and operational status of '{biz.name}'. "
            "Identify verifiable digital, operational, and customer experience gaps. "
            "Assign realistic confidence and uncertainty ratings."
        )

        messages = PromptInjectionGuardrail.build_secure_messages(
            system_instructions=system_instructions,
            platform_task=platform_task,
            untrusted_inputs=untrusted_data
        )

        # 5. Execute generation
        res: LLMStructuredResult[BusinessResearchAnalysis] = provider.generate_structured(
            messages=messages,
            response_schema=BusinessResearchAnalysis,
            temperature=0.1
        )

        # 6. Postflight accounting and audit logging
        if res.metadata.total_tokens > 0 and res.metadata.estimated_cost_usd > 0:
            CostGuardrail.record_usage_postflight(
                db=db,
                workspace_id=workspace_id,
                prompt_tokens=res.metadata.prompt_tokens,
                completion_tokens=res.metadata.completion_tokens,
                cost_usd=res.metadata.estimated_cost_usd
            )

        AIAuditService.record_ai_action(
            db=db,
            workspace_id=workspace_id,
            actor_id=actor_id,
            provider=res.metadata.provider_name,
            model_name=res.metadata.model_name,
            purpose="BUSINESS_RESEARCH_ANALYSIS",
            input_payload=untrusted_data,
            output_schema_name="BusinessResearchAnalysis",
            status=res.status,
            prompt_tokens=res.metadata.prompt_tokens,
            completion_tokens=res.metadata.completion_tokens,
            total_tokens=res.metadata.total_tokens,
            estimated_cost_usd=res.metadata.estimated_cost_usd,
            is_cost_estimated=res.metadata.is_cost_estimated,
            action_proposed=res.parsed.model_dump() if res.parsed else None,
            action_executed={"action": "RESEARCH_RECORD_GENERATED", "status": res.status.value},
            approval_state="AUTO_GATED"
        )

        if res.is_success:
            from app.services.capacity_service import CapacityService
            CapacityService.increment_usage_atomic(db, workspace_id, "LLM_OPERATION", units=1)
            CapacityService.increment_usage_atomic(db, workspace_id, "RESEARCH", units=1)

        return res.parsed, res.status, res.error_message

    @classmethod
    def evaluate_opportunity(
        cls,
        db: Session,
        workspace_id: str,
        business_id: str,
        analysis: BusinessResearchAnalysis,
        approved_catalog_solutions: Optional[List[str]] = None,
        actor_id: str = "SYSTEM_OPPORTUNITY_ENGINE"
    ) -> Tuple[Optional[OpportunityAnalysis], Optional[ProblemHypothesis], Optional[SolutionMapping], LLMExecutionStatus]:
        """
        Evaluate and persist a structured opportunity hypothesis and solution mapping.
        Enforces catalogue vs bespoke classification boundary.
        """
        catalog = approved_catalog_solutions or APPROVED_SOLUTION_TAXONOMY

        provider = get_llm_provider()
        model_name = provider.get_model_metadata().get("model", "unknown")

        untrusted_context = {
            "business_facts": analysis.observed_business_facts,
            "digital_gaps": analysis.digital_gaps,
            "operational_gaps": analysis.operational_gaps,
            "recommended_categories": analysis.recommended_solution_categories,
            "approved_solution_catalog": catalog
        }

        approx_input = CostGuardrail.estimate_token_count(str(untrusted_context)) + 250
        approx_output = 500

        is_allowed, reason = CostGuardrail.check_budget_preflight(
            db=db,
            workspace_id=workspace_id,
            estimated_input_tokens=approx_input,
            estimated_output_tokens=approx_output,
            model=model_name
        )

        if not is_allowed:
            return None, None, None, LLMExecutionStatus.LLM_BLOCKED

        system_instructions = (
            "You are an enterprise solutions architect. Formulate a single high-impact problem hypothesis "
            "and corresponding solution. If the solution is directly included in the approved catalog, "
            "mark solution_type as 'CATALOGUE_SOLUTION'. Otherwise, you MUST classify it as 'BESPOKE_PROPOSAL'. "
            "Do NOT fabricate non-existent products."
        )
        platform_task = (
            "Review the identified gaps and propose a concrete solution opportunity. "
            "Ensure solution_type strictly adheres to catalog matching rules."
        )

        messages = PromptInjectionGuardrail.build_secure_messages(
            system_instructions=system_instructions,
            platform_task=platform_task,
            untrusted_inputs=untrusted_context
        )

        res: LLMStructuredResult[OpportunityAnalysis] = provider.generate_structured(
            messages=messages,
            response_schema=OpportunityAnalysis,
            temperature=0.1
        )

        if not res.parsed:
            return None, None, None, res.status

        opp = res.parsed

        # ANTI-HALLUCINATION ENFORCEMENT: Validate catalogue boundary in deterministic application code
        is_catalog_match = any(
            cat.lower() in opp.potential_solution.lower() or opp.potential_solution.lower() in cat.lower()
            for cat in catalog
        )
        enforced_solution_type = "CATALOGUE_SOLUTION" if is_catalog_match else "BESPOKE_PROPOSAL"

        # Record postflight usage
        if res.metadata.total_tokens > 0:
            CostGuardrail.record_usage_postflight(
                db=db,
                workspace_id=workspace_id,
                prompt_tokens=res.metadata.prompt_tokens,
                completion_tokens=res.metadata.completion_tokens,
                cost_usd=res.metadata.estimated_cost_usd
            )

        # Persist ProblemHypothesis
        hypothesis = ProblemHypothesis(
            workspace_id=workspace_id,
            business_id=business_id,
            category=opp.affected_workflow[:100],
            title=opp.problem[:255],
            description=f"{opp.problem}\n\nExpected Impact: {opp.expected_business_impact}",
            status="PROPOSED",
            confidence=opp.confidence,
            supporting_evidence_ids=analysis.evidence_references,
        )
        db.add(hypothesis)
        db.flush()

        # Persist SolutionMapping
        solution = SolutionMapping(
            workspace_id=workspace_id,
            hypothesis_id=hypothesis.id,
            solution_type=enforced_solution_type,
            title=opp.potential_solution[:255],
            capability_summary=f"Workflow: {opp.affected_workflow}. Impact: {opp.expected_business_impact}",
            feasibility_score=opp.confidence,
            operator_approved=False # AI cannot auto-approve actions
        )
        db.add(solution)
        db.commit()
        db.refresh(hypothesis)
        db.refresh(solution)

        AIAuditService.record_ai_action(
            db=db,
            workspace_id=workspace_id,
            actor_id=actor_id,
            provider=res.metadata.provider_name,
            model_name=res.metadata.model_name,
            purpose="OPPORTUNITY_ANALYSIS",
            input_payload=untrusted_context,
            output_schema_name="OpportunityAnalysis",
            status=res.status,
            prompt_tokens=res.metadata.prompt_tokens,
            completion_tokens=res.metadata.completion_tokens,
            total_tokens=res.metadata.total_tokens,
            estimated_cost_usd=res.metadata.estimated_cost_usd,
            is_cost_estimated=res.metadata.is_cost_estimated,
            action_proposed=opp.model_dump(),
            action_executed={
                "action": "OPPORTUNITY_CREATED",
                "hypothesis_id": hypothesis.id,
                "solution_id": solution.id,
                "solution_type": enforced_solution_type,
            },
            approval_state="AUTO_GATED"
        )

        return opp, hypothesis, solution, res.status
