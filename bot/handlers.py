import asyncio
import logging
import os
import tempfile
import threading
import time
import uuid

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from aiogram.types import ReplyKeyboardRemove

from bot.states import AskQuestion, BroadcastMessage, RenameRecord, WaitingPhone

from bot.audio_splitter import extract_audio
from bot.callbacks import dispatch_callback, _create_and_send_payment
from bot.config import SUPPORTED_AUDIO_EXTENSIONS
from bot.logo import send_logo
from bot.database import (
    add_referral,
    create_short_link,
    deduct_balance,
    get_all_short_links_with_stats,
    get_all_user_ids,
    find_user_by_ref_code,
    get_or_create_user,
    get_record,
    get_records_count,
    get_short_link,
    get_user_balance,
    get_user_phone,
    get_user_plan_info,
    get_user_records,
    get_user_role,
    get_user_settings,
    has_sufficient_balance,
    is_user_onboarded,
    mark_payment_paid,
    rename_record,
    save_payment,
    save_user_phone,
    save_record,
    set_user_onboarded,
    track_short_link_visit,
)
from bot.keyboards import (
    ONBOARDING_TEXT,
    back_to_menu_kb,
    error_kb,
    help_kb,
    main_menu_kb,
    onboarding_kb,
    plans_kb,
    post_transcription_kb,
    records_list_kb,
    settings_kb,
)
from bot.link_downloader import download_audio_from_url, extract_media_url
from bot.payment import create_payment, get_payment_status
from bot.summarizer import summarize_text
from bot.s3_storage import upload_text
from bot.transcriber import TranscriptionError, transcribe_audio

logger = logging.getLogger(__name__)

router = Router()

# Хранилище контекста транскрибации для кнопки "Сделать саммари"
# Ключ: callback payload (уникальный ID), значение: (text, audio_stem)
_summary_context: dict[str, tuple[str, str]] = {}

# Per-user блокировка: не даём начать новую транскрибацию, пока предыдущая не завершена
_user_processing_locks: dict[int, asyncio.Lock] = {}


def _get_user_lock(user_id: int) -> asyncio.Lock:
    if user_id not in _user_processing_locks:
        _user_processing_locks[user_id] = asyncio.Lock()
    return _user_processing_locks[user_id]


BUSY_TEXT = "⏳ Подожди — предыдущий запрос ещё обрабатывается."

WELCOME_TEXT = (
    "👋 Привет! Я Даша.\n\n"
    "Говори — я запишу.\n\n"
    "Обрабатываю:\n"
    "🎵 Аудиофайлы\n"
    "🔗 Ссылки YouTube, VK, Instagram\n"
    "💬 Голосовые из Telegram\n\n"
    "Выбери, что хочешь сделать:"
)

DOWNLOADING_TEXT = "⏳ Скачиваю аудио…"
PREPARING_TEXT = "⏳ Подготавливаю аудио…"


# Временное хранилище текста demo-транскрибации (per-user)
_demo_context: dict[int, str] = {}


def get_demo_context(user_id: int) -> str | None:
    """Получить текст demo-транскрибации для пользователя."""
    return _demo_context.get(user_id)


async def process_demo_audio(message: Message, sent_audio_msg: Message) -> None:
    """Обработать demo-аудио при онбординге.

    Облегчённый пайплайн: транскрибирует, показывает текст в чате,
    НЕ сохраняет в БД/S3 и НЕ списывает минуты.
    """
    from bot.keyboards import demo_post_transcription_kb

    bot: Bot = message.bot
    user_id = message.chat.id

    audio = sent_audio_msg.audio
    if not audio:
        return

    file_id = audio.file_id
    filename = audio.file_name or "demo.m4a"

    status_msg = await message.answer(DOWNLOADING_TEXT)
    tmp_dir = tempfile.mkdtemp(prefix="transcriber_")

    try:
        dest_path = os.path.join(tmp_dir, filename)
        tg_file = await bot.get_file(file_id)
        await bot.download_file(tg_file.file_path, dest_path)
        logger.info("Скачан demo-файл: %s", dest_path)

        if status_msg:
            await status_msg.edit_text(PREPARING_TEXT)

        text = await transcribe_audio(dest_path)

        if not text.strip():
            await message.answer("⚠️ Не удалось распознать речь в демо-аудио.")
            return

        # Сохраняем текст в памяти для demo-кнопок
        _demo_context[user_id] = text

        if status_msg:
            await status_msg.edit_text("✅ Транскрибация завершена!")

        # Показываем текст в чате (обрезаем если длинный)
        display_text = text if len(text) <= 4000 else text[:4000] + "\n\n✂️ <i>Текст обрезан</i>"
        try:
            await message.answer(display_text, parse_mode="HTML")
        except Exception:
            await message.answer(display_text, parse_mode=None)

        # Показываем demo-кнопки действий
        await message.answer(
            "✅ Вот что я умею! Попробуй одно из действий:",
            reply_markup=demo_post_transcription_kb(),
        )

    except TranscriptionError as exc:
        logger.error("[user_id=%s] Ошибка транскрибации demo: %s", user_id, exc)
        await send_logo(message, "❌ Произошла ошибка при обработке файла.",
                        reply_markup=error_kb("transcription_error"), image="error")

    except Exception as exc:
        logger.exception("[user_id=%s] Ошибка обработки demo: %s", user_id, exc)
        await send_logo(message, "❌ Произошла ошибка при обработке файла.",
                        reply_markup=error_kb("transcription_error"), image="error")

    finally:
        _cleanup_tmp(tmp_dir)

INVALID_FILE_TEXT = (
    "❌ Пожалуйста, отправьте аудиофайл, голосовое сообщение "
    "или ссылку на видео/аудио.\n"
    "Поддерживаемые форматы: mp3, m4a, wav, webm, ogg, mpeg, mpga."
)


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    _register_user(message.from_user)
    user_id = message.from_user.id if message.from_user else None

    # Обработка deeplink-параметра: /start <payload>
    if user_id and message.text:
        args = message.text.split(maxsplit=1)
        if len(args) > 1:
            payload = args[1]
            if payload.startswith("ref_"):
                # Реферальная ссылка
                ref_code = payload[4:]
                referrer_id = find_user_by_ref_code(ref_code)
                if referrer_id and referrer_id != user_id:
                    if add_referral(referrer_id, user_id):
                        await message.answer(
                            "🎉 Добро пожаловать! Твой друг получил +30 минут за приглашение."
                        )
            else:
                # Короткая ссылка с UTM-параметрами
                link = get_short_link(payload)
                if link:
                    track_short_link_visit(payload, user_id)
                    logger.info("Переход по короткой ссылке %s от пользователя %d", payload, user_id)

    if user_id and not is_user_onboarded(user_id):
        await send_logo(message, ONBOARDING_TEXT, reply_markup=onboarding_kb())
    else:
        await _send_welcome(message)


@router.message(Command("record"))
async def cmd_record(message: Message) -> None:
    await send_logo(
        message,
        "🎤 Готова слушать! Запиши голосовое сообщение — я сразу его расшифрую ✨",
        reply_markup=back_to_menu_kb(),
        image="voice_message",
    )


@router.message(Command("upload"))
async def cmd_upload(message: Message) -> None:
    await send_logo(
        message,
        "📤 Отправь мне файл или ссылку — я приму почти всё!\n\n"
        "🎵 Аудио: MP3, WAV, OGG, FLAC, M4A\n"
        # "🎬 Видео: MP4, AVI, MOV, MKV, WebM\n"
        "🔗 Ссылки: YouTube, TikTok, VK, Instagram и другие",
        reply_markup=back_to_menu_kb(),
        image="send_file",
    )


@router.message(Command("records"))
async def cmd_records(message: Message) -> None:
    user_id = message.from_user.id if message.from_user else 0
    records = get_user_records(user_id, limit=100)
    if not records:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🎤 Записать аудио", callback_data="scenario:record")],
            [InlineKeyboardButton(text="🔙 Главное меню", callback_data="menu:main")],
        ])
        await send_logo(
            message,
            "📁 Здесь пока пусто. Запиши первое аудио — и оно появится тут!",
            reply_markup=kb,
            image="my_notes",
        )
    else:
        await send_logo(
            message,
            f"📁 Твои записи ({len(records)} шт.):",
            reply_markup=records_list_kb(records, page=0),
            image="my_notes",
        )


@router.message(Command("plan"))
async def cmd_plan(message: Message) -> None:
    user_id = message.from_user.id if message.from_user else 0
    balance = get_user_balance(user_id)
    if balance == -1:
        balance_str = "безлимит ♾"
    else:
        balance_str = f"{balance} мин"
    await send_logo(
        message,
        f"⏱ Твой баланс: <b>{balance_str}</b>\n\n"
        "⭐ Выбери тариф, который подходит именно тебе:",
        parse_mode="HTML",
        reply_markup=plans_kb(),
        image="payments",
    )


@router.message(Command("balance"))
async def cmd_balance(message: Message) -> None:
    user_id = message.from_user.id if message.from_user else 0
    plan_info = get_user_plan_info(user_id)
    balance = plan_info["balance"]
    if balance == -1:
        balance_str = "безлимит ♾"
    else:
        balance_str = f"{balance} мин"
    await send_logo(
        message,
        f"📋 Тариф: {plan_info['name']}\n⏱ Остаток: {balance_str}",
        reply_markup=back_to_menu_kb(),
    )


@router.message(Command("invite"))
async def cmd_invite(message: Message) -> None:
    from bot.database import get_referral_count, get_referral_minutes_earned, get_user_ref_code

    user_id = message.from_user.id if message.from_user else 0
    ref_code = get_user_ref_code(user_id)
    count = get_referral_count(user_id)
    earned = get_referral_minutes_earned(user_id)

    bot_info = await message.bot.get_me()
    bot_username = bot_info.username or "dasha_bot"
    ref_link = f"https://t.me/{bot_username}?start=ref_{ref_code}"

    await send_logo(
        message,
        f"💌 <b>Пригласи друга!</b>\n\n"
        f"Поделись ссылкой — и ты получишь <b>+30 минут</b> за каждого друга!\n\n"
        f"🔗 Твоя ссылка:\n<code>{ref_link}</code>\n\n"
        f"👥 Приглашено: {count}\n"
        f"⏱ Заработано минут: {earned}",
        parse_mode="HTML",
        reply_markup=back_to_menu_kb(),
        image="invite_friend",
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await send_logo(message, "❓ Чем могу помочь?", reply_markup=help_kb(), image="faq")


@router.message(Command("settings"))
async def cmd_settings(message: Message) -> None:
    user_id = message.from_user.id if message.from_user else 0
    s = get_user_settings(user_id)
    await send_logo(message, "⚙️ Настройки:", reply_markup=settings_kb(s))


@router.message(Command("get_short_link"))
async def cmd_get_short_link(message: Message) -> None:
    """Создать короткую ссылку с UTM-параметрами (только для админов)."""
    user_id = message.from_user.id if message.from_user else 0
    if get_user_role(user_id) != "ADMIN":
        return

    import json

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


@router.message(Command("get_short_link_stats"))
async def cmd_get_short_link_stats(message: Message) -> None:
    """Статистика по всем коротким ссылкам (только для админов)."""
    user_id = message.from_user.id if message.from_user else 0
    if get_user_role(user_id) != "ADMIN":
        return

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
    # Telegram лимит 4096 символов
    if len(text) > 4096:
        text = text[:4090] + "\n…"
    await message.answer(text, parse_mode="HTML")


@router.message(F.audio | F.voice | F.video_note | F.video | F.document)
async def on_audio(message: Message, bot: Bot, state: FSMContext) -> None:
    if await state.get_state() is not None:
        await state.clear()
        await message.answer("⚠️ Режим вопросов завершён. Начинаю транскрибацию.")
    user_id = message.from_user.id if message.from_user else 0

    lock = _get_user_lock(user_id)
    if lock.locked():
        await message.answer(BUSY_TEXT)
        return

    async with lock:
        if get_user_role(user_id) != "ADMIN" and not has_sufficient_balance(user_id):
            await send_logo(
                message,
                "⚠️ У тебя закончились минуты транскрибации.\n"
                "Пополни баланс или пригласи друга!",
                reply_markup=error_kb("limit_exceeded"),
                image="time_limit",
            )
            return

        is_video = False

        if message.audio:
            file_id = message.audio.file_id
            filename = message.audio.file_name or f"audio_{file_id}.mp3"
        elif message.voice:
            file_id = message.voice.file_id
            filename = f"voice_{file_id}.ogg"
        elif message.video_note:
            file_id = message.video_note.file_id
            filename = f"videonote_{file_id}.mp4"
            is_video = True
        elif message.video:
            file_id = message.video.file_id
            filename = message.video.file_name or f"video_{file_id}.mp4"
            is_video = True
        elif message.document:
            file_id = message.document.file_id
            filename = message.document.file_name or ""
            ext = os.path.splitext(filename)[1].lower()
            if ext not in SUPPORTED_AUDIO_EXTENSIONS:
                await send_logo(message, INVALID_FILE_TEXT,
                                reply_markup=error_kb("unsupported_format"), image="error")
                return
        else:
            await send_logo(message, INVALID_FILE_TEXT,
                            reply_markup=error_kb("unsupported_format"), image="error")
            return

        status_msg = await message.answer(DOWNLOADING_TEXT)
        tmp_dir = tempfile.mkdtemp(prefix="transcriber_")

        try:
            dest_path = os.path.join(tmp_dir, filename)
            tg_file = await bot.get_file(file_id)
            await bot.download_file(tg_file.file_path, dest_path)
            logger.info("Скачан файл: %s", dest_path)

            if is_video:
                dest_path = await extract_audio(dest_path)
                logger.info("Аудио извлечено из видео: %s", dest_path)

            await _process_audio(message, dest_path, tmp_dir, status_msg)

        except TranscriptionError as exc:
            logger.error("[user_id=%s] Ошибка транскрибации файла: %s", user_id, exc)
            await send_logo(message, "❌ Произошла ошибка при обработке файла. Пожалуйста, напишите в поддержку.",
                            reply_markup=error_kb("transcription_error"), image="error")

        except Exception as exc:
            logger.exception("[user_id=%s] Непредвиденная ошибка при обработке файла: %s", user_id, exc)
            await send_logo(message, "❌ Произошла ошибка при обработке файла. Пожалуйста, напишите в поддержку.",
                            reply_markup=error_kb("transcription_error"), image="error")

        finally:
            _cleanup_tmp(tmp_dir)


@router.message(RenameRecord.waiting_for_title, F.text)
async def on_rename_title(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    record_id = data.get("rename_record_id")
    await state.clear()

    new_title = (message.text or "").strip()[:100]
    if not new_title:
        await message.answer("⚠️ Название не может быть пустым.")
        return

    if not record_id:
        await message.answer("⚠️ Запись не найдена.")
        return

    rename_record(record_id, new_title)
    record = get_record(record_id)
    if record:
        from bot.keyboards import record_card_kb
        await send_logo(
            message,
            f"✅ Запись переименована в «{new_title}»",
            reply_markup=record_card_kb(record_id),
        )
    else:
        await send_logo(message, "✅ Запись переименована.", reply_markup=back_to_menu_kb())


import re


def _normalize_phone(raw: str) -> str | None:
    """Нормализовать номер телефона в формат +7XXXXXXXXXX.

    Принимает: +79001234567, 89001234567, 79001234567, 9001234567.
    Возвращает None если формат не распознан.
    """
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 11 and digits[0] in ("7", "8"):
        return "+7" + digits[1:]
    if len(digits) == 10:
        return "+7" + digits
    return None


@router.message(WaitingPhone.waiting_for_phone, F.contact)
async def on_phone_contact(message: Message, state: FSMContext) -> None:
    """Получение телефона через кнопку «Отправить номер»."""
    phone = _normalize_phone(message.contact.phone_number)
    if not phone:
        await message.answer("⚠️ Не удалось распознать номер. Отправь в формате +7XXXXXXXXXX:")
        return

    await _process_phone_and_pay(message, state, phone)


@router.message(WaitingPhone.waiting_for_phone, F.text)
async def on_phone_text(message: Message, state: FSMContext) -> None:
    """Получение телефона текстовым сообщением."""
    phone = _normalize_phone(message.text or "")
    if not phone:
        await message.answer("⚠️ Неверный формат. Отправь номер в формате +7XXXXXXXXXX:")
        return

    await _process_phone_and_pay(message, state, phone)


async def _process_phone_and_pay(message: Message, state: FSMContext, phone: str) -> None:
    """Сохранить телефон и создать платёж."""
    data = await state.get_data()
    plan_code = data.get("pay_plan_code")
    await state.clear()

    user_id = message.from_user.id if message.from_user else 0
    save_user_phone(user_id, phone)

    # Убираем reply-клавиатуру с кнопкой «Отправить номер»
    await message.answer("✅ Номер сохранён!", reply_markup=ReplyKeyboardRemove())

    if plan_code:
        await _create_and_send_payment(message, user_id, plan_code, phone)
    else:
        await message.answer("⚠️ Не удалось определить тариф. Выбери тариф заново.",
                             reply_markup=back_to_menu_kb())


@router.message(AskQuestion.waiting_for_question, F.text)
async def on_question(message: Message, state: FSMContext) -> None:
    url = extract_media_url(message.text or "")
    if url:
        await state.clear()
        await message.answer("⚠️ Режим вопросов завершён. Начинаю транскрибацию.")
        await _handle_url(message, url)
        return

    data = await state.get_data()
    record_id = data.get("qa_record_id")

    if not record_id:
        await state.clear()
        await message.answer("⚠️ Запись не найдена.")
        return

    record = get_record(record_id)
    if not record:
        await state.clear()
        await message.answer("⚠️ Запись не найдена.")
        return

    from bot.callbacks import _load_transcription
    text = await _load_transcription(record)
    if not text.strip():
        await state.clear()
        await message.answer("⚠️ Текст записи пуст.")
        return

    question = (message.text or "").strip()
    status_msg = await message.answer("⏳ Ищу ответ на вопрос…")

    from bot.report_generator import answer_question
    result = await asyncio.to_thread(answer_question, text, question)

    if not result:
        try:
            await status_msg.edit_text("❌ Не удалось найти ответ на вопрос.")
        except Exception:
            pass
        return

    try:
        await status_msg.delete()
    except Exception:
        pass

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад к действиям", callback_data=f"questions:back:{record_id}")],
    ])

    expandable = f"<blockquote expandable>{result}</blockquote>"
    if len(expandable) < 4096:
        try:
            await message.answer(expandable, parse_mode="HTML", reply_markup=kb)
        except Exception:
            await message.answer(result, parse_mode=None, reply_markup=kb)
    else:
        await message.answer(result, parse_mode=None, reply_markup=kb)


# ── Рассылка сообщения пользователям (только ADMIN) ──


@router.message(Command("send_message"))
async def cmd_send_message(message: Message, state: FSMContext) -> None:
    """Начать рассылку сообщения пользователям (только для админов)."""
    user_id = message.from_user.id if message.from_user else 0
    if get_user_role(user_id) != "ADMIN":
        return

    # Парсим аргументы: /send_message 123,456 или /send_message 123
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


@router.message(BroadcastMessage.waiting_for_message)
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


@router.message(F.text)
async def on_text(message: Message, bot: Bot) -> None:
    url = extract_media_url(message.text or "")
    if url:
        await _handle_url(message, url)
    else:
        await send_logo(message, INVALID_FILE_TEXT,
                        reply_markup=error_kb("unsupported_format"), image="error")


@router.callback_query()
async def on_callback(callback: CallbackQuery, bot: Bot, state: FSMContext) -> None:
    await callback.answer()
    payload = callback.data or ""
    user_id = callback.from_user.id

    # Новый dispatch по префиксам
    if await dispatch_callback(callback, state):
        return

    # Legacy-маршруты подписки
    if payload == "sub_info":
        await _handle_sub_info(callback.message, user_id)
        return

    if payload == "sub_pay":
        await _handle_sub_pay(callback.message)
        return

    if payload == "sub_topup":
        await _handle_sub_topup(callback.message, bot, user_id)
        return

    if payload == "sub_back":
        await _send_welcome(callback.message)
        return

    # Legacy: саммари по старому callback ID
    context = _summary_context.pop(payload, None)
    if not context:
        await callback.message.answer(
            "⚠️ Данные не найдены. Попробуйте отправить аудио ещё раз."
        )
        return

    text, audio_stem = context
    await _handle_summary(callback.message, text, audio_stem)


async def _send_welcome(message: Message) -> None:
    await send_logo(message, WELCOME_TEXT, reply_markup=main_menu_kb())


async def _handle_url(message: Message, url: str) -> None:
    user_id = message.from_user.id if message.from_user else 0

    lock = _get_user_lock(user_id)
    if lock.locked():
        await message.answer(BUSY_TEXT)
        return

    async with lock:
        if get_user_role(user_id) != "ADMIN" and not has_sufficient_balance(user_id):
            await send_logo(
                message,
                "⚠️ У тебя закончились минуты транскрибации.\n"
                "Пополни баланс или пригласи друга!",
                reply_markup=error_kb("limit_exceeded"),
                image="time_limit",
            )
            return

        status_msg = await message.answer("⏳ Скачиваю аудио по ссылке…")
        tmp_dir = tempfile.mkdtemp(prefix="transcriber_url_")

        try:
            audio_path = await asyncio.to_thread(download_audio_from_url, url, tmp_dir)
            logger.info("Аудио скачано из URL: %s → %s", url, audio_path)

            await _process_audio(message, audio_path, tmp_dir, status_msg)

        except RuntimeError as exc:
            logger.error("[user_id=%s] Ошибка скачивания по ссылке %s: %s", user_id, url, exc)
            await send_logo(message, "❌ Не удалось обработать ссылку. Пожалуйста, напишите в поддержку.",
                            reply_markup=error_kb("unavailable_link"), image="error")

        except TranscriptionError as exc:
            logger.error("[user_id=%s] Ошибка транскрибации по ссылке %s: %s", user_id, url, exc)
            await send_logo(message, "❌ Произошла ошибка при обработке ссылки. Пожалуйста, напишите в поддержку.",
                            reply_markup=error_kb("transcription_error"), image="error")

        except Exception as exc:
            logger.exception("[user_id=%s] Непредвиденная ошибка при обработке ссылки %s: %s", user_id, url, exc)
            await send_logo(message, "❌ Произошла ошибка при обработке ссылки. Пожалуйста, напишите в поддержку.",
                            reply_markup=error_kb("transcription_error"), image="error")

        finally:
            _cleanup_tmp(tmp_dir)


async def _process_audio(
    message: Message,
    audio_path: str,
    tmp_dir: str,
    status_msg: Message | None,
) -> None:
    def _report_progress(current: int, total: int) -> None:
        if status_msg is None:
            return
        pct = current * 100 // total
        try:
            async def _update_status(p: int = pct) -> None:
                try:
                    await status_msg.edit_text(f"⏳ Транскрибирую аудио… {p}%")
                except Exception:
                    pass
            asyncio.ensure_future(_update_status())
        except Exception as exc:
            logger.warning("Не удалось обновить статус прогресса: %s", exc)

    if status_msg:
        await status_msg.edit_text(PREPARING_TEXT)

    text = await transcribe_audio(audio_path, _report_progress)

    if not text.strip():
        await message.answer("⚠️ Не удалось распознать речь в аудио.")
        return

    audio_stem = os.path.splitext(os.path.basename(audio_path))[0]
    txt_path = os.path.join(tmp_dir, audio_stem + ".txt")
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(text)

    if status_msg:
        await status_msg.edit_text("⏳ Отправляю результат…")

    # Определяем длительность аудио для списания минут
    duration_seconds = None
    try:
        import subprocess
        probe = await asyncio.to_thread(
            subprocess.run,
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
            capture_output=True, text=True, timeout=10,
        )
        if probe.returncode == 0 and probe.stdout.strip():
            duration_seconds = int(float(probe.stdout.strip()))
    except Exception as exc:
        logger.warning("Не удалось определить длительность аудио: %s", exc)

    # Списываем минуты (округляем вверх), кроме ADMIN
    user_id = message.from_user.id if message.from_user else 0
    if duration_seconds and duration_seconds > 0:
        if get_user_role(user_id) != "ADMIN":
            minutes_to_deduct = max(1, (duration_seconds + 59) // 60)
            deduct_balance(user_id, minutes_to_deduct)

    # Сохраняем запись в БД + текст в S3
    record_id = uuid.uuid4().hex[:16]

    # Авто-название по первым словам транскрибации для голосовых сообщений
    title = audio_stem[:100] or "Запись"
    if audio_stem.startswith("voice_") or audio_stem.startswith("videonote_"):
        settings = get_user_settings(user_id)
        if settings.get("auto_title", 1):
            words = text.strip().split()
            auto = " ".join(words[:6])
            if len(auto) > 100:
                auto = auto[:97] + "…"
            if auto:
                title = auto
    try:
        s3_key = await asyncio.to_thread(upload_text, user_id, record_id, text, audio_stem)
        save_record(
            record_id=record_id,
            user_id=user_id,
            title=title,
            text_s3_key=s3_key,
            duration_seconds=duration_seconds,
        )
    except Exception as exc:
        logger.error("Ошибка сохранения записи: %s", exc)

    # Также сохраняем в legacy-контекст для обратной совместимости
    _summary_context[record_id] = (text, audio_stem)

    kb = post_transcription_kb(record_id)
    await message.answer_document(FSInputFile(txt_path), reply_markup=kb)
    logger.info("Транскрипция отправлена в чат %s", message.chat.id)

    # if len(text) < 4096:
    #     await message.answer(text, parse_mode=None)

    if status_msg:
        await status_msg.edit_text("✅ Транскрибация завершена!")


async def _handle_sub_info(message: Message, user_id: int) -> None:
    balance = get_user_balance(user_id)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оплатить подписку", callback_data="sub_pay")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="sub_back")],
    ])
    await message.answer(f"💰 Ваш текущий баланс: {balance} руб.", reply_markup=kb)


async def _handle_sub_pay(message: Message) -> None:
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💵 Пополнить на 1000 рублей", callback_data="sub_topup")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="sub_info")],
    ])
    await message.answer("Выберите сумму пополнения:", reply_markup=kb)


async def _handle_sub_topup(message: Message, bot: Bot, user_id: int) -> None:
    from bot.database import get_subscription
    phone = get_user_phone(user_id)
    if not phone:
        await message.answer("⚠️ Для оплаты необходимо указать номер телефона. Используй /plan для выбора тарифа.")
        return

    plan = get_subscription("basic")
    if not plan:
        await message.answer("⚠️ Тариф не найден.")
        return

    plan_price = plan["price"]
    plan_name = plan["name"]

    await message.answer("⏳ Создаю платёж…")

    result = await asyncio.to_thread(create_payment, plan_price, f"Тариф «{plan_name}»", phone)
    if not result:
        await message.answer("❌ Не удалось создать платёж. Попробуйте позже.")
        return

    payment_id, payment_url = result

    try:
        save_payment(payment_id, user_id, plan_price, subscription_code="basic")
    except Exception as exc:
        logger.error("Ошибка сохранения платежа %s: %s", payment_id, exc)

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="💳 Оплатить", url=payment_url),
    ]])
    await message.answer("Нажмите кнопку ниже для оплаты:", reply_markup=kb)

    loop = asyncio.get_running_loop()
    threading.Thread(
        target=_poll_payment,
        args=(bot, loop, message.chat.id, user_id, payment_id),
        daemon=True,
    ).start()


def _poll_payment(
    bot: Bot,
    loop: asyncio.AbstractEventLoop,
    chat_id: int,
    user_id: int,
    payment_id: str,
) -> None:
    """Поллинг статуса платежа каждые 5 сек, до 10 минут."""
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
                mark_payment_paid(payment_id, user_id)
                new_balance = get_user_balance(user_id)
                if new_balance == -1:
                    bal_str = "безлимит ♾"
                else:
                    bal_str = f"{new_balance} мин"
                asyncio.run_coroutine_threadsafe(
                    bot.send_message(
                        chat_id,
                        f"✅ Оплата прошла успешно!\nТвой баланс: {bal_str}",
                        reply_markup=back_to_menu_kb(),
                    ),
                    loop,
                )
            except Exception as exc:
                logger.error("Ошибка зачисления платежа %s: %s", payment_id, exc)
            return

        if status == "canceled":
            asyncio.run_coroutine_threadsafe(
                bot.send_message(
                    chat_id,
                    "❌ Платёж отменён.",
                    reply_markup=back_to_menu_kb(),
                ),
                loop,
            )
            return


async def _handle_summary(message: Message, text: str, audio_stem: str) -> None:
    status_msg = await message.answer("⏳ Создаю краткий отчёт…")
    tmp_dir = tempfile.mkdtemp(prefix="summary_")

    try:
        summary = await asyncio.to_thread(summarize_text, text)
        if not summary:
            await message.answer("❌ Не удалось создать краткий отчёт.")
            return

        summary_path = os.path.join(tmp_dir, f"summary_{audio_stem}.txt")
        with open(summary_path, "w", encoding="utf-8") as f:
            f.write(summary)

        await status_msg.edit_text("⏳ Отправляю краткий отчёт…")
        await message.answer_document(FSInputFile(summary_path))
        logger.info("Саммари отправлено в чат %s", message.chat.id)

        expandable = f"<blockquote expandable>{summary}</blockquote>"
        if len(expandable) < 4096:
            try:
                await message.answer(expandable, parse_mode="HTML")
            except Exception:
                await message.answer(summary)

        await status_msg.edit_text("✅ Краткий отчёт готов!")

    except Exception as exc:
        uid = message.from_user.id if message.from_user else 0
        logger.exception("[user_id=%s] Ошибка при создании саммари: %s", uid, exc)
        await send_logo(message, "❌ Произошла ошибка при создании саммари. Пожалуйста, напишите в поддержку.",
                        reply_markup=error_kb("transcription_error"), image="error")

    finally:
        _cleanup_tmp(tmp_dir)




def _register_user(user: object) -> None:
    if not user:
        return
    user_id = getattr(user, "id", None)
    if not user_id:
        return
    username = getattr(user, "username", None)
    first_name = getattr(user, "first_name", None)
    try:
        get_or_create_user(user_id, username, first_name)
    except Exception as exc:
        logger.error("Ошибка регистрации пользователя %s: %s", user_id, exc)


def _cleanup_tmp(tmp_dir: str) -> None:
    try:
        for f in os.listdir(tmp_dir):
            os.remove(os.path.join(tmp_dir, f))
        os.rmdir(tmp_dir)
    except OSError as exc:
        logger.warning("Не удалось очистить %s: %s", tmp_dir, exc)
