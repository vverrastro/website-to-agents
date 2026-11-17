"""initial schema

Revision ID: 0001
Revises: 
Create Date: 2026-11-17 23:37:46.039505

"""
from typing import Sequence, Union

from alembic import op
import pgvector.sqlalchemy
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '0001'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.create_table('embedding_spaces',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('provider', sa.String(length=50), nullable=False),
    sa.Column('model', sa.String(length=100), nullable=False),
    sa.Column('dimensions', sa.Integer(), nullable=False),
    sa.Column('config', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('provider', 'model', 'dimensions', name='uq_embedding_spaces')
    )
    op.create_index('uq_embedding_spaces_one_active', 'embedding_spaces', ['active'], unique=True, postgresql_where='active')
    op.create_table('workspaces',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('agents',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('workspace_id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('public_id', sa.String(length=64), nullable=False),
    sa.Column('published', sa.Boolean(), nullable=False),
    sa.Column('allowed_origins', sa.ARRAY(sa.String(length=300)), nullable=False),
    sa.Column('instructions', sa.Text(), nullable=True),
    sa.Column('crawl_config', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('public_id')
    )
    op.create_index('ix_agents_workspace', 'agents', ['workspace_id'], unique=False)
    op.create_table('sources',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('workspace_id', sa.UUID(), nullable=False),
    sa.Column('agent_id', sa.UUID(), nullable=False),
    sa.Column('url', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['agent_id'], ['agents.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('agent_id', 'url', name='uq_sources_agent_url')
    )
    op.create_table('crawl_runs',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('workspace_id', sa.UUID(), nullable=False),
    sa.Column('agent_id', sa.UUID(), nullable=False),
    sa.Column('source_id', sa.UUID(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('stats', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['agent_id'], ['agents.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['source_id'], ['sources.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_crawl_runs_agent', 'crawl_runs', ['agent_id', 'created_at'], unique=False)
    op.create_table('pages',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('workspace_id', sa.UUID(), nullable=False),
    sa.Column('agent_id', sa.UUID(), nullable=False),
    sa.Column('source_id', sa.UUID(), nullable=False),
    sa.Column('url', sa.Text(), nullable=False),
    sa.Column('canonical_url', sa.Text(), nullable=True),
    sa.Column('render_mode', sa.String(length=10), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('etag', sa.Text(), nullable=True),
    sa.Column('content_hash', sa.String(length=64), nullable=True),
    sa.Column('last_seen_in_sitemap', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_crawled_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('origin_last_modified', sa.DateTime(timezone=True), nullable=True),
    sa.Column('indexed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['agent_id'], ['agents.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['source_id'], ['sources.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('agent_id', 'url', name='uq_pages_agent_url')
    )
    op.create_index('ix_pages_workspace', 'pages', ['workspace_id'], unique=False)
    op.create_table('page_versions',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('workspace_id', sa.UUID(), nullable=False),
    sa.Column('agent_id', sa.UUID(), nullable=False),
    sa.Column('page_id', sa.UUID(), nullable=False),
    sa.Column('content_hash', sa.String(length=64), nullable=False),
    sa.Column('raw_html_gz', sa.LargeBinary(), nullable=True),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('title', sa.Text(), nullable=True),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('meta', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['agent_id'], ['agents.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['page_id'], ['pages.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('page_id', 'content_hash', name='uq_page_versions_page_hash')
    )
    op.create_table('skipped_urls',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('workspace_id', sa.UUID(), nullable=False),
    sa.Column('agent_id', sa.UUID(), nullable=False),
    sa.Column('crawl_run_id', sa.UUID(), nullable=False),
    sa.Column('url', sa.Text(), nullable=False),
    sa.Column('reason', sa.String(length=100), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['agent_id'], ['agents.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['crawl_run_id'], ['crawl_runs.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_skipped_urls_run', 'skipped_urls', ['crawl_run_id'], unique=False)
    op.create_table('chunks',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('workspace_id', sa.UUID(), nullable=False),
    sa.Column('agent_id', sa.UUID(), nullable=False),
    sa.Column('page_id', sa.UUID(), nullable=False),
    sa.Column('page_version_id', sa.UUID(), nullable=False),
    sa.Column('chunk_index', sa.Integer(), nullable=False),
    sa.Column('heading_path', sa.ARRAY(sa.Text()), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('content_hash', sa.String(length=64), nullable=False),
    sa.Column('tsv', postgresql.TSVECTOR(), sa.Computed("to_tsvector('simple', text)", persisted=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['agent_id'], ['agents.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['page_id'], ['pages.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['page_version_id'], ['page_versions.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('page_version_id', 'chunk_index', name='uq_chunks_version_index')
    )
    op.create_index('ix_chunks_agent', 'chunks', ['agent_id'], unique=False)
    op.create_index('ix_chunks_hash', 'chunks', ['agent_id', 'content_hash'], unique=False)
    op.create_index('ix_chunks_tsv', 'chunks', ['tsv'], unique=False, postgresql_using='gin')
    op.create_table('chunk_embeddings',
    sa.Column('chunk_id', sa.UUID(), nullable=False),
    sa.Column('embedding_space_id', sa.UUID(), nullable=False),
    sa.Column('agent_id', sa.UUID(), nullable=False),
    sa.Column('embedding', pgvector.sqlalchemy.vector.VECTOR(dim=1024), nullable=False),
    sa.ForeignKeyConstraint(['agent_id'], ['agents.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['chunk_id'], ['chunks.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['embedding_space_id'], ['embedding_spaces.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('chunk_id', 'embedding_space_id')
    )
    op.create_index('ix_chunk_embeddings_agent_space', 'chunk_embeddings', ['agent_id', 'embedding_space_id'], unique=False)
    op.create_index('ix_chunk_embeddings_hnsw', 'chunk_embeddings', ['embedding'], unique=False, postgresql_using='hnsw', postgresql_ops={'embedding': 'vector_cosine_ops'})
    # ### end Alembic commands ###


def downgrade() -> None:
    """Downgrade schema."""
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_index('ix_chunk_embeddings_hnsw', table_name='chunk_embeddings', postgresql_using='hnsw', postgresql_ops={'embedding': 'vector_cosine_ops'})
    op.drop_index('ix_chunk_embeddings_agent_space', table_name='chunk_embeddings')
    op.drop_table('chunk_embeddings')
    op.drop_index('ix_chunks_tsv', table_name='chunks', postgresql_using='gin')
    op.drop_index('ix_chunks_hash', table_name='chunks')
    op.drop_index('ix_chunks_agent', table_name='chunks')
    op.drop_table('chunks')
    op.drop_index('ix_skipped_urls_run', table_name='skipped_urls')
    op.drop_table('skipped_urls')
    op.drop_table('page_versions')
    op.drop_index('ix_pages_workspace', table_name='pages')
    op.drop_table('pages')
    op.drop_index('ix_crawl_runs_agent', table_name='crawl_runs')
    op.drop_table('crawl_runs')
    op.drop_table('sources')
    op.drop_index('ix_agents_workspace', table_name='agents')
    op.drop_table('agents')
    op.drop_table('workspaces')
    op.drop_index('uq_embedding_spaces_one_active', table_name='embedding_spaces', postgresql_where='active')
    op.drop_table('embedding_spaces')
    # ### end Alembic commands ###
