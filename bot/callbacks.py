"""Маршрутизация callback-запросов по префиксам."""

import logging

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from bot.states import RenameRecord

from bot.database import (
    delete_record,
    get_record,
    get_records_count,
    get_user_records,
    get_user_settings,
    is_user_onboarded,
    set_user_onboarded,
    update_user_setting,
)
from bot.keyboards import (
    ONBOARDING_MESSAGES,
    back_to_menu_kb,
    delete_confirm_kb,
    help_kb,
    main_menu_kb,
    onboarding_kb,
    plans_kb,
    post_transcription_kb,
    record_card_kb,
    records_list_kb,
    reports_submenu_kb,
    settings_kb,
)
from bot.logo import edit_or_send_logo, send_logo

logger = logging.getLogger(__name__)

MAIN_MENU_TEXT = (
    "Привет! 👋 Я Даша — твой личный транскрибатор.\n"
    "Записывай голос или загружай файл — я превращу его в текст за секунды ✨\n\n"
    "Выбери, что хочешь сделать:"
)

HELP_FAQ = {
    "record": (
        "🤔 <b>Как записать аудио?</b>\n\n"
        "1. Нажми «🎤 Записать аудио» в главном меню\n"
        "2. Запиши голосовое сообщение\n"
        "3. Я расшифрую его за несколько секунд!"
    ),
    "formats": (
        "📎 <b>Поддерживаемые форматы</b>\n\n"
        "🎵 Аудио: MP3, WAV, OGG, FLAC, M4A, AAC, WMA, OPUS\n"
        "🎬 Видео: MP4, AVI, MOV, MKV, WebM, 3GP\n"
        "🔗 Ссылки: YouTube, TikTok, VK Video, Vimeo, Rutube, ОК, Twitch, Twitter/X, Instagram Reels"
    ),
    "youtube": (
        "🌐 <b>Как загрузить с YouTube?</b>\n\n"
        "Просто отправь мне ссылку на видео — я сама скачаю аудио и расшифрую.\n"
        "Работает с YouTube, VK, Instagram и другими платформами."
    ),
    "payment": (
        "💳 <b>Как оплатить?</b>\n\n"
        "Нажми «⭐ Тарифы» в главном меню и выбери подходящий план.\n"
        "Оплата через ЮKassa (карты, СБП)."
    ),
    "support": (
        "👤 <b>Поддержка</b>\n\n"
        "Если у тебя вопрос или проблема — напиши @dasha_support."
    ),
}


async def dispatch_callback(callback: CallbackQuery, state: FSMContext | None = None) -> bool:
    """Обработать callback по префиксу. Возвращает True если обработан."""
    payload = callback.data or ""

    if payload == "menu:main":
        await _show_main_menu(callback)
        return True

    if payload.startswith("onboarding:"):
        await _handle_onboarding(callback, payload)
        return True

    if payload.startswith("scenario:"):
        await _handle_scenario(callback, payload)
        return True

    if payload.startswith("record:"):
        await _handle_record(callback, payload, state)
        return True

    if payload.startswith("records:page:"):
        page = int(payload.split(":", 2)[2])
        user_id = callback.from_user.id
        records = get_user_records(user_id, limit=100)
        count = len(records)
        await edit_or_send_logo(
            callback.message,
            f"📁 Твои записи ({count} шт.):",
            reply_markup=records_list_kb(records, page=page),
        )
        return True

    if payload.startswith("reports:menu:"):
        record_id = payload.split(":", 2)[2]
        await edit_or_send_logo(callback.message, "📊 Дополнительные отчёты:",
                                reply_markup=reports_submenu_kb(record_id))
        return True

    if payload.startswith("help:faq:"):
        topic = payload.split(":", 2)[2]
        text = HELP_FAQ.get(topic, "Раздел не найден.")
        await edit_or_send_logo(callback.message, text, parse_mode="HTML",
                                reply_markup=back_to_menu_kb())
        return True

    if payload.startswith("settings:"):
        await _handle_settings(callback, payload)
        return True

    if payload.startswith("plan:"):
        await edit_or_send_logo(callback.message,
                                "⭐ Скоро здесь можно будет выбрать тариф!",
                                reply_markup=back_to_menu_kb())
        return True

    if payload.startswith("referral:"):
        await send_logo(callback.message, "💌 Реферальная программа скоро будет доступна!")
        return True

    return False


async def _show_main_menu(callback: CallbackQuery) -> None:
    await edit_or_send_logo(callback.message, MAIN_MENU_TEXT, reply_markup=main_menu_kb())


async def _handle_onboarding(callback: CallbackQuery, payload: str) -> None:
    parts = payload.split(":")
    if len(parts) == 3 and parts[1] == "step":
        step = int(parts[2])
        text = ONBOARDING_MESSAGES.get(step, "")
        if text:
            await edit_or_send_logo(callback.message, text, reply_markup=onboarding_kb(step))
        if step == 3:
            set_user_onboarded(callback.from_user.id)


async def _handle_scenario(callback: CallbackQuery, payload: str) -> None:
    scenario = payload.split(":", 1)[1]

    if scenario == "record":
        await edit_or_send_logo(
            callback.message,
            "🎤 Готова слушать! Запиши голосовое сообщение — я сразу его расшифрую ✨",
            reply_markup=back_to_menu_kb(),
        )

    elif scenario == "upload":
        await edit_or_send_logo(
            callback.message,
            "📤 Отправь мне файл или ссылку — я приму почти всё!\n\n"
            "🎵 Аудио: MP3, WAV, OGG, FLAC, M4A\n"
            "🎬 Видео: MP4, AVI, MOV, MKV, WebM\n"
            "🔗 Ссылки: YouTube, TikTok, VK, Instagram и другие",
            reply_markup=back_to_menu_kb(),
        )

    elif scenario == "records":
        user_id = callback.from_user.id
        records = get_user_records(user_id, limit=100)
        if not records:
            from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🎤 Записать аудио", callback_data="scenario:record")],
                [InlineKeyboardButton(text="🔙 Главное меню", callback_data="menu:main")],
            ])
            await edit_or_send_logo(
                callback.message,
                "📁 Здесь пока пусто. Запиши первое аудио — и оно появится тут!",
                reply_markup=kb,
            )
        else:
            await edit_or_send_logo(
                callback.message,
                f"📁 Твои записи ({len(records)} шт.):",
                reply_markup=records_list_kb(records, page=0),
            )

    elif scenario == "referral":
        await edit_or_send_logo(
            callback.message,
            "💌 Реферальная программа скоро будет доступна!\n"
            "Пригласи подругу — и вам обеим по +60 минут бесплатно.",
            reply_markup=back_to_menu_kb(),
        )

    elif scenario == "plans":
        await edit_or_send_logo(
            callback.message,
            "⭐ Выбери тариф, который подходит именно тебе:",
            reply_markup=plans_kb(),
        )

    elif scenario == "help":
        await edit_or_send_logo(callback.message, "❓ Чем могу помочь?", reply_markup=help_kb())


async def _handle_record(callback: CallbackQuery, payload: str, state: FSMContext | None = None) -> None:
    parts = payload.split(":", 2)
    if len(parts) < 3:
        return
    action, record_id = parts[1], parts[2]

    record = get_record(record_id)
    if not record:
        await edit_or_send_logo(callback.message, "⚠️ Запись не найдена.",
                                reply_markup=back_to_menu_kb())
        return

    if action == "open":
        title = record["title"]
        date = record["created_at"][:10] if record.get("created_at") else ""
        dur = record.get("duration_seconds")
        dur_str = f"{dur // 60}:{dur % 60:02d}" if dur else "—"
        text = f"📄 <b>{title}</b>\n📅 {date}\n⏱ {dur_str}"
        await edit_or_send_logo(callback.message, text, parse_mode="HTML",
                                reply_markup=record_card_kb(record_id))

    elif action == "view":
        text = record.get("transcription_text") or "Текст не найден."
        if len(text) > 4000:
            text = text[:4000] + "…\n\n(текст обрезан)"
        await callback.message.answer(text, parse_mode=None)

    elif action == "actions":
        await edit_or_send_logo(callback.message, "✅ Что сделать с текстом?",
                                reply_markup=post_transcription_kb(record_id))

    elif action == "delete":
        title = record["title"]
        await edit_or_send_logo(
            callback.message,
            f"🗑️ Удалить запись «{title}»?\nЭто действие нельзя отменить.",
            reply_markup=delete_confirm_kb(record_id),
        )

    elif action == "confirm_delete":
        delete_record(record_id)
        await edit_or_send_logo(callback.message, "🗑️ Запись удалена.",
                                reply_markup=back_to_menu_kb())

    elif action == "rename":
        if state:
            await state.set_state(RenameRecord.waiting_for_title)
            await state.update_data(rename_record_id=record_id)
        await edit_or_send_logo(callback.message, "✏️ Отправь новое название для записи:",
                                reply_markup=back_to_menu_kb())

    elif action == "download":
        text = record.get("transcription_text") or ""
        if not text:
            await callback.message.answer("⚠️ Текст записи пуст.")
            return
        import os
        import tempfile
        from aiogram.types import FSInputFile
        tmp_dir = tempfile.mkdtemp(prefix="download_")
        filename = f"{record['title']}.txt"
        path = os.path.join(tmp_dir, filename)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        await callback.message.answer_document(FSInputFile(path))
        try:
            os.remove(path)
            os.rmdir(tmp_dir)
        except OSError:
            pass


async def _handle_settings(callback: CallbackQuery, payload: str) -> None:
    user_id = callback.from_user.id
    parts = payload.split(":")

    if len(parts) == 2:
        param = parts[1]
        if param == "lang":
            from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🤖 Авто", callback_data="settings:lang:auto")],
                [InlineKeyboardButton(text="🇷🇺 Русский", callback_data="settings:lang:ru")],
                [InlineKeyboardButton(text="🇬🇧 English", callback_data="settings:lang:en")],
                [InlineKeyboardButton(text="🔙 Назад", callback_data="settings:back")],
            ])
            await edit_or_send_logo(callback.message, "🌐 Выбери язык транскрибации:", reply_markup=kb)
        elif param == "diarization":
            from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Вкл", callback_data="settings:diarization:on")],
                [InlineKeyboardButton(text="❌ Выкл", callback_data="settings:diarization:off")],
                [InlineKeyboardButton(text="🔙 Назад", callback_data="settings:back")],
            ])
            await edit_or_send_logo(callback.message, "👥 Разделение по спикерам:", reply_markup=kb)
        elif param == "export":
            from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="TXT", callback_data="settings:export:txt")],
                [InlineKeyboardButton(text="DOCX", callback_data="settings:export:docx")],
                [InlineKeyboardButton(text="PDF", callback_data="settings:export:pdf")],
                [InlineKeyboardButton(text="🔙 Назад", callback_data="settings:back")],
            ])
            await edit_or_send_logo(callback.message, "📤 Формат экспорта по умолчанию:", reply_markup=kb)
        elif param == "autotitle":
            from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Вкл (дата+тема)", callback_data="settings:autotitle:on")],
                [InlineKeyboardButton(text="❌ Выкл", callback_data="settings:autotitle:off")],
                [InlineKeyboardButton(text="🔙 Назад", callback_data="settings:back")],
            ])
            await edit_or_send_logo(callback.message, "⏰ Авто-название:", reply_markup=kb)
        elif param == "back":
            s = get_user_settings(user_id)
            await edit_or_send_logo(callback.message, "⚙️ Настройки:", reply_markup=settings_kb(s))
        return

    if len(parts) == 3:
        param, value = parts[1], parts[2]
        if param == "lang":
            update_user_setting(user_id, "transcription_language", value)
        elif param == "diarization":
            update_user_setting(user_id, "diarization", 1 if value == "on" else 0)
        elif param == "export":
            update_user_setting(user_id, "export_format", value)
        elif param == "autotitle":
            update_user_setting(user_id, "auto_title", 1 if value == "on" else 0)

        s = get_user_settings(user_id)
        await edit_or_send_logo(callback.message, "✅ Настройки обновлены!\n\n⚙️ Настройки:",
                                reply_markup=settings_kb(s))
