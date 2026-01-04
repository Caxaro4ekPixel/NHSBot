from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.repositories.database import get_all_users


def build_keyboard(items, selected_ids):
    kb = InlineKeyboardBuilder()

    selected_set = {str(s) for s in selected_ids}
    for it in items:
        id_str = str(it["id"])
        mark = "🟢" if id_str in selected_set else "⚫"
        title = it.get("name_ru") or it.get("title") or it.get("name") or id_str
        kb.button(text=f"{mark} {title[:40]}", callback_data=f"rl:tg:{id_str}")
    kb.adjust(1)

    kb.row(
        InlineKeyboardButton(text="Сохранить", callback_data="rl:save"),
        InlineKeyboardButton(text="Сброс", callback_data="rl:clear"),
        InlineKeyboardButton(text="Отмена", callback_data="rl:cancel"),
    )
    return kb.as_markup()


def kb_releases(releases: dict):
    rows = []
    for reales in releases:
        name = (reales.get('name_ru') or reales.get('name') or str(reales.get('id', '')))[:48]
        cb_data = f"rel:{reales['id']}"
        rows.append([InlineKeyboardButton(text=name, callback_data=cb_data)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_set_releases(releases: dict):
    rows = []
    for reales in releases:
        name = (reales.get('name_ru') or reales.get('name') or str(reales.get('id', '')))[:48]
        cb_data = f"relpick:{reales['id']}"
        rows.append([InlineKeyboardButton(text=name, callback_data=cb_data)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def kb_multi(role_users: dict, picked: list, can_next: bool):
    rows = []
    picked_set = set(picked) if isinstance(picked, list) else set()
    all_users = await get_all_users()
    for uid in role_users:
        mark = "✅ " if uid in picked_set else ""
        user = all_users.get(uid, {})
        name = user.get("name", f"ID {uid}")[:60]
        rows.append([InlineKeyboardButton(text=mark + name, callback_data=f"u:{uid}")])
    controls = [InlineKeyboardButton(text="Далее ▶️", callback_data="next")] if can_next else []
    rows.append(controls or [InlineKeyboardButton(text="Выберите участников", callback_data="noop")])
    rows.append([InlineKeyboardButton(text="Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def btn_user(uid: int):
    all_users = await get_all_users()
    u = all_users.get(uid, {})
    name = u.get("name", f"ID {uid}")
    username = u.get("username")
    label = f"{name} ({username})" if username else name
    return InlineKeyboardButton(text=label[:64], callback_data=f"u:{uid}")


async def kb_single(role_users: dict):
    rows = [[await btn_user(uid)] for uid in role_users]
    rows.append([InlineKeyboardButton(text="Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
