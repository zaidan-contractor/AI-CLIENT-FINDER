"""
Alembic migration 0006: LeadQualification, EmailDraft, EmailDraftVersion, CampaignLead tables.

Revision ID: 0006_lead_draft_campaign
Revises: 0005_research
"""
from alembic import op
import sqlalchemy as sa

revision = "0006_lead_draft_campaign"
down_revision = "0005_research"
branch_labels = None
depends_on = None


def upgrade():
    # --- lead_qualifications ---
    op.create_table(
        "lead_qualifications",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("business_id", sa.String(36), sa.ForeignKey("master_business_records.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="DISCOVERED"),
        sa.Column("evidence_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("fact_evidence_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("validated_hypothesis_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("has_eligible_contact", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("contact_verification_tier", sa.String(50), nullable=True),
        sa.Column("primary_contact_id", sa.String(36), nullable=True),
        sa.Column("primary_opportunity_id", sa.String(36), nullable=True),
        sa.Column("solution_fit_type", sa.String(50), nullable=True),
        sa.Column("opportunity_score_snapshot", sa.Float, nullable=True),
        sa.Column("rationale", sa.Text, nullable=True),
        sa.Column("qualification_notes", sa.Text, nullable=True),
        sa.Column("qualified_by_user_id", sa.String(36), nullable=True),
        sa.Column("disqualification_reason", sa.Text, nullable=True),
        sa.Column("qualified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_for_draft_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_for_outbound_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("workspace_id", "business_id", name="uq_lead_qual_workspace_business"),
    )
    op.create_index("ix_lead_qual_workspace", "lead_qualifications", ["workspace_id"])
    op.create_index("ix_lead_qual_business", "lead_qualifications", ["business_id"])
    op.create_index("ix_lead_qual_status", "lead_qualifications", ["status"])
    op.create_index("ix_lead_qual_contact", "lead_qualifications", ["primary_contact_id"])
    op.create_index("ix_lead_qual_opportunity", "lead_qualifications", ["primary_opportunity_id"])

    # --- email_drafts ---
    op.create_table(
        "email_drafts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("business_id", sa.String(36), sa.ForeignKey("master_business_records.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("lead_qualification_id", sa.String(36), sa.ForeignKey("lead_qualifications.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("opportunity_id", sa.String(36), nullable=True, index=True),
        sa.Column("contact_id", sa.String(36), nullable=True, index=True),
        sa.Column("subject", sa.String(500), nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("personalization_points", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("supporting_evidence_ids", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("model_name", sa.String(100), nullable=False, server_default="rule_based_fallback_v1"),
        sa.Column("provider", sa.String(50), nullable=False, server_default="RULE_BASED"),
        sa.Column("prompt_version", sa.String(50), nullable=False, server_default="v1"),
        sa.Column("prompt_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("estimated_cost_usd", sa.Numeric(10, 6), nullable=False, server_default="0.000000"),
        sa.Column("ai_audit_ledger_id", sa.String(36), nullable=True, index=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="GENERATED"),
        sa.Column("approved_by_user_id", sa.String(36), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejection_reason", sa.Text, nullable=True),
        sa.Column("version_count", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_email_drafts_workspace", "email_drafts", ["workspace_id"])
    op.create_index("ix_email_drafts_business", "email_drafts", ["business_id"])
    op.create_index("ix_email_drafts_lead_qual", "email_drafts", ["lead_qualification_id"])
    op.create_index("ix_email_drafts_status", "email_drafts", ["status"])
    op.create_index("ix_email_drafts_opportunity", "email_drafts", ["opportunity_id"])
    op.create_index("ix_email_drafts_contact", "email_drafts", ["contact_id"])

    # --- email_draft_versions ---
    op.create_table(
        "email_draft_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("draft_id", sa.String(36), sa.ForeignKey("email_drafts.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("version_number", sa.Integer, nullable=False),
        sa.Column("subject", sa.String(500), nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False, index=True),
        sa.Column("previous_hash", sa.String(64), nullable=True, index=True),
        sa.Column("changed_fields", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("editor_user_id", sa.String(36), nullable=True, index=True),
        sa.Column("editor_type", sa.String(20), nullable=False, server_default="AI"),
        sa.Column("approval_state_snapshot", sa.String(50), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_email_draft_versions_workspace", "email_draft_versions", ["workspace_id"])
    op.create_index("ix_email_draft_versions_draft", "email_draft_versions", ["draft_id"])
    op.create_index("ix_email_draft_versions_hash", "email_draft_versions", ["content_hash"])
    op.create_index("ix_email_draft_versions_prev_hash", "email_draft_versions", ["previous_hash"])

    # --- campaign_leads ---
    op.create_table(
        "campaign_leads",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("campaign_id", sa.String(36), sa.ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("lead_qualification_id", sa.String(36), sa.ForeignKey("lead_qualifications.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("email_draft_id", sa.String(36), sa.ForeignKey("email_drafts.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="ENROLLED"),
        sa.Column("enrolled_by_user_id", sa.String(36), nullable=True, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("campaign_id", "lead_qualification_id", name="uq_campaign_lead"),
    )
    op.create_index("ix_campaign_leads_workspace", "campaign_leads", ["workspace_id"])
    op.create_index("ix_campaign_leads_campaign", "campaign_leads", ["campaign_id"])
    op.create_index("ix_campaign_leads_lead_qual", "campaign_leads", ["lead_qualification_id"])
    op.create_index("ix_campaign_leads_status", "campaign_leads", ["status"])


def downgrade():
    op.drop_table("campaign_leads")
    op.drop_table("email_draft_versions")
    op.drop_table("email_drafts")
    op.drop_table("lead_qualifications")
