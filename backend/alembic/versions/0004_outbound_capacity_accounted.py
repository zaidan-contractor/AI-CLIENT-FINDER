"""0004_outbound_capacity_accounted

Revision ID: 0004_cap_account
Revises: 0003_capacity
Create Date: 2026-09-18 21:35:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine.reflection import Inspector


# revision identifiers, used by Alembic.
revision: str = '0004_cap_account'
down_revision: Union[str, Sequence[str], None] = '0003_capacity'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = Inspector.from_engine(bind)
    columns = [c['name'] for c in inspector.get_columns('outbound_messages')]
    if 'capacity_accounted' not in columns:
        op.add_column(
            'outbound_messages',
            sa.Column('capacity_accounted', sa.Boolean(), server_default=sa.text('false'), nullable=False)
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = Inspector.from_engine(bind)
    columns = [c['name'] for c in inspector.get_columns('outbound_messages')]
    if 'capacity_accounted' in columns:
        op.drop_column('outbound_messages', 'capacity_accounted')
