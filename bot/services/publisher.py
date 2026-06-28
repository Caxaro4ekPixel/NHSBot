import asyncio
import re
from pathlib import Path
from typing import Optional, Dict, List, Callable, Awaitable

from aiogram import Bot
from aiogram.enums import ParseMode
from telethon.tl.types import PeerChannel, InputChannel
from telethon.tl.functions.channels import GetChannelsRequest
from bot.core.logger import get_logger

logger = get_logger(__name__)

ROLE_LABEL = {
    "VOICE":      "🎙Роли озвучивали",
    "TIMING":     "🎧Тайминг и работа со звуком",
    "DESIGNER":   "📺Локализация графики",
    "TRANSLATOR": "✍️Перевод",
    "CURATOR":    "🎬Куратор",
}

ROLE_ORDER = ["TRANSLATOR", "VOICE", "TIMING", "CURATOR", "DESIGNER"]


def _hashtag(name: str) -> str:
    clean = re.sub(r'[^\w\s]', '', name, flags=re.UNICODE)
    if re.search(r'[а-яА-ЯёЁ]', clean):
        return '#' + re.sub(r'\s+', '', clean)
    return '#' + re.sub(r'\s+', '_', clean.strip())


def _group_link(group_id: int, message_id: int, topic_id: Optional[int] = None) -> str:
    cid = str(group_id).lstrip('-')
    if cid.startswith('100'):
        cid = cid[3:]
    return f"https://t.me/c/{cid}/{message_id}"


async def _resolve_entity(client, chat_id: int):
    """Resolve entity for a bot client, even if not in session cache."""
    try:
        return await client.get_entity(chat_id)
    except ValueError:
        s = str(abs(chat_id))
        channel_id = int(s[3:]) if s.startswith('100') else int(s)
        result = await client(GetChannelsRequest([InputChannel(channel_id, access_hash=0)]))
        if result.chats:
            return result.chats[0]
        raise ValueError(f"Cannot resolve entity for chat_id={chat_id}")


def _to_peer(chat_id: int):
    if chat_id > 0:
        return PeerChannel(chat_id)
    s = str(abs(chat_id))
    if s.startswith('100'):
        return PeerChannel(int(s[3:]))
    return chat_id


def _format_credits(credits: Dict[str, List[dict]]) -> str:
    lines = []
    for role in ROLE_ORDER:
        users = credits.get(role)
        if not users:
            continue
        label = ROLE_LABEL.get(role, role)
        names = []
        for u in users:
            nm = u.get("name") or u.get("username") or "???"
            url = u.get("channel_url")
            names.append(f'<a href="{url}">{nm}</a>' if url else nm)
        lines.append(f"{label}: {', '.join(names)}")
    return "\n".join(lines)


def _build_tags(release: dict) -> list:
    custom = (release.get("custom_tags") or "").strip()
    if custom:
        return [custom]
    name_en = release.get("name") or ""
    name_ru = release.get("name_ru") or ""
    tags = []
    if name_en:
        tags.append(_hashtag(name_en))
    if name_ru and name_ru != name_en:
        tags.append(_hashtag(name_ru))
    return tags


def build_group_mp4_caption(release: dict, episode: int) -> str:
    tags = _build_tags(release)
    return f"{episode} серия\n\n{' '.join(tags)}"


def build_group_mkv_caption(release: dict, episode: int) -> str:
    tags = _build_tags(release)
    return (
        f"{episode} серия\n"
        f"📥 Качество: 1080p\n"
        f"📦 Формат: MKV | H.264\n\n"
        f"{' '.join(tags)}"
    )


def build_channel_caption(
    release: dict, episode: int,
    credits: Dict[str, List[dict]],
    mp4_url: str,
) -> str:
    name_ru = release.get("name_ru") or release.get("name") or ""
    tags = _build_tags(release)

    credits_text = _format_credits(credits)
    parts = [f"💛{name_ru} — {episode} серия", ""]
    if credits_text:
        parts.append(credits_text)
        parts.append("")
    parts.append(f'🆕<a href="{mp4_url}">СМОТРЕТЬ</a>')
    parts.append("")
    parts.append(" ".join(tags))
    return "\n".join(parts)


async def publish_to_staging(
    bot: Bot,
    staging_chat_id: int,
    release: dict,
    episode: int,
    mkv_path: Path,
    mp4_path: Path,
    screenshot_path: Path,
    credits: Dict[str, List[dict]],
    on_progress: Callable[[str, Optional[int]], Awaitable[None]],
    cover_path: Optional[Path] = None,
    telethon_client=None,
) -> Dict[str, Optional[int]]:
    if telethon_client is None:
        raise RuntimeError("Telethon client required")

    staging_entity = await _resolve_entity(telethon_client, staging_chat_id)
    thumb = cover_path or (screenshot_path if screenshot_path.exists() else None)

    await on_progress("upload_mp4", None)
    mp4_msg = await asyncio.wait_for(
        telethon_client.send_file(
            entity=staging_entity,
            file=str(mp4_path),
            caption=build_group_mp4_caption(release, episode),
            supports_streaming=True,
            thumb=str(thumb) if thumb else None,
            parse_mode="html",
        ),
        timeout=3600,
    )
    logger.info(f"Staging MP4 id={mp4_msg.id}")

    await on_progress("upload_mkv", None)
    mkv_msg = await asyncio.wait_for(
        telethon_client.send_file(
            entity=staging_entity,
            file=str(mkv_path),
            caption=build_group_mkv_caption(release, episode),
            force_document=True,
            parse_mode="html",
        ),
        timeout=3600,
    )
    logger.info(f"Staging MKV id={mkv_msg.id}")

    staging_channel_msg_id = None
    if screenshot_path.exists():
        await on_progress("upload_channel", None)
        ch_msg = await asyncio.wait_for(
            telethon_client.send_file(
                entity=staging_entity,
                file=str(screenshot_path),
                caption=build_channel_caption(release, episode, credits, ""),
                parse_mode="html",
            ),
            timeout=300,
        )
        staging_channel_msg_id = ch_msg.id
        logger.info(f"Staging channel preview id={ch_msg.id}")

    return {
        "staging_mp4_id": mp4_msg.id,
        "staging_mkv_id": mkv_msg.id,
        "staging_channel_msg_id": staging_channel_msg_id,
    }


async def publish_from_staging(
    bot: Bot,
    staging_chat_id: int,
    staging_post: dict,
    release: dict,
    credits: Dict[str, List[dict]],
    telethon_client=None,
) -> Dict[str, Optional[int]]:
    group_id = staging_post["group_id"]
    topic_id = staging_post["topic_id"]
    channel_id = staging_post["channel_id"]
    episode = staging_post["episode"]

    result: Dict[str, Optional[int]] = {
        "group_mp4_id": None, "group_mkv_id": None, "channel_msg_id": None,
    }

    fwd_mp4 = await bot.copy_message(
        chat_id=group_id,
        from_chat_id=staging_chat_id,
        message_id=staging_post["staging_mp4_id"],
        message_thread_id=topic_id,
    )
    mp4_id = fwd_mp4.message_id
    result["group_mp4_id"] = mp4_id
    logger.info(f"Copied MP4 to group={group_id} topic={topic_id} msg_id={mp4_id}")

    fwd_mkv = await bot.copy_message(
        chat_id=group_id,
        from_chat_id=staging_chat_id,
        message_id=staging_post["staging_mkv_id"],
        message_thread_id=topic_id,
    )
    result["group_mkv_id"] = fwd_mkv.message_id
    logger.info(f"Copied MKV to group={group_id} topic={topic_id}")

    if channel_id and staging_post.get("staging_channel_msg_id") and mp4_id:
        mp4_url = _group_link(group_id, mp4_id, topic_id)
        ch_caption = build_channel_caption(release, episode, credits, mp4_url)
        ch_msg = await bot.copy_message(
            chat_id=channel_id,
            from_chat_id=staging_chat_id,
            message_id=staging_post["staging_channel_msg_id"],
            caption=ch_caption,
            parse_mode=ParseMode.HTML,
        )
        result["channel_msg_id"] = ch_msg.message_id
        logger.info(f"Channel post sent: channel={channel_id} msg_id={ch_msg.message_id}")

    return result
