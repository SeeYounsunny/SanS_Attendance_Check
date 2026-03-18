from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MessageRender:
    text: str
    is_complete: bool


def render_session_open(start_hour: int, end_hour: int) -> str:
    return (
        "📋 [출석체크 시작]\n"
        f"금일 세션 출석체크가 시작되었습니다. (오후 {start_hour}시 ~ {end_hour}시)\n"
        "아래 출석 버튼을 눌러 출석해 주세요!"
    )


def render_attendance_progress(
    names: list[str],
    max_attendees: int,
    include_attend_cta: bool,
) -> MessageRender:
    n = len(names)
    header = f"📋 출석 현황 ({n}/{max_attendees})"

    lines: list[str] = [header, ""]
    for idx, name in enumerate(names, start=1):
        lines.append(f"{idx}. {name}")

    lines.append("")

    is_complete = n >= max_attendees
    if is_complete:
        lines.append("✅ 완료 - 전원 출석!")
        return MessageRender(text="\n".join(lines), is_complete=True)

    if include_attend_cta:
        lines.append("아래 출석 버튼을 눌러주세요.")
    return MessageRender(text="\n".join(lines), is_complete=False)


def render_guide(
    *,
    timezone: str,
    start_hour: int,
    end_hour: int,
    open_hour: int,
    open_minute: int,
    max_attendees: int,
    dev_mode: bool,
) -> str:
    time_line = f"일요일 {open_hour:02d}:{open_minute:02d} ~ {end_hour:02d}:00 ({timezone})"
    if dev_mode:
        time_line = f"{time_line}\n(DEV_MODE=1: 시간 제한 없이 테스트 가능)"

    return (
        "📌 출석체크 사용법\n\n"
        f"- 출석 가능 시간: {time_line}\n"
        f"- 목표 인원: {max_attendees}명\n\n"
        "✅ 출석하기\n"
        "- 단체방의 출석 현황 메시지에서 출석 버튼을 누르세요.\n\n"
        "📋 현황 보기\n"
        "- `/status` 활성 세션 명단(버튼 포함)\n"
        "- `/result` 오늘 날짜 기준 출석 현황(진행/종료 여부 포함)\n\n"
        "ℹ️ 안내 메시지\n"
        "- 시간 외: `출석 시간이 아닙니다.`\n"
        "- 중복: `이미 출석 처리되었습니다.`\n"
        "- 완료 후: `출석이 이미 완료되었습니다.`\n"
    )

