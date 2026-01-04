import asyncio
import os
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, BufferedInputFile
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramBadRequest
from bot.middleware.logging import LoggingMiddleware
from bot.services.service import users_with, load_reales, fmt_users, save_assignment, mention_html, collect_member_ids, \
    build_chat_title, load_assignment, search_reales, download_bytes, add_release, update_release_chat_id
from bot.core.state import RealesState, SetReales
from bot.keyboards.keyboard import build_keyboard, kb_multi, kb_releases, kb_single, kb_set_releases
from bot.api.shikimori import fetch_shikimori_releases, extract_anime_id_from_url, fetch_anime_by_id
from bot.models import init_db
from bot.repositories.database import complete_release, update_release_from_shikimori, update_release_search_prefix, migrate_users_from_file
from bot.core.logger import setup_logging, get_logger
from bot.utils.subtitle_converter import convert_ass_to_srt

setup_logging()
logger = get_logger(__name__)

load_dotenv(".env")
TOKEN = os.getenv("BOT_TOKEN")

ADMINS_STR = os.getenv("ADMINS") or ""
ADMINS = [a.strip() for a in ADMINS_STR.split(",") if a.strip()]

router = Router()
dp = Dispatcher()
dp.include_router(router)

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

# Register middleware
dp.message.middleware(LoggingMiddleware())
dp.callback_query.middleware(LoggingMiddleware())


async def safe_edit_markup(msg, markup):
    try:
        await msg.edit_reply_markup(reply_markup=markup)
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            return
        raise


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
    # if title_src.get('image'):
    #     try:
    #         image_url = title_src['image']
    #         data = await download_bytes(image_url)
    #         if len(data) > 10 * 1024 * 1024:
    #             logging.warning(f"Image too large for release {release_id}: {len(data)} bytes")
    #         else:
    #             im = Image.open(io.BytesIO(data))
    #             if im.mode not in ("RGB", "RGBA"):
    #                 im = im.convert("RGB")
    #             w, h = im.size
    #             side = min(w, h)
    #             x = (w - side) // 2
    #             y = (h - side) // 2
    #             im = im.crop((x, y, x + side, y + side)).resize((512, 512), Image.LANCZOS)
    #             buf = io.BytesIO()
    #             im.save(buf, format="JPEG", quality=90)
    #             photo = BufferedInputFile(buf.getvalue(), filename="poster.jpg")
    #             await cb.message.bot.set_chat_photo(cb.message.chat.id, photo)
    #     except Exception as e:
    #         logging.error(f"Failed to set chat photo for release {release_id}: {e}")
    # for uid in member_ids:
    #     try:
    #         await cb.message.bot.send_message(uid, f"Приглашение в группу релиза:\n{invite_link}")
    #     except Exception:
    #         pass
    await cb.message.edit_text("\n".join(text_lines), parse_mode=ParseMode.HTML)
    await cb.answer()


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
        await message.answer("Использование: /complete_release <release_id>")
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
        await message.answer("Использование: /check_release <release_id>")
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
        await message.answer("Использование: /set_prefix <release_id> <prefix>\nПример: /set_prefix 12345 [Erai-raws]")
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
        await message.answer(f"✅ Префикс поиска обновлен:\n<b>{name_ru}</b>\nID: {release_id}\nПрефикс: <code>{prefix}</code>")
    else:
        await message.answer(f"Ошибка при обновлении префикса для релиза {release_id}")


@router.message(Command("srt"))
async def command_srt_handler(message: Message) -> None:
    if not message.document:
        await message.answer("Пожалуйста, отправьте файл формата ASS вместе с командой /srt")
        return
    
    file_name = message.document.file_name or "subtitle.ass"
    
    if not file_name.lower().endswith('.ass'):
        await message.answer("❌ Файл должен быть в формате ASS (.ass)")
        return
    
    try:
        await message.answer("⏳ Конвертирую файл...")
        
        file = await bot.get_file(message.document.file_id)
        file_bytes = await bot.download_file(file.file_path)
        
        if hasattr(file_bytes, 'read'):
            file_content = file_bytes.read()
        elif isinstance(file_bytes, bytes):
            file_content = file_bytes
        else:
            raise ValueError(f"Unexpected file content type: {type(file_bytes)}")
        
        srt_file = convert_ass_to_srt(file_content, file_name)
        
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


async def main() -> None:
    await init_db()
    migrated_count = await migrate_users_from_file()
    if migrated_count > 0:
        logger.info(f"Migrated {migrated_count} users from file to database")
    logger.info("Bot started and ready to receive updates")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
