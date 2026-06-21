from pathlib import Path
import os
from dotenv import load_dotenv

load_dotenv(".env")

DB_TYPE = os.getenv("DB_TYPE", "sqlite")

if DB_TYPE == "postgres":
    DB_HOST = os.getenv("DB_HOST", "localhost")
    DB_PORT = os.getenv("DB_PORT", "5432")
    DB_NAME = os.getenv("DB_NAME", "reales_bot")
    DB_USER = os.getenv("DB_USER", "reales_bot")
    DB_PASSWORD = os.getenv("DB_PASSWORD", "reales_bot")
    DATABASE_URL = f"postgresql+asyncpg://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
else:
    DB_PATH = Path("reales_bot.db")
    DATABASE_URL = f"sqlite+aiosqlite:///{DB_PATH}"

BOT_VERSION = "1.0"

LOCAL_API_URL = os.getenv("LOCAL_API_URL", "")
RELEASE_GROUP_ID = int(os.getenv("RELEASE_GROUP_ID", "0"))
ANNOUNCEMENT_CHANNEL_ID = int(os.getenv("ANNOUNCEMENT_CHANNEL_ID", "0"))