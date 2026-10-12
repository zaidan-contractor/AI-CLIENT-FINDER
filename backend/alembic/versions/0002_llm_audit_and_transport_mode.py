"""0002_llm_audit_and_transport_mode

Revision ID: 0002_llm_audit
Revises: d011cc78158f
Create Date: 2026-09-18 20:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine.reflection import Inspector


# revision identifiers, used by Alembic.
revision: str = '0002_llm_audit'
down_revision: Union[str, Sequence[str], None] = 'd011cc78158f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = Inspector.from_engine(bind)

    # 1. Add transport_mode column to outbound_messages if not already present
    outbound_cols = [c["name"] for c in inspector.get_columns("outbound_messages")]
    if "transport_mode" not in outbound_cols:
        op.add_column(
            "outbound_messages",
            sa.Column("transport_mode", sa.String(length=20), nullable=True, server_default="REAL")
        )

    # 2. Create ai_action_audit_ledger table
    tables = inspector.get_table_names()
    if "ai_action_audit_ledger" not in tables:
        op.create_table(
            "ai_action_audit_ledger",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("workspace_id", sa.String(length=36), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
            sa.Column("actor_id", sa.String(length=36), nullable=False),
            sa.Column("actor_type", sa.String(length=50), nullable=False, server_default="SYSTEM"),
            sa.Column("provider", sa.String(length=50), nullable=False),
            sa.Column("model_name", sa.String(length=100), nullable=False),
            sa.Column("purpose", sa.String(length=100), nullable=False),
            sa.Column("input_hash", sa.String(length=64), nullable=False),
            sa.Column("output_schema_name", sa.String(length=100), nullable=False),
            sa.Column("status", sa.String(length=50), nullable=False),
            sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("estimated_cost_usd", sa.Numeric(precision=10, scale=6), nullable=False, server_default="0.000000"),
            sa.Column("is_cost_estimated", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("action_proposed", sa.JSON(), nullable=True),
            sa.Column("action_executed", sa.JSON(), nullable=True),
            sa.Column("approval_state", sa.String(length=50), nullable=False, server_default="AUTO_GATED"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_ai_audit_workspace_id", "ai_action_audit_ledger", ["workspace_id"])
        op.create_index("ix_ai_audit_actor_id", "ai_action_audit_ledger", ["actor_id"])
        op.create_index("ix_ai_audit_purpose", "ai_action_audit_ledger", ["purpose"])
        op.create_index("ix_ai_audit_input_hash", "ai_action_audit_ledger", ["input_hash"])
        op.create_index("ix_ai_audit_status", "ai_action_audit_ledger", ["status"])
        op.create_index("ix_ai_audit_approval_state", "ai_action_audit_ledger", ["approval_state"])
        op.create_index("ix_ai_audit_created_at", "ai_action_audit_ledger", ["created_at"])

    # 3. Create ai_workspace_daily_costs table
    if "ai_workspace_daily_costs" not in tables:
        op.create_table(
            "ai_workspace_daily_costs",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("workspace_id", sa.String(length=36), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
            sa.Column("usage_date", sa.Date(), nullable=False),
            sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("total_cost_usd", sa.Numeric(precision=10, scale=4), nullable=False, server_default="0.0000"),
            sa.Column("run_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("workspace_id", "usage_date", name="uq_workspace_ai_usage_date"),
        )
        op.create_index("ix_ai_daily_costs_workspace_id", "ai_workspace_daily_costs", ["workspace_id"])
        op.create_index("ix_ai_daily_costs_usage_date", "ai_workspace_daily_costs", ["usage_date"])


def downgrade() -> None:
    op.drop_table("ai_workspace_daily_costs")
    op.drop_table("ai_action_audit_ledger")
    op.drop_column("outbound_messages", "transport_mode")
