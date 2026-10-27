"""Private, revocable, universe-scoped machine grants; never narrative canon."""
from alembic import op
import sqlalchemy as sa

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('bridge_grants',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('universe_id', sa.String(), sa.ForeignKey('universes.id'), nullable=False),
        sa.Column('label', sa.String(), nullable=False),
        sa.Column('token_hash', sa.String(64), nullable=False),
        sa.Column('created_at', sa.String(), nullable=False),
        sa.Column('expires_at', sa.String(), nullable=False),
        sa.Column('revoked_at', sa.String(), nullable=True))
    op.create_index('ix_bridge_grants_universe_id', 'bridge_grants', ['universe_id'])
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        op.execute('ALTER TABLE narrative_core.bridge_grants ENABLE ROW LEVEL SECURITY')
        op.execute('REVOKE ALL ON TABLE narrative_core.bridge_grants FROM PUBLIC')
        roles = set(bind.execute(sa.text("SELECT rolname FROM pg_roles WHERE rolname IN ('anon', 'authenticated')")).scalars())
        for role in roles:
            op.execute(f'REVOKE ALL ON TABLE narrative_core.bridge_grants FROM "{role}"')


def downgrade():
    raise RuntimeError('No se permite retirar las protecciones mediante downgrade.')
