import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand

from bot.config import TELEGRAM_BOT_TOKEN, validate_config
from bot.database import init_db
from bot.handlers import router

logger = logging.getLogger(__name__)


async def _main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    validate_config()
    init_db()

    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)

    await bot.set_my_commands([
        BotCommand(command="start", description="Главное меню"),
        BotCommand(command="record", description="Записать аудио"),
        BotCommand(command="upload", description="Загрузить файл или ссылку"),
        BotCommand(command="records", description="Мои записи"),
        BotCommand(command="plan", description="Тарифы и баланс"),
        BotCommand(command="balance", description="Остаток минут"),
        BotCommand(command="invite", description="Пригласить другa"),
        BotCommand(command="help", description="Помощь и FAQ"),
        BotCommand(command="settings", description="Настройки"),
    ])
    logger.info("Команды бота установлены")

    logger.info("Бот запущен. Ожидаю сообщения...")
    await dp.start_polling(bot)


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
