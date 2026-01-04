from datetime import datetime
from zoneinfo import ZoneInfo
from typing import List

import aiohttp

from bot.database import (
    get_all_releases, get_release, add_release as db_add_release,
    save_assignment as db_save_assignment, get_assignment as db_get_assignment,
    search_releases as db_search_releases, update_release_chat_id as db_update_release_chat_id,
    get_all_users, get_users_by_role, get_user
)
from bot.logger import get_logger

logger = get_logger(__name__)

WEEKDAY_RU_SHORT = ["ПН", "ВТ", "СР", "ЧТ", "ПТ", "СБ", "ВС"]


async def load_reales():
    return await get_all_releases()


async def add_release(release_data: dict, chat_id: int = None) -> bool:
    result = await db_add_release(release_data, chat_id)
    if result:
        logger.info(
            f"Added release: id={release_data.get('id')}, name={release_data.get('name_ru')}, chat_id={chat_id}")
    else:
        logger.warning(f"Release already exists: id={release_data.get('id')}")
    return result


async def save_assignment(release_id: str, data: dict):
    result = await db_save_assignment(int(release_id), data)
    if result:
        logger.info(f"Saved assignment for release_id={release_id}: {data}")
    return result


async def search_reales(rid: str, data: List[dict] = None):
    if data is None:
        release = await get_release(int(rid))
        return release
    for a in data:
        if str(a["id"]) == rid:
            return a
    return None


async def load_assignment(release_id: str):
    return await db_get_assignment(int(release_id))


async def update_release_chat_id(release_id: int, chat_id: int) -> bool:
    return await db_update_release_chat_id(release_id, chat_id)


async def users_with(role: str):
    return await get_users_by_role(role)


async def fmt_users(uids):
    if not uids:
        return "—"
    result = []
    all_users = await get_all_users()
    for uid in uids:
        user = all_users.get(uid, {})
        name = user.get("name", "")
        username = user.get("username")
        if username:
            result.append(f"{name} ({username})")
        else:
            result.append(name)
    return ", ".join(result)


def build_chat_title(ru: str):
    tz = ZoneInfo("Europe/Moscow")
    wd = WEEKDAY_RU_SHORT[datetime.now(tz).weekday()]
    return f"[{wd}] {ru}"


def collect_member_ids(assignment: dict):
    ids = set()
    for k in ("translator", "voice", "designer"):
        for uid in assignment.get(k, []):
            ids.add(int(uid))
    for k in ("timing", "curator"):
        v = assignment.get(k)
        if v is not None:
            ids.add(int(v))
    return list(ids)


async def mention_html(uid: int):
    user = await get_user(uid)
    name = user.get("name") if user else None
    if not name:
        name = f"ID {uid}"
    return f'<a href="tg://user?id={uid}">{name}</a>'


async def download_bytes(url: str):
    try:
        headers = {"User-Agent": "TelegramBot/1.0"}
        async with aiohttp.ClientSession(headers=headers) as sess:
            async with sess.get(url, timeout=30) as r:
                r.raise_for_status()
                data = await r.read()
                logger.debug(f"Downloaded {len(data)} bytes from {url}")
                return data
    except Exception as e:
        logger.error(f"Error downloading bytes from {url}: {e}", exc_info=True)
        raise
