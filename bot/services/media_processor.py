import asyncio
import hashlib
import os
import random
import shutil
import time
from pathlib import Path
from typing import Callable, Awaitable, Optional, Dict

import aiohttp
from aiogram import Bot
from bot.core.logger import get_logger
from bot.services.yadisk import download_from_yadisk

logger = get_logger(__name__)

CACHE_DIR = Path("/tmp/nhs_cache")
CACHE_TTL = 3600


def _cache_path(file_id: str, filename: str, message_id: int = None, group_id: int = None) -> Path:
    if file_id.startswith("http"):
        key = "cl_" + hashlib.md5(file_id.encode()).hexdigest()[:16]
    elif message_id and group_id:
        key = f"tg_{abs(group_id)}_{message_id}"
    else:
        key = "fid_" + file_id[:24].replace(":", "_").replace("/", "_")
    return CACHE_DIR / f"{key}_{filename}"


def _is_cached(path: Path) -> bool:
    return path.exists() and (time.time() - path.stat().st_mtime) < CACHE_TTL


def _cleanup_cache() -> None:
    if not CACHE_DIR.exists():
        return
    now = time.time()
    for f in CACHE_DIR.iterdir():
        if f.is_file() and (now - f.stat().st_mtime) > CACHE_TTL:
            f.unlink(missing_ok=True)


STEPS = {
    "download":   "📥 Скачиваю файлы...",
    "extract":    "📦 Распаковываю архив...",
    "audio":      "🎵 Конвертирую аудио...",
    "mux":        "🎬 Собираю MKV...",
    "mp4":        "📦 Конвертирую MP4...",
    "screenshot": "📸 Делаю скриншот...",
}

ProgressCb = Callable[[str, Optional[int]], Awaitable[None]]


async def _run(args: list, step: str) -> None:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    ok = proc.returncode == 0 or (args[0] == "mkvmerge" and proc.returncode == 1)
    if not ok:
        raise RuntimeError(f"{step} failed (exit {proc.returncode}): {stderr.decode()[-500:]}")


async def _run_with_progress(args: list, duration: float, on_pct: Callable[[int], Awaitable[None]]) -> None:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    last_pct = -1
    try:
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            line = line.decode().strip()
            if line.startswith("out_time_ms="):
                try:
                    ms = int(line.split("=", 1)[1])
                    if ms > 0 and duration > 0:
                        pct = min(99, int(ms / 1e6 / duration * 100))
                        if pct != last_pct:
                            last_pct = pct
                            await on_pct(pct)
                except (ValueError, ZeroDivisionError):
                    pass
    except Exception:
        pass
    await proc.wait()
    if proc.returncode not in (0, None):
        stderr = await proc.stderr.read()
        raise RuntimeError(f"ffmpeg failed (exit {proc.returncode}): {stderr.decode()[-500:]}")


async def get_duration(path: Path) -> float:
    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "quiet", "-show_entries", "format=duration",
        "-of", "csv=p=0", str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    try:
        return float(stdout.decode().strip())
    except ValueError:
        return 0.0


async def download_file(
    bot: Bot,
    file_id: str,
    dest: Path,
    telethon_client=None,
    group_id: int = None,
    message_id: int = None,
    on_pct: Callable[[int], Awaitable[None]] = None,
) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _cleanup_cache()
    cached = _cache_path(file_id, dest.name, message_id, group_id)

    if _is_cached(cached):
        logger.info(f"Cache hit: {dest.name}")
        try:
            os.link(cached, dest)
        except OSError:
            shutil.copy2(cached, dest)
        if on_pct:
            await on_pct(100)
        return

    if file_id.startswith("http"):
        ok = await download_from_yadisk(file_id, dest, on_pct=on_pct)
        if not ok:
            raise RuntimeError(f"Не удалось скачать файл с Яндекс Диска: {file_id}")
    elif telethon_client and group_id and message_id:
        try:
            msg = await telethon_client.get_messages(group_id, ids=message_id)
            if msg and msg.media:
                last: dict = {"pct": -1}

                async def _progress(downloaded: int, total: int) -> None:
                    if not on_pct or not total:
                        return
                    pct = min(99, int(downloaded / total * 100))
                    if pct != last["pct"]:
                        last["pct"] = pct
                        await on_pct(pct)

                await telethon_client.download_media(msg, file=str(dest), progress_callback=_progress)
                if on_pct:
                    await on_pct(100)
        except Exception as e:
            logger.warning(f"Telethon download failed, falling back to Bot API: {e}")

    if not dest.exists():
        file = await bot.get_file(file_id)
        file_path = file.file_path
        local = Path(file_path)
        if local.exists():
            shutil.copy2(local, dest)
        else:
            url = f"https://api.telegram.org/file/bot{bot.token}/{file_path}"
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    resp.raise_for_status()
                    with open(dest, "wb") as f:
                        async for chunk in resp.content.iter_chunked(1024 * 1024):
                            f.write(chunk)

    if dest.exists():
        try:
            os.link(dest, cached)
        except OSError:
            shutil.copy2(dest, cached)
        logger.info(f"Cached: {dest.name}")


async def extract_archive(archive_path: Path, dest_dir: Path) -> Path:
    await _run(["7z", "x", f"-o{dest_dir}", "-y", str(archive_path)], "extract")
    video_exts = {".mkv", ".mov", ".mp4", ".avi"}
    candidates = [f for f in dest_dir.rglob("*") if f.suffix.lower() in video_exts]
    if not candidates:
        raise FileNotFoundError("No video file found in archive")
    return max(candidates, key=lambda f: f.stat().st_size)


async def convert_audio(src: Path, dst: Path, on_progress: ProgressCb) -> None:
    duration = await get_duration(src)
    last: dict = {"pct": -1}

    async def on_pct(pct: int) -> None:
        if pct != last["pct"]:
            last["pct"] = pct
            await on_progress("audio", pct)

    await on_progress("audio", 0)
    await _run_with_progress([
        "ffmpeg", "-y", "-progress", "pipe:1",
        "-i", str(src),
        "-c:a", "aac", "-b:a", "256k", "-ac", "2", "-ar", "48000",
        "-profile:a", "aac_low",
        str(dst),
    ], duration, on_pct)
    await on_progress("audio", 100)


async def mux_mkv(video: Path, audio: Path, subs: Optional[Path], output: Path) -> None:
    args = [
        "mkvmerge", "-o", str(output),
        "--track-name", "0:New Horizons Studio",
        str(video),
        "--track-name", "0:New Horizons Studio",
        str(audio),
    ]
    if subs:
        args += [
            "--track-name", "0:New Horizons Studio",
            "--default-track", "0:no",
            "--forced-track", "0:no",
            str(subs),
        ]
    await _run(args, "mux")


async def convert_mp4(mkv: Path, output: Path, on_progress: ProgressCb) -> None:
    duration = await get_duration(mkv)
    last: dict = {"pct": -1}

    async def on_pct(pct: int) -> None:
        if pct != last["pct"]:
            last["pct"] = pct
            await on_progress("mp4", pct)

    await on_progress("mp4", 0)
    await _run_with_progress([
        "ffmpeg", "-y", "-progress", "pipe:1",
        "-i", str(mkv),
        "-c:v", "h264", "-profile:v", "high", "-preset", "slow",
        "-level", "4.1", "-tune", "animation", "-pix_fmt", "yuv420p", "-crf", "15",
        "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(output),
    ], duration, on_pct)
    await on_progress("mp4", 100)


async def take_screenshot(mkv: Path, output: Path) -> None:
    duration = await get_duration(mkv)
    if duration <= 0:
        duration = 1400.0
    ts = random.uniform(duration * 0.20, duration * 0.80)
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-ss", str(ts), "-i", str(mkv),
        "-frames:v", "1", "-q:v", "2",
        str(output),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()


def _detect_file_type(file_name: Optional[str]) -> Optional[str]:
    if not file_name:
        return None
    ext = Path(file_name).suffix.lower()
    return {
        ".ass": "ass", ".flac": "flac", ".wav": "wav",
        ".mkv": "mkv", ".mov": "mov",
        ".zip": "zip", ".rar": "rar", ".7z": "7z",
    }.get(ext)


async def process_episode(
    bot: Bot,
    files: Dict[str, dict],
    work_dir: Path,
    on_progress: ProgressCb,
    telethon_client=None,
) -> Dict[str, Path]:
    """
    Full processing pipeline. Returns {mkv, mp4, screenshot} as absolute Paths.
    files keys: ass (optional), flac or wav (required), mkv/mov/zip/rar/7z (required)
    """
    work_dir.mkdir(parents=True, exist_ok=True)

    await on_progress("download", None)
    raw_dir = work_dir / "raw"
    raw_dir.mkdir(exist_ok=True)

    sub_path: Optional[Path] = None
    audio_path: Optional[Path] = None
    video_path: Optional[Path] = None

    file_list = list(files.items())
    for idx, (ftype, finfo) in enumerate(file_list):
        fname = finfo.get("file_name") or f"{ftype}.bin"
        dest = raw_dir / fname
        logger.info(f"Downloading {ftype}: {fname}")
        n_files = len(file_list)

        async def _on_pct(pct: int, _ftype=ftype, _idx=idx) -> None:
            suffix = f" ({_idx + 1}/{n_files})" if n_files > 1 else ""
            await on_progress(f"download_{_ftype}{suffix}", pct)

        await download_file(
            bot, finfo["file_id"], dest,
            telethon_client=telethon_client,
            group_id=finfo.get("group_id"),
            message_id=finfo.get("message_id"),
            on_pct=_on_pct,
        )
        if ftype == "ass":
            sub_path = dest
        elif ftype in ("flac", "wav"):
            audio_path = dest
        elif ftype in ("mkv", "mov"):
            video_path = dest
        elif ftype in ("zip", "rar", "7z"):
            video_path = dest

    if video_path and video_path.suffix.lower() in (".zip", ".rar", ".7z"):
        await on_progress("extract", None)
        extract_dir = work_dir / "extracted"
        extract_dir.mkdir(exist_ok=True)
        video_path = await extract_archive(video_path, extract_dir)
        logger.info(f"Extracted video: {video_path}")

    if not audio_path:
        raise ValueError("No audio file (flac/wav) found in topic files")
    if not video_path:
        raise ValueError("No video file found in topic files")

    audio_m4a = work_dir / "audio.m4a"
    await convert_audio(audio_path, audio_m4a, on_progress)

    await on_progress("mux", None)
    out_mkv = work_dir / "output.mkv"
    await mux_mkv(video_path, audio_m4a, sub_path, out_mkv)

    out_mp4 = work_dir / "output.mp4"
    await convert_mp4(out_mkv, out_mp4, on_progress)

    await on_progress("screenshot", None)
    screenshot = work_dir / "screenshot.jpg"
    await take_screenshot(out_mkv, screenshot)

    return {"mkv": out_mkv, "mp4": out_mp4, "screenshot": screenshot}
