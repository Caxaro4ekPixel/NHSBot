import os
import sys
import aiohttp
import traceback
import asyncio
import re
from pathlib import Path
from urllib.parse import quote
from dotenv import load_dotenv
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
import feedparser
from difflib import SequenceMatcher
from typing import Dict, Tuple, Optional, List
from aiogram.types import BufferedInputFile

from bot.core.logger import setup_logging, get_logger

setup_logging()
logger = get_logger(__name__)

sys.path.insert(0, str(Path(__file__).parent.parent))

from bot.models import init_db
from bot.repositories.database import (
    get_incomplete_releases_with_chat, get_release, update_release_from_shikimori,
    is_episode_sent, mark_episode_sent, is_episode_fully_sent, is_page_link_seen
)
from bot.api.shikimori import fetch_anime_by_id

load_dotenv(".env")
TOKEN = os.getenv("BOT_TOKEN")

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

RSS_BASE_URL = "https://nyaa.si/?page=rss&c=1_2"

SIM_THRESHOLD = 0.70
REQUIRED_QUALITIES = {480, 1080}
POLL_INTERVAL = 360


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def normalize_title(s: str, prefix: str = None) -> str:
    s = s.lower()
    
    if prefix:
        prefix_lower = prefix.lower()
        s = re.sub(re.escape(prefix_lower), " ", s)

    s = re.sub(r"\berai-raws\b", " ", s)

    s = re.sub(r"\[[^\]]*\]|\([^\)]*\)|\{[^\}]*\}", " ", s)

    s = re.sub(r"\b(480p|720p|1080p|2160p|4k)\b", " ", s)
    s = re.sub(r"\b(x264|x265|hevc|avc|aac|flac|opus)\b", " ", s)

    s = re.sub(r"[^a-z0-9]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def extract_quality(title: str) -> Optional[int]:
    m = re.search(r"\b(480|720|1080)\s*p\b", title.lower())
    return int(m.group(1)) if m else None


def extract_episode(title: str) -> Optional[int]:
    t = title.lower()

    m = re.search(r"s\d{1,2}e(\d{1,3})\b", t)
    if m:
        return int(m.group(1))

    m = re.search(r"\b(?:episode|ep)\s*(\d{1,3})\b", t)
    if m:
        return int(m.group(1))

    m = re.search(r"\be(\d{1,3})\b", t)
    if m:
        return int(m.group(1))

    t2 = re.sub(r"\b(480|720|1080)\s*p\b", " ", t)
    t2 = re.sub(r"\b(2160)\s*p\b", " ", t2)
    t2 = re.sub(r"\b\d{3,4}x\d{3,4}\b", " ", t2)

    m = re.search(r"(?:^|[ \-_.])(\d{1,3})(?:$|[ \-_.])", t2)
    if m:
        return int(m.group(1))

    return None


def sanitize_filename(name: str) -> str:
    name = re.sub(r"[\\/:*?\"<>|]+", "_", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name[:150]


def extract_torrent_and_magnet(entry) -> Tuple[Optional[str], Optional[str]]:
    torrent_url = None
    magnet = None

    for l in getattr(entry, "links", []) or []:
        href = l.get("href")
        rel = l.get("rel")
        ltype = (l.get("type") or "").lower()
        if href and (ltype == "application/x-bittorrent" or rel == "enclosure" or href.endswith(".torrent")):
            torrent_url = href
        if href and href.startswith("magnet:?"):
            magnet = href

    if magnet is None:
        blob = " ".join(
            [
                getattr(entry, "summary", "") or "",
                getattr(entry, "description", "") or "",
            ]
        )
        m = re.search(r"(magnet:\?xt=urn:btih:[a-zA-Z0-9]+[^\"' <]+)", blob)
        if m:
            magnet = m.group(1)

    return torrent_url, magnet


def format_telegram_message(anime: dict, episode: int, qualities: dict, complete: bool) -> str:
    title = anime.get("name", "Unknown title")
    title_ru = anime.get("name_ru")

    lines = []
    lines.append(f"🎬 <b>{title}</b>")
    if title_ru:
        lines.append(f"🇷🇺 <i>{title_ru}</i>")
    lines.append(f"📺 <b>Серия {episode:02d}</b>")

    magnets = [
        (q, qualities[q]["magnet"])
        for q in sorted(qualities.keys())
        if qualities[q].get("magnet")
    ]
    if magnets:
        lines.append("")
        for q, magnet in magnets:
            lines.append(f"🔗 <b>{q}p</b>: <code>{magnet}</code>")

    lines.append("")
    return "\n".join(lines)


async def send_to_telegram(
        anime: dict,
        episode: int,
        qualities: dict,
        complete: bool,
        chat_id: int,
        http: aiohttp.ClientSession,
) -> None:
    anime_id = int(anime["id"])
    anime_name = anime.get("name_ru") or anime.get("name", f"ID {anime_id}")
    
    if await is_episode_fully_sent(anime_id, episode, REQUIRED_QUALITIES):
        logger.info(f"Episode {anime_id} ({anime_name}) ep{episode} already fully sent, skipping")
        return
    
    logger.info(f"📤 Sending episode {anime_id} ({anime_name}) ep{episode} to chat {chat_id}")
    logger.debug(f"Qualities available: {list(qualities.keys())}, Required: {REQUIRED_QUALITIES}")
    
    text = format_telegram_message(anime, episode, qualities, complete)
    try:
        await bot.send_message(chat_id=chat_id, text=text, disable_web_page_preview=True)
        logger.info(f"✅ Sent message for {anime_name} ep{episode} to chat {chat_id}")
    except Exception as e:
        logger.error(f"❌ Failed to send message for {anime_name} ep{episode}: {e}", exc_info=True)
        return

    sent_count = 0
    for q in sorted(REQUIRED_QUALITIES):
        if q not in qualities:
            continue
            
        t_url = qualities[q].get("torrent_url")
        magnet = qualities[q].get("magnet")
        
        if await is_episode_sent(anime_id, episode, q, t_url, magnet):
            logger.debug(f"Episode {anime_id} ep{episode} {q}p already sent, skipping")
            continue

        if not t_url:
            logger.warning(f"No torrent URL for {anime_id} ep{episode} {q}p")
            continue

        try:
            async with http.get(t_url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status != 200:
                    logger.warning(f"Failed to download torrent ({q}p): HTTP {resp.status}")
                    continue
                data = await resp.read()
        except Exception as e:
            logger.error(f"Failed to download torrent ({q}p): {t_url}. Error: {e}", exc_info=True)
            continue

        t_title = qualities[q].get("title", f"{anime.get('name', 'release')} ep{episode} {q}p")
        fname = sanitize_filename(f"{t_title}.torrent")
        file = BufferedInputFile(data, filename=fname)

        try:
            await bot.send_document(
                chat_id=chat_id,
                document=file,
                caption=f"🧩 <b>{q}p</b>",
                disable_content_type_detection=True,
            )
            page_link = qualities[q].get("page_link")
            await mark_episode_sent(anime_id, episode, q, t_url, magnet, page_link)
            sent_count += 1
            logger.info(f"✅ Sent torrent: {anime_name} ep{episode} {q}p to chat {chat_id}")
        except Exception as e:
            logger.error(f"Failed to send torrent ({q}p): {e}", exc_info=True)
    
    if sent_count > 0:
        logger.info(f"✅ Successfully sent {sent_count}/{len(REQUIRED_QUALITIES)} torrent(s) for {anime_name} ep{episode}")
    else:
        logger.warning(f"⚠️ No torrents were sent for {anime_name} ep{episode} (expected {len(REQUIRED_QUALITIES)})")


def find_best_match(entry_title: str, catalog: List[dict]) -> Optional[dict]:
    best = None
    best_score = 0.0

    for item in catalog:
        name = item.get("name", "")
        if not name:
            continue
        
        prefix = item.get("search_prefix", "[Erai-raws]")
        name_norm = normalize_title(name)
        
        entry_norm_with_prefix = normalize_title(entry_title, prefix)
        score_with_prefix = similarity(entry_norm_with_prefix, name_norm)
        
        if score_with_prefix >= SIM_THRESHOLD and score_with_prefix > best_score:
            best_score = score_with_prefix
            best = item
    
    if best:
        return best
    
    for item in catalog:
        name = item.get("name", "")
        if not name:
            continue
        
        name_norm = normalize_title(name)
        entry_norm_without = normalize_title(entry_title)
        score_without = similarity(entry_norm_without, name_norm)
        
        if score_without >= SIM_THRESHOLD and score_without > best_score:
            best_score = score_without
            best = item

    return best if (best and best_score >= SIM_THRESHOLD) else None


def build_rss_url(search_query: str) -> str:
    encoded_query = quote(search_query)
    return f"{RSS_BASE_URL}&q={encoded_query}"


async def process_feed(catalog: List[dict], http) -> None:
    total_episodes_found = 0
    
    for anime in catalog:
        anime_id = int(anime.get("id", -1))
        if anime_id == -1:
            continue
            
        chat_id = anime.get("chat_id")
        if not chat_id:
            logger.debug(f"Skipping anime ID {anime_id}: no chat_id")
            continue
        
        search_prefix = anime.get("search_prefix") or ""
        search_prefix = search_prefix.strip() if search_prefix else ""
        name = anime.get("name") or ""
        name = name.strip() if name else ""
        
        if not name:
            logger.warning(f"Skipping anime ID {anime_id}: no name")
            continue
        
        if search_prefix:
            search_query = f"{search_prefix} {name}"
        else:
            search_query = name
        
        rss_url = build_rss_url(search_query)
        anime_name = anime.get("name_ru") or anime.get("name", f"ID {anime_id}")
        logger.info(f"🔍 Fetching RSS for: {anime_name} (query: {search_query})")
        
        try:
            feed = feedparser.parse(rss_url)
            status = getattr(feed, "status", None)
            if status != 200:
                logger.warning(f"Failed to get RSS feed for {anime_name}. Status code: {status}")
                continue
            
            entries_count = len(feed.entries) if hasattr(feed, 'entries') else 0
            logger.debug(f"RSS feed for {anime_name}: {entries_count} entries found")
            
            episode_qualities: Dict[Tuple[int, int], Dict[int, Dict[str, str]]] = {}
            
            for entry in feed.entries:
                title = getattr(entry, "title", "") or ""
                page_link = getattr(entry, "link", "") or ""
                
                if not title or not page_link:
                    continue
                
                if await is_page_link_seen(page_link):
                    logger.debug(f"Skipping already seen entry: {title[:60]}")
                    continue
                
                q = extract_quality(title)
                ep = extract_episode(title)
                if q is None or ep is None:
                    logger.debug(f"Skipping entry (no quality/episode): {title[:60]}")
                    continue
                
                if not find_best_match(title, [anime]):
                    logger.debug(f"Entry doesn't match {anime_name}: {title[:60]}")
                    continue
                
                logger.debug(f"Matched entry '{title[:60]}' to {anime_name} (ep{ep}, {q}p)")
                
                torrent_url, magnet = extract_torrent_and_magnet(entry)
                
                key = (anime_id, ep)
                if key not in episode_qualities:
                    episode_qualities[key] = {}
                
                if q not in episode_qualities[key]:
                    episode_qualities[key][q] = {
                        "title": title,
                        "page_link": page_link,
                        "torrent_url": torrent_url,
                        "magnet": magnet,
                    }
            
            for (ep_anime_id, ep), qualities in episode_qualities.items():
                if ep_anime_id != anime_id:
                    continue
                
                if REQUIRED_QUALITIES.issubset(set(qualities.keys())):
                    if not await is_episode_fully_sent(anime_id, ep, REQUIRED_QUALITIES):
                        logger.info(f"🎯 Found complete episode: {anime_name} ep{ep} (qualities: {list(qualities.keys())})")
                        await send_to_telegram(anime, ep, qualities, complete=True, chat_id=chat_id, http=http)
                        
                        for q in REQUIRED_QUALITIES:
                            torrent_url = qualities[q].get("torrent_url")
                            magnet = qualities[q].get("magnet")
                            page_link = qualities[q].get("page_link")
                            await mark_episode_sent(anime_id, ep, q, torrent_url, magnet, page_link)
                        total_episodes_found += 1
                    else:
                        logger.debug(f"Episode {anime_name} ep{ep} already fully sent, skipping")
                else:
                    missing = REQUIRED_QUALITIES - set(qualities.keys())
                    logger.debug(f"Episode {anime_name} ep{ep} incomplete: missing qualities {missing} (have: {list(qualities.keys())})")
        
        except Exception as e:
            logger.error(f"Error processing RSS for {anime_name}: {e}", exc_info=True)
            continue
    
    logger.info(f"✅ Finished processing RSS feed: found {total_episodes_found} new complete episode(s)")


async def rss_watcher() -> None:
    logger.info("🚀 RSS watcher starting...")
    await init_db()
    logger.info("✅ Database initialized")

    try:
        async with aiohttp.ClientSession() as http:
            while True:
                logger.info(f"checking RSS...")
                try:
                    catalog = await get_incomplete_releases_with_chat()
                    
                    if not catalog:
                        logger.info("No incomplete releases with chat_id found")
                    else:
                        logger.info(f"Found {len(catalog)} incomplete releases to track")
                        release_ids_this_cycle = [r["id"] for r in catalog]

                        updated_count = 0
                        for release in catalog:
                            try:
                                anime_data = await fetch_anime_by_id(release["id"])
                                if anime_data:
                                    await update_release_from_shikimori(release["id"], anime_data)
                                    updated_count += 1
                            except Exception as e:
                                logger.error(f"Error updating release {release['id']}: {e}", exc_info=True)

                        logger.info(f"Updated {updated_count}/{len(catalog)} releases from Shikimori")

                        catalog_for_rss = []
                        for rid in release_ids_this_cycle:
                            row = await get_release(rid)
                            if row and row.get("chat_id"):
                                catalog_for_rss.append(row)

                        logger.info(f"Processing RSS feed for {len(catalog_for_rss)} releases...")
                        await process_feed(catalog_for_rss, http=http)
                        logger.info(f"Finished processing RSS feed")
                except Exception as e:
                    logger.error(f"Error while processing feed: {e}", exc_info=True)
                    logger.error(f"Traceback: {traceback.format_exc()}")

                logger.info(f"⏳ Waiting {POLL_INTERVAL} seconds before next poll...")
                try:
                    await asyncio.sleep(POLL_INTERVAL)
                    logger.info(f"⏰ Sleep finished, starting next poll...")
                except Exception as sleep_error:
                    logger.error(f"Error during sleep: {sleep_error}", exc_info=True)
                    await asyncio.sleep(5)
    except KeyboardInterrupt:
        logger.info("RSS watcher stopped by user")
        raise
    except Exception as e:
        logger.error(f"Fatal error in RSS watcher: {e}", exc_info=True)
        logger.error(f"Traceback: {traceback.format_exc()}")
        raise


if __name__ == "__main__":
    asyncio.run(rss_watcher())
