"""Private encrypted runtime sessions, excluded from portable canon."""
from alembic import op
import sqlalchemy as sa

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('runtime_secrets',
        sa.Column('name', sa.String(), primary_key=True),
        sa.Column('host_id', sa.String(), nullable=False),
        sa.Column('sealed', sa.Text(), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False))
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        op.execute('ALTER TABLE narrative_core.runtime_secrets ENABLE ROW LEVEL SECURITY')
        op.execute('REVOKE ALL ON TABLE narrative_core.runtime_secrets FROM PUBLIC')
        roles = set(bind.execute(sa.text("SELECT rolname FROM pg_roles WHERE rolname IN ('anon', 'authenticated')")).scalars())
        for role in roles:
            op.execute(f'REVOKE ALL ON TABLE narrative_core.runtime_secrets FROM "{role}"')


def downgrade():
    raise RuntimeError('No se permite retirar la protección de sesiones mediante downgrade.')
