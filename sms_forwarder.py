import asyncio
import hashlib
import logging
from datetime import datetime, timedelta, timezone

from config import (
    MESSAGES_URL,
    MESSAGES_PER_PAGE,
    MESSAGES_TYPE,
    POLL_INTERVAL,
    MESSAGES_START_DATE,
    MESSAGES_END_DATE,
)
from augestel import api_get
from database import (
    init_sms_database,
    normalize_number,
    get_number_owner,
    is_message_seen,
    mark_message_seen,
    is_first_poll,
    mark_initialized,
)

logger = logging.getLogger(__name__)


def message_hash(item):
    raw = "|".join(
        str(item.get(key, ""))
        for key in ("number", "received_at", "source", "message")
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def fetch_messages():
    if not MESSAGES_URL:
        raise RuntimeError("MESSAGES_URL belum dikonfigurasi.")

    today = datetime.now(timezone.utc).date()

    start_date = (
        MESSAGES_START_DATE
        or (today - timedelta(days=1)).isoformat()
    )
    end_date = MESSAGES_END_DATE or today.isoformat()

    messages = []
    page = 1
    last_page = 1

    while page <= last_page and page <= 100:
        result = await api_get(
            MESSAGES_URL,
            {
                "start_date": start_date,
                "end_date": end_date,
                "per_page": MESSAGES_PER_PAGE,
                "page": page,
                "type": MESSAGES_TYPE,
            },
        )

        if not isinstance(result, dict):
            raise RuntimeError("Respons messages Augestel tidak valid.")

        if result.get("success") is not True:
            raise RuntimeError(
                f"Augestel gagal mengembalikan SMS: {str(result)[:300]}"
            )

        data = result.get("data", [])

        if isinstance(data, list):
            messages.extend(
                item for item in data
                if isinstance(item, dict)
            )

        pagination = result.get("pagination") or {}

        try:
            last_page = max(
                1,
                int(pagination.get("last_page", page))
            )
        except (TypeError, ValueError):
            last_page = page

        page += 1

    return messages


async def process_messages(application, messages):
    first_run = is_first_poll()

    for item in messages:
        number = normalize_number(item.get("number", ""))
        message = str(item.get("message", "")).strip()

        if not number or not message:
            continue

        digest = message_hash(item)

        if is_message_seen(digest):
            continue

        # Polling pertama hanya menyimpan SMS lama.
        if first_run:
            mark_message_seen(digest)
            continue

        user_id = get_number_owner(number)

        if not user_id:
            mark_message_seen(digest)
            continue

        source = str(item.get("source", "Unknown"))
        received_at = str(item.get("received_at", ""))

        notification = (
            "📩 SMS BARU\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            f"📱 Nomor: {number}\n"
            f"👤 Pengirim: {source}\n"
            f"🕒 Waktu: {received_at}\n\n"
            "💬 Pesan:\n"
            f"{message}"
        )

        try:
            # Jangan kirim SMS jika pengguna sudah keluar dari grup.
            member = await application.bot.get_chat_member(
                chat_id=application.bot_data["required_group_id"],
                user_id=user_id,
            )

            allowed_statuses = ("creator", "administrator", "member")
            is_member = member.status in allowed_statuses

            if member.status == "restricted":
                is_member = bool(member.is_member)

            if not is_member:
                mark_message_seen(digest)
                logger.info(
                    "SMS tidak diteruskan: user %s bukan anggota grup.",
                    user_id,
                )
                continue

            await application.bot.send_message(
                chat_id=user_id,
                text=notification[:4000],
                disable_web_page_preview=True,
            )

        except Exception:
            logger.exception(
                "Gagal meneruskan SMS ke Telegram user %s",
                user_id,
            )
            continue

        mark_message_seen(digest)

        logger.info(
            "SMS dari nomor %s berhasil diteruskan ke user %s",
            number,
            user_id,
        )

    if first_run:
        mark_initialized()
        logger.info("Polling pertama selesai; SMS lama tidak diteruskan.")


async def sms_poll_loop(application):
    init_sms_database()

    logger.info(
        "SMS forwarder aktif. Interval %s detik.",
        POLL_INTERVAL,
    )

    while True:
        try:
            messages = await fetch_messages()
            await process_messages(application, messages)

        except asyncio.CancelledError:
            raise

        except Exception:
            logger.exception("SMS polling error")

        await asyncio.sleep(POLL_INTERVAL)