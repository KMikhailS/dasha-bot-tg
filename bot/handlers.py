import asyncio
import logging
import os
import tempfile
import threading
import time
import uuid

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.audio_splitter import extract_audio
from bot.callbacks import dispatch_callback
from bot.config import SUPPORTED_AUDIO_EXTENSIONS
from bot.logo import send_logo
from bot.database import (
    get_or_create_user,
    get_records_count,
    get_user_balance,
    get_user_records,
    get_user_settings,
    is_user_onboarded,
    mark_payment_paid,
    save_payment,
    save_record,
    set_user_onboarded,
)
from bot.keyboards import (
    ONBOARDING_MESSAGES,
    back_to_menu_kb,
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
from bot.transcriber import TranscriptionError, transcribe_audio

logger = logging.getLogger(__name__)

router = Router()

# Хранилище контекста транскрибации для кнопки "Сделать саммари"
# Ключ: callback payload (уникальный ID), значение: (text, audio_stem)
_summary_context: dict[str, tuple[str, str]] = {}

WELCOME_TEXT = (
    "Привет! 👋 Я Даша — твой личный транскрибатор.\n"
    "Записывай голос или загружай файл — я превращу его в текст за секунды ✨\n\n"
    "Выбери, что хочешь сделать:"
)

DOWNLOADING_TEXT = "⏳ Скачиваю аудио…"
PREPARING_TEXT = "⏳ Подготавливаю аудио…"

INVALID_FILE_TEXT = (
    "❌ Пожалуйста, отправьте аудиофайл, голосовое, видеосообщение "
    "или ссылку на YouTube / Instagram / VK / Одноклассники видео.\n"
    "Поддерживаемые форматы: mp3, mp4, m4a, wav, webm, ogg, mpeg, mpga.\n"
    "Ссылки: YouTube, Instagram (Reels, посты с видео), VK Видео, Одноклассники."
)


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    _register_user(message.from_user)
    user_id = message.from_user.id if message.from_user else None

    if user_id and not is_user_onboarded(user_id):
        await send_logo(message, ONBOARDING_MESSAGES[1], reply_markup=onboarding_kb(1))
    else:
        await _send_welcome(message)


@router.message(Command("record"))
async def cmd_record(message: Message) -> None:
    await send_logo(
        message,
        "🎤 Готова слушать! Запиши голосовое сообщение — я сразу его расшифрую ✨",
        reply_markup=back_to_menu_kb(),
    )


@router.message(Command("upload"))
async def cmd_upload(message: Message) -> None:
    await send_logo(
        message,
        "📤 Отправь мне файл или ссылку — я приму почти всё!\n\n"
        "🎵 Аудио: MP3, WAV, OGG, FLAC, M4A\n"
        "🎬 Видео: MP4, AVI, MOV, MKV, WebM\n"
        "🔗 Ссылки: YouTube, TikTok, VK, Instagram и другие",
        reply_markup=back_to_menu_kb(),
    )


@router.message(Command("records"))
async def cmd_records(message: Message) -> None:
    user_id = message.from_user.id if message.from_user else 0
    count = get_records_count(user_id)
    if count == 0:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🎤 Записать аудио", callback_data="scenario:record")],
            [InlineKeyboardButton(text="🔙 Главное меню", callback_data="menu:main")],
        ])
        await send_logo(
            message,
            "📁 Здесь пока пусто. Запиши первое аудио — и оно появится тут!",
            reply_markup=kb,
        )
    else:
        records = get_user_records(user_id)
        await send_logo(
            message,
            f"📁 Твои записи ({count} шт.):",
            reply_markup=records_list_kb(records),
        )


@router.message(Command("plan"))
async def cmd_plan(message: Message) -> None:
    await send_logo(
        message,
        "⭐ Выбери тариф, который подходит именно тебе:",
        reply_markup=plans_kb(),
    )


@router.message(Command("balance"))
async def cmd_balance(message: Message) -> None:
    user_id = message.from_user.id if message.from_user else 0
    balance = get_user_balance(user_id)
    await send_logo(
        message,
        f"💰 Твой баланс: {balance} руб.",
        reply_markup=back_to_menu_kb(),
    )


@router.message(Command("invite"))
async def cmd_invite(message: Message) -> None:
    await send_logo(
        message,
        "💌 Реферальная программа скоро будет доступна!\n"
        "Пригласи подругу — и вам обеим по +60 минут бесплатно.",
        reply_markup=back_to_menu_kb(),
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await send_logo(message, "❓ Чем могу помочь?", reply_markup=help_kb())


@router.message(Command("settings"))
async def cmd_settings(message: Message) -> None:
    user_id = message.from_user.id if message.from_user else 0
    s = get_user_settings(user_id)
    await send_logo(message, "⚙️ Настройки:", reply_markup=settings_kb(s))


@router.message(F.audio | F.voice | F.video_note | F.video | F.document)
async def on_audio(message: Message, bot: Bot) -> None:
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
            await send_logo(message, INVALID_FILE_TEXT)
            return
    else:
        await send_logo(message, INVALID_FILE_TEXT)
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
        logger.error("Ошибка транскрибации: %s", exc)
        await message.answer(f"❌ {exc}")

    except Exception as exc:
        logger.exception("Непредвиденная ошибка: %s", exc)
        await message.answer("❌ Произошла ошибка при обработке файла.")

    finally:
        _cleanup_tmp(tmp_dir)


@router.message(F.text)
async def on_text(message: Message, bot: Bot) -> None:
    url = extract_media_url(message.text or "")
    if url:
        await _handle_url(message, url)
    else:
        await send_logo(message, INVALID_FILE_TEXT)


@router.callback_query()
async def on_callback(callback: CallbackQuery, bot: Bot) -> None:
    await callback.answer()
    payload = callback.data or ""
    user_id = callback.from_user.id

    # Новый dispatch по префиксам
    if await dispatch_callback(callback):
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
    status_msg = await message.answer("⏳ Скачиваю аудио по ссылке…")
    tmp_dir = tempfile.mkdtemp(prefix="transcriber_url_")

    try:
        audio_path = await asyncio.to_thread(download_audio_from_url, url, tmp_dir)
        logger.info("Аудио скачано из URL: %s → %s", url, audio_path)

        await _process_audio(message, audio_path, tmp_dir, status_msg)

    except RuntimeError as exc:
        logger.error("Ошибка скачивания по ссылке: %s", exc)
        await message.answer(f"❌ {exc}")

    except TranscriptionError as exc:
        logger.error("Ошибка транскрибации: %s", exc)
        await message.answer(f"❌ {exc}")

    except Exception as exc:
        logger.exception("Непредвиденная ошибка: %s", exc)
        await message.answer("❌ Произошла ошибка при обработке ссылки.")

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

    # Сохраняем запись в БД
    record_id = uuid.uuid4().hex[:16]
    user_id = message.from_user.id if message.from_user else 0
    title = audio_stem[:100] or "Запись"
    try:
        save_record(
            record_id=record_id,
            user_id=user_id,
            title=title,
            transcription_text=text,
        )
    except Exception as exc:
        logger.error("Ошибка сохранения записи: %s", exc)

    # Также сохраняем в legacy-контекст для обратной совместимости
    _summary_context[record_id] = (text, audio_stem)

    kb = post_transcription_kb(record_id)
    await message.answer_document(FSInputFile(txt_path), reply_markup=kb)
    logger.info("Транскрипция отправлена в чат %s", message.chat.id)

    if len(text) < 4096:
        await message.answer(text, parse_mode=None)

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
    await message.answer("⏳ Создаю платёж…")

    result = await asyncio.to_thread(create_payment, 1000, "Пополнение баланса на 1000 руб.")
    if not result:
        await message.answer("❌ Не удалось создать платёж. Попробуйте позже.")
        return

    payment_id, payment_url = result

    try:
        save_payment(payment_id, user_id, 1000)
    except Exception as exc:
        logger.error("Ошибка сохранения платежа %s: %s", payment_id, exc)

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="💳 Оплатить", url=payment_url),
    ]])
    await message.answer("Нажмите кнопку ниже для оплаты:", reply_markup=kb)

    loop = asyncio.get_running_loop()
    threading.Thread(
        target=_poll_payment,
        args=(bot, loop, message.chat.id, user_id, payment_id, 1000),
        daemon=True,
    ).start()


def _poll_payment(
    bot: Bot,
    loop: asyncio.AbstractEventLoop,
    chat_id: int,
    user_id: int,
    payment_id: str,
    amount: int,
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
                mark_payment_paid(payment_id, user_id, amount)
                new_balance = get_user_balance(user_id)
                asyncio.run_coroutine_threadsafe(
                    bot.send_message(
                        chat_id,
                        f"✅ Оплата прошла успешно!\nВаш баланс: {new_balance} руб.",
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
        logger.exception("Ошибка при создании саммари: %s", exc)
        await message.answer("❌ Произошла ошибка при создании саммари.")

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
