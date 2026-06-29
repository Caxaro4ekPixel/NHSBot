import asyncio
import os
import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.client.telegram import TelegramAPIServer
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, BufferedInputFile, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramBadRequest
from telethon import TelegramClient
from telethon.sessions import StringSession
from bot.middleware.logging import LoggingMiddleware
from bot.services.service import users_with, load_reales, fmt_users, save_assignment, mention_html, collect_member_ids, \
    build_chat_title, load_assignment, search_reales, download_bytes, add_release, update_release_chat_id
from bot.core.state import RealesState, SetReales
from bot.keyboards.keyboard import build_keyboard, kb_multi, kb_releases, kb_single, kb_set_releases
from bot.api.shikimori import fetch_shikimori_releases, extract_anime_id_from_url, fetch_anime_by_id
from bot.models import init_db
from bot.repositories.database import complete_release, update_release_from_shikimori, update_release_search_prefix, \
    migrate_users_from_file
from bot.core.logger import setup_logging, get_logger
from bot.utils.subtitle_converter import convert_ass_to_srt
from bot.repositories.publishing import (
    get_release_by_topic, set_release_topic, save_topic_file,
    get_latest_topic_files, save_release_post, get_release_credits,
    get_topic_for_release_in_group, set_release_tags, set_release_file_prefix,
    save_staging_post, get_staging_post, mark_staging_published,
)
from bot.services.media_processor import process_episode, STEPS, _detect_file_type
from bot.services.yadisk import extract_cloud_url, get_resource_info
from bot.services.publisher import publish_to_staging, publish_from_staging
from config import BOT_VERSION, LOCAL_API_URL, RELEASE_GROUP_ID, ANNOUNCEMENT_CHANNEL_ID, STAGING_CHAT_ID

setup_logging()
logger = get_logger(__name__)

load_dotenv(".env")
TOKEN = os.getenv("BOT_TOKEN")

ADMINS_STR = os.getenv("ADMINS") or ""
ADMINS = [a.strip() for a in ADMINS_STR.split(",") if a.strip()]

router = Router()
dp = Dispatcher()
dp.include_router(router)

if LOCAL_API_URL:
    _api_server = TelegramAPIServer.from_base(LOCAL_API_URL, is_local=False)
else:
    _api_server = None

bot = Bot(
    token=TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    **({"server": _api_server} if _api_server else {}),
)

_tg_api_id = int(os.getenv("TELEGRAM_API_ID", "0"))
_tg_api_hash = os.getenv("TELEGRAM_API_HASH", "")
telethon_client = None
if _tg_api_id and _tg_api_hash:
    telethon_client = TelegramClient(StringSession(), _tg_api_id, _tg_api_hash)

dp.message.middleware(LoggingMiddleware())
dp.callback_query.middleware(LoggingMiddleware())


async def safe_edit_markup(msg, markup):
    try:
        await msg.edit_reply_markup(reply_markup=markup)
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            return
        raise


@router.message(Command("id"))
async def get_id(msg: Message):
    print(msg.chat.id)


@router.message(Command("start"))
async def command_start_handler(message: Message) -> None:
    if message.chat.type not in ("group", "supergroup"):
        return
    reales = await load_reales()
    if not reales:
        await message.answer("Список релизов пуст.")
        return
    if str(message.from_user.id) not in ADMINS:
        await message.answer('Not authorized!')
        return
    await message.answer("Выберите релиз:", reply_markup=kb_set_releases(reales))


@router.message(Command("reales"))
async def command_reales_handler(message: Message, state: FSMContext) -> None:
    if message.chat.type == "private":
        if str(message.chat.id) not in ADMINS:
            await message.answer('Not authorized!')
            return
        try:
            items = await fetch_shikimori_releases()
            if not items:
                await message.answer("Не удалось получить релизы из Shikimori или список пуст.")
                return
            items = sorted(items, key=lambda x: (x.get("name_ru") or x.get("name") or "").lower())
            await state.set_state(RealesState.choosing)
            await state.update_data(items=items, selected=[])
            await message.answer(
                "Выбери релизы, затем нажми «Сохранить»:",
                reply_markup=build_keyboard(items, []),
            )
        except Exception as e:
            logger.error(f"Error fetching shikimori releases: {e}", exc_info=True)
            await message.answer("Ошибка при получении релизов из Shikimori.")
    elif message.chat.type == "group" or message.chat.type == "supergroup":
        logger.debug(f"Command /reales in group: {message.text}")


@router.message(Command("release_add"))
async def command_release_add_handler(message: Message) -> None:
    if message.chat.type != "private":
        return
    if str(message.from_user.id) not in ADMINS:
        await message.answer('Not authorized!')
        return

    text = message.text or ""
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Использование: /release_add <url_shikimori>")
        return

    url = parts[1].strip()
    anime_id = extract_anime_id_from_url(url)

    if not anime_id:
        await message.answer("Не удалось извлечь ID аниме из URL. Убедитесь, что URL содержит /animes/ID")
        return

    await message.answer("Получаю данные об аниме...")

    anime_data = await fetch_anime_by_id(anime_id)
    if not anime_data:
        await message.answer(f"Не удалось получить данные об аниме с ID {anime_id}. Проверьте URL.")
        return

    success = await add_release(anime_data)
    if not success:
        await message.answer(f"Релиз с ID {anime_id} уже существует в списке.")
        return

    name_ru = anime_data.get("name_ru") or anime_data.get("name") or f"ID {anime_id}"
    await message.answer(f"✅ Релиз добавлен:\n<b>{name_ru}</b>\nID: {anime_id}")


@router.callback_query(RealesState.choosing, F.data.startswith("rl:tg:"))
async def cb_toggle(cb: CallbackQuery, state: FSMContext):
    if str(cb.from_user.id) not in ADMINS:
        await cb.answer("Not authorized!", show_alert=True)
        return

    parts = cb.data.split(":")
    if len(parts) < 3:
        await cb.answer("Ошибка формата данных", show_alert=True)
        return

    item_id = parts[2]
    data = await state.get_data()
    selected = list(data.get("selected", []))
    items = data.get("items", [])

    if item_id in selected:
        selected.remove(item_id)
    else:
        selected.append(item_id)

    await state.update_data(selected=selected)
    await safe_edit_markup(cb.message, build_keyboard(items, selected))
    await cb.answer()


@router.callback_query(RealesState.choosing, F.data == "rl:clear")
async def cb_clear(cb: CallbackQuery, state: FSMContext):
    if str(cb.from_user.id) not in ADMINS:
        await cb.answer("Not authorized!", show_alert=True)
        return

    data = await state.get_data()
    items = data.get("items", [])
    await state.update_data(selected=[])
    await safe_edit_markup(cb.message, build_keyboard(items, []))
    await cb.answer("Сброшено")


@router.callback_query(RealesState.choosing, F.data == "rl:cancel")
async def cb_cancel(cb: CallbackQuery, state: FSMContext):
    if str(cb.from_user.id) not in ADMINS:
        await cb.answer("Not authorized!", show_alert=True)
        return

    await state.clear()
    await cb.message.edit_text("Отменено.")
    await cb.answer()


@router.callback_query(RealesState.choosing, F.data == "rl:save")
async def cb_save(cb: CallbackQuery, state: FSMContext):
    if str(cb.from_user.id) not in ADMINS:
        await cb.answer("Not authorized!", show_alert=True)
        return

    data = await state.get_data()
    items = data.get("items", [])
    selected = data.get("selected", [])
    if not selected:
        await cb.answer("Ничего не выбрано", show_alert=True)
        return
    selected_set = {str(s) for s in selected}
    picked_full = [
        it for it in items if str(it.get("id", "")) in selected_set
    ]

    added_count = 0
    for item in picked_full:
        if await add_release(item):
            added_count += 1

    await state.clear()
    names = ",\n".join([
        (it.get("name_ru") or it.get("title") or it.get("name") or str(it["id"]))
        for it in picked_full[:5]
    ])
    extra = "…" if len(picked_full) > 5 else ""
    await cb.message.edit_text(f"Сохранено! Добавлено: {added_count}\n{names}{extra}")
    await cb.answer()


@router.message(Command("setreales"))
async def setreales_start(message: Message, state: FSMContext):
    if message.chat.type != "private":
        return
    if str(message.chat.id) not in ADMINS:
        await message.answer("Not authorized!")
        return
    reales = await load_reales()
    await state.update_data(reales=reales)
    await state.set_state(SetReales.PickRelease)
    await message.answer("Выберите релиз:", reply_markup=kb_releases(reales))


@router.callback_query(SetReales.PickRelease, F.data.startswith("rel:"))
async def pick_release(cb: CallbackQuery, state: FSMContext):
    if str(cb.from_user.id) not in ADMINS:
        await cb.answer("Not authorized!", show_alert=True)
        return

    parts = cb.data.split(":", 1)
    if len(parts) < 2:
        await cb.answer("Ошибка формата данных", show_alert=True)
        return

    rid = parts[1]
    data = (await state.get_data()).get('reales', [])
    reales = await search_reales(rid, data)
    if not reales:
        await cb.answer("Релиз не найден", show_alert=True)
        return
    name = reales.get('name_ru', '')
    await state.update_data(rid=rid, title=name, translators=[], voices=[], designers=[], timing=None,
                            curator=None)
    trs = await users_with("translator")
    await state.set_state(SetReales.PickTranslator)
    await cb.message.edit_text(f'Релиз: {name}\nВыберите переводчиков (≥0):',
                               reply_markup=await kb_multi(trs, [], True))
    await cb.answer()


@router.callback_query(SetReales.PickTranslator, F.data.startswith("u:"))
async def pick_translator_toggle(cb: CallbackQuery, state: FSMContext):
    if str(cb.from_user.id) not in ADMINS:
        await cb.answer("Not authorized!", show_alert=True)
        return

    parts = cb.data.split(":", 1)
    if len(parts) < 2:
        await cb.answer("Ошибка формата данных", show_alert=True)
        return

    try:
        uid = int(parts[1])
    except ValueError:
        await cb.answer("Неверный формат ID", show_alert=True)
        return

    data = await state.get_data()
    trs = await users_with("translator")
    picked = list(data.get("translators", []))
    if uid in picked:
        picked.remove(uid)
    else:
        picked.append(uid)
    await state.update_data(translators=picked)
    await cb.message.edit_reply_markup(reply_markup=await kb_multi(trs, picked, True))
    await cb.answer()


@router.callback_query(SetReales.PickTranslator, F.data == "next")
async def next_to_voice(cb: CallbackQuery, state: FSMContext):
    if str(cb.from_user.id) not in ADMINS:
        await cb.answer("Not authorized!", show_alert=True)
        return

    vcs = await users_with("voice")
    await state.set_state(SetReales.PickVoice)
    await cb.message.edit_text("Выберите войсеров (>1):", reply_markup=await kb_multi(vcs, [], False))
    await cb.answer()


@router.callback_query(SetReales.PickVoice, F.data.startswith("u:"))
async def pick_voice_toggle(cb: CallbackQuery, state: FSMContext):
    if str(cb.from_user.id) not in ADMINS:
        await cb.answer("Not authorized!", show_alert=True)
        return

    parts = cb.data.split(":", 1)
    if len(parts) < 2:
        await cb.answer("Ошибка формата данных", show_alert=True)
        return

    try:
        uid = int(parts[1])
    except ValueError:
        await cb.answer("Неверный формат ID", show_alert=True)
        return

    data = await state.get_data()
    vcs = await users_with("voice")
    picked = list(data.get("voices", []))
    if uid in picked:
        picked.remove(uid)
    else:
        picked.append(uid)
    await state.update_data(voices=picked)
    await cb.message.edit_reply_markup(reply_markup=await kb_multi(vcs, picked, len(picked) > 1))
    await cb.answer()


@router.callback_query(SetReales.PickVoice, F.data == "next")
async def next_to_timing(cb: CallbackQuery, state: FSMContext):
    if str(cb.from_user.id) not in ADMINS:
        await cb.answer("Not authorized!", show_alert=True)
        return

    data = await state.get_data()
    voices = data.get("voices", [])
    if len(voices) <= 1:
        await cb.answer("Нужно >1 войсер", show_alert=True)
        return
    tms = await users_with("timing")
    await state.set_state(SetReales.PickTiming)
    await cb.message.edit_text("Выберите таймера (1):", reply_markup=await kb_single(tms))
    await cb.answer()


@router.callback_query(SetReales.PickTiming, F.data.startswith("u:"))
async def pick_timing(cb: CallbackQuery, state: FSMContext):
    if str(cb.from_user.id) not in ADMINS:
        await cb.answer("Not authorized!", show_alert=True)
        return

    parts = cb.data.split(":", 1)
    if len(parts) < 2:
        await cb.answer("Ошибка формата данных", show_alert=True)
        return

    try:
        uid = int(parts[1])
    except ValueError:
        await cb.answer("Неверный формат ID", show_alert=True)
        return

    await state.update_data(timing=uid)
    curs = await users_with("curator")
    await state.set_state(SetReales.PickCurator)
    await cb.message.edit_text("Выберите куратора (1):", reply_markup=await kb_single(curs))
    await cb.answer()


@router.callback_query(SetReales.PickCurator, F.data.startswith("u:"))
async def pick_curator(cb: CallbackQuery, state: FSMContext):
    if str(cb.from_user.id) not in ADMINS:
        await cb.answer("Not authorized!", show_alert=True)
        return

    parts = cb.data.split(":", 1)
    if len(parts) < 2:
        await cb.answer("Ошибка формата данных", show_alert=True)
        return

    try:
        uid = int(parts[1])
    except ValueError:
        await cb.answer("Неверный формат ID", show_alert=True)
        return

    await state.update_data(curator=uid)
    dsg = await users_with("designer")
    await state.set_state(SetReales.PickDesigner)
    await cb.message.edit_text("Выберите оформителей (≥0):", reply_markup=await kb_multi(dsg, [], True))
    await cb.answer()


@router.callback_query(SetReales.PickDesigner, F.data.startswith("u:"))
async def pick_designer_toggle(cb: CallbackQuery, state: FSMContext):
    if str(cb.from_user.id) not in ADMINS:
        await cb.answer("Not authorized!", show_alert=True)
        return

    parts = cb.data.split(":", 1)
    if len(parts) < 2:
        await cb.answer("Ошибка формата данных", show_alert=True)
        return

    try:
        uid = int(parts[1])
    except ValueError:
        await cb.answer("Неверный формат ID", show_alert=True)
        return

    data = await state.get_data()
    dsg = await users_with("designer")
    picked = list(data.get("designers", []))
    if uid in picked:
        picked.remove(uid)
    else:
        picked.append(uid)
    await state.update_data(designers=picked)
    await cb.message.edit_reply_markup(reply_markup=await kb_multi(dsg, picked, True))
    await cb.answer()


@router.callback_query(SetReales.PickDesigner, F.data == "next")
async def finish_assignment(cb: CallbackQuery, state: FSMContext):
    if str(cb.from_user.id) not in ADMINS:
        await cb.answer("Not authorized!", show_alert=True)
        return

    data = await state.get_data()
    rid = data.get("rid")
    title = data.get("title", "")

    if not rid:
        await cb.answer("Ошибка: ID релиза не найден", show_alert=True)
        return

    try:
        translators = [int(x) for x in data.get("translators", [])]
        voices = [int(x) for x in data.get("voices", [])]
        timing = int(data["timing"]) if data.get("timing") is not None else None
        curator = int(data["curator"]) if data.get("curator") is not None else None
        designers = [int(x) for x in data.get("designers", [])]

        if timing is None or curator is None:
            await cb.answer("Ошибка: не все обязательные поля заполнены", show_alert=True)
            return

        result = {
            "translator": translators,
            "voice": voices,
            "timing": timing,
            "curator": curator,
            "designer": designers
        }
    except (ValueError, TypeError) as e:
        logger.error(f"Error processing assignment data: {e}", exc_info=True)
        await cb.answer("Ошибка обработки данных", show_alert=True)
        return
    try:
        await save_assignment(rid, result)
        translator_text = await fmt_users(result['translator'])
        voice_text = await fmt_users(result['voice'])
        timing_text = await fmt_users([result['timing']])
        curator_text = await fmt_users([result['curator']])
        designer_text = await fmt_users(result['designer'])
        text = (
            f"🎬 {title} (ID: {rid})\n"
            f"• Перевод: {translator_text}\n"
            f"• Войсы: {voice_text}\n"
            f"• Тайминг: {timing_text}\n"
            f"• Куратор: {curator_text}\n"
            f"• Оформление: {designer_text}"
        )
        await cb.message.edit_text(text)
        await state.clear()
        await cb.answer()
    except Exception as e:
        logger.error(f"Error saving assignment: {e}", exc_info=True)
        await cb.answer("Ошибка при сохранении назначения", show_alert=True)


@router.callback_query(F.data == "cancel")
async def cancel_any(cb: CallbackQuery, state: FSMContext):
    if str(cb.from_user.id) not in ADMINS:
        await cb.answer("Not authorized!", show_alert=True)
        return

    await state.clear()
    await cb.message.edit_text("Отменено.")
    await cb.answer()


@router.callback_query(F.data.startswith("relpick:"))
async def on_release_pick(cb: CallbackQuery):
    if str(cb.from_user.id) not in ADMINS:
        await cb.answer("Not authorized!", show_alert=True)
        return

    parts = cb.data.split(":", 1)
    if len(parts) < 2:
        await cb.answer("Ошибка формата данных", show_alert=True)
        return

    rid = parts[1]

    try:
        release_id = int(rid)
    except ValueError:
        await cb.answer("Неверный формат ID релиза", show_alert=True)
        return

    title_src = await search_reales(rid)
    if not title_src:
        await cb.answer("Релиз не найден", show_alert=True)
        return

    chat_title = build_chat_title(title_src.get("name_ru", ""))
    try:
        await cb.message.bot.set_chat_title(cb.message.chat.id, chat_title)
    except Exception as e:
        logger.warning(f"Failed to set chat title: {e}")

    try:
        success = await update_release_chat_id(release_id, cb.message.chat.id)
        if not success:
            await cb.answer("Релиз не найден в базе данных", show_alert=True)
            return
        logger.info(f"Release {release_id} attached to chat {cb.message.chat.id}")
    except Exception as e:
        logger.error(f"Failed to update release chat_id: {e}", exc_info=True)
        await cb.answer("Ошибка при сохранении chat_id", show_alert=True)
        return

    assignment = await load_assignment(rid)
    member_ids = collect_member_ids(assignment or {})
    invite_link = None
    try:
        link = await cb.message.bot.create_chat_invite_link(cb.message.chat.id, name=chat_title)
        invite_link = link.invite_link
        logger.info(f"Created invite link for release {release_id}: {invite_link}")
    except Exception as e:
        logger.warning(f"Failed to create invite link: {e}")
    text_lines = [
        f"Установлено название:\n<b>{chat_title}</b>",
        "Участники:"
    ]
    if member_ids:
        mentions = [await mention_html(uid) for uid in member_ids]
        text_lines.append(", ".join(mentions))
    else:
        text_lines.append("—")
    if invite_link:
        text_lines.append(f"\nСсылка-приглашение:\n{invite_link}")
    await cb.message.edit_text("\n".join(text_lines), parse_mode=ParseMode.HTML)
    await cb.answer()


@router.callback_query(F.data.startswith("stg_pub:"))
async def on_staging_publish(cb: CallbackQuery):
    if str(cb.from_user.id) not in ADMINS:
        await cb.answer("Not authorized!", show_alert=True)
        return

    staging_post_id = int(cb.data.split(":", 1)[1])
    staging_post = await get_staging_post(staging_post_id)
    if not staging_post:
        await cb.answer("Пост не найден", show_alert=True)
        return
    if staging_post["status"] == "published":
        await cb.answer("Уже опубликовано", show_alert=True)
        return

    await cb.answer("Публикую...")
    try:
        release = await search_reales(str(staging_post["release_id"]))
        credits = await get_release_credits(staging_post["release_id"])

        post_ids = await publish_from_staging(
            bot=bot,
            staging_chat_id=STAGING_CHAT_ID,
            staging_post=staging_post,
            release=release,
            credits=credits,
        )

        await save_release_post(
            release_id=staging_post["release_id"],
            episode=staging_post["episode"],
            group_id=staging_post["group_id"],
            topic_id=staging_post["topic_id"],
            mp4_msg_id=post_ids.get("group_mp4_id"),
            mkv_msg_id=post_ids.get("group_mkv_id"),
            channel_id=staging_post["channel_id"],
            channel_msg_id=post_ids.get("channel_msg_id"),
        )

        await mark_staging_published(staging_post_id)

        await cb.message.edit_text(
            cb.message.text + "\n\n✅ Опубликовано!",
            reply_markup=None,
        )
    except Exception as e:
        logger.error(f"Staging publish failed: {e}", exc_info=True)
        await cb.message.edit_text(
            cb.message.text + f"\n\n❌ Ошибка: {str(e)[:200]}",
        )


@router.callback_query(F.data == "noop")
async def noop(cb: CallbackQuery):
    await cb.answer("Выберите хотя бы одного участника", show_alert=True)


@router.message(Command("complete_release"))
async def command_complete_release_handler(message: Message) -> None:
    if message.chat.type != "private":
        return
    if str(message.from_user.id) not in ADMINS:
        await message.answer('Not authorized!')
        return

    text = message.text or ""
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Использование: /complete_release <code>release_id</code>")
        return

    try:
        release_id = int(parts[1].strip())
    except ValueError:
        await message.answer("Неверный формат ID. Используйте число.")
        return

    release = await search_reales(str(release_id))
    if not release:
        await message.answer(f"Релиз с ID {release_id} не найден.")
        return

    success = await complete_release(release_id)
    if success:
        name_ru = release.get("name_ru") or release.get("name") or f"ID {release_id}"
        await message.answer(f"✅ Релиз завершен:\n<b>{name_ru}</b>\nID: {release_id}")
    else:
        await message.answer(f"Ошибка при завершении релиза {release_id}")


@router.message(Command("check_release"))
async def command_check_release_handler(message: Message) -> None:
    if message.chat.type != "private":
        return
    if str(message.from_user.id) not in ADMINS:
        await message.answer('Not authorized!')
        return

    text = message.text or ""
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Использование: /check_release <code>release_id</code>")
        return

    try:
        release_id = int(parts[1].strip())
    except ValueError:
        await message.answer("Неверный формат ID. Используйте число.")
        return

    await message.answer("Проверяю данные на Shikimori...")

    anime_data = await fetch_anime_by_id(release_id)
    if not anime_data:
        await message.answer(f"Не удалось получить данные об аниме с ID {release_id}.")
        return

    success = await update_release_from_shikimori(release_id, anime_data)
    if success:
        release = await search_reales(str(release_id))
        name_ru = release.get("name_ru") or release.get("name") or f"ID {release_id}"
        episodes = release.get("episodes")
        episodes_aired = release.get("episodes_aired")
        is_completed = release.get("is_completed", False)

        status_text = "✅ Завершен" if is_completed else "⏳ В процессе"
        await message.answer(
            f"✅ Данные обновлены:\n"
            f"<b>{name_ru}</b>\n"
            f"ID: {release_id}\n"
            f"Серий: {episodes_aired}/{episodes if episodes else '?'}\n"
            f"Статус: {status_text}"
        )
    else:
        await message.answer(f"Релиз с ID {release_id} не найден в базе данных.")


@router.message(Command("set_prefix"))
async def command_set_prefix_handler(message: Message) -> None:
    if message.chat.type != "private":
        return
    if str(message.from_user.id) not in ADMINS:
        await message.answer('Not authorized!')
        return

    text = message.text or ""
    parts = text.split(maxsplit=2)
    if len(parts) < 3:
        await message.answer("Использование: /set_prefix <code>release_id</code> <code>prefix</code>\nПример: /set_prefix 12345 [Erai-raws]")
        return

    try:
        release_id = int(parts[1].strip())
    except ValueError:
        await message.answer("Неверный формат ID. Используйте число.")
        return

    prefix = parts[2].strip()

    release = await search_reales(str(release_id))
    if not release:
        await message.answer(f"Релиз с ID {release_id} не найден.")
        return

    success = await update_release_search_prefix(release_id, prefix)
    if success:
        name_ru = release.get("name_ru") or release.get("name") or f"ID {release_id}"
        await message.answer(
            f"✅ Префикс поиска обновлен:\n<b>{name_ru}</b>\nID: {release_id}\nПрефикс: <code>{prefix}</code>")
    else:
        await message.answer(f"Ошибка при обновлении префикса для релиза {release_id}")


@router.message(Command("srt"))
async def command_srt_handler(message: Message) -> None:
    doc_message = message
    if not message.document:
        if message.reply_to_message and message.reply_to_message.document:
            doc_message = message.reply_to_message
        else:
            await message.answer(
                "Отправьте .ass файл с командой /srt или ответьте командой /srt на сообщение с файлом"
            )
            return

    file_name = doc_message.document.file_name or "subtitle.ass"

    if not file_name.lower().endswith('.ass'):
        await message.answer("❌ Файл должен быть в формате ASS (.ass)")
        return

    try:
        file = await bot.get_file(doc_message.document.file_id)
        file_bytes = await bot.download_file(file.file_path)

        if hasattr(file_bytes, 'read'):
            file_content = file_bytes.read()
        elif isinstance(file_bytes, bytes):
            file_content = file_bytes
        else:
            raise ValueError(f"Unexpected file content type: {type(file_bytes)}")

        tz = ZoneInfo("Europe/Moscow")
        generated_at = datetime.now(tz).strftime("%Y-%m-%d %H:%M МСК")
        srt_file = convert_ass_to_srt(file_content, file_name, generated_at, BOT_VERSION)

        await message.answer_document(
            document=srt_file,
            caption=f"✅ Конвертировано: {srt_file.filename}"
        )
        logger.info(f"Converted ASS to SRT: {file_name} -> {srt_file.filename}")

    except ValueError as e:
        error_msg = str(e).replace('<', '&lt;').replace('>', '&gt;')
        await message.answer(f"❌ Ошибка: {error_msg}", parse_mode=ParseMode.HTML)
        logger.error(f"Error converting ASS file {file_name}: {e}", exc_info=True)
    except Exception as e:
        error_msg = str(e).replace('<', '&lt;').replace('>', '&gt;')
        await message.answer(f"❌ Произошла ошибка при конвертации: {error_msg}", parse_mode=ParseMode.HTML)
        logger.error(f"Error converting ASS file {file_name}: {e}", exc_info=True)


@router.message(Command("settopic"))
async def cmd_settopic(message: Message) -> None:
    if not message.message_thread_id:
        await message.reply("Команду нужно отправить внутри топика.")
        return
    parts = (message.text or "").split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.reply("Использование: /settopic <code>release_id</code>\nПример: /settopic 62001")
        return
    release_id = int(parts[1])
    group_id = message.chat.id
    topic_id = message.message_thread_id
    ok = await set_release_topic(release_id, group_id, topic_id)
    if ok:
        await message.reply(f"✅ Топик привязан к релизу ID {release_id}")
    else:
        await message.reply("❌ Ошибка при сохранении.")


_MIME_TO_TYPE = {
    "audio/flac": "flac",
    "audio/x-flac": "flac",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "video/x-matroska": "mkv",
    "video/quicktime": "mov",
    "application/zip": "zip",
    "application/x-zip-compressed": "zip",
    "application/x-rar-compressed": "rar",
    "application/vnd.rar": "rar",
    "application/x-7z-compressed": "7z",
}


@router.message(F.document | F.audio)
async def track_topic_file(message: Message) -> None:
    if not message.message_thread_id:
        return
    file_obj = message.document or message.audio
    if not file_obj:
        return
    group_id = message.chat.id
    topic_id = message.message_thread_id
    release = await get_release_by_topic(group_id, topic_id)
    if not release:
        return
    file_type = _detect_file_type(file_obj.file_name) or _MIME_TO_TYPE.get(
        getattr(file_obj, "mime_type", None) or ""
    )
    if not file_type:
        logger.debug(f"Skipped unrecognized file: name={file_obj.file_name} mime={getattr(file_obj, 'mime_type', None)}")
        return
    await save_topic_file(
        release_id=release["id"],
        group_id=group_id,
        topic_id=topic_id,
        message_id=message.message_id,
        file_type=file_type,
        file_id=file_obj.file_id,
        file_name=file_obj.file_name,
        file_size=file_obj.file_size,
    )
    logger.info(f"Tracked {file_type} ({file_obj.file_name}) in release {release['id']} topic {topic_id}")


COVERS_DIR = Path("/app/covers")


def cover_path(release_id: int, episode: int) -> Path:
    return COVERS_DIR / str(release_id) / f"{episode}.jpg"


@router.message(Command("setcover"))
async def cmd_setcover(message: Message) -> None:
    parts = (message.text or "").split()
    if len(parts) < 3 or not parts[1].isdigit() or not parts[2].isdigit():
        await message.reply("Использование: ответь на фото командой /setcover <code>release_id</code> <code>episode</code>")
        return

    release_id, episode = int(parts[1]), int(parts[2])

    photo = None
    if message.reply_to_message and message.reply_to_message.photo:
        photo = message.reply_to_message.photo[-1]
    elif message.photo:
        photo = message.photo[-1]

    if not photo:
        await message.reply("Прикрепи фото или ответь на сообщение с фото.")
        return

    dest = cover_path(release_id, episode)
    dest.parent.mkdir(parents=True, exist_ok=True)

    file = await bot.get_file(photo.file_id)
    await bot.download_file(file.file_path, destination=str(dest))

    await message.reply(f"✅ Обложка для релиза {release_id}, серия {episode} сохранена.")
    logger.info(f"Cover saved: release={release_id} episode={episode} path={dest}")


@router.message(F.text & ~F.text.startswith("/"))
async def track_yadisk_link(message: Message) -> None:
    if not message.message_thread_id or not message.text:
        return
    result = extract_cloud_url(message.text)
    if not result:
        return
    url, provider = result
    group_id = message.chat.id
    topic_id = message.message_thread_id
    release = await get_release_by_topic(group_id, topic_id)
    if not release:
        return
    info = await get_resource_info(url, provider)
    provider_name = "Google Drive" if provider == "gdrive" else "Яндекс Диска"
    if not info or not info.get("name"):
        await message.reply(f"⚠️ Не удалось получить информацию о файле с {provider_name}.")
        return
    file_name = info["name"]
    file_type = _detect_file_type(file_name)
    if not file_type:
        return
    await save_topic_file(
        release_id=release["id"],
        group_id=group_id,
        topic_id=topic_id,
        message_id=message.message_id,
        file_type=file_type,
        file_id=url,
        file_name=file_name,
        file_size=info.get("size"),
    )
    logger.info(f"Tracked {provider} {file_type} ({file_name}) in release {release['id']} topic {topic_id}")
    await message.reply(f"✅ Ссылка сохранена ({provider_name}): {file_type.upper()} — {file_name}")


@router.message(Command("pub"))
async def cmd_pub(message: Message) -> None:
    if not message.message_thread_id:
        await message.reply("Команду нужно отправить внутри топика.")
        return
    parts = (message.text or "").split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.reply("Использование: /pub <code>номер_серии</code>\nПример: /pub 10")
        return
    episode = int(parts[1])
    src_group_id = message.chat.id
    src_topic_id = message.message_thread_id

    release = await get_release_by_topic(src_group_id, src_topic_id)
    if not release:
        await message.reply(
            "❌ Этот топик не привязан к релизу.\n"
            "Привяжи этот (рабочий) топик: /settopic <code>release_id</code>\n"
            "И топик в группе релизов тоже: /settopic <code>release_id</code>"
        )
        return

    if not RELEASE_GROUP_ID:
        await message.reply("❌ RELEASE_GROUP_ID не задан в .env")
        return

    dest_topic_id = await get_topic_for_release_in_group(release["id"], RELEASE_GROUP_ID)
    if not dest_topic_id:
        await message.reply(
            f"❌ Не найден топик для этого релиза в группе релизов.\n"
            f"Зайди в нужный топик той группы и напиши: /settopic {release['id']}"
        )
        return

    files = await get_latest_topic_files(release["id"], src_topic_id)

    has_audio = any(k in files for k in ("flac", "wav"))
    has_video = any(k in files for k in ("mkv", "mov", "zip", "rar", "7z"))
    if not has_audio or not has_video:
        missing = []
        if not has_audio:
            missing.append("аудио (.flac/.wav)")
        if not has_video:
            missing.append("видео (.mkv/.mov или архив)")
        await message.reply(f"❌ Не хватает файлов в этом топике: {', '.join(missing)}")
        return

    release_name = release.get("name_ru") or release.get("name") or f"ID {release['id']}"
    status_msg = await message.reply(
        f"⏳ Начинаю обработку серии {episode} «{release_name}»..."
    )

    async def on_progress(step: str, pct=None):
        if step.startswith("download_"):
            rest = step[len("download_"):]
            parts = rest.split(" ", 1)
            ext = parts[0].upper()
            suffix = f" {parts[1]}" if len(parts) > 1 else ""
            label = f"📥 Скачиваю {ext}{suffix}..."
        elif step == "upload_mp4":
            label = "📤 Загружаю MP4..."
        elif step == "upload_mkv":
            label = "📤 Загружаю MKV..."
        elif step == "upload_channel":
            label = "📣 Публикую в канал..."
        else:
            label = STEPS.get(step, step)
        text = label if pct is None else f"{label} {pct}%"
        try:
            await status_msg.edit_text(text)
        except Exception:
            pass

    work_dir = Path(tempfile.mkdtemp(prefix=f"pub_{release['id']}_{episode}_"))
    try:
        paths = await process_episode(bot, files, work_dir, on_progress, telethon_client=telethon_client)

        _prefix = release.get("file_prefix") or release.get("name") or release_name
        _safe = re.sub(r'[^\w\s-]', '', _prefix).strip()
        _safe = re.sub(r'\s+', '_', _safe)
        named_mkv = paths["mkv"].parent / f"{_safe}_E{episode:02d}.mkv"
        named_mp4 = paths["mp4"].parent / f"{_safe}_E{episode:02d}.mp4"
        paths["mkv"].rename(named_mkv)
        paths["mp4"].rename(named_mp4)
        paths["mkv"] = named_mkv
        paths["mp4"] = named_mp4

        credits = await get_release_credits(release["id"])
        channel_id = ANNOUNCEMENT_CHANNEL_ID or None
        ep_cover = cover_path(release["id"], episode)

        staging_ids = await publish_to_staging(
            bot=bot,
            staging_chat_id=STAGING_CHAT_ID,
            release=release,
            episode=episode,
            mkv_path=paths["mkv"],
            mp4_path=paths["mp4"],
            screenshot_path=paths["screenshot"],
            credits=credits,
            cover_path=ep_cover if ep_cover.exists() else None,
            on_progress=on_progress,
            telethon_client=telethon_client,
        )

        staging_post_id = await save_staging_post(
            release_id=release["id"],
            episode=episode,
            group_id=RELEASE_GROUP_ID,
            topic_id=dest_topic_id,
            channel_id=channel_id,
            staging_mp4_id=staging_ids["staging_mp4_id"],
            staging_mkv_id=staging_ids["staging_mkv_id"],
            staging_channel_msg_id=staging_ids["staging_channel_msg_id"],
        )

        release_name_display = release.get("name_ru") or release.get("name") or str(release["id"])
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text="✅ Опубликовать",
                callback_data=f"stg_pub:{staging_post_id}"
            )
        ]])
        await bot.send_message(
            chat_id=STAGING_CHAT_ID,
            text=f"📋 <b>{release_name_display}</b> — серия {episode}\n\nГотово к публикации в группу (топик {dest_topic_id})",
            reply_markup=kb,
            parse_mode="HTML",
        )

        await status_msg.edit_text(
            f"✅ Серия {episode} «{release_name}» загружена в стейджинг!\nАдмин должен подтвердить публикацию."
        )
    except Exception as e:
        logger.error(f"Publishing failed for release {release['id']} ep{episode}: {e}", exc_info=True)
        try:
            await status_msg.edit_text(f"❌ Ошибка: {str(e)[:300]}")
        except Exception:
            pass
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


@router.message(Command("settags"))
async def cmd_settags(message: Message) -> None:
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 3 or not parts[1].isdigit():
        await message.reply(
            "Использование: /settags <code>release_id</code> <code>#тег1 #тег2</code>\n"
            "Пример: /settags 62001 #NewHorizons #Anime"
        )
        return
    release_id = int(parts[1])
    tags = parts[2].strip()
    ok = await set_release_tags(release_id, tags)
    if ok:
        await message.reply(f"✅ Теги релиза {release_id} обновлены:\n<code>{tags}</code>")
    else:
        await message.reply(f"❌ Релиз {release_id} не найден.")


@router.message(Command("setfilename"))
async def cmd_setfilename(message: Message) -> None:
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 3 or not parts[1].isdigit():
        await message.reply(
            "Использование: /setfilename <code>release_id</code> <code>ИмяФайла</code>\n"
            "Пример: /setfilename 62001 NHS_HaruNoMai\n"
            "Результат: <code>NHS_HaruNoMai_E12.mkv</code>"
        )
        return
    release_id = int(parts[1])
    prefix = parts[2].strip()
    ok = await set_release_file_prefix(release_id, prefix)
    if ok:
        await message.reply(
            f"✅ Имя файла для релиза {release_id} обновлено:\n"
            f"<code>{prefix}_E01.mkv / {prefix}_E01.mp4</code>"
        )
    else:
        await message.reply(f"❌ Релиз {release_id} не найден.")


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    text = (
        "<b>📖 Инструкция по боту</b>\n\n"

        "<b>1. Подготовка релиза</b>\n"
        "• <code>/release_add &lt;shikimori_url&gt;</code> — добавить аниме из Shikimori\n"
        "• <code>/settags &lt;release_id&gt; &lt;#тег1 #тег2&gt;</code> — задать кастомные теги для постов\n"
        "• <code>/setfilename &lt;release_id&gt; &lt;имя&gt;</code> — имя файла MKV/MP4 (пример: <code>NHS_HaruNoMai</code>)\n"
        "• <code>/setcover &lt;release_id&gt; &lt;серия&gt;</code> — обложка серии (ответить на фото)\n\n"

        "<b>2. Привязка топиков</b>\n"
        "Команда пишется <b>внутри топика</b>:\n"
        "• <code>/settopic &lt;release_id&gt;</code> — привязать топик к релизу\n"
        "  Нужно сделать в двух местах:\n"
        "  — в рабочем топике (где лежат файлы)\n"
        "  — в топике группы релизов (куда идут посты)\n\n"

        "<b>3. Файлы в рабочем топике</b>\n"
        "После <code>/settopic</code> бот автоматически запоминает:\n"
        "• Аудио (.flac / .wav)\n"
        "• Видео (.mkv / .mov) или архив (.zip / .rar)\n"
        "• Субтитры (.ass) — опционально\n"
        "• Ссылки Яндекс Диска / Google Drive\n\n"

        "<b>4. Публикация</b>\n"
        "• <code>/pub &lt;серия&gt;</code> — конвертация + загрузка в группу и канал\n"
        "  Команда пишется в рабочем топике\n\n"

        "<b>5. Управление командой</b>\n"
        "• <code>/reales</code> — выбрать релизы из Shikimori (личный чат)\n"
        "• <code>/setreales</code> — назначить команду на релиз (личный чат)\n\n"

        "<b>6. Утилиты</b>\n"
        "• <code>/srt</code> — конвертировать ASS → SRT\n"
        "  (отправить файл с командой или ответить на файл)\n"
        "• <code>/check_release &lt;release_id&gt;</code> — обновить данные с Shikimori\n"
        "• <code>/complete_release &lt;release_id&gt;</code> — отметить релиз завершённым\n"
    )
    await message.reply(text)


async def main() -> None:
    await init_db()
    migrated_count = await migrate_users_from_file()
    if migrated_count > 0:
        logger.info(f"Migrated {migrated_count} users from file to database")
    async def _start_telethon():
        from telethon.errors import FloodWaitError
        for attempt in range(5):
            try:
                await telethon_client.start(bot_token=TOKEN)
                logger.info("Telethon client started (large file downloads enabled)")
                return
            except FloodWaitError as e:
                logger.warning(f"Telethon FloodWait {e.seconds}s (attempt {attempt+1}/5), waiting...")
                await asyncio.sleep(e.seconds + 5)
        logger.error("Telethon failed to start after 5 attempts, /pub will be unavailable")

    if telethon_client:
        asyncio.create_task(_start_telethon())

    logger.info("Bot started and ready to receive updates")
    try:
        await dp.start_polling(bot)
    finally:
        if telethon_client:
            await telethon_client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
