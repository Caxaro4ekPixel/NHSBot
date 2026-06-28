from typing import Optional, Dict, List
from sqlalchemy import select, and_, desc
from bot.models import ReleaseTopic, TopicFile, ReleasePost, User, ReleaseAssignment, Release, StagingPost, async_session_maker
from bot.core.logger import get_logger

logger = get_logger(__name__)


async def get_release_by_topic(group_id: int, topic_id: int) -> Optional[dict]:
    """Find release linked to a group topic."""
    async with async_session_maker() as session:
        rt = await session.execute(
            select(ReleaseTopic).where(
                and_(ReleaseTopic.group_id == group_id, ReleaseTopic.topic_id == topic_id)
            )
        )
        rt_row = rt.scalar_one_or_none()
        if not rt_row:
            return None
        release = await session.get(Release, rt_row.release_id)
        return release.to_dict() if release else None


async def set_release_topic(release_id: int, group_id: int, topic_id: int) -> bool:
    """Link a release to a group topic. Upsert."""
    async with async_session_maker() as session:
        existing = await session.execute(
            select(ReleaseTopic).where(
                and_(ReleaseTopic.group_id == group_id, ReleaseTopic.topic_id == topic_id)
            )
        )
        row = existing.scalar_one_or_none()
        if row:
            row.release_id = release_id
        else:
            session.add(ReleaseTopic(release_id=release_id, group_id=group_id, topic_id=topic_id))
        await session.commit()
        return True


async def save_topic_file(
    release_id: int, group_id: int, topic_id: int, message_id: int,
    file_type: str, file_id: str, file_name: Optional[str], file_size: Optional[int]
) -> bool:
    """Record a file uploaded to a topic."""
    async with async_session_maker() as session:
        session.add(TopicFile(
            release_id=release_id,
            group_id=group_id,
            topic_id=topic_id,
            message_id=message_id,
            file_type=file_type,
            file_id=file_id,
            file_name=file_name,
            file_size=file_size,
        ))
        await session.commit()
        return True


async def get_topic_for_release_in_group(release_id: int, group_id: int) -> Optional[int]:
    """Get topic_id linked to a release in a specific group."""
    async with async_session_maker() as session:
        result = await session.execute(
            select(ReleaseTopic.topic_id).where(
                and_(ReleaseTopic.release_id == release_id, ReleaseTopic.group_id == group_id)
            )
        )
        row = result.fetchone()
        return row[0] if row else None


async def get_latest_topic_files(release_id: int, topic_id: int) -> Dict[str, dict]:
    """Get the most recent file of each type for a topic."""
    async with async_session_maker() as session:
        result = await session.execute(
            select(TopicFile).where(
                and_(TopicFile.release_id == release_id, TopicFile.topic_id == topic_id)
            ).order_by(desc(TopicFile.created_at))
        )
        rows = result.scalars().all()
        latest: Dict[str, dict] = {}
        for row in rows:
            if row.file_type not in latest:
                latest[row.file_type] = {
                    "file_id": row.file_id,
                    "file_name": row.file_name,
                    "file_size": row.file_size,
                    "message_id": row.message_id,
                    "group_id": row.group_id,
                }
        return latest


async def save_release_post(
    release_id: int, episode: int,
    group_id: int, topic_id: int,
    mp4_msg_id: Optional[int], mkv_msg_id: Optional[int],
    channel_id: Optional[int], channel_msg_id: Optional[int],
) -> bool:
    async with async_session_maker() as session:
        session.add(ReleasePost(
            release_id=release_id, episode=episode,
            group_id=group_id, topic_id=topic_id,
            group_mp4_message_id=mp4_msg_id,
            group_mkv_message_id=mkv_msg_id,
            channel_id=channel_id,
            channel_message_id=channel_msg_id,
        ))
        await session.commit()
        return True


async def set_release_tags(release_id: int, tags: str) -> bool:
    async with async_session_maker() as session:
        release = await session.get(Release, release_id)
        if not release:
            return False
        release.custom_tags = tags
        await session.commit()
        return True


async def set_release_file_prefix(release_id: int, prefix: str) -> bool:
    async with async_session_maker() as session:
        release = await session.get(Release, release_id)
        if not release:
            return False
        release.file_prefix = prefix
        await session.commit()
        return True


async def save_staging_post(
    release_id: int, episode: int,
    group_id: int, topic_id: int, channel_id: Optional[int],
    staging_mp4_id: Optional[int], staging_mkv_id: Optional[int],
    staging_channel_msg_id: Optional[int],
) -> int:
    async with async_session_maker() as session:
        post = StagingPost(
            release_id=release_id, episode=episode,
            group_id=group_id, topic_id=topic_id, channel_id=channel_id,
            staging_mp4_id=staging_mp4_id, staging_mkv_id=staging_mkv_id,
            staging_channel_msg_id=staging_channel_msg_id, status="pending",
        )
        session.add(post)
        await session.commit()
        await session.refresh(post)
        return post.id


async def get_staging_post(staging_post_id: int) -> Optional[dict]:
    async with async_session_maker() as session:
        post = await session.get(StagingPost, staging_post_id)
        return post.to_dict() if post else None


async def mark_staging_published(staging_post_id: int) -> None:
    async with async_session_maker() as session:
        post = await session.get(StagingPost, staging_post_id)
        if post:
            post.status = "published"
            await session.commit()


async def get_release_credits(release_id: int) -> Dict[str, List[dict]]:
    """Returns {role_uppercase: [{name, channel_url, username}]} for a release."""
    async with async_session_maker() as session:
        result = await session.execute(
            select(ReleaseAssignment).where(ReleaseAssignment.release_id == release_id)
        )
        assignments = result.scalars().all()
        credits: Dict[str, List[dict]] = {}
        for a in assignments:
            role_key = a.role.name.upper()
            user = await session.get(User, a.user_id)
            if user:
                credits.setdefault(role_key, []).append({
                    "name": user.name,
                    "channel_url": user.channel_url,
                    "username": user.username,
                })
        return credits
