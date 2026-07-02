"""
Centralized configuration for the VC-to-VC forwarding userbot.

All environment variables are read exactly once, validated, and exposed
as typed module-level constants so every other module can simply do
`import config` and use `config.API_ID`, etc.
"""

import os
import sys

from dotenv import load_dotenv

load_dotenv()


def _require(name: str) -> str:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        sys.exit(f"[config] Missing required environment variable: {name}")
    return value.strip()


def _require_int(name: str) -> int:
    raw = _require(name)
    try:
        return int(raw)
    except ValueError:
        sys.exit(f"[config] Environment variable {name} must be an integer, got: {raw!r}")


# ---- Telegram credentials -------------------------------------------------
API_ID: int = _require_int("API_ID")
API_HASH: str = _require("API_HASH")
BOT_TOKEN: str = _require("BOT_TOKEN")
STRING_SESSION: str = _require("STRING_SESSION")
OWNER_ID: int = _require_int("OWNER_ID")

# ---- Group configuration ---------------------------------------------------
RECORD_GROUP: int = _require_int("RECORD_GROUP")
LOGGER_GROUP: int = _require_int("LOGGER_GROUP")

# ---- Database ---------------------------------------------------------------
MONGO_URI: str = _require("MONGO_URI")
DB_NAME: str = _require("DB_NAME")

# ---- Audio bridge -------------------------------------------------------
PULSE_SINK_NAME: str = os.environ.get("PULSE_SINK_NAME", "vcrelay").strip() or "vcrelay"

# ---- Filesystem -------------------------------------------------------------
WORK_DIR: str = os.environ.get("WORK_DIR", "/tmp/vc_forward_bot").strip() or "/tmp/vc_forward_bot"
LOG_DIR: str = os.path.join(WORK_DIR, "logs")
PIPE_DIR: str = os.path.join(WORK_DIR, "pipes")
RECORDINGS_DIR: str = os.path.join(WORK_DIR, "recordings")
SCREEN_SHARE_DEVICE: str = os.environ.get("SCREEN_SHARE_DEVICE", ":1.0")

for _directory in (WORK_DIR, LOG_DIR, PIPE_DIR, RECORDINGS_DIR):
    os.makedirs(_directory, exist_ok=True)

# ---- Audio defaults -----------------------------------------------------
DEFAULT_LEVEL: int = 5   # 1-25
DEFAULT_BASS: int = 0    # 0-15
MIN_LEVEL, MAX_LEVEL = 1, 25
MIN_BASS, MAX_BASS = 0, 15

SESSION_NAME_ASSISTANT = "assistant_session"
SESSION_NAME_BOT = "bot_session"
