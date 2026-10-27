"""Keep PostgreSQL application tables private; local SQLite has no public API roles."""
from alembic import op
import sqlalchemy as sa

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name != 'postgresql':
        return
    names = ['universes', 'sources', 'records', 'proposals', 'revisions', 'turns', 'alembic_version']
    roles = set(bind.execute(sa.text("SELECT rolname FROM pg_roles WHERE rolname IN ('anon', 'authenticated')")).scalars())
    for name in names:
        op.execute(f'ALTER TABLE narrative_core."{name}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'REVOKE ALL ON TABLE narrative_core."{name}" FROM PUBLIC')
        for role in roles:
            op.execute(f'REVOKE ALL ON TABLE narrative_core."{name}" FROM "{role}"')
    for role in roles:
        op.execute(f'REVOKE ALL ON SCHEMA narrative_core FROM "{role}"')


def downgrade():
    raise RuntimeError('No se permite retirar las protecciones mediante downgrade.')
