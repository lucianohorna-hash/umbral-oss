"""Initial persistent canon schema; immutable migration definition."""
from alembic import op
import sqlalchemy as sa

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('universes', sa.Column('id', sa.String(), primary_key=True), sa.Column('name', sa.String(), nullable=False), sa.Column('description', sa.Text(), nullable=False), sa.Column('version', sa.Integer(), nullable=False), sa.Column('is_demo', sa.Boolean(), nullable=False), sa.Column('created_at', sa.String(), nullable=False))
    op.create_table('sources', sa.Column('id', sa.String(), primary_key=True), sa.Column('universe_id', sa.String(), sa.ForeignKey('universes.id'), nullable=False), sa.Column('title', sa.String(), nullable=False), sa.Column('content', sa.Text(), nullable=False), sa.Column('sha256', sa.String(), nullable=False), sa.Column('created_at', sa.String(), nullable=False))
    op.create_table('records', sa.Column('id', sa.String(), primary_key=True), sa.Column('universe_id', sa.String(), sa.ForeignKey('universes.id'), nullable=False), sa.Column('kind', sa.String(), nullable=False), sa.Column('name', sa.String(), nullable=False), sa.Column('world_id', sa.String(), sa.ForeignKey('records.id')), sa.Column('campaign_id', sa.String(), sa.ForeignKey('records.id')), sa.Column('source_id', sa.String(), sa.ForeignKey('sources.id')), sa.Column('source_ref', sa.JSON(), nullable=False), sa.Column('data', sa.JSON(), nullable=False), sa.Column('revision', sa.Integer(), nullable=False), sa.Column('deleted', sa.Boolean(), nullable=False), sa.Column('created_at', sa.String(), nullable=False), sa.Column('updated_at', sa.String(), nullable=False))
    op.create_table('proposals', sa.Column('id', sa.String(), primary_key=True), sa.Column('universe_id', sa.String(), sa.ForeignKey('universes.id'), nullable=False), sa.Column('title', sa.String(), nullable=False), sa.Column('status', sa.String(), nullable=False), sa.Column('operation', sa.String(), nullable=False), sa.Column('target_id', sa.String()), sa.Column('record', sa.JSON(), nullable=False), sa.Column('base_version', sa.Integer(), nullable=False), sa.Column('issues', sa.JSON(), nullable=False), sa.Column('decision', sa.Text(), nullable=False), sa.Column('created_at', sa.String(), nullable=False), sa.Column('updated_at', sa.String(), nullable=False))
    op.create_table('revisions', sa.Column('id', sa.String(), primary_key=True), sa.Column('universe_id', sa.String(), sa.ForeignKey('universes.id'), nullable=False), sa.Column('version', sa.Integer(), nullable=False), sa.Column('summary', sa.Text(), nullable=False), sa.Column('actor', sa.String(), nullable=False), sa.Column('proposal_id', sa.String(), sa.ForeignKey('proposals.id')), sa.Column('before', sa.JSON(), nullable=False), sa.Column('after', sa.JSON(), nullable=False), sa.Column('created_at', sa.String(), nullable=False), sa.UniqueConstraint('universe_id', 'version'))
    op.create_table('turns', sa.Column('id', sa.String(), primary_key=True), sa.Column('universe_id', sa.String(), sa.ForeignKey('universes.id'), nullable=False), sa.Column('campaign_id', sa.String(), sa.ForeignKey('records.id'), nullable=False), sa.Column('protagonist_id', sa.String(), sa.ForeignKey('records.id'), nullable=False), sa.Column('role', sa.String(), nullable=False), sa.Column('content', sa.Text(), nullable=False), sa.Column('proposal_id', sa.String(), sa.ForeignKey('proposals.id')), sa.Column('canon_version', sa.Integer(), nullable=False), sa.Column('created_at', sa.String(), nullable=False))
    for table, columns in {'sources': ['universe_id'], 'records': ['universe_id', 'kind', 'world_id', 'campaign_id'], 'proposals': ['universe_id'], 'revisions': ['universe_id'], 'turns': ['universe_id', 'campaign_id']}.items():
        for column in columns:
            op.create_index(f'ix_{table}_{column}', table, [column])


def downgrade():
    raise RuntimeError('No se permite un downgrade destructivo. Restaure una copia verificada.')
