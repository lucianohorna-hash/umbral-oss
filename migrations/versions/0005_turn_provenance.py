"""Preserve external message origin and the owner's attribution review."""
from alembic import op
import sqlalchemy as sa

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('turns', sa.Column('provenance', sa.JSON(), nullable=False, server_default='{}'))


def downgrade():
    raise RuntimeError('No se permite eliminar la trazabilidad de las intervenciones.')
