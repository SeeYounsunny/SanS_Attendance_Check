from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from zoneinfo import ZoneInfo

from config import (
    SESSION_DAY,
    SESSION_END_HOUR,
    SESSION_END_MINUTE,
    SESSION_OPEN_HOUR,
    SESSION_OPEN_MINUTE,
    TIMEZONE,
)


SEOUL = ZoneInfo(TIMEZONE)


@dataclass(frozen=True)
class ScheduleConfig:
    day_of_week: int
    open_hour: int
    open_minute: int
    end_hour: int
    end_minute: int
    timezone: str


def build_scheduler(
    on_open: callable,
    on_close: callable,
    cfg: ScheduleConfig | None = None,
) -> AsyncIOScheduler:
    cfg = cfg or ScheduleConfig(
        day_of_week=SESSION_DAY,
        open_hour=SESSION_OPEN_HOUR,
        open_minute=SESSION_OPEN_MINUTE,
        end_hour=SESSION_END_HOUR,
        end_minute=SESSION_END_MINUTE,
        timezone=TIMEZONE,
    )

    scheduler = AsyncIOScheduler(timezone=ZoneInfo(cfg.timezone))

    scheduler.add_job(
        on_open,
        CronTrigger(
            day_of_week=cfg.day_of_week,
            hour=cfg.open_hour,
            minute=cfg.open_minute,
            second=0,
            timezone=ZoneInfo(cfg.timezone),
        ),
        id="attendance_open",
        replace_existing=True,
        misfire_grace_time=60 * 5,
    )

    scheduler.add_job(
        on_close,
        CronTrigger(
            day_of_week=cfg.day_of_week,
            hour=cfg.end_hour,
            minute=cfg.end_minute,
            second=0,
            timezone=ZoneInfo(cfg.timezone),
        ),
        id="attendance_close",
        replace_existing=True,
        misfire_grace_time=60 * 5,
    )

    return scheduler


def now_utc() -> datetime:
    return datetime.utcnow().replace(tzinfo=ZoneInfo("UTC"))

