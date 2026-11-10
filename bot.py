
import asyncio
import logging
import random
import sqlite3
import os
from html import escape
from datetime import datetime

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    CopyTextButton,
)

from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from config import BOT_TOKEN, ADMIN_USERNAME
from database import (
    init_sms_database,
    save_number_owner,
    get_number_owner,
    normalize_number,
)
from sms_forwarder import sms_poll_loop
from augestel import (
    get_all_numbers,
    get_statistics,
    start_cache,
    stop_cache,
)

# =====================================
# SETTINGS
# =====================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)

NUMBERS_PER_REQUEST = 3
DATABASE = "users.db"
REFERRAL_BONUS = 100
MIN_WITHDRAWAL = 5000
ADMIN_ID = int(os.getenv("ADMIN_ID", "1932008453"))

REQUIRED_GROUP_ID = int(
    os.getenv("REQUIRED_GROUP_ID", "-1004387643125")
)

REQUIRED_GROUP_LINK = os.getenv(
    "REQUIRED_GROUP_LINK",
    "https://t.me/+2xpSHUjQfmM3ZWM1",
)

REQUIRED_CHANNEL_ID = int(
    os.getenv("REQUIRED_CHANNEL_ID", "-1004363973467")
)

REQUIRED_CHANNEL_LINK = os.getenv(
    "REQUIRED_CHANNEL_LINK",
    "https://t.me/+hBeB1LSE3W4zZGM1",
)

COUNTRIES = {
    "indonesia": "INDONESIA",
    "malaysia": "MALAYSIA",
    "philippines": "PHILIPPINES",
    "myanmar": "MYANMAR",
    "pakistan": "PAKISTAN",
}


# =====================================
# SAFE TELEGRAM MESSAGE EDIT
# =====================================

async def safe_edit_message(query, text, **kwargs):
    try:
        return await query.edit_message_text(
            text=text,
            **kwargs
        )

    except BadRequest as error:
        if "Message is not modified" in str(error):
            logger.info("Pesan tidak mengalami perubahan.")
            return None

        raise


# =====================================
# GROUP + CHANNEL MEMBERSHIP
# =====================================

def membership_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 JOIN GROUP", url=REQUIRED_GROUP_LINK)],
        [InlineKeyboardButton("📢 JOIN CHANNEL", url=REQUIRED_CHANNEL_LINK)],
        [InlineKeyboardButton("🔄 CEK KEANGGOTAAN", callback_data="check_membership")],
    ])


async def check_chat_membership(bot, chat_id, user_id, chat_label):
    try:
        member = await bot.get_chat_member(
            chat_id=chat_id,
            user_id=user_id,
        )

        if member.status in ("creator", "administrator", "member"):
            return True

        if member.status == "restricted":
            return bool(member.is_member)

        return False

    except TelegramError:
        logger.exception(
            "Gagal memeriksa keanggotaan %s (chat_id=%s).",
            chat_label,
            chat_id,
        )
        return None


async def check_required_memberships(bot, user_id):
    """True jika user join grup DAN channel; None jika pemeriksaan gagal."""
    group_member = await check_chat_membership(
        bot, REQUIRED_GROUP_ID, user_id, "grup"
    )
    channel_member = await check_chat_membership(
        bot, REQUIRED_CHANNEL_ID, user_id, "channel"
    )

    if group_member is False or channel_member is False:
        return False
    if group_member is None or channel_member is None:
        return None
    return True


async def send_membership_message(message):
    await message.reply_text(
        "🔒 <b>AKSES BOT TERKUNCI</b>\n\n"
        "Untuk menggunakan JING OTP BOT, kamu wajib bergabung "
        "ke grup DAN channel Telegram kami.\n\n"
        "1. Tekan JOIN GROUP.\n"
        "2. Tekan JOIN CHANNEL.\n"
        "3. Setelah bergabung ke keduanya, tekan CEK KEANGGOTAAN.",
        parse_mode="HTML",
        reply_markup=membership_keyboard(),
    )


async def verify_user_access(update, context):
    user = update.effective_user
    if not user:
        return False

    is_member = await check_required_memberships(context.bot, user.id)
    if is_member is True:
        return True

    if update.callback_query:
        text = (
            "⚠️ Pemeriksaan keanggotaan gagal. Pastikan bot menjadi admin "
            "channel dan anggota grup, lalu coba lagi."
            if is_member is None else
            "🔒 <b>AKSES BOT TERKUNCI</b>\n\n"
            "Kamu wajib bergabung ke grup DAN channel terlebih dahulu."
        )
        await safe_edit_message(
            update.callback_query,
            text,
            parse_mode="HTML",
            reply_markup=membership_keyboard(),
        )
    elif update.effective_message:
        if is_member is None:
            await update.effective_message.reply_text(
                "⚠️ Tidak dapat memeriksa keanggotaan grup/channel. "
                "Pastikan bot adalah admin channel dan anggota grup, lalu coba lagi.",
                reply_markup=membership_keyboard(),
            )
        else:
            await send_membership_message(update.effective_message)
    return False


# =====================================
# DATABASE
# =====================================

def init_database():
    with sqlite3.connect(DATABASE) as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                joined_at TEXT
            )
        """)

        columns = [
            row[1]
            for row in db.execute(
                "PRAGMA table_info(users)"
            ).fetchall()
        ]

        if "username" not in columns:
            db.execute(
                "ALTER TABLE users ADD COLUMN username TEXT"
            )

        if "first_name" not in columns:
            db.execute(
                "ALTER TABLE users ADD COLUMN first_name TEXT"
            )

        if "joined_at" not in columns:
            db.execute(
                "ALTER TABLE users ADD COLUMN joined_at TEXT"
            )

        if "balance" not in columns:
            db.execute(
                "ALTER TABLE users ADD COLUMN balance INTEGER NOT NULL DEFAULT 0"
            )

        db.execute("""
            CREATE TABLE IF NOT EXISTS referrals (
                referred_user_id INTEGER PRIMARY KEY,
                referrer_id INTEGER NOT NULL,
                created_at TEXT
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS wallet_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                transaction_type TEXT NOT NULL,
                description TEXT,
                created_at TEXT
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS withdrawal_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                payout_details TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT,
                processed_at TEXT
            )
        """)

        db.commit()


def register_user(user, referrer_id=None):
    if not user:
        return

    with sqlite3.connect(DATABASE) as db:
        cursor = db.execute("""
            INSERT OR IGNORE INTO users
            (user_id, username, first_name, joined_at)
            VALUES (?, ?, ?, ?)
        """, (
            user.id,
            user.username or "",
            user.first_name or "",
            datetime.now().isoformat(),
        ))
        is_new_user = cursor.rowcount == 1

        db.execute("""
            UPDATE users
            SET username = ?, first_name = ?
            WHERE user_id = ?
        """, (
            user.username or "",
            user.first_name or "",
            user.id,
        ))

        # Attribute an invite only once, when the account first registers.
        if is_new_user and referrer_id:
            try:
                referrer_id = int(referrer_id)
            except (TypeError, ValueError):
                referrer_id = None

            if referrer_id and referrer_id != user.id:
                referrer_exists = db.execute(
                    "SELECT 1 FROM users WHERE user_id = ?",
                    (referrer_id,),
                ).fetchone()
                if referrer_exists:
                    referral_cursor = db.execute("""
                        INSERT OR IGNORE INTO referrals
                        (referred_user_id, referrer_id, created_at)
                        VALUES (?, ?, ?)
                    """, (
                        user.id,
                        referrer_id,
                        datetime.now().isoformat(),
                    ))

                    if referral_cursor.rowcount == 1:
                        db.execute(
                            "UPDATE users SET balance = balance + ? WHERE user_id = ?",
                            (REFERRAL_BONUS, referrer_id),
                        )
                        db.execute("""
                            INSERT INTO wallet_transactions
                            (user_id, amount, transaction_type, description, created_at)
                            VALUES (?, ?, 'referral_bonus', ?, ?)
                        """, (
                            referrer_id,
                            REFERRAL_BONUS,
                            f"Bonus undangan user {user.id}",
                            datetime.now().isoformat(),
                        ))

        db.commit()


def get_referral_count(user_id):
    with sqlite3.connect(DATABASE) as db:
        return db.execute(
            "SELECT COUNT(*) FROM referrals WHERE referrer_id = ?",
            (user_id,),
        ).fetchone()[0]


def get_wallet_balance(user_id):
    with sqlite3.connect(DATABASE) as db:
        row = db.execute(
            "SELECT balance FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    return int(row[0]) if row else 0


def create_withdrawal(user_id, amount, payout_details):
    """Reserve balance and create a pending withdrawal atomically."""
    with sqlite3.connect(DATABASE) as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT balance FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        balance = int(row[0]) if row else 0

        if amount < MIN_WITHDRAWAL:
            return None, "minimum"
        if amount > balance:
            return None, "insufficient"

        db.execute(
            "UPDATE users SET balance = balance - ? WHERE user_id = ?",
            (amount, user_id),
        )
        cursor = db.execute("""
            INSERT INTO withdrawal_requests
            (user_id, amount, payout_details, status, created_at)
            VALUES (?, ?, ?, 'pending', ?)
        """, (user_id, amount, payout_details, datetime.now().isoformat()))
        request_id = cursor.lastrowid

        db.execute("""
            INSERT INTO wallet_transactions
            (user_id, amount, transaction_type, description, created_at)
            VALUES (?, ?, 'withdraw_hold', ?, ?)
        """, (
            user_id, -amount, f"Penahanan saldo untuk penarikan #{request_id}",
            datetime.now().isoformat()
        ))
        db.commit()
    return request_id, None


def process_withdrawal(request_id, approve):
    """Approve as paid, or reject and refund the reserved balance."""
    with sqlite3.connect(DATABASE) as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("""
            SELECT user_id, amount, status
            FROM withdrawal_requests WHERE id = ?
        """, (request_id,)).fetchone()

        if not row or row[2] != "pending":
            return None

        user_id, amount, _ = row
        new_status = "paid" if approve else "rejected"
        db.execute("""
            UPDATE withdrawal_requests
            SET status = ?, processed_at = ?
            WHERE id = ? AND status = 'pending'
        """, (new_status, datetime.now().isoformat(), request_id))

        if not approve:
            db.execute(
                "UPDATE users SET balance = balance + ? WHERE user_id = ?",
                (amount, user_id),
            )
            db.execute("""
                INSERT INTO wallet_transactions
                (user_id, amount, transaction_type, description, created_at)
                VALUES (?, ?, 'withdraw_refund', ?, ?)
            """, (
                user_id, amount,
                f"Pengembalian penarikan ditolak #{request_id}",
                datetime.now().isoformat()
            ))
        db.commit()
    return (user_id, amount, new_status)


def get_total_users():
    with sqlite3.connect(DATABASE) as db:
        return db.execute(
            "SELECT COUNT(*) FROM users"
        ).fetchone()[0]


# =====================================
# STATISTICS
# =====================================

async def get_total_sms():
    try:
        result = await get_statistics()
        summary = result.get("summary", {})

        return summary.get("total_messages", 0)

    except Exception:
        logger.exception("Gagal mengambil total SMS")
        return "Tidak tersedia"


# =====================================
# MAIN MENU
# =====================================

def main_menu():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "📱 GET NUMBER",
                callback_data="get_number",
            )
        ],
        [
            InlineKeyboardButton(
                "❌ CANCEL",
                callback_data="cancel",
            )
        ],
        [
            InlineKeyboardButton(
                "🎁 UNDANGAN / REFERRAL",
                callback_data="referral",
            )
        ],
        [
            InlineKeyboardButton(
                "💬 SUPPORT",
                url=f"https://t.me/{ADMIN_USERNAME.lstrip('@')}",
            )
        ],
    ])


def service_menu():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "WhatsApp",
                callback_data="service_whatsapp",
            )
        ],
        [
            InlineKeyboardButton(
                "⬅️ Kembali",
                callback_data="home",
            )
        ],
    ])


def country_menu():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🇮🇩 Indonesia",
                callback_data="country:indonesia",
            )
        ],
        [
            InlineKeyboardButton(
                "🇲🇾 Malaysia",
                callback_data="country:malaysia",
            )
        ],
        [
            InlineKeyboardButton(
                "🇵🇭 Philippines",
                callback_data="country:philippines",
            )
        ],
        [
            InlineKeyboardButton(
                "🇲🇲 Myanmar",
                callback_data="country:myanmar",
            )
        ],
                [
            InlineKeyboardButton(
                "🇵🇰 Pakistan",
                callback_data="country:pakistan",
            )
        ],
        [
            InlineKeyboardButton(
                "⬅️ Kembali",
                callback_data="get_number",
            )
        ],
    ])


# =====================================
# NUMBER KEYBOARD
# =====================================

def build_number_keyboard(numbers):
    keyboard = []

    for item in numbers:
        number = normalize_number(
            item.get("number", "")
        )

        if not number:
            continue

        keyboard.append([
            InlineKeyboardButton(
                f"📱 {item.get('number', number)}",
                callback_data=f"select_number:{number}",
            )
        ])

    keyboard.append([
        InlineKeyboardButton(
            "🟢 GANTI NOMOR",
            callback_data="next_numbers",
        )
    ])

    keyboard.append([
        InlineKeyboardButton(
            "📢 OTP CHANNEL",
            url="https://t.me/+hBeB1LSE3W4zZGM1",
        )
    ])

    keyboard.append([
        InlineKeyboardButton(
            "🔵 MENU UTAMA",
            callback_data="home",
        )
    ])

    return InlineKeyboardMarkup(keyboard)


# =====================================
# FILTER COUNTRY
# =====================================

def filter_numbers(numbers, country):
    keyword = COUNTRIES.get(country, "").upper()

    aliases = {
        "INDONESIA": ["INDONESIA"],
        "MALAYSIA": ["MALAYSIA"],
        "PHILIPPINES": [
            "PHILIPPINES",
            "PHILIPPINE",
        ],
        "MYANMAR": [
            "MYANMAR",
            "BURMA",
        ],
        "PAKISTAN": [
            "PAKISTAN",
        ],
    }

    keywords = aliases.get(keyword, [keyword])
    filtered = []

    for item in numbers:
        if not isinstance(item, dict):
            continue

        range_name = str(
            item.get("range_name", "")
        ).upper()

        if any(k in range_name for k in keywords):
            filtered.append(item)

    return filtered


# =====================================
# START
# =====================================

async def start(update, context):
    referrer_id = None
    if context.args:
        payload = context.args[0]
        if payload.startswith("ref_"):
            referrer_id = payload[4:]

    if not await verify_user_access(update, context):
        # Keep the inviter until the user passes the required membership check.
        if referrer_id:
            context.user_data["pending_referrer_id"] = referrer_id
        return

    user = update.effective_user
    register_user(user, referrer_id=referrer_id)

    total_users = get_total_users()
    total_sms = await get_total_sms()

    text = (
        "👋 <b>SELAMAT DATANG DI JING OTP BOT</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "📊 <b>STATISTIK BOT</b>\n"
        f"👥 Total pengguna: <b>{total_users:,}</b>\n"
        f"💬 Total SMS: <b>{total_sms}</b>\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Silakan pilih menu di bawah."
    )

    await update.effective_message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=main_menu(),
    )


# =====================================
# CANCEL
# =====================================

async def cancel_command(update, context):
    if not await verify_user_access(update, context):
        return

    context.user_data.clear()

    await update.effective_message.reply_text(
        "✅ Proses dibatalkan.",
        reply_markup=main_menu(),
    )


# =====================================
# SEND NUMBER LIST
# =====================================

async def send_number_list(
    update,
    context,
    country,
):
    query = update.callback_query

    try:
        await safe_edit_message(
            query,
            "⏳ Loading...",
        )

        all_numbers = await get_all_numbers()

        filtered = filter_numbers(
            all_numbers,
            country,
        )

        if not filtered:
            await safe_edit_message(
                query,
                f"❌ Tidak ada nomor tersedia untuk "
                f"{COUNTRIES[country]}.",
                reply_markup=country_menu(),
            )
            return

        context.user_data["numbers"] = filtered
        context.user_data["selected_country"] = country

        await send_next_three(
            query,
            context,
            edit_message=True,
        )

    except Exception:
        logger.exception("Gagal mengambil nomor")

        try:
            await safe_edit_message(
                query,
                "❌ Gagal mengambil nomor.\n"
                "Silakan coba kembali.",
                reply_markup=country_menu(),
            )
        except TelegramError:
            pass


# =====================================
# SEND / CHANGE RANDOM NUMBERS
# =====================================

async def send_next_three(
    query,
    context,
    edit_message=True,
):
    all_numbers = context.user_data.get(
        "numbers",
        [],
    )

    if not all_numbers:
        await safe_edit_message(
            query,
            "Silakan pilih negara kembali.",
            reply_markup=country_menu(),
        )
        return

    if len(all_numbers) <= NUMBERS_PER_REQUEST:
        selected = list(all_numbers)

    else:
        previous = {
            str(item.get("number", ""))
            for item in context.user_data.get(
                "current_numbers",
                [],
            )
        }

        available = [
            item for item in all_numbers
            if str(item.get("number", "")) not in previous
        ]

        if len(available) < NUMBERS_PER_REQUEST:
            available = all_numbers

        selected = random.sample(
            available,
            NUMBERS_PER_REQUEST,
        )

    context.user_data["current_numbers"] = selected

    country = context.user_data.get(
        "selected_country",
        "",
    )

    country_name = COUNTRIES.get(
        country,
        country.upper(),
    )

    remaining = max(
        len(all_numbers) - len(selected),
        0,
    )

    message = (
        f"🌍 <b>NOMOR {country_name}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📱 Jumlah nomor: <b>{len(selected)}</b>\n"
        f"📦 Total tersedia: <b>{len(all_numbers)}</b>\n"
        f"🔢 Sisa pilihan: <b>{remaining}</b>\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🟢 <i>Tekan PILIH pada nomor yang ingin kamu gunakan.\n"
        "Nomor akan ditautkan ke akun Telegram kamu.</i>"
    )

    keyboard = build_number_keyboard(selected)

    await safe_edit_message(
        query,
        message,
        parse_mode="HTML",
        reply_markup=keyboard,
    )


# =====================================
# MEMBERSHIP CHECK CALLBACK
# =====================================

async def handle_membership_check(update, context):
    query = update.callback_query
    user = update.effective_user

    is_member = await check_required_memberships(context.bot, user.id)

    if is_member is True:
        pending_referrer = context.user_data.pop("pending_referrer_id", None)
        register_user(user, referrer_id=pending_referrer)

        total_users = get_total_users()
        total_sms = await get_total_sms()

        welcome_text = (
            "👋 <b>SELAMAT DATANG DI JING OTP BOT</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "📊 <b>STATISTIK BOT</b>\n"
            f"👥 Total pengguna: <b>{total_users:,}</b>\n"
            f"💬 Total SMS: <b>{total_sms}</b>\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Silakan pilih menu di bawah."
        )

        await safe_edit_message(
            query,
            welcome_text,
            parse_mode="HTML",
            reply_markup=main_menu(),
        )

    elif is_member is False:
        await safe_edit_message(
            query,
            "🔒 <b>AKSES BOT TERKUNCI</b>\n\n"
            "Kamu belum bergabung ke grup dan/atau channel kami.\n"
            "Silakan bergabung ke keduanya, lalu cek kembali.",
            parse_mode="HTML",
            reply_markup=membership_keyboard(),
        )

    else:
        await safe_edit_message(
            query,
            "⚠️ Tidak dapat memeriksa keanggotaan.\n"
            "Pastikan bot menjadi admin channel dan anggota grup, lalu coba lagi.",
            reply_markup=membership_keyboard(),
        )


# =====================================
# REFERRAL / INVITATION
# =====================================

async def show_referral_page(query, context):
    user = query.from_user
    me = await context.bot.get_me()
    referral_link = f"https://t.me/{me.username}?start=ref_{user.id}"
    invited = get_referral_count(user.id)
    balance = get_wallet_balance(user.id)

    text = (
        "🎁 <b>PROGRAM UNDANGAN JING OTP</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👥 Total berhasil diundang: <b>{invited}</b> teman\n"
        f"💰 Saldo bonus: <b>Rp{balance:,}</b>\n"
        f"🎁 Bonus: <b>Rp{REFERRAL_BONUS:,}</b> per teman\n\n"
        "Bagikan link pribadi kamu. Bonus diberikan satu kali "
        "ketika pengguna baru berhasil terdaftar melalui link kamu.\n\n"
        "🔗 <b>LINK UNDANGAN KAMU:</b>\n"
        f"<code>{referral_link}</code>\n"
    )

    await safe_edit_message(
        query,
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("💸 WITHDRAW SALDO", callback_data="withdraw")],
            [InlineKeyboardButton("⬅️ MENU UTAMA", callback_data="home")]
        ]),
    )


# =====================================
# WITHDRAWAL FLOW
# =====================================

async def begin_withdrawal(query, context):
    user_id = query.from_user.id
    balance = get_wallet_balance(user_id)
    if balance < MIN_WITHDRAWAL:
        await safe_edit_message(
            query,
            f"💰 Saldo kamu: <b>Rp{balance:,}</b>\n"
            f"Minimal penarikan: <b>Rp{MIN_WITHDRAWAL:,}</b>.\n"
            "Undang teman untuk mengumpulkan bonus.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ KEMBALI", callback_data="referral")]
            ]),
        )
        return

    context.user_data["withdraw_state"] = "amount"
    await safe_edit_message(
        query,
        f"💸 <b>WITHDRAW SALDO</b>\n\n"
        f"Saldo tersedia: <b>Rp{balance:,}</b>\n"
        f"Minimal penarikan: <b>Rp{MIN_WITHDRAWAL:,}</b>\n\n"
        "Kirim jumlah penarikan dalam angka saja, contoh: <code>500</code>.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ BATAL", callback_data="referral")]
        ]),
    )


async def handle_withdraw_text(update, context):
    state = context.user_data.get("withdraw_state")
    if not state:
        return

    user = update.effective_user
    message = update.effective_message
    raw = (message.text or "").strip()

    if state == "amount":
        try:
            amount = int(raw.replace(".", "").replace(",", "").replace("Rp", "").replace("rp", "").strip())
        except ValueError:
            await message.reply_text("❌ Masukkan nominal angka yang valid, contoh: 1000.")
            return

        if amount < MIN_WITHDRAWAL:
            await message.reply_text(f"❌ Minimal withdraw Rp{MIN_WITHDRAWAL:,}. Masukkan nominal lain.")
            return
        balance = get_wallet_balance(user.id)
        if amount > balance:
            await message.reply_text(f"❌ Saldo tidak cukup. Saldo kamu Rp{balance:,}. Masukkan nominal lain.")
            return

        context.user_data["withdraw_amount"] = amount
        context.user_data["withdraw_state"] = "details"
        await message.reply_text(
            "🏦 Kirim tujuan pencairan dengan format, misalnya:\n"
            "<code>DANA 081234567890 a.n. Nama</code>\n"
            "atau <code>BCA 1234567890 a.n. Nama</code>.\n\n"
            "Pastikan data benar. Admin akan memproses secara manual.",
            parse_mode="HTML",
        )
        return

    if state == "details":
        amount = int(context.user_data.get("withdraw_amount", 0))
        details = raw[:500]
        if len(details) < 6:
            await message.reply_text("❌ Detail rekening/e-wallet terlalu singkat. Kirim ulang dengan lengkap.")
            return

        request_id, error = create_withdrawal(user.id, amount, details)
        if error == "insufficient":
            context.user_data.pop("withdraw_state", None)
            context.user_data.pop("withdraw_amount", None)
            await message.reply_text("❌ Saldo berubah atau tidak mencukupi. Silakan mulai withdraw kembali.")
            return
        if error:
            await message.reply_text("❌ Nominal di bawah batas minimum.")
            return

        context.user_data.pop("withdraw_state", None)
        context.user_data.pop("withdraw_amount", None)
        await message.reply_text(
            f"✅ Permintaan withdraw <b>#{request_id}</b> berhasil dikirim.\n"
            f"Nominal: <b>Rp{amount:,}</b>\n"
            "Status: <b>MENUNGGU ADMIN</b>. Saldo sudah ditahan sementara "
            "dan akan dikembalikan jika permintaan ditolak.",
            parse_mode="HTML",
            reply_markup=main_menu(),
        )

        try:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=(
                    f"💸 <b>PERMINTAAN WITHDRAW #{request_id}</b>\n"
                    f"User: <code>{user.id}</code> (@{user.username or '-'})\n"
                    f"Nama: {user.full_name}\n"
                    f"Nominal: <b>Rp{amount:,}</b>\n"
                    f"Tujuan: <code>{details}</code>\n"
                    "Silakan transfer secara manual sebelum menekan LUNAS."
                ),
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("✅ SUDAH DITRANSFER", callback_data=f"withdraw_paid:{request_id}"),
                        InlineKeyboardButton("❌ TOLAK", callback_data=f"withdraw_reject:{request_id}"),
                    ]
                ]),
            )
        except TelegramError:
            logger.exception("Gagal mengirim permintaan withdraw ke admin")


async def handle_withdraw_admin_callback(query, context, data):
    if query.from_user.id != ADMIN_ID:
        await query.answer("Khusus admin.", show_alert=True)
        return

    action, request_id_text = data.split(":", 1)
    try:
        request_id = int(request_id_text)
    except ValueError:
        await query.answer("ID permintaan tidak valid.", show_alert=True)
        return

    result = process_withdrawal(request_id, approve=(action == "withdraw_paid"))
    if not result:
        await query.answer("Permintaan sudah diproses atau tidak ditemukan.", show_alert=True)
        return

    user_id, amount, status = result
    if status == "paid":
        status_text = "LUNAS"
        user_text = f"✅ Withdraw #{request_id} sebesar Rp{amount:,} telah ditransfer admin."
    else:
        status_text = "DITOLAK"
        user_text = f"❌ Withdraw #{request_id} ditolak. Saldo Rp{amount:,} sudah dikembalikan."

    await query.edit_message_text(
        query.message.text + f"\n\nStatus: <b>{status_text}</b>",
        parse_mode="HTML",
    )
    try:
        await context.bot.send_message(chat_id=user_id, text=user_text)
    except TelegramError:
        logger.exception("Gagal memberi notifikasi status withdraw ke user")


# =====================================
# BUTTON HANDLER
# =====================================

async def button_handler(update, context):
    query = update.callback_query
    data = query.data

    try:
        await query.answer()
    except TelegramError:
        logger.warning(
            "Callback sudah kedaluwarsa: %s",
            data,
        )

    # Admin harus dapat memproses withdraw meski tidak melewati menu user.
    if data.startswith("withdraw_paid:") or data.startswith("withdraw_reject:"):
        await handle_withdraw_admin_callback(query, context, data)
        return

    # Tombol pengecekan grup harus diproses sebelum gate.
    if data == "check_membership":
        await handle_membership_check(
            update,
            context,
        )
        return

    # Semua tombol lainnya wajib melewati pengecekan grup.
    if not await verify_user_access(update, context):
        return

    if data == "home":
        context.user_data.clear()

        await safe_edit_message(
            query,
            "🏠 <b>MENU UTAMA</b>\n\n"
            "Silakan pilih menu:",
            parse_mode="HTML",
            reply_markup=main_menu(),
        )

    elif data == "cancel":
        context.user_data.clear()

        await safe_edit_message(
            query,
            "✅ Proses dibatalkan.",
            reply_markup=main_menu(),
        )

    elif data == "referral":
        await show_referral_page(query, context)

    elif data == "withdraw":
        await begin_withdrawal(query, context)

    elif data == "get_number":
        await safe_edit_message(
            query,
            "📱 SILAKAN PILIH LAYANAN:",
            reply_markup=service_menu(),
        )

    elif data == "service_whatsapp":
        await safe_edit_message(
            query,
            "🌍 PILIH NEGARA:",
            reply_markup=country_menu(),
        )

    elif data.startswith("country:"):
        country = data.split(":", 1)[1]

        if country not in COUNTRIES:
            return

        await send_number_list(
            update,
            context,
            country,
        )

    elif data.startswith("select_number:"):
        number = normalize_number(
            data.split(":", 1)[1]
        )

        user_id = update.effective_user.id

        owner_id = save_number_owner(
            user_id,
            number,
        )

        if owner_id != user_id:
            await query.message.reply_text(
                "❌ Nomor ini sudah ditautkan ke "
                "pengguna lain. Silakan pilih nomor berbeda."
            )
            return

        await query.message.reply_text(
            f"✅ Nomor <b>{number}</b> berhasil ditautkan "
            "ke akun kamu.\n\n"
            "SMS masuk untuk nomor ini akan diteruskan "
            "ke chat bot ini.\n"
            "Pastikan kamu sudah menekan /start.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "📋 SALIN NOMOR",
                        copy_text=CopyTextButton(text=number),
                    )
                ]
            ]),
        )

    elif data == "next_numbers":
        await send_next_three(
            query,
            context,
            edit_message=True,
        )


# =====================================
# ERROR HANDLER
# =====================================

async def error_handler(update, context):
    logger.error(
        "Terjadi kesalahan:",
        exc_info=context.error,
    )


# =====================================
# POST INIT / SHUTDOWN
# =====================================

async def post_init(application):
    # ID grup wajib tersedia sebelum SMS forwarder mulai berjalan.
    application.bot_data["required_group_id"] = REQUIRED_GROUP_ID
    application.bot_data["required_channel_id"] = REQUIRED_CHANNEL_ID

    init_database()
    init_sms_database()

    try:
        await start_cache(application)
    except Exception:
        logger.exception(
            "Cache awal gagal dimuat"
        )

    application.bot_data["sms_poll_task"] = asyncio.create_task(
        sms_poll_loop(application),
        name="sms-forwarder",
    )


async def post_shutdown(application):
    task = application.bot_data.get(
        "sms_poll_task"
    )

    if task is not None:
        task.cancel()

        try:
            await task
        except asyncio.CancelledError:
            pass

    await stop_cache(application)


# =====================================
# MAIN
# =====================================

def main():
    if not BOT_TOKEN:
        raise ValueError(
            "BOT_TOKEN belum diatur di .env"
        )

    init_database()

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    application.add_handler(
        CommandHandler("start", start)
    )

    application.add_handler(
        CommandHandler("cancel", cancel_command)
    )

    application.add_handler(
        CallbackQueryHandler(button_handler)
    )

    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_withdraw_text)
    )

    application.add_error_handler(
        error_handler
    )

    logger.info(
        "JING OTP Bot berjalan..."
    )

    application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
