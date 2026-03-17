from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes
from zoneinfo import ZoneInfo

import config
from attendance import AttendanceService, format_display_name, week_date_for
from database import (
    get_active_session,
    init_db,
    upsert_session_active,
    update_session_message_id,
    update_session_status,
)
from messages import render_attendance_progress, render_guide, render_session_open
from scheduler import build_scheduler


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("attendance-bot")


ATTEND_CB_DATA = "attend"


def _attend_keyboard(enabled: bool) -> InlineKeyboardMarkup | None:
    if not enabled:
        return None
    return InlineKeyboardMarkup([[InlineKeyboardButton("/attend", callback_data=ATTEND_CB_DATA)]])


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
    return datetime.utcnow().replace(tzinfo=ZoneInfo("UTC"))


async def cmd_attend(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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

    await _reply_alert(update, res.message_for_user)

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
    # Treat inline button as /attend
    await cmd_attend(update, context)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    svc: AttendanceService | None = context.application.bot_data.get("attendance_service")
    if svc is None:
        await update.message.reply_text("봇 초기화가 아직 완료되지 않았습니다. 잠시 후 다시 시도해 주세요.")
        return
    session, render = await svc.get_current_render()
    if not session or not render:
        await update.message.reply_text("현재 활성화된 세션이 없습니다.")
        return
    await update.message.reply_text(render.text, reply_markup=_attend_keyboard(not render.is_complete))


async def cmd_guide(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = render_guide(
        timezone=config.TIMEZONE,
        start_hour=config.SESSION_START_HOUR,
        end_hour=config.SESSION_END_HOUR,
        open_hour=config.SESSION_OPEN_HOUR,
        open_minute=config.SESSION_OPEN_MINUTE,
        max_attendees=config.MAX_ATTENDEES,
        dev_mode=config.DEV_MODE,
    )
    if update.message:
        await update.message.reply_text(text)


async def cmd_open(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not config.DEV_MODE:
        return
    await session_open(context.application)
    if update.message:
        await update.message.reply_text("세션을 열었습니다. 이제 /attend 로 테스트해 보세요.")


async def cmd_close(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not config.DEV_MODE:
        return
    await session_close(context.application)
    if update.message:
        await update.message.reply_text("세션을 종료했습니다.")


async def session_open(app: Application) -> None:
    chat_id = config.GROUP_CHAT_ID
    now = _utc_now()
    week_date = week_date_for(now.astimezone(ZoneInfo(config.TIMEZONE)))

    # send open announcement + initial attendance progress message
    open_text = render_session_open(config.SESSION_START_HOUR, config.SESSION_END_HOUR)
    await app.bot.send_message(chat_id=chat_id, text=open_text)

    progress = render_attendance_progress([], config.MAX_ATTENDEES, include_attend_cta=True)
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

    scheduler = build_scheduler(
        on_open=lambda: app.create_task(session_open(app)),
        on_close=lambda: app.create_task(session_close(app)),
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

    app.add_handler(CommandHandler("attend", cmd_attend))
    app.add_handler(CallbackQueryHandler(cb_attend, pattern=f"^{ATTEND_CB_DATA}$"))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("guide", cmd_guide))
    app.add_handler(CommandHandler("open", cmd_open))
    app.add_handler(CommandHandler("close", cmd_close))

    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

