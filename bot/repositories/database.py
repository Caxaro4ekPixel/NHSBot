from sqlalchemy import select, update, and_
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timezone
import json

from bot.models import Release, ReleaseAssignment, SentEpisode, User, UserRole, async_session_maker
from bot.core.logger import get_logger

logger = get_logger(__name__)


def get_current_season() -> Tuple[str, int]:
    m = datetime.now().month
    y = datetime.now().year
    
    if m in (1, 2, 3):
        s = "winter"
    elif m in (4, 5, 6):
        s = "spring"
    elif m in (7, 8, 9):
        s = "summer"
    else:
        s = "fall"
    
    return s, y


async def add_release(release_data: dict, chat_id: Optional[int] = None) -> bool:
    async with async_session_maker() as session:
        existing = await session.get(Release, release_data.get("id"))
        if existing:
            return False
        
        release = Release(
            id=release_data.get("id"),
            name=release_data.get("name"),
            name_ru=release_data.get("name_ru"),
            image=release_data.get("image"),
            kind=release_data.get("kind"),
            score=release_data.get("score"),
            status=release_data.get("status"),
            episodes=release_data.get("episodes"),
            episodes_aired=release_data.get("episodes_aired"),
            aired_on=release_data.get("aired_on"),
            released_on=release_data.get("released_on"),
            chat_id=chat_id,
            search_prefix=release_data.get("search_prefix", "[Erai-raws]"),
            is_completed=False
        )
        session.add(release)
        await session.commit()
        return True


async def get_release(release_id: int) -> Optional[Dict]:
    async with async_session_maker() as session:
        release = await session.get(Release, release_id)
        if not release:
            return None
        return release.to_dict()


async def get_all_releases() -> List[Dict]:
    async with async_session_maker() as session:
        result = await session.execute(select(Release).order_by(Release.name_ru))
        releases = result.scalars().all()
        return [r.to_dict() for r in releases]


async def get_incomplete_releases_with_chat() -> List[Dict]:
    async with async_session_maker() as session:
        result = await session.execute(
            select(Release).where(
                and_(
                    Release.is_completed == False,
                    Release.chat_id.isnot(None)
                )
            ).order_by(Release.aired_on)
        )
        releases = result.scalars().all()
        return [r.to_dict() for r in releases]


async def update_release_chat_id(release_id: int, chat_id: int) -> bool:
    async with async_session_maker() as session:
        release = await session.get(Release, release_id)
        if not release:
            logger.warning(f"Release {release_id} not found when updating chat_id")
            return False
        release.chat_id = chat_id
        release.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        await session.commit()
        logger.info(f"Updated chat_id={chat_id} for release_id={release_id}")
        return True


async def complete_release(release_id: int) -> bool:
    async with async_session_maker() as session:
        release = await session.get(Release, release_id)
        if not release:
            return False
        release.is_completed = True
        release.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
        await session.commit()
        return True


async def check_and_update_release_completion(release_id: int, episodes_aired: Optional[int], episodes: Optional[int]) -> bool:
    if episodes is None or episodes_aired is None:
        return False
    
    if episodes_aired >= episodes:
        return await complete_release(release_id)
    return False


async def save_assignment(release_id: int, assignment_data: dict) -> bool:
    async with async_session_maker() as session:
        existing = await session.execute(
            select(ReleaseAssignment).where(ReleaseAssignment.release_id == release_id)
        )
        existing_assignments = existing.scalars().all()
        
        if existing_assignments:
            logger.debug(f"Deleting {len(existing_assignments)} existing assignments for release_id={release_id}")
        
        for existing_assignment in existing_assignments:
            await session.delete(existing_assignment)
        
        role_mapping = {
            "translator": UserRole.TRANSLATOR,
            "voice": UserRole.VOICE,
            "timing": UserRole.TIMING,
            "curator": UserRole.CURATOR,
            "designer": UserRole.DESIGNER,
        }
        
        assignments_count = 0
        for role_name, role_enum in role_mapping.items():
            user_ids = assignment_data.get(role_name, [])
            if not isinstance(user_ids, list):
                user_ids = [user_ids] if user_ids is not None else []
            
            for telegram_id in user_ids:
                if telegram_id is not None:
                    user_result = await session.execute(
                        select(User).where(User.telegram_id == telegram_id).distinct()
                    )
                    users = user_result.scalars().all()
                    if users:
                        user = users[0]
                        assignment = ReleaseAssignment(
                            release_id=release_id,
                            user_id=user.id,
                            role=role_enum
                        )
                        session.add(assignment)
                        assignments_count += 1
                        logger.debug(f"Added assignment: release_id={release_id}, user_id={user.id}, role={role_enum.value}")
        
        await session.commit()
        logger.info(f"Saved assignment for release_id={release_id}: {assignments_count} assignments created")
        return True


async def get_assignment(release_id: int) -> Optional[Dict]:
    async with async_session_maker() as session:
        result = await session.execute(
            select(ReleaseAssignment).where(ReleaseAssignment.release_id == release_id)
        )
        assignments = result.scalars().all()
        
        if not assignments:
            return None
        
        assignment_dict = {
            "translator": [],
            "voice": [],
            "timing": None,
            "curator": None,
            "designer": []
        }
        
        for assignment in assignments:
            role_name = assignment.role.value
            user_result = await session.execute(
                select(User).where(User.id == assignment.user_id)
            )
            users = user_result.scalars().all()
            user = users[0] if users else None
            telegram_id = user.telegram_id if user else assignment.user_id
            
            if role_name in ["translator", "voice", "designer"]:
                assignment_dict[role_name].append(telegram_id)
            elif role_name == "timing":
                assignment_dict["timing"] = telegram_id
            elif role_name == "curator":
                assignment_dict["curator"] = telegram_id
        
        return assignment_dict


async def search_releases(query: str) -> List[Dict]:
    async with async_session_maker() as session:
        try:
            query_id = int(query)
        except ValueError:
            query_id = None
        
        if query_id:
            stmt = select(Release).where(
                (Release.name_ru.like(f"%{query}%")) |
                (Release.name.like(f"%{query}%")) |
                (Release.id == query_id)
            ).order_by(Release.name_ru)
        else:
            stmt = select(Release).where(
                (Release.name_ru.like(f"%{query}%")) |
                (Release.name.like(f"%{query}%"))
            ).order_by(Release.name_ru)
        
        result = await session.execute(stmt)
        releases = result.scalars().all()
        return [r.to_dict() for r in releases]


async def update_release_from_shikimori(release_id: int, shikimori_data: dict) -> bool:
    async with async_session_maker() as session:
        release = await session.get(Release, release_id)
        if not release:
            return False
        
        release.name = shikimori_data.get("name", release.name)
        release.name_ru = shikimori_data.get("name_ru", release.name_ru)
        release.image = shikimori_data.get("image", release.image)
        release.kind = shikimori_data.get("kind", release.kind)
        release.score = shikimori_data.get("score", release.score)
        release.status = shikimori_data.get("status", release.status)
        release.episodes = shikimori_data.get("episodes", release.episodes)
        release.episodes_aired = shikimori_data.get("episodes_aired", release.episodes_aired)
        release.aired_on = shikimori_data.get("aired_on", release.aired_on)
        release.released_on = shikimori_data.get("released_on", release.released_on)
        if "search_prefix" in shikimori_data:
            release.search_prefix = shikimori_data.get("search_prefix", release.search_prefix)
        release.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        
        if release.episodes and release.episodes_aired:
            if release.episodes_aired >= release.episodes:
                release.is_completed = True
                if not release.completed_at:
                    release.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
        
        await session.commit()
        return True


async def is_page_link_seen(page_link: str) -> bool:
    async with async_session_maker() as session:
        if not page_link:
            return False
        stmt = select(SentEpisode).where(SentEpisode.page_link == page_link)
        result = await session.execute(stmt)
        return result.scalar_one_or_none() is not None


async def is_episode_sent(release_id: int, episode: int, quality: int, torrent_url: str = None, magnet_link: str = None) -> bool:
    async with async_session_maker() as session:
        conditions = [
            SentEpisode.release_id == release_id,
            SentEpisode.episode == episode,
            SentEpisode.quality == quality
        ]
        
        if torrent_url:
            conditions.append(SentEpisode.torrent_url == torrent_url)
        elif magnet_link:
            conditions.append(SentEpisode.magnet_link == magnet_link)
        
        stmt = select(SentEpisode).where(and_(*conditions))
        result = await session.execute(stmt)
        return result.scalar_one_or_none() is not None


async def is_episode_fully_sent(release_id: int, episode: int, required_qualities: set) -> bool:
    async with async_session_maker() as session:
        stmt = select(SentEpisode.quality).where(
            and_(
                SentEpisode.release_id == release_id,
                SentEpisode.episode == episode
            )
        )
        result = await session.execute(stmt)
        sent_qualities = set(result.scalars().all())
        return required_qualities.issubset(sent_qualities)


async def mark_episode_sent(release_id: int, episode: int, quality: int, torrent_url: str = None, magnet_link: str = None, page_link: str = None) -> bool:
    async with async_session_maker() as session:
        existing = await session.execute(
            select(SentEpisode).where(
                and_(
                    SentEpisode.release_id == release_id,
                    SentEpisode.episode == episode,
                    SentEpisode.quality == quality
                )
            )
        )
        if existing.scalar_one_or_none():
            return False
        
        sent_episode = SentEpisode(
            release_id=release_id,
            episode=episode,
            quality=quality,
            torrent_url=torrent_url,
            magnet_link=magnet_link,
            page_link=page_link
        )
        session.add(sent_episode)
        await session.commit()
        return True


async def update_release_search_prefix(release_id: int, prefix: str) -> bool:
    async with async_session_maker() as session:
        release = await session.get(Release, release_id)
        if not release:
            return False
        release.search_prefix = prefix
        release.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        await session.commit()
        return True


async def get_user(user_id: int) -> Optional[Dict]:
    async with async_session_maker() as session:
        result = await session.execute(
            select(User).where(User.telegram_id == user_id)
        )
        users = result.scalars().all()
        if not users:
            return None
        return users[0].to_dict()


async def get_all_users() -> Dict[int, Dict]:
    async with async_session_maker() as session:
        result = await session.execute(select(User))
        users = result.scalars().all()
        return {(u.telegram_id or u.id): u.to_dict() for u in users}


async def get_users_by_role(role: str) -> Dict[int, Dict]:
    async with async_session_maker() as session:
        result = await session.execute(
            select(User).where(User.roles == role)
        )
        users = result.scalars().all()
        logger.debug(f"Found {len(users)} users with role '{role}' in users.roles")
        result_dict = {}
        for u in users:
            if u.telegram_id:
                result_dict[u.telegram_id] = u.to_dict()
        
        if not result_dict:
            logger.debug(f"No users found with role '{role}' in users.roles, showing all users as fallback")
            all_users_result = await session.execute(select(User))
            all_users = all_users_result.scalars().all()
            logger.debug(f"Total users in database: {len(all_users)}")
            for u in all_users:
                if u.telegram_id:
                    result_dict[u.telegram_id] = u.to_dict()
        
        logger.debug(f"get_users_by_role returning {len(result_dict)} users for role '{role}'")
        return result_dict


async def add_or_update_user(user_id: int, name: str, username: str = None, roles: List[str] = None) -> bool:
    async with async_session_maker() as session:
        result = await session.execute(
            select(User).where(User.telegram_id == user_id)
        )
        user = result.scalar_one_or_none()
        roles_json = json.dumps(roles or [])
        
        if user:
            user.name = name
            user.username = username
            user.roles = roles_json
            user.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        else:
            user = User(
                telegram_id=user_id,
                name=name,
                username=username,
                roles=roles_json
            )
            session.add(user)
        
        await session.commit()
        return True


async def migrate_users_from_file() -> int:
    try:
        from nhs.users import nhs_users
    except ImportError:
        return 0
    
    count = 0
    for user_id, user_data in nhs_users.items():
        await add_or_update_user(
            user_id=user_id,
            name=user_data.get("name", ""),
            username=user_data.get("username"),
            roles=user_data.get("role", [])
        )
        count += 1
    
    return count
