"""Все клавиатуры бота (InlineKeyboardMarkup)."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎤 Записать аудио", callback_data="scenario:record")],
        [InlineKeyboardButton(text="📤 Загрузить файл", callback_data="scenario:upload")],
        [InlineKeyboardButton(text="📁 Мои записи", callback_data="scenario:records")],
        [InlineKeyboardButton(text="💌 Пригласи друга", callback_data="scenario:referral")],
        [InlineKeyboardButton(text="⭐ Тарифы", callback_data="scenario:plans")],
        [InlineKeyboardButton(text="❓ Помощь", callback_data="scenario:help")],
    ])


def onboarding_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✨ Покажи пример", callback_data="onboarding:demo")],
        [InlineKeyboardButton(text="Начать пользоваться", callback_data="onboarding:start")],
    ])


ONBOARDING_TEXT = (
    "Привет! Я Даша 👋\n\n"
    "Тебе доступно 60 бесплатных минут.\n\n"
    "Отправь мне голосовое, аудио или ссылку на YouTube, Instagram, "
    "RuTube — я расшифрую и сделаю конспект."
)


def back_to_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Главное меню", callback_data="menu:main")],
    ])


def post_transcription_kb(record_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✨ Краткий конспект", callback_data=f"summary:gen:{record_id}")],
        [InlineKeyboardButton(text="💡 Ключевые инсайты", callback_data=f"report:insights:{record_id}")],
        [InlineKeyboardButton(text="✅ Список задач", callback_data=f"report:action_items:{record_id}")],
        [InlineKeyboardButton(text="❓ Вопросы к тексту", callback_data=f"questions:gen:{record_id}")],
        # [InlineKeyboardButton(text="📊 Дополнительные отчёты ▶", callback_data=f"reports:menu:{record_id}")],
        [InlineKeyboardButton(text="🔙 Назад к записи", callback_data=f"record:open:{record_id}")],
    ])


def reports_submenu_kb(record_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🧠 Mind Map", callback_data=f"report:mind_map:{record_id}")],
        [InlineKeyboardButton(text="📈 SWOT-анализ", callback_data=f"report:swot:{record_id}")],
        [InlineKeyboardButton(text="🕒 Timeline", callback_data=f"report:timeline:{record_id}")],
        [InlineKeyboardButton(text="🗣️ Цитаты спикеров", callback_data=f"report:quotes:{record_id}")],
        [InlineKeyboardButton(text="🎯 Решения и договорённости", callback_data=f"report:decisions:{record_id}")],
        [InlineKeyboardButton(text="📝 Глоссарий терминов", callback_data=f"report:glossary:{record_id}")],
        [InlineKeyboardButton(text="📊 Статистика текста", callback_data=f"report:stats:{record_id}")],
        [InlineKeyboardButton(text="🌐 Перевод", callback_data=f"report:translate:{record_id}")],
        [InlineKeyboardButton(text="📧 Письмо по итогам", callback_data=f"report:followup:{record_id}")],
        [InlineKeyboardButton(text="🔙 Назад к действиям", callback_data=f"record:actions:{record_id}")],
    ])


_PLAN_ICONS = {"basic": "💜", "standard": "⭐", "pro": "💎"}


def plans_kb() -> InlineKeyboardMarkup:
    from bot.database import get_all_subscriptions
    plans = get_all_subscriptions()
    buttons = []
    for p in plans:
        icon = _PLAN_ICONS.get(p["code"], "📦")
        minutes = p["amount"]
        if minutes == -1:
            min_str = "безлимит"
        else:
            min_str = f"{minutes} мин"
        label = f"{icon} {p['name']} — {min_str} / {p['price']}₽"
        buttons.append([InlineKeyboardButton(
            text=label, callback_data=f"plan:buy:{p['code']}"
        )])
    buttons.append([InlineKeyboardButton(text="🔙 Главное меню", callback_data="menu:main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def help_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🤔 Как записать аудио?", callback_data="help:faq:record")],
        [InlineKeyboardButton(text="📎 Какие форматы поддерживаются?", callback_data="help:faq:formats")],
        [InlineKeyboardButton(text="🌐 Как загрузить с YouTube?", callback_data="help:faq:youtube")],
        [InlineKeyboardButton(text="💳 Как оплатить?", callback_data="help:faq:payment")],
        [InlineKeyboardButton(text="👤 Связаться с поддержкой", callback_data="help:faq:support")],
        [InlineKeyboardButton(text="🔙 Главное меню", callback_data="menu:main")],
    ])


def settings_kb(settings: dict) -> InlineKeyboardMarkup:
    lang = settings.get("transcription_language", "ru")
    lang_label = {"ru": "🇷🇺 Русский", "en": "🇬🇧 English", "auto": "🤖 Авто"}.get(lang, lang)
    diar = "✅ Вкл" if settings.get("diarization") else "❌ Выкл"
    fmt = settings.get("export_format", "txt").upper()
    auto_t = "✅ Вкл" if settings.get("auto_title", 1) else "❌ Выкл"

    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🌐 Язык: {lang_label}", callback_data="settings:lang")],
        [InlineKeyboardButton(text=f"👥 Спикеры: {diar}", callback_data="settings:diarization")],
        [InlineKeyboardButton(text=f"📤 Экспорт: {fmt}", callback_data="settings:export")],
        [InlineKeyboardButton(text=f"⏰ Авто-название: {auto_t}", callback_data="settings:autotitle")],
        [InlineKeyboardButton(text="🔙 Главное меню", callback_data="menu:main")],
    ])


RECORDS_PAGE_SIZE = 5


def records_list_kb(records: list[dict], page: int = 0) -> InlineKeyboardMarkup:
    total = len(records)
    start = page * RECORDS_PAGE_SIZE
    end = start + RECORDS_PAGE_SIZE
    page_records = records[start:end]

    buttons = []
    for r in page_records:
        date = r["created_at"][:10] if r.get("created_at") else ""
        label = f"{r['title']} · {date}"
        if len(label) > 60:
            label = label[:57] + "…"
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"record:open:{r['id']}")])

    # Навигация по страницам
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀ Назад", callback_data=f"records:page:{page - 1}"))
    if end < total:
        nav.append(InlineKeyboardButton(text="Вперёд ▶", callback_data=f"records:page:{page + 1}"))
    if nav:
        buttons.append(nav)

    buttons.append([InlineKeyboardButton(text="🔙 Главное меню", callback_data="menu:main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def record_card_kb(record_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👀 Посмотреть текст", callback_data=f"record:view:{record_id}")],
        [InlineKeyboardButton(text="✨ Действия", callback_data=f"record:actions:{record_id}")],
        [InlineKeyboardButton(text="✏️ Переименовать", callback_data=f"record:rename:{record_id}")],
        [InlineKeyboardButton(text="📥 Скачать (.txt)", callback_data=f"record:download:{record_id}")],
        [InlineKeyboardButton(text="🗑️ Удалить", callback_data=f"record:delete:{record_id}")],
        [InlineKeyboardButton(text="🔙 К списку записей", callback_data="scenario:records")],
    ])


def delete_confirm_kb(record_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑️ Да, удалить", callback_data=f"record:confirm_delete:{record_id}")],
        [InlineKeyboardButton(text="🔙 Отмена", callback_data=f"record:open:{record_id}")],
    ])


def error_kb(error_type: str) -> InlineKeyboardMarkup:
    if error_type == "transcription_error":
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Главное меню", callback_data="menu:main")],
        ])
    if error_type == "unsupported_format":
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📤 Загрузить другой файл", callback_data="scenario:upload")],
            [InlineKeyboardButton(text="🔙 Главное меню", callback_data="menu:main")],
        ])
    if error_type == "limit_exceeded":
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💌 Пригласить друга", callback_data="scenario:referral")],
            [InlineKeyboardButton(text="⭐ Выбрать тариф", callback_data="scenario:plans")],
        ])
    if error_type == "unavailable_link":
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📤 Попробовать снова", callback_data="scenario:upload")],
            [InlineKeyboardButton(text="🔙 Главное меню", callback_data="menu:main")],
        ])
    return back_to_menu_kb()
