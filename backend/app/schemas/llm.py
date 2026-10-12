from typing import List, Optional, Literal
from pydantic import BaseModel, Field, ConfigDict


class LLMBaseSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class BusinessResearchAnalysis(LLMBaseSchema):
    """
    Structured reasoning output for external business research analysis.
    Grounds all claims strictly in observed evidence.
    """
    observed_business_facts: List[str] = Field(..., description="Verifiable facts observed from collected evidence")
    evidence_references: List[str] = Field(..., description="Identifiers or source references for the facts")
    digital_gaps: List[str] = Field(default_factory=list, description="Digital or online presence shortcomings identified")
    operational_gaps: List[str] = Field(default_factory=list, description="Operational friction or inefficiencies identified")
    customer_experience_gaps: List[str] = Field(default_factory=list, description="Customer journey friction identified")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Confidence score between 0.0 and 1.0")
    uncertainty: float = Field(..., ge=0.0, le=1.0, description="Uncertainty / risk score between 0.0 and 1.0")
    recommended_solution_categories: List[str] = Field(..., description="High-level solution domains from approved taxonomy")
    reasoning: str = Field(..., description="Structured justification linking evidence to identified gaps")


class OpportunityAnalysis(LLMBaseSchema):
    """
    Structured evaluation of a specific commercial opportunity.
    Strictly differentiates between catalog solutions and bespoke proposals.
    """
    problem: str = Field(..., description="Core business bottleneck or problem")
    evidence: List[str] = Field(..., description="Direct citations of collected evidence")
    affected_workflow: str = Field(..., description="Business workflow or operational unit affected")
    potential_solution: str = Field(..., description="Concrete solution proposed")
    solution_type: Literal["CATALOGUE_SOLUTION", "BESPOKE_PROPOSAL"] = Field(
        ..., description="Must be CATALOGUE_SOLUTION if within approved catalog, else BESPOKE_PROPOSAL"
    )
    expected_business_impact: str = Field(..., description="Projected ROI or efficiency gain")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Confidence score between 0.0 and 1.0")
    assumptions: List[str] = Field(default_factory=list, description="Key assumptions underlying the opportunity")
    missing_information: List[str] = Field(default_factory=list, description="Data points required before outreach")


class OutreachPersonalization(LLMBaseSchema):
    """
    Structured outreach personalization hook.
    Ensures message angle is derived from factual business evidence without hallucinations.
    """
    personalization_facts: List[str] = Field(..., description="Verified business facts used for personalization")
    evidence_references: List[str] = Field(..., description="Evidence sources proving each fact")
    proposed_value_proposition: str = Field(..., description="Tailored value proposition for the target business")
    email_angle: str = Field(..., description="High-level narrative theme for outreach")
    uncertainty_flags: List[str] = Field(default_factory=list, description="Potential sensitivities or unverified items")


class InboundReplyClassification(LLMBaseSchema):
    """
    Deterministic classification of an inbound prospect reply.
    """
    intent_category: Literal["OPT_OUT", "HOT_DEAL", "TECHNICAL_INQUIRY", "OBJECTION", "GENERAL"] = Field(
        ..., description="Classification category"
    )
    confidence: float = Field(..., ge=0.0, le=1.0, description="Classification confidence")
    requested_action: Optional[str] = Field(default=None, description="Action explicitly requested by sender")
    objections: List[str] = Field(default_factory=list, description="Specific objections raised")
    human_escalation_required: bool = Field(default=False, description="True if sentiment or complexity requires human review")
    proposed_response: Optional[str] = Field(default=None, description="Draft response suggestion for human approval")
