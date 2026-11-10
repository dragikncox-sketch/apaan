import os
from dotenv import load_dotenv

load_dotenv()

# TELEGRAM
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "@admin")

# WAJIB JOIN GRUP
REQUIRED_GROUP_ID = int(
    os.getenv("REQUIRED_GROUP_ID", "-1004387643125")
)
REQUIRED_GROUP_LINK = os.getenv(
    "REQUIRED_GROUP_LINK",
    "https://t.me/+2xpSHUjQfmM3ZWM1"
)
REQUIRED_GROUP_ID = int(
    os.getenv("REQUIRED_GROUP_ID", "-1004363973467")
)
REQUIRED_GROUP_LINK = os.getenv(
    "REQUIRED_GROUP_LINK",
    "https://t.me/+hBeB1LSE3W4zZGM1"
)

# AUGESTEL API
API_KEY = os.getenv("API_KEY", "")
AUTH_PREFIX = os.getenv("AUTH_PREFIX", "Bearer")
ACCEPT = os.getenv("ACCEPT", "application/json")

NUMBERS_URL = os.getenv(
    "NUMBERS_URL",
    "https://augestel.com/api/v1/iprn/numbers"
)
STATISTICS_URL = os.getenv(
    "STATISTICS_URL",
    "https://augestel.com/api/v1/iprn/statistics"
)
MESSAGES_URL = os.getenv(
    "MESSAGES_URL",
    "https://augestel.com/api/v1/iprn/messages"
)

# PAGINATION
PER_PAGE = int(os.getenv("PER_PAGE", "100"))
PAGE_PARAM = os.getenv("PAGE_PARAM", "page")
PER_PAGE_PARAM = os.getenv("PER_PAGE_PARAM", "per_page")

# STATISTICS
START_DATE = os.getenv("START_DATE", "2026-11-08")
END_DATE = os.getenv("END_DATE", "2027-12-08")
GROUP_BY = os.getenv("GROUP_BY", "day")

# MESSAGES
MESSAGES_START_DATE = os.getenv("MESSAGES_START_DATE", "")
MESSAGES_END_DATE = os.getenv("MESSAGES_END_DATE", "")
MESSAGES_TYPE = os.getenv("MESSAGES_TYPE", "a2p")
MESSAGES_PER_PAGE = int(os.getenv("MESSAGES_PER_PAGE", "100"))
MESSAGES_PAGE = int(os.getenv("MESSAGES_PAGE", "1"))

# SMS FORWARDER
POLL_INTERVAL = max(
    120,
    int(os.getenv("POLL_INTERVAL", "120"))
)
