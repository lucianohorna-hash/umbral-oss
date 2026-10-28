"""Private rolling recovery points; no authentication material."""
from alembic import op
import sqlalchemy as sa
revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('recovery_points',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('universe_id', sa.String(), sa.ForeignKey('universes.id'), nullable=False),
        sa.Column('bucket', sa.String(), nullable=False),
        sa.Column('created_at', sa.String(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('sha256', sa.String(64), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.UniqueConstraint('universe_id', 'bucket'))
    op.create_index('ix_recovery_points_universe_id', 'recovery_points', ['universe_id'])
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        op.execute('ALTER TABLE narrative_core.recovery_points ENABLE ROW LEVEL SECURITY')
        op.execute('REVOKE ALL ON TABLE narrative_core.recovery_points FROM PUBLIC')
        roles = set(bind.execute(sa.text("SELECT rolname FROM pg_roles WHERE rolname IN ('anon', 'authenticated')")).scalars())
        for role in roles:
            op.execute(f'REVOKE ALL ON TABLE narrative_core.recovery_points FROM "{role}"')


def downgrade():
    raise RuntimeError('Los puntos de recuperación requieren conservación explícita.')
