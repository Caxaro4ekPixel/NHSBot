"""Add staging_posts table

Revision ID: a1b2c3d4e5f6
Revises: e5f6a7b8c9d0
Create Date: 2026-06-28

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'e5f6a7b8c9d0'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'staging_posts',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('release_id', sa.Integer(), nullable=False),
        sa.Column('episode', sa.Integer(), nullable=False),
        sa.Column('group_id', sa.BigInteger(), nullable=False),
        sa.Column('topic_id', sa.Integer(), nullable=False),
        sa.Column('channel_id', sa.BigInteger(), nullable=True),
        sa.Column('staging_mp4_id', sa.Integer(), nullable=True),
        sa.Column('staging_mkv_id', sa.Integer(), nullable=True),
        sa.Column('staging_channel_msg_id', sa.Integer(), nullable=True),
        sa.Column('status', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
    )
    op.create_index('idx_staging_posts_status', 'staging_posts', ['status'])
    op.create_index('idx_staging_posts_release', 'staging_posts', ['release_id', 'episode'])


def downgrade() -> None:
    op.drop_index('idx_staging_posts_release', 'staging_posts')
    op.drop_index('idx_staging_posts_status', 'staging_posts')
    op.drop_table('staging_posts')
