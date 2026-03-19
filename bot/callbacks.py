"""Маршрутизация callback-запросов по префиксам."""

import asyncio
import logging
import threading
import time

from aiogram import Bot
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove

from bot.states import AskQuestion, RenameRecord, WaitingPhone

from bot.database import (
    PLANS,
    add_referral,
    delete_record,
    get_record,
    get_records_count,
    get_referral_count,
    get_referral_minutes_earned,
    get_user_balance,
    get_user_phone,
    get_user_plan_info,
    get_user_records,
    get_user_ref_code,
    get_user_settings,
    is_user_onboarded,
    mark_payment_paid,
    save_payment,
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
from bot.payment import create_payment, get_payment_status
from bot.config import SUMMARIZER_MAX_CHARS
from bot.report_generator import generate_report
from bot.s3_storage import delete_object, download_text

logger = logging.getLogger(__name__)

MAIN_MENU_TEXT = (
    "👋 Привет! Я Даша.\n\n"
    "Говори — я запишу.\n\n"
    "Обрабатываю:\n"
    "🎵 Аудиофайлы\n"
    "🔗 Ссылки YouTube, VK, Instagram\n"
    "💬 Голосовые из Telegram\n\n"
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
        "Оплата через T-Bank (карты, СБП, T-Pay)."
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
            image="my_notes",
        )
        return True

    if payload.startswith("summary:gen:"):
        record_id = payload.split(":", 2)[2]
        await _handle_report(callback, "summary", record_id)
        return True

    if payload.startswith("summary:back:"):
        record_id = payload.split(":", 2)[2]
        await edit_or_send_logo(callback.message, "✅ Что сделать с текстом?",
                                reply_markup=post_transcription_kb(record_id))
        return True

    if payload.startswith("questions:gen:"):
        record_id = payload.split(":", 2)[2]
        await _start_qa_mode(callback, record_id, state)
        return True

    if payload.startswith("questions:back:"):
        record_id = payload.split(":", 2)[2]
        if state:
            await state.clear()
        await edit_or_send_logo(callback.message, "✅ Что сделать с текстом?",
                                reply_markup=post_transcription_kb(record_id))
        return True

    if payload.startswith("report:"):
        parts = payload.split(":", 2)
        if len(parts) == 3:
            report_type, record_id = parts[1], parts[2]
            await _handle_report(callback, report_type, record_id)
            return True

    if payload.startswith("reports:menu:"):
        record_id = payload.split(":", 2)[2]
        await edit_or_send_logo(callback.message, "📊 Дополнительные отчёты:",
                                reply_markup=reports_submenu_kb(record_id))
        return True

    if payload.startswith("help:faq:"):
        topic = payload.split(":", 2)[2]
        text = HELP_FAQ.get(topic, "Раздел не найден.")
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад к вопросам", callback_data="scenario:help")],
        ])
        await edit_or_send_logo(callback.message, text, parse_mode="HTML",
                                reply_markup=kb, image="faq")
        return True

    if payload.startswith("settings:"):
        await _handle_settings(callback, payload)
        return True

    if payload.startswith("plan:"):
        await _handle_plan(callback, payload, state)
        return True

    if payload.startswith("referral:"):
        await _handle_referral_callback(callback, payload)
        return True

    return False


_REPORT_LABELS = {
    "summary": "✨ Краткий конспект",
    "insights": "💡 Ключевые инсайты",
    "action_items": "✅ Список задач",
    "questions": "❓ Вопросы к тексту",
    "mind_map": "🧠 Mind Map",
    "swot": "📈 SWOT-анализ",
    "timeline": "🕒 Timeline",
    "quotes": "🗣️ Цитаты спикеров",
    "decisions": "🎯 Решения и договорённости",
    "glossary": "📝 Глоссарий терминов",
    "stats": "📊 Статистика текста",
    "translate": "🌐 Перевод",
    "followup": "📧 Письмо по итогам",
}


async def _load_transcription(record: dict) -> str:
    """Загрузить текст транскрипции из S3 (или из БД для старых записей)."""
    s3_key = record.get("text_s3_key")
    if s3_key:
        return await asyncio.to_thread(download_text, s3_key)
    # Fallback для старых записей, сохранённых в БД
    return record.get("transcription_text") or ""


async def _handle_report(callback: CallbackQuery, report_type: str, record_id: str) -> None:
    """Генерация отчёта по record_id и отправка результата."""
    record = get_record(record_id)
    if not record:
        await edit_or_send_logo(callback.message, "⚠️ Запись не найдена.",
                                reply_markup=back_to_menu_kb())
        return

    text = await _load_transcription(record)
    if not text.strip():
        await edit_or_send_logo(callback.message, "⚠️ Текст записи пуст.",
                                reply_markup=post_transcription_kb(record_id))
        return

    if len(text) > SUMMARIZER_MAX_CHARS:
        await callback.message.answer(
            "❌ Текст записи слишком большой для генерации отчёта: превышен лимит контекста."
        )
        return

    label = _REPORT_LABELS.get(report_type, report_type)
    status_msg = await callback.message.answer(f"⏳ Генерирую: {label}…")

    result = await asyncio.to_thread(generate_report, report_type, text)

    if not result:
        try:
            await status_msg.edit_text(f"❌ Не удалось сгенерировать: {label}")
        except Exception:
            pass
        return

    try:
        await status_msg.edit_text(f"✅ {label} готов!")
    except Exception:
        pass

    # Отправляем результат в expandable blockquote, если влезает
    expandable = f"<blockquote expandable>{result}</blockquote>"
    if len(expandable) < 4096:
        try:
            await callback.message.answer(expandable, parse_mode="HTML",
                                          reply_markup=post_transcription_kb(record_id))
        except Exception:
            await callback.message.answer(result, parse_mode=None,
                                          reply_markup=post_transcription_kb(record_id))
    else:
        # Длинный текст — отправляем файлом
        import os
        import tempfile
        from aiogram.types import FSInputFile
        tmp_dir = tempfile.mkdtemp(prefix="report_")
        filename = f"{report_type}_{record['title']}.txt"
        path = os.path.join(tmp_dir, filename)
        with open(path, "w", encoding="utf-8") as f:
            f.write(result)
        await callback.message.answer_document(
            FSInputFile(path),
            reply_markup=post_transcription_kb(record_id),
        )
        try:
            os.remove(path)
            os.rmdir(tmp_dir)
        except OSError:
            pass


async def _start_qa_mode(callback: CallbackQuery, record_id: str, state: FSMContext | None) -> None:
    """Перевести пользователя в режим вопросов по тексту."""
    record = get_record(record_id)
    if not record:
        await edit_or_send_logo(callback.message, "⚠️ Запись не найдена.",
                                reply_markup=back_to_menu_kb())
        return

    if state:
        await state.set_state(AskQuestion.waiting_for_question)
        await state.update_data(qa_record_id=record_id)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад к действиям", callback_data=f"questions:back:{record_id}")],
    ])
    await edit_or_send_logo(
        callback.message,
        "❓ Задай любой вопрос по тексту — я отвечу на основе содержания записи.\n\n"
        "Просто напиши свой вопрос:",
        reply_markup=kb,
    )


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
            image="voice_message",
        )

    elif scenario == "upload":
        await edit_or_send_logo(
            callback.message,
            "📤 Отправь мне файл или ссылку — я приму почти всё!\n\n"
            "🎵 Аудио: MP3, WAV, OGG, FLAC, M4A\n"
            # "🎬 Видео: MP4, AVI, MOV, MKV, WebM\n"
            "🔗 Ссылки: YouTube, TikTok, VK, Instagram и другие",
            reply_markup=back_to_menu_kb(),
            image="send_file",
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
                image="my_notes",
            )
        else:
            await edit_or_send_logo(
                callback.message,
                f"📁 Твои записи ({len(records)} шт.):",
                reply_markup=records_list_kb(records, page=0),
                image="my_notes",
            )

    elif scenario == "referral":
        await _show_referral(callback)


    elif scenario == "plans":
        user_id = callback.from_user.id
        balance = get_user_balance(user_id)
        if balance == -1:
            balance_str = "безлимит ♾"
        else:
            balance_str = f"{balance} мин"
        await edit_or_send_logo(
            callback.message,
            f"⏱ Твой баланс: <b>{balance_str}</b>\n\n"
            "⭐ Выбери тариф, который подходит именно тебе:",
            parse_mode="HTML",
            reply_markup=plans_kb(),
            image="payments",
        )

    elif scenario == "help":
        await edit_or_send_logo(callback.message, "❓ Чем могу помочь?", reply_markup=help_kb(),
                                image="faq")


async def _handle_record(callback: CallbackQuery, payload: str, state: FSMContext | None = None) -> None:
    parts = payload.split(":", 2)
    if len(parts) < 3:
        return
    action, record_id = parts[1], parts[2]

    record = get_record(record_id)
    if not record:
        await edit_or_send_logo(callback.message, "⚠️ Запись не найдена.",
                                reply_markup=back_to_menu_kb(),
                                image="my_notes")
        return

    if action == "open":
        title = record["title"]
        date = record["created_at"][:10] if record.get("created_at") else ""
        dur = record.get("duration_seconds")
        dur_str = f"{dur // 60}:{dur % 60:02d}" if dur else "—"
        text = f"📄 <b>{title}</b>\n📅 {date}\n⏱ {dur_str}"
        await edit_or_send_logo(callback.message, text, parse_mode="HTML",
                                reply_markup=record_card_kb(record_id),
                                image="my_notes")

    elif action == "view":
        text = await _load_transcription(record) or "Текст не найден."
        # Обрезаем текст, чтобы влез в expandable blockquote (лимит 4096 символов)
        max_text_len = 4096 - len("<blockquote expandable></blockquote>")
        if len(text) > max_text_len:
            text = text[:max_text_len - 20] + "…\n\n(текст обрезан)"
        expandable = f"<blockquote expandable>{text}</blockquote>"
        try:
            await callback.message.answer(expandable, parse_mode="HTML",
                                          reply_markup=record_card_kb(record_id))
        except Exception:
            await callback.message.answer(text, parse_mode=None,
                                          reply_markup=record_card_kb(record_id))

    elif action == "actions":
        await edit_or_send_logo(callback.message, "✅ Что сделать с текстом?",
                                reply_markup=post_transcription_kb(record_id),
                                image="my_notes")

    elif action == "delete":
        title = record["title"]
        await edit_or_send_logo(
            callback.message,
            f"🗑️ Удалить запись «{title}»?\nЭто действие нельзя отменить.",
            reply_markup=delete_confirm_kb(record_id),
            image="my_notes",
        )

    elif action == "confirm_delete":
        s3_key = record.get("text_s3_key")
        if s3_key:
            try:
                await asyncio.to_thread(delete_object, s3_key)
            except Exception as exc:
                logger.warning("Не удалось удалить S3 объект %s: %s", s3_key, exc)
        delete_record(record_id)
        await edit_or_send_logo(callback.message, "🗑️ Запись удалена.",
                                reply_markup=back_to_menu_kb(),
                                image="my_notes")

    elif action == "rename":
        if state:
            await state.set_state(RenameRecord.waiting_for_title)
            await state.update_data(rename_record_id=record_id)
        await edit_or_send_logo(callback.message, "✏️ Отправь новое название для записи:",
                                reply_markup=back_to_menu_kb(),
                                image="my_notes")

    elif action == "download":
        text = await _load_transcription(record)
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


# ── Тарифы и оплата ──────────────────────────────────────

async def _handle_plan(callback: CallbackQuery, payload: str, state: FSMContext | None = None) -> None:
    """Обработка callback'ов plan:current:{code} и plan:buy:{code}."""
    parts = payload.split(":")
    user_id = callback.from_user.id

    if len(parts) >= 3 and parts[1] == "current":
        # Показать информацию о текущем тарифе
        plan_info = get_user_plan_info(user_id)
        balance = plan_info["balance"]
        if balance == -1:
            balance_str = "безлимит"
        else:
            balance_str = f"{balance} мин"
        text = (
            f"📋 <b>Твой тариф: {plan_info['name']}</b>\n\n"
            f"⏱ Остаток: {balance_str}\n"
        )
        await edit_or_send_logo(callback.message, text, parse_mode="HTML",
                                reply_markup=plans_kb(),
                                image="payments")
        return

    if len(parts) >= 3 and parts[1] == "buy":
        plan_code = parts[2]
        plan = PLANS.get(plan_code)
        if not plan:
            await edit_or_send_logo(callback.message, "⚠️ Тариф не найден.",
                                    reply_markup=back_to_menu_kb())
            return

        plan_name, plan_minutes, plan_price = plan
        if plan_price <= 0:
            await edit_or_send_logo(callback.message, "🌿 Бесплатный тариф уже активен!",
                                    reply_markup=back_to_menu_kb())
            return

        if plan_minutes == -1:
            minutes_str = "безлимит"
        else:
            minutes_str = f"+{plan_minutes} мин"

        # Подтверждение покупки
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=f"💳 Оплатить {plan_price}₽",
                callback_data=f"plan:pay:{plan_code}",
            )],
            [InlineKeyboardButton(text="🔙 Назад к тарифам", callback_data="scenario:plans")],
        ])
        await edit_or_send_logo(
            callback.message,
            f"🛒 <b>Тариф «{plan_name}»</b>\n\n"
            f"📦 {minutes_str} транскрибации\n"
            f"💰 Стоимость: {plan_price}₽\n\n"
            f"Нажми «Оплатить» для перехода к оплате.",
            parse_mode="HTML",
            reply_markup=kb,
        )
        return

    if len(parts) >= 3 and parts[1] == "pay":
        plan_code = parts[2]
        plan = PLANS.get(plan_code)
        if not plan:
            await edit_or_send_logo(callback.message, "⚠️ Тариф не найден.",
                                    reply_markup=back_to_menu_kb())
            return

        phone = get_user_phone(user_id)
        if not phone:
            # Телефон не указан — запрашиваем перед оплатой
            if state:
                await state.set_state(WaitingPhone.waiting_for_phone)
                await state.update_data(pay_plan_code=plan_code)
            phone_kb = ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text="📱 Отправить номер", request_contact=True)]],
                resize_keyboard=True,
                one_time_keyboard=True,
            )
            await callback.message.answer(
                "📱 Для формирования чека укажи номер телефона.\n\n"
                "Нажми кнопку ниже или отправь номер в формате +7XXXXXXXXXX:",
                reply_markup=phone_kb,
            )
            return

        await _create_and_send_payment(callback.message, user_id, plan_code, phone)


async def _create_and_send_payment(
    message,
    user_id: int,
    plan_code: str,
    phone: str,
) -> None:
    """Создать платёж в T-Bank и отправить ссылку на оплату."""
    plan = PLANS.get(plan_code)
    if not plan:
        await message.answer("⚠️ Тариф не найден.")
        return

    plan_name, _, plan_price = plan

    await message.answer("⏳ Создаю платёж…")

    result = await asyncio.to_thread(
        create_payment, plan_price, f"Тариф «{plan_name}»", phone
    )
    if not result:
        await message.answer("❌ Не удалось создать платёж. Попробуй позже.")
        return

    payment_id, payment_url = result

    try:
        save_payment(payment_id, user_id, plan_price, subscription_code=plan_code)
    except Exception as exc:
        logger.error("Ошибка сохранения платежа %s: %s", payment_id, exc)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оплатить", url=payment_url)],
        [InlineKeyboardButton(text="🔙 Главное меню", callback_data="menu:main")],
    ])
    await message.answer("Нажми кнопку ниже для оплаты:", reply_markup=kb)

    # Запускаем поллинг статуса платежа
    bot: Bot = message.bot
    loop = asyncio.get_running_loop()
    threading.Thread(
        target=_poll_plan_payment,
        args=(bot, loop, message.chat.id, user_id, payment_id, plan_code),
        daemon=True,
    ).start()


def _poll_plan_payment(
    bot: Bot,
    loop: asyncio.AbstractEventLoop,
    chat_id: int,
    user_id: int,
    payment_id: str,
    plan_code: str,
) -> None:
    """Поллинг статуса платежа каждые 5 сек, до 10 минут."""
    plan = PLANS.get(plan_code)
    plan_name = plan[0] if plan else plan_code
    deadline = time.time() + 600

    while time.time() < deadline:
        time.sleep(5)
        try:
            status = get_payment_status(payment_id)
        except Exception as exc:
            logger.error("Ошибка проверки статуса платежа %s: %s", payment_id, exc)
            continue

        if status == "succeeded":
            try:
                mark_payment_paid(payment_id, user_id, subscription_code=plan_code)
                new_balance = get_user_balance(user_id)
                if new_balance == -1:
                    bal_str = "безлимит"
                else:
                    bal_str = f"{new_balance} мин"
                asyncio.run_coroutine_threadsafe(
                    bot.send_message(
                        chat_id,
                        f"✅ Оплата прошла успешно!\n"
                        f"Тариф «{plan_name}» активирован.\n"
                        f"Твой баланс: {bal_str}",
                        reply_markup=main_menu_kb(),
                    ),
                    loop,
                )
            except Exception as exc:
                logger.error("Ошибка зачисления платежа %s: %s", payment_id, exc)
            return

        if status == "canceled":
            asyncio.run_coroutine_threadsafe(
                bot.send_message(chat_id, "❌ Платёж отменён."),
                loop,
            )
            return


# ── Реферальная программа ─────────────────────────────────

async def _show_referral(callback: CallbackQuery) -> None:
    """Показать реферальную ссылку и статистику."""
    user_id = callback.from_user.id
    ref_code = get_user_ref_code(user_id)
    count = get_referral_count(user_id)
    earned = get_referral_minutes_earned(user_id)

    bot_info = await callback.message.bot.get_me()
    bot_username = bot_info.username or "dasha_bot"
    ref_link = f"https://t.me/{bot_username}?start=ref_{ref_code}"

    text = (
        f"💌 <b>Пригласи друга!</b>\n\n"
        f"Поделись ссылкой — и ты получишь <b>+30 минут</b> за каждого друга!\n\n"
        f"🔗 Твоя ссылка:\n<code>{ref_link}</code>\n\n"
        f"👥 Приглашено: {count}\n"
        f"⏱ Заработано минут: {earned}"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Главное меню", callback_data="menu:main")],
    ])
    await edit_or_send_logo(callback.message, text, parse_mode="HTML", reply_markup=kb,
                            image="invite_friend")


async def _handle_referral_callback(callback: CallbackQuery, payload: str) -> None:
    """Обработка callback'ов referral:*."""
    # На данный момент все referral: callback'ы ведут на показ реферальной страницы
    await _show_referral(callback)
