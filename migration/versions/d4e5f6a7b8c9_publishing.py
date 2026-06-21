"""Add publishing tables and channel_url to users

Revision ID: d4e5f6a7b8c9
Revises: 1e7f0ca6b127
Create Date: 2026-06-21

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, Sequence[str], None] = '1e7f0ca6b127'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('channel_url', sa.String(), nullable=True))

    op.create_table(
        'release_topics',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('release_id', sa.Integer(), nullable=False),
        sa.Column('group_id', sa.BigInteger(), nullable=False),
        sa.Column('topic_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['release_id'], ['releases.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_release_topics_group_topic', 'release_topics', ['group_id', 'topic_id'], unique=True)

    op.create_table(
        'topic_files',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('release_id', sa.Integer(), nullable=False),
        sa.Column('group_id', sa.BigInteger(), nullable=False),
        sa.Column('topic_id', sa.Integer(), nullable=False),
        sa.Column('message_id', sa.Integer(), nullable=False),
        sa.Column('file_type', sa.String(), nullable=False),
        sa.Column('file_id', sa.String(), nullable=False),
        sa.Column('file_name', sa.String(), nullable=True),
        sa.Column('file_size', sa.BigInteger(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['release_id'], ['releases.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_topic_files_release_topic', 'topic_files', ['release_id', 'topic_id'])

    op.create_table(
        'release_posts',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('release_id', sa.Integer(), nullable=False),
        sa.Column('episode', sa.Integer(), nullable=False),
        sa.Column('group_id', sa.BigInteger(), nullable=True),
        sa.Column('topic_id', sa.Integer(), nullable=True),
        sa.Column('group_mp4_message_id', sa.Integer(), nullable=True),
        sa.Column('group_mkv_message_id', sa.Integer(), nullable=True),
        sa.Column('channel_id', sa.BigInteger(), nullable=True),
        sa.Column('channel_message_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['release_id'], ['releases.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_release_posts_release_episode', 'release_posts', ['release_id', 'episode'])


def downgrade() -> None:
    op.drop_table('release_posts')
    op.drop_table('topic_files')
    op.drop_table('release_topics')
    op.drop_column('users', 'channel_url')
