"""0001_initial_schema

Revision ID: d011cc78158f
Revises: 
Create Date: 2026-09-18 15:12:04.710307

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd011cc78158f'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


from app.core.database import Base
import app.models.tenancy
import app.models.business
import app.models.research
import app.models.opportunity
import app.models.campaign
import app.models.outreach
import app.models.inbound
import app.models.learning

def upgrade() -> None:
    """Upgrade schema."""
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    """Downgrade schema."""
    Base.metadata.drop_all(bind=op.get_bind())
