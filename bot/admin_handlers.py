"""Хэндлеры для admin-команд. Роутер регистрируется раньше основного."""

import asyncio
import json
import logging

from aiogram import Bot, Router
from aiogram.filters import Command, Filter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.database import (
    create_short_link,
    get_all_short_links_with_stats,
    get_all_user_ids,
    get_user_role,
    set_user_onboarded_flag,
    set_user_role,
)
from bot.states import BroadcastMessage

logger = logging.getLogger(__name__)

admin_router = Router()


class IsAdmin(Filter):
    """Пропускает только пользователей с ролью ADMIN."""

    async def __call__(self, message: Message) -> bool:
        user_id = message.from_user.id if message.from_user else 0
        return get_user_role(user_id) == "ADMIN"


admin_router.message.filter(IsAdmin())


@admin_router.message(Command("get_short_link"))
async def cmd_get_short_link(message: Message) -> None:
    """Создать короткую ссылку с UTM-параметрами."""
    user_id = message.from_user.id if message.from_user else 0

    raw = (message.text or "").split(maxsplit=1)
    if len(raw) < 2:
        await message.answer(
            "⚠️ Укажи JSON после команды. Пример:\n\n"
            '<code>/get_short_link {"utm_source": "telegain", "utm_medium": "cpp", '
            '"utm_campaign": "kampaniya", "erid": "2W5zFJ4UyYL"}</code>',
            parse_mode="HTML",
        )
        return

    try:
        data = json.loads(raw[1])
    except json.JSONDecodeError:
        await message.answer("❌ Невалидный JSON. Проверь формат и попробуй снова.")
        return

    if not isinstance(data, dict):
        await message.answer("❌ JSON должен быть объектом (словарём).")
        return

    code = create_short_link(
        utm_source=data.get("utm_source"),
        utm_medium=data.get("utm_medium"),
        utm_campaign=data.get("utm_campaign"),
        utm_content=data.get("utm_content"),
        utm_term=data.get("utm_term"),
        erid=data.get("erid"),
        created_by=user_id,
    )

    bot_info = await message.bot.get_me()
    bot_username = bot_info.username or "dasha_write_bot"
    link = f"https://t.me/{bot_username}?start={code}"

    parts = [f"✅ Короткая ссылка создана:\n<code>{link}</code>\n"]
    utm_fields = [
        ("source", data.get("utm_source")),
        ("medium", data.get("utm_medium")),
        ("campaign", data.get("utm_campaign")),
        ("content", data.get("utm_content")),
        ("term", data.get("utm_term")),
        ("erid", data.get("erid")),
    ]
    shown = [f"• {name}: {val}" for name, val in utm_fields if val]
    if shown:
        parts.append("UTM-параметры:\n" + "\n".join(shown))

    await message.answer("\n".join(parts), parse_mode="HTML")


@admin_router.message(Command("get_short_link_stats"))
async def cmd_get_short_link_stats(message: Message) -> None:
    """Статистика по всем коротким ссылкам."""
    links = get_all_short_links_with_stats()
    if not links:
        await message.answer("Нет созданных коротких ссылок.")
        return

    bot_info = await message.bot.get_me()
    bot_username = bot_info.username or "dasha_write_bot"

    lines = ["📊 <b>Статистика коротких ссылок</b>\n"]
    for link in links:
        url = f"https://t.me/{bot_username}?start={link['code']}"
        label = link.get("utm_campaign") or link.get("utm_source") or link["code"]
        lines.append(
            f"🔗 <b>{label}</b>\n"
            f"   <code>{url}</code>\n"
            f"   Переходов: {link['visits']} | Уникальных: {link['unique_users']}\n"
        )

    text = "\n".join(lines)
    if len(text) > 4096:
        text = text[:4090] + "\n…"
    await message.answer(text, parse_mode="HTML")


@admin_router.message(Command("set_admin"))
async def cmd_set_admin(message: Message) -> None:
    """Установить роль ADMIN для пользователя по ID."""
    args = (message.text or "").split()
    if len(args) < 2 or not args[1].lstrip("-").isdigit():
        await message.answer("⚠️ Использование: /set_admin <user_id>")
        return

    user_id = int(args[1])
    found = set_user_role(user_id, "ADMIN")
    if found:
        await message.answer(f"✅ Пользователь {user_id} теперь имеет роль ADMIN.")
    else:
        await message.answer(f"❌ Пользователь {user_id} не найден.")


@admin_router.message(Command("set_onboarding"))
async def cmd_set_onboarding(message: Message) -> None:
    """Установить флаг is_onboarded (0 или 1) для пользователя по ID."""
    args = (message.text or "").split()
    if len(args) < 3 or not args[1].lstrip("-").isdigit() or args[2] not in ("0", "1"):
        await message.answer("⚠️ Использование: /set_onboarding <user_id> <0|1>")
        return

    user_id = int(args[1])
    value = int(args[2])
    found = set_user_onboarded_flag(user_id, value)
    if found:
        await message.answer(f"✅ Пользователь {user_id}: is_onboarded = {value}.")
    else:
        await message.answer(f"❌ Пользователь {user_id} не найден.")


@admin_router.message(Command("send_message"))
async def cmd_send_message(message: Message, state: FSMContext) -> None:
    """Начать рассылку сообщения пользователям."""
    args = (message.text or "").split(maxsplit=1)
    target_ids: list[int] = []
    if len(args) > 1:
        raw = args[1].strip()
        for part in raw.split(","):
            part = part.strip()
            if part.isdigit():
                target_ids.append(int(part))
        if not target_ids:
            await message.answer("❌ Не удалось распознать ID пользователей.")
            return

    await state.update_data(target_ids=target_ids)
    await state.set_state(BroadcastMessage.waiting_for_message)

    if target_ids:
        ids_str = ", ".join(str(uid) for uid in target_ids)
        await message.answer(f"✏️ Введите сообщение для отправки пользователям ({ids_str}):")
    else:
        await message.answer("✏️ Введите сообщение для рассылки всем пользователям:")


@admin_router.message(BroadcastMessage.waiting_for_message)
async def process_broadcast_message(message: Message, state: FSMContext, bot: Bot) -> None:
    """Отправить введённое сообщение пользователям."""
    data = await state.get_data()
    target_ids = data.get("target_ids", [])
    await state.clear()

    user_ids = target_ids if target_ids else await asyncio.to_thread(get_all_user_ids)
    status_msg = await message.answer(f"📤 Начинаю рассылку для {len(user_ids)} пользователей...")

    sent = 0
    failed = 0
    for uid in user_ids:
        try:
            await message.copy_to(chat_id=uid)
            sent += 1
        except Exception:
            failed += 1

    await status_msg.edit_text(
        f"✅ Рассылка завершена!\n\n"
        f"📨 Отправлено: {sent}\n"
        f"❌ Не доставлено: {failed}"
    )
