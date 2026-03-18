import os


def _get_env(name: str, default: str | None = None) -> str | None:
    v = os.getenv(name)
    if v is None or v == "":
        return default
    return v


BOT_TOKEN = _get_env("BOT_TOKEN", None)

# Telegram Group Chat ID (e.g. -1001234567890)
GROUP_CHAT_ID = int(_get_env("GROUP_CHAT_ID", "0"))

# Session scheduling/config
SESSION_DAY = int(_get_env("SESSION_DAY", "6"))  # 0=Mon ... 6=Sun
SESSION_OPEN_HOUR = int(_get_env("SESSION_OPEN_HOUR", "20"))
SESSION_OPEN_MINUTE = int(_get_env("SESSION_OPEN_MINUTE", "30"))

# Informational message text (PRD: 21:00 start ~ 23:00 end)
SESSION_START_HOUR = int(_get_env("SESSION_START_HOUR", "21"))
SESSION_END_HOUR = int(_get_env("SESSION_END_HOUR", "23"))
SESSION_END_MINUTE = int(_get_env("SESSION_END_MINUTE", "0"))

MAX_ATTENDEES = int(_get_env("MAX_ATTENDEES", "24"))
TIMEZONE = _get_env("TIMEZONE", "Asia/Seoul") or "Asia/Seoul"

# Storage
DB_PATH = _get_env("DB_PATH", "data/attendance.db") or "data/attendance.db"

# Reset data password: required for /reset command. If not set, /reset is disabled.
# Stored only in env; never logged or exposed in responses.
RESET_PASSWORD = _get_env("RESET_PASSWORD", "") or ""

