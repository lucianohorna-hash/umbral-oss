"""Index provenance and protagonist references without changing canon data."""
from alembic import op

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade():
    for table, column in [
        ('records', 'source_id'),
        ('revisions', 'proposal_id'),
        ('turns', 'proposal_id'),
        ('turns', 'protagonist_id'),
    ]:
        op.create_index(f'ix_{table}_{column}', table, [column])


def downgrade():
    raise RuntimeError('Restaure una copia verificada para volver a una versión anterior.')
