from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta

from zoneinfo import ZoneInfo

from config import (
    DEV_MODE,
    MAX_ATTENDEES,
    SESSION_DAY,
    SESSION_END_HOUR,
    SESSION_END_MINUTE,
    SESSION_OPEN_HOUR,
    SESSION_OPEN_MINUTE,
    SESSION_START_HOUR,
    TIMEZONE,
)
from database import (
    SessionRow,
    get_active_session,
    insert_attendance_atomic,
    list_attendances,
    update_session_status,
)
from messages import MessageRender, render_attendance_progress


SEOUL = ZoneInfo(TIMEZONE)


def week_date_for(dt: datetime) -> str:
    return dt.date().isoformat()


def is_within_attendance_window(now: datetime) -> bool:
    """
    Attendance is allowed in [open_time, end_time).
    PRD: Sunday 20:30 ~ 23:00.
    """
    if DEV_MODE:
        return True
    local = now.astimezone(SEOUL)
    if local.weekday() != SESSION_DAY:
        return False
    open_dt = local.replace(hour=SESSION_OPEN_HOUR, minute=SESSION_OPEN_MINUTE, second=0, microsecond=0)
    end_dt = local.replace(hour=SESSION_END_HOUR, minute=SESSION_END_MINUTE, second=0, microsecond=0)
    return open_dt <= local < end_dt


def format_display_name(first_name: str | None, last_name: str | None, username: str | None) -> str:
    full = " ".join([p for p in [first_name or "", last_name or ""] if p]).strip()
    if full:
        return full
    if username:
        return username
    return "Unknown"


@dataclass
class AttendResult:
    ok: bool
    message_for_user: str
    should_update_message: bool = False
    render: MessageRender | None = None


class AttendanceService:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._lock = asyncio.Lock()

    async def handle_attend(self, user_id: int, user_name: str, now_utc: datetime) -> AttendResult:
        if not is_within_attendance_window(now_utc):
            return AttendResult(
                ok=False,
                message_for_user=f"출석 시간이 아닙니다. (일요일 오후 {SESSION_START_HOUR}시 ~ {SESSION_END_HOUR}시)",
            )

        async with self._lock:
            session = await get_active_session(self.db_path)
            if session is None:
                return AttendResult(ok=False, message_for_user="현재 활성화된 출석 세션이 없습니다.")

            if session.status != "active":
                return AttendResult(ok=False, message_for_user="출석이 이미 종료되었습니다.")

            attendees = await list_attendances(self.db_path, session.id)
            if len(attendees) >= MAX_ATTENDEES:
                return AttendResult(ok=False, message_for_user="출석이 이미 완료되었습니다.")

            inserted, _order = await insert_attendance_atomic(self.db_path, session.id, user_id, user_name)
            if not inserted:
                return AttendResult(ok=False, message_for_user="이미 출석 처리되었습니다. 😊")

            attendees = await list_attendances(self.db_path, session.id)
            names = [a.user_name for a in attendees]
            render = render_attendance_progress(names, MAX_ATTENDEES, include_attend_cta=False)

            if render.is_complete:
                await update_session_status(self.db_path, session.id, "completed")

            return AttendResult(
                ok=True,
                message_for_user="",  # 봇이 단체방에 보내지 않음; 명단 edit만 갱신
                should_update_message=True,
                render=render,
            )

    async def get_current_render(self) -> tuple[SessionRow | None, MessageRender | None]:
        session = await get_active_session(self.db_path)
        if not session:
            return None, None
        attendees = await list_attendances(self.db_path, session.id)
        names = [a.user_name for a in attendees]
        render = render_attendance_progress(names, MAX_ATTENDEES, include_attend_cta=False)
        return session, render

