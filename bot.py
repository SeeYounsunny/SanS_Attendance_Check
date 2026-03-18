from __future__ import annotations

import asyncio
import hmac
import logging
from datetime import date, datetime, timedelta, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes
from zoneinfo import ZoneInfo

import config
from attendance import AttendanceService, format_display_name, week_date_for
from database import (
    get_active_session,
    get_session_by_week_date,
    init_db,
    list_attendances,
    list_attendances_for_sessions,
    list_monthly_attendance_counts,
    list_sessions_between,
    list_sessions_recent,
    reset_all_data,
    top_attendees_between,
    count_user_attendances_between,
    upsert_session_active,
    update_session_message_id,
    update_session_status,
)
from messages import render_attendance_progress, render_guide, render_session_open
from scheduler import build_scheduler


logging.basicConfig(level=logging.INFO)
# 텔레그램 HTTP 요청 URL에 토큰이 포함되므로 httpx INFO 로그 비활성화
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger("attendance-bot")


ATTEND_CB_DATA = "attend"

MEMBER_CACHE_TTL_SEC = 300


def _attend_keyboard(enabled: bool) -> InlineKeyboardMarkup | None:
    if not enabled:
        return None
    return InlineKeyboardMarkup([[InlineKeyboardButton("출석", callback_data=ATTEND_CB_DATA)]])


async def _reply_alert(update: Update, text: str) -> None:
    if update.callback_query:
        try:
            await update.callback_query.answer(text=text, show_alert=False)
        except TelegramError:
            pass
        return
    if update.message:
        try:
            await update.message.reply_text(text)
        except TelegramError:
            pass


async def _edit_or_fallback(
    bot,
    chat_id: int,
    message_id: int | None,
    text: str,
    reply_markup: InlineKeyboardMarkup | None,
) -> int:
    if message_id is not None:
        for attempt in range(3):
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=text,
                    reply_markup=reply_markup,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
                return message_id
            except TelegramError as e:
                logger.warning("edit failed attempt=%s err=%s", attempt + 1, e)
                await asyncio.sleep(0.3 * (attempt + 1))

    sent = await bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=reply_markup,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )
    return sent.message_id


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _local_today() -> date:
    return datetime.now(tz=ZoneInfo(config.TIMEZONE)).date()


def _month_range(d: date) -> tuple[date, date]:
    start = d.replace(day=1)
    if start.month == 12:
        next_month = start.replace(year=start.year + 1, month=1)
    else:
        next_month = start.replace(month=start.month + 1)
    end = next_month - timedelta(days=1)
    return start, end


def _now_ts() -> float:
    return datetime.now(timezone.utc).timestamp()


async def _is_group_member_cached(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Returns True if user is a member/admin/creator of configured GROUP_CHAT_ID.
    Cached for a short TTL to avoid excessive API calls.
    """
    u = update.effective_user
    if not u:
        return False

    cache: dict[int, tuple[float, bool]] = context.application.bot_data.setdefault("member_cache", {})
    ts = _now_ts()
    cached = cache.get(u.id)
    if cached and (ts - cached[0]) < MEMBER_CACHE_TTL_SEC:
        return cached[1]

    try:
        member = await context.bot.get_chat_member(chat_id=config.GROUP_CHAT_ID, user_id=u.id)
        ok = member.status in {"member", "administrator", "creator"}
    except TelegramError as e:
        logger.warning("get_chat_member failed user_id=%s err=%s", u.id, e)
        ok = False

    cache[u.id] = (ts, ok)
    return ok


async def _require_allowed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Access control:
    - Only in the configured group/supergroup (no 1:1 / private chat).
    - User must be a member of that group.
    """
    chat = update.effective_chat
    if not chat:
        return False

    if chat.type == "private":
        await _reply_alert(
            update,
            "출석·명령은 1:1 대화가 아니라 단체방에서만 사용할 수 있어요.",
        )
        return False

    if chat.type not in {"group", "supergroup"} or chat.id != config.GROUP_CHAT_ID:
        if chat.type in {"group", "supergroup"}:
            await _reply_alert(update, "이 봇은 지정된 단체방에서만 사용할 수 있어요.")
        else:
            await _reply_alert(update, "출석·명령은 단체방에서만 사용할 수 있어요.")
        return False

    if not await _is_group_member_cached(update, context):
        await _reply_alert(update, "이 봇은 SanS 1조 단체방 멤버만 사용할 수 있어요.")
        return False

    return True


async def handle_attend(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_allowed(update, context):
        return
    svc: AttendanceService | None = context.application.bot_data.get("attendance_service")
    if svc is None:
        logger.error("attendance_service not initialized (startup hook not run?)")
        await _reply_alert(update, "봇 초기화가 아직 완료되지 않았습니다. 잠시 후 다시 시도해 주세요.")
        return
    chat_id = config.GROUP_CHAT_ID

    u = update.effective_user
    if not u:
        return
    user_name = format_display_name(u.first_name, u.last_name, u.username)

    res = await svc.handle_attend(user_id=u.id, user_name=user_name, now_utc=_utc_now())
    if not res.ok:
        await _reply_alert(update, res.message_for_user)
        return

    # 성공: 단체방에 메시지 없음. 버튼은 상단 짧은 알림만(채팅에 남지 않음).
    if update.callback_query:
        try:
            await update.callback_query.answer(text="출석되었습니다.")
        except TelegramError:
            pass

    if not res.should_update_message or not res.render:
        return

    session = await get_active_session(config.DB_PATH)
    if not session:
        return

    enabled = not res.render.is_complete
    new_message_id = await _edit_or_fallback(
        bot=context.bot,
        chat_id=chat_id,
        message_id=session.message_id,
        text=res.render.text,
        reply_markup=_attend_keyboard(enabled),
    )
    if session.message_id != new_message_id:
        await update_session_message_id(config.DB_PATH, session.id, new_message_id)


async def cb_attend(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await handle_attend(update, context)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_allowed(update, context):
        return
    svc: AttendanceService | None = context.application.bot_data.get("attendance_service")
    if svc is None:
        await update.message.reply_text("봇 초기화가 아직 완료되지 않았습니다. 잠시 후 다시 시도해 주세요.")
        return
    session, render = await svc.get_current_render()
    if not session or not render:
        await update.message.reply_text("현재 활성화된 세션이 없습니다.")
        return
    await update.message.reply_text(render.text, reply_markup=_attend_keyboard(not render.is_complete))


async def cmd_result(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """오늘(타임존 기준) 세션의 출석 현황."""
    if not await _require_allowed(update, context):
        return
    today = _local_today()
    week_date_str = today.isoformat()
    session = await get_session_by_week_date(config.DB_PATH, week_date_str)
    if not session:
        if update.message:
            await update.message.reply_text(
                f"📋 오늘({week_date_str})에는 아직 출석 세션이 없습니다.\n"
                "세션이 열린 뒤 다시 확인해 주세요."
            )
        return
    rows = await list_attendances(config.DB_PATH, session.id)
    names = [r.user_name for r in rows]
    render = render_attendance_progress(names, config.MAX_ATTENDEES, include_attend_cta=False)
    status_ko = {
        "active": "진행 중",
        "completed": "목표 달성(완료)",
        "ended": "세션 종료",
    }.get(session.status, session.status)
    header = f"📋 오늘 출석 현황 ({week_date_str}) · {status_ko}\n\n"
    if update.message:
        await update.message.reply_text(header + render.text, disable_web_page_preview=True)


async def cmd_guide(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_allowed(update, context):
        return
    text = render_guide(
        timezone=config.TIMEZONE,
        start_hour=config.SESSION_START_HOUR,
        end_hour=config.SESSION_END_HOUR,
        open_hour=config.SESSION_OPEN_HOUR,
        open_minute=config.SESSION_OPEN_MINUTE,
        max_attendees=config.MAX_ATTENDEES,
    )
    if update.message:
        await update.message.reply_text(text)


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_allowed(update, context):
        return
    today = _local_today()

    # Recent 4 sessions (by session date)
    recent_sessions = await list_sessions_recent(config.DB_PATH, limit=4, offset=0)
    recent_sessions_sorted = list(reversed(recent_sessions))  # oldest -> newest
    attendance_map = await list_attendances_for_sessions(config.DB_PATH, [s.id for s in recent_sessions_sorted])

    lines: list[str] = ["📊 출석 통계"]

    if recent_sessions_sorted:
        lines.append("")
        lines.append("🗓️ 최근 4회 세션 참석자 수")
        for s in recent_sessions_sorted:
            lines.append(f"- {s.week_date}: {len(attendance_map.get(s.id, []))}명")
    else:
        lines.append("")
        lines.append("🗓️ 최근 4회 세션: 데이터가 없습니다.")

    # Last 12 months monthly totals
    start_12m = (today.replace(day=1) - timedelta(days=365)).replace(day=1)
    end_12m = today
    monthly = await list_monthly_attendance_counts(config.DB_PATH, start_month=start_12m, end_month=end_12m)

    sessions_12m = await list_sessions_between(config.DB_PATH, start_12m, end_12m)
    attend_12m_map = await list_attendances_for_sessions(config.DB_PATH, [s.id for s in sessions_12m])
    total_att_12m = sum(len(attend_12m_map.get(s.id, [])) for s in sessions_12m)
    avg_per_session_12m = (total_att_12m / len(sessions_12m)) if sessions_12m else 0.0

    lines.append("")
    lines.append("📅 최근 12개월 월별 참석(합계)")
    if monthly:
        for ym, cnt in monthly[-12:]:
            lines.append(f"- {ym}: {cnt}명")
    else:
        lines.append("- 데이터가 없습니다.")

    lines.append("")
    lines.append(f"📈 월 평균(최근 12개월, 세션당): {avg_per_session_12m:.2f}명")

    # Year stats (recent 365 days)
    start_1y = today - timedelta(days=365)
    sessions_1y = await list_sessions_between(config.DB_PATH, start_1y, today)
    attend_1y_map = await list_attendances_for_sessions(config.DB_PATH, [s.id for s in sessions_1y])
    total_att_1y = sum(len(attend_1y_map.get(s.id, [])) for s in sessions_1y)
    avg_per_session_1y = (total_att_1y / len(sessions_1y)) if sessions_1y else 0.0
    lines.append(f"📈 연 평균(최근 1년, 세션당): {avg_per_session_1y:.2f}명")

    if update.message:
        await update.message.reply_text("\n".join(lines))


async def cmd_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_allowed(update, context):
        return
    args = (context.args or [])
    mode = (args[0].strip().lower() if args else "week")
    if mode not in {"week", "month"}:
        mode = "week"

    if mode == "week":
        sessions = await list_sessions_recent(config.DB_PATH, limit=2, offset=0)
        if len(sessions) < 2:
            await update.message.reply_text("지난주 세션 데이터가 없습니다.")
            return
        target = sessions[1]  # newest is [0], previous is [1]
        att_map = await list_attendances_for_sessions(config.DB_PATH, [target.id])
        names = [a.user_name for a in att_map.get(target.id, [])]
        text = render_attendance_progress(names, config.MAX_ATTENDEES, include_attend_cta=False).text
        await update.message.reply_text(f"🗓️ 지난주 출석 현황 ({target.week_date})\n\n{text}")
        return

    # mode == "month" (previous calendar month)
    today = _local_today()
    first_this_month = today.replace(day=1)
    prev_month_end = first_this_month - timedelta(days=1)
    prev_start, prev_end = _month_range(prev_month_end)
    sessions = await list_sessions_between(config.DB_PATH, prev_start, prev_end)
    if not sessions:
        await update.message.reply_text("지난달 세션 데이터가 없습니다.")
        return
    att_map = await list_attendances_for_sessions(config.DB_PATH, [s.id for s in sessions])
    lines: list[str] = [f"📅 지난달 출석 현황 ({prev_start:%Y-%m})", ""]
    for s in sessions:
        names = [a.user_name for a in att_map.get(s.id, [])]
        lines.append(f"- {s.week_date}: {len(names)}명")
        if names:
            lines.append("  " + ", ".join(names))
    await update.message.reply_text("\n".join(lines))


async def cmd_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_allowed(update, context):
        return
    if not context.args:
        await update.message.reply_text("사용법: /search <이름 일부>\n예: /search 홍길동")
        return
    q = " ".join(context.args).strip()
    today = _local_today()
    start_30 = today - timedelta(days=30)
    start_365 = today - timedelta(days=365)

    s30, a30 = await count_user_attendances_between(config.DB_PATH, q, start_30, today)
    s365, a365 = await count_user_attendances_between(config.DB_PATH, q, start_365, today)

    text = (
        f"🔎 출석 검색: `{q}`\n\n"
        f"- 최근 30일: {s30}회 세션 / {a30}건 출석\n"
        f"- 최근 1년: {s365}회 세션 / {a365}건 출석"
    )
    await update.message.reply_text(text)


async def cmd_top10(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_allowed(update, context):
        return
    args = (context.args or [])
    mode = (args[0].strip().lower() if args else "year")
    if mode not in {"month", "year"}:
        mode = "year"

    today = _local_today()
    if mode == "month":
        start = today - timedelta(days=30)
        title = "🏆 출석 TOP10 (최근 30일)"
    else:
        start = today - timedelta(days=365)
        title = "🏆 출석 TOP10 (최근 1년)"

    rows = await top_attendees_between(config.DB_PATH, start, today, limit=10)
    if not rows:
        await update.message.reply_text("데이터가 없습니다.")
        return
    lines = [title, ""]
    for i, r in enumerate(rows, start=1):
        lines.append(f"{i}. {r.user_name} — {r.attendee_count}회")
    await update.message.reply_text("\n".join(lines))

async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Delete all attendance/session data. Requires RESET_PASSWORD env to be set and matching."""
    if not await _require_allowed(update, context):
        return
    if not config.RESET_PASSWORD:
        await _reply_alert(update, "데이터 초기화 기능이 비활성화되어 있습니다.")
        return
    args = (context.args or [])
    if len(args) != 1:
        await _reply_alert(update, "사용법: /reset <비밀번호>")
        return
    if not hmac.compare_digest(config.RESET_PASSWORD, args[0]):
        await _reply_alert(update, "비밀번호가 올바르지 않습니다.")
        return
    await reset_all_data(config.DB_PATH)
    await _reply_alert(update, "모든 출석·세션 데이터가 삭제되었습니다.")


async def session_open(app: Application) -> None:
    chat_id = config.GROUP_CHAT_ID
    now = _utc_now()
    week_date = week_date_for(now.astimezone(ZoneInfo(config.TIMEZONE)))

    # send open announcement + initial attendance progress message
    open_text = render_session_open(config.SESSION_START_HOUR, config.SESSION_END_HOUR)
    await app.bot.send_message(chat_id=chat_id, text=open_text)

    progress = render_attendance_progress([], config.MAX_ATTENDEES, include_attend_cta=False)
    sent = await app.bot.send_message(
        chat_id=chat_id,
        text=progress.text,
        reply_markup=_attend_keyboard(True),
        disable_web_page_preview=True,
    )

    s = await upsert_session_active(config.DB_PATH, week_date=week_date, message_id=sent.message_id)
    if s.message_id != sent.message_id:
        await update_session_message_id(config.DB_PATH, s.id, sent.message_id)


async def session_close(app: Application) -> None:
    chat_id = config.GROUP_CHAT_ID
    svc: AttendanceService = app.bot_data["attendance_service"]

    session, render = await svc.get_current_render()
    if not session or not render:
        return

    # remove button on last message
    final_message_id = await _edit_or_fallback(
        bot=app.bot,
        chat_id=chat_id,
        message_id=session.message_id,
        text=render.text,
        reply_markup=None,
    )
    if session.message_id != final_message_id:
        await update_session_message_id(config.DB_PATH, session.id, final_message_id)

    await update_session_status(config.DB_PATH, session.id, "ended")
    await app.bot.send_message(chat_id=chat_id, text="📋 [출석체크 종료]\n최종 출석자 명단이 확정되었습니다.")


async def on_startup(app: Application) -> None:
    await init_db(config.DB_PATH)
    app.bot_data["attendance_service"] = AttendanceService(config.DB_PATH)

    # APScheduler jobs may run in a worker thread (default executor).
    # Ensure our async session_open/session_close always execute on the main asyncio loop.
    loop = asyncio.get_running_loop()

    def _run_on_loop(coro) -> None:
        asyncio.run_coroutine_threadsafe(coro, loop)

    scheduler = build_scheduler(
        on_open=lambda: _run_on_loop(session_open(app)),
        on_close=lambda: _run_on_loop(session_close(app)),
    )
    scheduler.start()
    app.bot_data["scheduler"] = scheduler

    logger.info("Bot started. chat_id=%s tz=%s", config.GROUP_CHAT_ID, config.TIMEZONE)


def _validate_config() -> None:
    if not config.BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is required (env BOT_TOKEN)")
    if config.GROUP_CHAT_ID == 0:
        raise RuntimeError("GROUP_CHAT_ID is required (env GROUP_CHAT_ID)")


def main() -> None:
    _validate_config()
    app = Application.builder().token(config.BOT_TOKEN).post_init(on_startup).build()

    app.add_handler(CallbackQueryHandler(cb_attend, pattern=f"^{ATTEND_CB_DATA}$"))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("result", cmd_result))
    app.add_handler(CommandHandler("guide", cmd_guide))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("history", cmd_history))
    app.add_handler(CommandHandler("search", cmd_search))
    app.add_handler(CommandHandler("top10", cmd_top10))
    app.add_handler(CommandHandler("reset", cmd_reset))

    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

