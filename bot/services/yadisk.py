"""Cloud storage downloaders: Yandex Disk and Google Drive (public links, no auth)."""
import re
import aiohttp
from pathlib import Path
from typing import Optional
from bot.core.logger import get_logger

logger = get_logger(__name__)

# ── URL patterns ───────────────────────────────────────────────────────────────

_YADISK_RE = re.compile(r'https?://(?:disk\.yandex\.(?:ru|com)/d/|yadi\.sk/d/)\S+')
_GDRIVE_RE = re.compile(r'https?://(?:drive|docs)\.google\.com/\S+')

_YD_API = "https://cloud-api.yandex.net/v1/disk/public/resources"


def extract_yadisk_url(text: str) -> Optional[str]:
    m = _YADISK_RE.search(text)
    return m.group(0).rstrip(".,)>") if m else None


def _extract_gdrive_id(url: str) -> Optional[str]:
    m = re.search(r'/file/d/([a-zA-Z0-9_-]+)', url)
    if m:
        return m.group(1)
    m = re.search(r'[?&]id=([a-zA-Z0-9_-]+)', url)
    return m.group(1) if m else None


def extract_cloud_url(text: str) -> Optional[tuple[str, str]]:
    """Return (url, provider) where provider is 'yadisk' or 'gdrive', or None."""
    m = _YADISK_RE.search(text)
    if m:
        return m.group(0).rstrip(".,)>"), "yadisk"
    m = _GDRIVE_RE.search(text)
    if m:
        url = m.group(0).rstrip(".,)>")
        if _extract_gdrive_id(url):
            return url, "gdrive"
    return None


# ── Yandex Disk ────────────────────────────────────────────────────────────────

async def _yadisk_info(public_url: str) -> Optional[dict]:
    params = {"public_key": public_url, "fields": "name,size"}
    async with aiohttp.ClientSession() as s:
        try:
            async with s.get(_YD_API, params=params, timeout=aiohttp.ClientTimeout(total=15)) as r:
                if r.status != 200:
                    return None
                d = await r.json()
                return {"name": d.get("name"), "size": d.get("size")}
        except Exception as e:
            logger.warning(f"Yandex Disk info error: {e}")
            return None


async def _yadisk_download_url(public_url: str) -> Optional[str]:
    params = {"public_key": public_url}
    async with aiohttp.ClientSession() as s:
        try:
            async with s.get(f"{_YD_API}/download", params=params, timeout=aiohttp.ClientTimeout(total=15)) as r:
                if r.status != 200:
                    return None
                return (await r.json()).get("href")
        except Exception as e:
            logger.warning(f"Yandex Disk resolve error: {e}")
            return None


# ── Google Drive ───────────────────────────────────────────────────────────────

def _gdrive_direct_url(file_id: str) -> str:
    return f"https://drive.usercontent.google.com/download?id={file_id}&export=download&confirm=t"


async def _gdrive_info(public_url: str) -> Optional[dict]:
    file_id = _extract_gdrive_id(public_url)
    if not file_id:
        return None
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                _gdrive_direct_url(file_id),
                allow_redirects=True,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as r:
                cd = r.headers.get("Content-Disposition", "")
                name_m = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";\r\n]+)\"?", cd, re.IGNORECASE)
                name = name_m.group(1).strip().strip('"') if name_m else f"gdrive_{file_id}"
                size = int(r.headers.get("Content-Length", 0)) or None
                return {"name": name, "size": size}
    except Exception as e:
        logger.warning(f"Google Drive info error: {e}")
        return None


# ── Unified interface ──────────────────────────────────────────────────────────

async def get_resource_info(public_url: str, provider: str = "yadisk") -> Optional[dict]:
    if provider == "gdrive":
        return await _gdrive_info(public_url)
    return await _yadisk_info(public_url)


async def download_from_yadisk(public_url: str, dest, on_pct=None) -> bool:
    """Download Yandex Disk or Google Drive file. Returns True on success."""
    provider = "gdrive" if _GDRIVE_RE.search(public_url) else "yadisk"
    return await _download_cloud(public_url, provider, dest, on_pct)


async def _download_cloud(public_url: str, provider: str, dest, on_pct=None) -> bool:
    if provider == "gdrive":
        file_id = _extract_gdrive_id(public_url)
        if not file_id:
            return False
        direct_url = _gdrive_direct_url(file_id)
    else:
        direct_url = await _yadisk_download_url(public_url)
        if not direct_url:
            return False

    async with aiohttp.ClientSession() as s:
        async with s.get(direct_url, timeout=aiohttp.ClientTimeout(total=3600)) as r:
            r.raise_for_status()
            total = int(r.headers.get("Content-Length", 0))
            downloaded = 0
            last_pct = -1
            with open(dest, "wb") as f:
                async for chunk in r.content.iter_chunked(512 * 1024):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if on_pct and total:
                        pct = min(99, int(downloaded / total * 100))
                        if pct != last_pct:
                            last_pct = pct
                            await on_pct(pct)
    if on_pct:
        await on_pct(100)
    return True
