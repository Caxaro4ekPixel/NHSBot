from datetime import datetime
from typing import Dict, List, Optional
import aiohttp
import re

from bot.core.logger import get_logger

logger = get_logger(__name__)



def season_key_for_now() -> str:
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
        # y += 1

    return f"{s}_{y}"



async def fetch_shikimori_releases(limit: int = 1000) -> List[Dict]:
    season = season_key_for_now()
    url = "https://shikimori.one/api/animes"
    params = {"season": season, "limit": limit}
    headers = {"Accept": "application/json"}
    try:
        async with aiohttp.ClientSession(headers=headers) as sess:
            async with sess.get(url, params=params, timeout=20) as resp:
                resp.raise_for_status()
                data = await resp.json()
        
        releases = []
        for a in data:
            releases.append({
                "id": a.get("id"),
                "name": a.get("name"),
                "name_ru": a.get("russian"),
                "image": "https://shikimori.one" + a['image']['original'],
                "kind": a.get("kind"),
                "score": a.get("score"),
                "status": a.get("status"),
                "episodes": a.get("episodes"),
                "episodes_aired": a.get("episodes_aired"),
                "aired_on": a.get("aired_on"),
                "released_on": a.get("released_on"),
            })
        logger.info(f"Fetched {len(releases)} releases from Shikimori for season {season}")
        return releases
    except Exception as e:
        logger.error(f"Error fetching Shikimori releases: {e}", exc_info=True)
        raise


async def shiki_fetch(title_ru: str):
    url = "https://shikimori.one/api/animes"
    headers = {"User-Agent": "TelegramBot/1.0 (+https://example.com)"}
    params = {"search": title_ru}
    async with aiohttp.ClientSession(headers=headers) as sess:
        async with sess.get(url, params=params, timeout=20) as r:
            r.raise_for_status()
            items = await r.json()
    if not items:
        return {"ru": title_ru, "en": "", "poster": None}
    it = items[0]
    ru = it.get("russian") or it.get("name") or title_ru
    en = it.get("name") or ""
    img = it.get("image") or {}
    poster_path = img.get("original") or img.get("preview") or ""
    poster = f"https://shikimori.one{poster_path}" if poster_path else None
    return {"ru": ru, "en": en, "poster": poster}


def extract_anime_id_from_url(url: str) -> Optional[int]:
    match = re.search(r'/animes/(\d+)', url)
    if match:
        return int(match.group(1))
    return None


async def fetch_anime_by_id(anime_id: int) -> Optional[Dict]:
    url = f"https://shikimori.one/api/animes/{anime_id}"
    headers = {"Accept": "application/json", "User-Agent": "TelegramBot/1.0"}
    try:
        async with aiohttp.ClientSession(headers=headers) as sess:
            async with sess.get(url, timeout=20) as resp:
                resp.raise_for_status()
                data = await resp.json()
        
        image_path = ""
        if data.get("image"):
            if data["image"].get("original"):
                image_path = "https://shikimori.one" + data["image"]["original"]
            elif data["image"].get("preview"):
                image_path = "https://shikimori.one" + data["image"]["preview"]
        
        score = data.get("score")
        if score is None:
            score = "0.0"
        else:
            score = str(score)
        
        result = {
            "id": data.get("id"),
            "name": data.get("name"),
            "name_ru": data.get("russian"),
            "image": image_path,
            "kind": data.get("kind"),
            "score": score,
            "status": data.get("status"),
            "episodes": data.get("episodes"),
            "episodes_aired": data.get("episodes_aired"),
            "aired_on": data.get("aired_on"),
            "released_on": data.get("released_on"),
        }
        logger.debug(f"Fetched anime by id={anime_id}: {result.get('name_ru')}")
        return result
    except Exception as e:
        logger.error(f"Error fetching anime by id={anime_id}: {e}", exc_info=True)
        return None
