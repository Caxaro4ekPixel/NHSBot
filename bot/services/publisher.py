"""Publish episode to Telegram group topic and announcement channel."""
import re
from pathlib import Path
from typing import Optional, Dict, List, Callable, Awaitable

from aiogram import Bot
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
    clean = re.sub(r'[^\w\s]', '', name, flags=re.UNICODE)  # strip : - ! ? etc.
    if re.search(r'[а-яА-ЯёЁ]', clean):
        return '#' + re.sub(r'\s+', '', clean)
    return '#' + re.sub(r'\s+', '_', clean.strip())


def _group_link(group_id: int, message_id: int, topic_id: Optional[int] = None) -> str:
    """Build t.me/c/ link to a message in a private group."""
    cid = str(group_id).lstrip('-')
    if cid.startswith('100'):
        cid = cid[3:]
    return f"https://t.me/c/{cid}/{message_id}"


def _to_peer(chat_id: int):
    """Normalize Telegram ID for Telethon: bare positive ID → PeerChannel."""
    if chat_id > 0:
        from telethon.tl.types import PeerChannel
        return PeerChannel(chat_id)
    s = str(abs(chat_id))
    if s.startswith('100'):
        from telethon.tl.types import PeerChannel
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


def build_group_mp4_caption(release: dict, episode: int) -> str:
    name_ru = release.get("name_ru") or release.get("name") or ""
    name_en = release.get("name") or ""
    tags = []
    if name_en:
        tags.append(_hashtag(name_en))
    if name_ru and name_ru != name_en:
        tags.append(_hashtag(name_ru))
    return f"{episode} серия\n\n{' '.join(tags)}"


def build_group_mkv_caption(release: dict, episode: int) -> str:
    name_ru = release.get("name_ru") or release.get("name") or ""
    name_en = release.get("name") or ""
    tags = []
    if name_en:
        tags.append(_hashtag(name_en))
    if name_ru and name_ru != name_en:
        tags.append(_hashtag(name_ru))
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
    name_en = release.get("name") or ""
    tags = []
    if name_en:
        tags.append(_hashtag(name_en))
    if name_ru and name_ru != name_en:
        tags.append(_hashtag(name_ru))

    credits_text = _format_credits(credits)
    parts = [f"💛{name_ru} — {episode} серия", ""]
    if credits_text:
        parts.append(credits_text)
        parts.append("")
    parts.append(f'🆕<a href="{mp4_url}">СМОТРЕТЬ</a>')
    parts.append("")
    parts.append(" ".join(tags))
    return "\n".join(parts)


async def publish_episode(
    bot: Bot,
    release: dict,
    episode: int,
    mkv_path: Path,
    mp4_path: Path,
    screenshot_path: Path,
    group_id: int,
    topic_id: int,
    channel_id: int,
    credits: Dict[str, List[dict]],
    on_progress: Callable[[str, Optional[int]], Awaitable[None]],
    cover_path: Optional[Path] = None,
    telethon_client=None,
) -> Dict[str, Optional[int]]:
    """
    Publishes episode via Telethon MTProto (no file size limits).
    Falls back to Bot API for small files if Telethon unavailable.
    Returns {group_mp4_id, group_mkv_id, channel_msg_id}.
    """
    if telethon_client is None:
        raise RuntimeError("Telethon client required for large file uploads")

    result: Dict[str, Optional[int]] = {
        "group_mp4_id": None,
        "group_mkv_id": None,
        "channel_msg_id": None,
    }

    # Thumbnail priority: cover > screenshot
    thumb = cover_path or (screenshot_path if screenshot_path.exists() else None)

    # MP4 → send as video
    await on_progress("upload_mp4", None)
    mp4_caption = build_group_mp4_caption(release, episode)
    mp4_msg = await telethon_client.send_file(
        entity=_to_peer(group_id),
        file=str(mp4_path),
        caption=mp4_caption,
        reply_to=topic_id,
        supports_streaming=True,
        thumb=str(thumb) if thumb else None,
        parse_mode="html",
    )
    result["group_mp4_id"] = mp4_msg.id
    logger.info(f"MP4 posted: group={group_id} topic={topic_id} msg_id={mp4_msg.id}")

    # MKV → send as document
    await on_progress("upload_mkv", None)
    mkv_caption = build_group_mkv_caption(release, episode)
    mkv_msg = await telethon_client.send_file(
        entity=_to_peer(group_id),
        file=str(mkv_path),
        caption=mkv_caption,
        reply_to=topic_id,
        force_document=True,
        parse_mode="html",
    )
    result["group_mkv_id"] = mkv_msg.id
    logger.info(f"MKV posted: group={group_id} topic={topic_id} msg_id={mkv_msg.id}")

    # Channel → screenshot + caption
    if channel_id and screenshot_path.exists():
        await on_progress("upload_channel", None)
        mp4_url = _group_link(group_id, mp4_msg.id, topic_id)
        ch_caption = build_channel_caption(release, episode, credits, mp4_url)
        ch_msg = await telethon_client.send_file(
            entity=_to_peer(channel_id),
            file=str(screenshot_path),
            caption=ch_caption,
            parse_mode="html",
        )
        result["channel_msg_id"] = ch_msg.id
        logger.info(f"Channel post: channel={channel_id} msg_id={ch_msg.id}")

    return result
