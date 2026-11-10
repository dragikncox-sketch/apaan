import sqlite3

DB_FILE = "jing_otp.db"


def get_db():
    db = sqlite3.connect(DB_FILE, timeout=10)
    db.execute("PRAGMA busy_timeout=10000")
    return db


def init_sms_database():
    with get_db() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS number_owners (
                number TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS seen_messages (
                message_hash TEXT PRIMARY KEY,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS sms_forwarder_state (
                state_key TEXT PRIMARY KEY,
                state_value TEXT
            )
        """)


def normalize_number(number):
    return "".join(
        char for char in str(number or "")
        if char.isdigit()
    )


def save_number_owner(user_id, number):
    number = normalize_number(number)

    if not number:
        return None

    with get_db() as db:
        db.execute("""
            INSERT OR IGNORE INTO number_owners
            (number, user_id)
            VALUES (?, ?)
        """, (number, int(user_id)))

        row = db.execute(
            "SELECT user_id FROM number_owners WHERE number = ?",
            (number,)
        ).fetchone()

    return row[0] if row else None


def get_number_owner(number):
    number = normalize_number(number)

    with get_db() as db:
        row = db.execute(
            "SELECT user_id FROM number_owners WHERE number = ?",
            (number,)
        ).fetchone()

    return row[0] if row else None


def is_message_seen(message_hash):
    with get_db() as db:
        row = db.execute(
            "SELECT 1 FROM seen_messages WHERE message_hash = ?",
            (message_hash,)
        ).fetchone()

    return row is not None


def mark_message_seen(message_hash):
    with get_db() as db:
        db.execute(
            "INSERT OR IGNORE INTO seen_messages (message_hash) VALUES (?)",
            (message_hash,)
        )


def is_first_poll():
    with get_db() as db:
        row = db.execute(
            """
            SELECT state_value
            FROM sms_forwarder_state
            WHERE state_key = 'initialized'
            """
        ).fetchone()

    return row is None


def mark_initialized():
    with get_db() as db:
        db.execute("""
            INSERT OR REPLACE INTO sms_forwarder_state
            (state_key, state_value)
            VALUES ('initialized', '1')
        """)