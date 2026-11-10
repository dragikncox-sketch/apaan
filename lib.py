
import logging

from telegram import Bot

from config import BOT_TOKEN

logger = logging.getLogger(__name__)


async def send_telegram(bot, chat_id, message):
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=message,
            disable_web_page_preview=True,
        )

        return True

    except Exception:
        logger.exception("Telegram notification failed")
        return False
