#!/usr/bin/env python3
"""
Первоначальная настройка команды.
Собирает участников из Telegram-чата, назначает роли, добавляет аниме в БД.

Установка зависимостей:
    pip install telethon asyncpg aiohttp python-dotenv

Все параметры читаются из .env — редактировать только там.
"""
import asyncio
import json
import os
import sys
from typing import Optional

import asyncpg
import aiohttp
from dotenv import load_dotenv

load_dotenv(".env")

# ── Конфигурация из .env ───────────────────────────────────────────────────────

TELEGRAM_API_ID   = int(os.getenv("TELEGRAM_API_ID", "0"))
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH", "")

_raw_chat = os.getenv("SETUP_TEAM_CHAT_ID", "0").lstrip("-")
CHAT_ID = int(_raw_chat) if _raw_chat.isdigit() else 0

_raw_urls = os.getenv("SETUP_ANIME_URLS", "")
ANIME_URLS = [u.strip() for u in _raw_urls.split(",") if u.strip()]

DB_DSN = (
    f"postgresql://{os.getenv('DB_USER', 'reales_bot')}:"
    f"{os.getenv('DB_PASSWORD', 'reales_bot')}@"
    f"{os.getenv('DB_HOST', 'localhost')}:"
    f"{os.getenv('DB_PORT', '5432')}/"
    f"{os.getenv('DB_NAME', 'reales_bot')}"
)

ROLE_MENU = {
    "1": "TRANSLATOR",
    "2": "VOICE",
    "3": "TIMING",
    "4": "CURATOR",
    "5": "DESIGNER",
}

# ── Shikimori ──────────────────────────────────────────────────────────────────

def _extract_id(url: str) -> int:
    import re
    m = re.search(r'/animes/(\d+)', url)
    if not m:
        raise ValueError(f"Cannot extract anime ID from: {url}")
    return int(m.group(1))


async def fetch_anime(session: aiohttp.ClientSession, anime_id: int) -> Optional[dict]:
    url = f"https://shikimori.one/api/animes/{anime_id}"
    headers = {"Accept": "application/json", "User-Agent": "reales_bot/1.0"}
    async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=20)) as resp:
        if resp.status != 200:
            print(f"  ⚠️  Shikimori {anime_id}: HTTP {resp.status}")
            return None
        data = await resp.json()

    image = ""
    if data.get("image", {}).get("original"):
        image = "https://shikimori.one" + data["image"]["original"]

    return {
        "id": data["id"],
        "name": data.get("name"),
        "name_ru": data.get("russian"),
        "image": image,
        "kind": data.get("kind"),
        "score": str(data.get("score") or "0.0"),
        "status": data.get("status"),
        "episodes": data.get("episodes"),
        "episodes_aired": data.get("episodes_aired"),
        "aired_on": data.get("aired_on"),
        "released_on": data.get("released_on"),
    }

# ── База данных ────────────────────────────────────────────────────────────────

async def db_upsert_user(conn, telegram_id: int, name: str, username: Optional[str], roles: list) -> int:
    roles_json = json.dumps(roles)
    row = await conn.fetchrow("SELECT id FROM users WHERE telegram_id = $1", telegram_id)
    if row:
        await conn.execute(
            "UPDATE users SET name=$2, username=$3, roles=$4, updated_at=now() WHERE telegram_id=$1",
            telegram_id, name, username, roles_json,
        )
        return row["id"]
    row = await conn.fetchrow(
        "INSERT INTO users(telegram_id, name, username, roles, created_at, updated_at) "
        "VALUES($1,$2,$3,$4,now(),now()) RETURNING id",
        telegram_id, name, username, roles_json,
    )
    return row["id"]


async def db_upsert_release(conn, anime: dict) -> int:
    row = await conn.fetchrow("SELECT id FROM releases WHERE id = $1", anime["id"])
    if row:
        await conn.execute(
            """UPDATE releases SET name=$2, name_ru=$3, image=$4, kind=$5, score=$6,
               status=$7, episodes=$8, episodes_aired=$9, aired_on=$10, released_on=$11,
               updated_at=now() WHERE id=$1""",
            anime["id"], anime["name"], anime["name_ru"], anime["image"],
            anime["kind"], anime["score"], anime["status"],
            anime["episodes"], anime["episodes_aired"],
            anime["aired_on"], anime["released_on"],
        )
        return anime["id"]
    await conn.execute(
        """INSERT INTO releases(id, name, name_ru, image, kind, score, status,
           episodes, episodes_aired, aired_on, released_on, search_prefix,
           is_completed, created_at, updated_at)
           VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,'[Erai-raws]',false,now(),now())""",
        anime["id"], anime["name"], anime["name_ru"], anime["image"],
        anime["kind"], anime["score"], anime["status"],
        anime["episodes"], anime["episodes_aired"],
        anime["aired_on"], anime["released_on"],
    )
    return anime["id"]


async def db_add_assignment(conn, release_id: int, user_internal_id: int, role: str):
    await conn.execute(
        """INSERT INTO release_assignments(release_id, user_id, role)
           VALUES($1, $2, $3::userrole)
           ON CONFLICT (release_id, user_id, role) DO NOTHING""",
        release_id, user_internal_id, role.upper(),
    )

# ── Интерактив ─────────────────────────────────────────────────────────────────

def ask_roles(display: str) -> list:
    print(f"\n  👤 {display}")
    print("     1=Переводчик  2=Войс  3=Тайминг  4=Куратор  5=Дизайнер")
    print("     Enter = пропустить (наблюдатель)  |  q = завершить")
    raw = input("     Роли (например 1,3): ").strip()
    if raw.lower() == "q":
        raise KeyboardInterrupt
    if not raw:
        return []
    result = []
    for token in raw.split(","):
        token = token.strip()
        if token in ROLE_MENU:
            result.append(ROLE_MENU[token].lower())
    return result


def ask_releases(anime_list: list) -> list:
    print("     Релизы:")
    for i, a in enumerate(anime_list, 1):
        name = a.get("name_ru") or a.get("name") or f"ID {a['id']}"
        print(f"       {i}. {name}")
    raw = input("     Номера релизов (1,2,3 или Enter = все): ").strip()
    if not raw:
        return [a["id"] for a in anime_list]
    ids = []
    for token in raw.split(","):
        token = token.strip()
        if token.isdigit():
            idx = int(token) - 1
            if 0 <= idx < len(anime_list):
                ids.append(anime_list[idx]["id"])
    return ids

# ── Точка входа ────────────────────────────────────────────────────────────────

async def main():
    if not TELEGRAM_API_ID or not TELEGRAM_API_HASH:
        print("❌ Укажи TELEGRAM_API_ID и TELEGRAM_API_HASH в .env")
        sys.exit(1)
    if not CHAT_ID:
        print("❌ Укажи SETUP_TEAM_CHAT_ID в .env")
        sys.exit(1)
    if not ANIME_URLS:
        print("❌ Укажи SETUP_ANIME_URLS в .env (через запятую)")
        sys.exit(1)

    print("=" * 60)
    print("  reales_bot — настройка команды")
    print("=" * 60)
    print(f"  Чат: {CHAT_ID}")
    print(f"  Аниме: {len(ANIME_URLS)} шт.")

    try:
        from telethon import TelegramClient
        from telethon.tl.functions.channels import GetParticipantsRequest
        from telethon.tl.types import ChannelParticipantsSearch, PeerChannel
    except ImportError:
        print("\n❌ Не установлен telethon.")
        print("   Запусти: pip install telethon asyncpg aiohttp python-dotenv")
        sys.exit(1)

    client = TelegramClient("setup_session", TELEGRAM_API_ID, TELEGRAM_API_HASH)
    await client.start()
    me = await client.get_me()
    print(f"\n✅ Telegram: вошёл как {me.first_name} (@{me.username})")

    # Загрузка участников
    print(f"\n🔍 Загружаю участников чата {CHAT_ID}...")
    entity = await client.get_entity(PeerChannel(CHAT_ID))
    participants = []
    offset = 0
    while True:
        chunk = await client(GetParticipantsRequest(
            channel=entity,
            filter=ChannelParticipantsSearch(""),
            offset=offset,
            limit=200,
            hash=0,
        ))
        if not chunk.users:
            break
        participants.extend(chunk.users)
        offset += len(chunk.users)
        if offset >= chunk.count:
            break

    humans = [u for u in participants if not u.bot]
    print(f"✅ Найдено участников: {len(humans)} (боты исключены)")

    # Загрузка аниме с Shikimori
    print("\n🌐 Загружаю аниме с Shikimori...")
    anime_list = []
    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=connector) as http:
        for url in ANIME_URLS:
            anime_id = _extract_id(url)
            data = await fetch_anime(http, anime_id)
            if data:
                label = data.get("name_ru") or data.get("name") or f"ID {anime_id}"
                print(f"  ✅ [{anime_id}] {label}")
                anime_list.append(data)
            else:
                print(f"  ❌ Не удалось загрузить ID {anime_id}")

    if not anime_list:
        print("❌ Ни одно аниме не загружено — прерываю")
        await client.disconnect()
        return

    # Назначение ролей
    print("\n" + "=" * 60)
    print("  Назначение ролей (Enter = пропустить, q = завершить)")
    print("=" * 60)

    team = []
    try:
        for user in humans:
            name = " ".join(filter(None, [user.first_name, user.last_name])) or "???"
            uname = f"@{user.username}" if user.username else f"tg:{user.id}"
            roles = ask_roles(f"{name} ({uname})")
            if not roles:
                print("     ↩️  пропущен")
                continue
            release_ids = ask_releases(anime_list)
            team.append({
                "telegram_id": user.id,
                "name": name,
                "username": user.username,
                "roles": roles,
                "release_ids": release_ids,
            })
            print(f"     ✅ {', '.join(r.upper() for r in roles)} → {len(release_ids)} релиз(а)")
    except KeyboardInterrupt:
        print("\n⏹  Ввод завершён")

    if not team:
        print("\nНикому не назначены роли — ничего не записываю.")
        await client.disconnect()
        return

    print("\n" + "=" * 60)
    print("  Итог:")
    for m in team:
        print(f"  {m['name']} ({m['username'] or m['telegram_id']}): {', '.join(r.upper() for r in m['roles'])}")
    print(f"\n  Аниме ({len(anime_list)}):")
    for a in anime_list:
        print(f"  [{a['id']}] {a.get('name_ru') or a.get('name')}")

    if input("\nЗаписать в базу данных? (y/n): ").strip().lower() != "y":
        print("Отменено.")
        await client.disconnect()
        return

    print("\n💾 Записываю...")
    conn = await asyncpg.connect(DB_DSN)
    try:
        for anime in anime_list:
            rid = await db_upsert_release(conn, anime)
            print(f"  📀 Релиз [{rid}]: {anime.get('name_ru') or anime.get('name')}")
        for m in team:
            uid = await db_upsert_user(conn, m["telegram_id"], m["name"], m["username"], m["roles"])
            for release_id in m["release_ids"]:
                for role in m["roles"]:
                    await db_add_assignment(conn, release_id, uid, role)
            print(f"  👤 {m['name']}: {', '.join(r.upper() for r in m['roles'])}")
    finally:
        await conn.close()

    print("\n✅ Готово! Привяжи релизы к чатам через /setreales в боте.")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
