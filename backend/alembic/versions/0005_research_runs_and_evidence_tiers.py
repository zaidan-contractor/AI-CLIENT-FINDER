"""0005_research_runs_and_evidence_tiers

Revision ID: 0005_research
Revises: 0004_cap_account
Create Date: 2026-09-18 21:55:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine.reflection import Inspector


# revision identifiers, used by Alembic.
revision: str = '0005_research'
down_revision: Union[str, Sequence[str], None] = '0004_cap_account'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = Inspector.from_engine(bind)
    tables = inspector.get_table_names()

    if 'research_runs' not in tables:
        op.create_table(
            'research_runs',
            sa.Column('id', sa.String(36), primary_key=True),
            sa.Column('workspace_id', sa.String(36), sa.ForeignKey('workspaces.id', ondelete='CASCADE'), nullable=False, index=True),
            sa.Column('business_id', sa.String(36), sa.ForeignKey('master_business_records.id', ondelete='CASCADE'), nullable=False, index=True),
            sa.Column('status', sa.String(50), nullable=False, server_default='QUEUED', index=True),
            sa.Column('trigger_type', sa.String(50), nullable=False, server_default='MANUAL'),
            sa.Column('requested_scope', sa.JSON(), nullable=False, server_default='[]'),
            sa.Column('evidence_count', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('error_message', sa.Text(), nullable=True),
            sa.Column('model_name', sa.String(100), nullable=True),
            sa.Column('prompt_tokens', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('completion_tokens', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('estimated_cost_usd', sa.Numeric(10, 6), nullable=False, server_default='0.000000'),
            sa.Column('idempotency_key', sa.String(255), nullable=True, index=True),
            sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()'))
        )

    ev_cols = [c['name'] for c in inspector.get_columns('evidence_records')]
    if 'research_run_id' not in ev_cols:
        op.add_column(
            'evidence_records',
            sa.Column('research_run_id', sa.String(36), sa.ForeignKey('research_runs.id', ondelete='SET NULL'), nullable=True)
        )
        op.create_index('ix_evidence_records_research_run_id', 'evidence_records', ['research_run_id'])

    if 'content_hash' not in ev_cols:
        op.add_column(
            'evidence_records',
            sa.Column('content_hash', sa.String(64), nullable=True)
        )
        op.create_index('ix_evidence_records_content_hash', 'evidence_records', ['content_hash'])

    if 'epistemic_tier' not in ev_cols:
        op.add_column(
            'evidence_records',
            sa.Column('epistemic_tier', sa.String(50), server_default='FACT', nullable=False)
        )
        op.create_index('ix_evidence_records_epistemic_tier', 'evidence_records', ['epistemic_tier'])

    biz_cols = [c['name'] for c in inspector.get_columns('master_business_records')]
    if 'normalized_phone' not in biz_cols:
        op.add_column(
            'master_business_records',
            sa.Column('normalized_phone', sa.String(50), nullable=True)
        )
        op.create_index('ix_master_business_records_normalized_phone', 'master_business_records', ['normalized_phone'])

    if 'possible_duplicate_of_id' not in biz_cols:
        op.add_column(
            'master_business_records',
            sa.Column('possible_duplicate_of_id', sa.String(36), nullable=True)
        )
        op.create_index('ix_master_business_records_possible_duplicate_of_id', 'master_business_records', ['possible_duplicate_of_id'])

    if 'identity_confidence' not in biz_cols:
        op.add_column(
            'master_business_records',
            sa.Column('identity_confidence', sa.Float(), server_default='1.0', nullable=False)
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = Inspector.from_engine(bind)
    tables = inspector.get_table_names()

    biz_cols = [c['name'] for c in inspector.get_columns('master_business_records')]
    if 'identity_confidence' in biz_cols:
        op.drop_column('master_business_records', 'identity_confidence')
    if 'possible_duplicate_of_id' in biz_cols:
        op.drop_index('ix_master_business_records_possible_duplicate_of_id', 'master_business_records')
        op.drop_column('master_business_records', 'possible_duplicate_of_id')
    if 'normalized_phone' in biz_cols:
        op.drop_index('ix_master_business_records_normalized_phone', 'master_business_records')
        op.drop_column('master_business_records', 'normalized_phone')

    ev_cols = [c['name'] for c in inspector.get_columns('evidence_records')]
    if 'epistemic_tier' in ev_cols:
        op.drop_index('ix_evidence_records_epistemic_tier', 'evidence_records')
        op.drop_column('evidence_records', 'epistemic_tier')
    if 'content_hash' in ev_cols:
        op.drop_index('ix_evidence_records_content_hash', 'evidence_records')
        op.drop_column('evidence_records', 'content_hash')
    if 'research_run_id' in ev_cols:
        op.drop_index('ix_evidence_records_research_run_id', 'evidence_records')
        op.drop_column('evidence_records', 'research_run_id')

    if 'research_runs' in tables:
        op.drop_table('research_runs')
