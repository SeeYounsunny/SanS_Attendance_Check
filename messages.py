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
        "아래 /attend 를 눌러 출석해 주세요!\n\n"
        "/attend"
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
        lines.append("/attend")
    return MessageRender(text="\n".join(lines), is_complete=False)

