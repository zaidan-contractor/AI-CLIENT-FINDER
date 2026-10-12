"""0003_capacity_policy_and_usage

Revision ID: 0003_capacity
Revises: 0002_llm_audit
Create Date: 2026-09-18 21:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine.reflection import Inspector


# revision identifiers, used by Alembic.
revision: str = '0003_capacity'
down_revision: Union[str, Sequence[str], None] = '0002_llm_audit'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = Inspector.from_engine(bind)
    tables = inspector.get_table_names()

    # 1. Create capacity_policies table
    if "capacity_policies" not in tables:
        op.create_table(
            "capacity_policies",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("workspace_id", sa.String(length=36), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, unique=True),
            sa.Column("discovery_limit_per_day", sa.Integer(), nullable=False, server_default="50"),
            sa.Column("research_limit_per_day", sa.Integer(), nullable=False, server_default="100"),
            sa.Column("llm_operations_limit_per_day", sa.Integer(), nullable=False, server_default="200"),
            sa.Column("outreach_limit_per_day", sa.Integer(), nullable=False, server_default="50"),
            sa.Column("sender_limit_per_day", sa.Integer(), nullable=False, server_default="30"),
            sa.Column("campaign_limit_per_day", sa.Integer(), nullable=False, server_default="10"),
            sa.Column("min_quality_floor", sa.String(length=50), nullable=False, server_default="TIER_1_ADVANCED"),
            sa.Column("timezone", sa.String(length=50), nullable=False, server_default="UTC"),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_capacity_policies_workspace_id", "capacity_policies", ["workspace_id"])

    # 2. Create workspace_capacity_daily_usages table
    if "workspace_capacity_daily_usages" not in tables:
        op.create_table(
            "workspace_capacity_daily_usages",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("workspace_id", sa.String(length=36), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
            sa.Column("usage_date", sa.Date(), nullable=False),
            sa.Column("discovery_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("research_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("llm_operations_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("outreach_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("sender_usage_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("campaign_usage_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("workspace_id", "usage_date", name="uq_workspace_capacity_date"),
        )
        op.create_index("ix_workspace_capacity_workspace_id", "workspace_capacity_daily_usages", ["workspace_id"])
        op.create_index("ix_workspace_capacity_usage_date", "workspace_capacity_daily_usages", ["usage_date"])

    # 3. Create capacity_decision_audits table
    if "capacity_decision_audits" not in tables:
        op.create_table(
            "capacity_decision_audits",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("workspace_id", sa.String(length=36), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
            sa.Column("operation_type", sa.String(length=50), nullable=False),
            sa.Column("requested_provider", sa.String(length=50), nullable=True),
            sa.Column("requested_model", sa.String(length=100), nullable=True),
            sa.Column("decision", sa.String(length=20), nullable=False),
            sa.Column("reason", sa.Text(), nullable=False),
            sa.Column("remaining_capacity", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("job_id", sa.String(length=128), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_capacity_audit_workspace_id", "capacity_decision_audits", ["workspace_id"])
        op.create_index("ix_capacity_audit_operation_type", "capacity_decision_audits", ["operation_type"])
        op.create_index("ix_capacity_audit_decision", "capacity_decision_audits", ["decision"])
        op.create_index("ix_capacity_audit_job_id", "capacity_decision_audits", ["job_id"])
        op.create_index("ix_capacity_audit_created_at", "capacity_decision_audits", ["created_at"])


def downgrade() -> None:
    op.drop_table("capacity_decision_audits")
    op.drop_table("workspace_capacity_daily_usages")
    op.drop_table("capacity_policies")
