# SanS Attendance Check Bot (Telegram)

PRD 기반 텔레그램 **출석체크 자동화 봇**입니다. 매주 일요일 정해진 시간에 출석 세션을 자동으로 열고, **출석** 인라인 버튼으로 출석을 기록하며, 출석 현황 메시지를 **edit(수정)** 으로 계속 갱신합니다.

## 기능

- **세션 자동 시작/종료**
  - 일요일 **20:30** 세션 오픈
  - 일요일 **23:00** 세션 종료 (버튼 제거 + 종료 안내)
- **출석 처리**
  - 출석 현황 메시지의 **출석** 버튼 클릭 (`/attend` 명령은 제공하지 않음)
  - 시간 외 요청 차단: “출석 시간이 아닙니다.”
  - 중복 출석 방지: “이미 출석 처리되었습니다.”
  - **최대 인원(기본 24명)** 달성 시 `✅ 완료` 전환
- **안정성**
  - SQLite 영속 저장으로 재시작 시 데이터 유실 방지
  - 동시 요청 대비: DB 트랜잭션 + 앱 레벨 락
  - edit 실패 시 **retry 3회 후 새 메시지 전송 fallback**
- **접근 제한**
  - **1:1 대화(private)에서는 사용 불가** — 출석·모든 명령은 `GROUP_CHAT_ID`로 지정한 **단체방(그룹/슈퍼그룹)에서만** 동작합니다. 다른 단체방에서도 동일하게 차단됩니다.

## 요구사항

- Python **3.10+**
- 텔레그램 봇 토큰 (`BOT_TOKEN`)
- 출석을 운영할 단체방 Chat ID (`GROUP_CHAT_ID`, 보통 `-100...` 형태). 사용자는 이 방에 초대되어 있어야 하며, 봇 명령은 이 방에서만 처리됩니다.

## 설치

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 설정

이 프로젝트는 **환경변수**로 설정을 주입하는 방식을 기본으로 사용합니다.

### 필수 환경변수

```bash
export BOT_TOKEN="123:ABC"
export GROUP_CHAT_ID="-1001234567890"
```

### 선택 환경변수

```bash
export TIMEZONE="Asia/Seoul"
export MAX_ATTENDEES="24"

# 0=월요일 ... 6=일요일
export SESSION_DAY="6"

# 출석 오픈(허용) 시작 시각 (PRD: 20:30)
export SESSION_OPEN_HOUR="20"
export SESSION_OPEN_MINUTE="30"

# 세션 종료 시각 (PRD: 23:00)
export SESSION_END_HOUR="23"
export SESSION_END_MINUTE="0"

# 안내 문구용 “공식 세션 시간” (PRD: 21~23)
export SESSION_START_HOUR="21"

# DB 경로
export DB_PATH="data/attendance.db"

# 데이터 초기화(/resetdata) 비밀번호. 설정하지 않으면 /resetdata 명령 비활성화.
# 비밀번호는 환경변수로만 관리되며, 코드·로그·응답에 노출되지 않습니다.
export RESET_PASSWORD="your-secret-reset-password"
```

> 참고: 출석 인정 시간은 **`SESSION_OPEN_*` ~ `SESSION_END_*`** 입니다.

## 실행 (Polling)

```bash
python bot.py
```

## 명령어

아래 명령은 **지정된 단체방에서만** 동작합니다. 봇과의 1:1 채팅에서는 사용할 수 없습니다.

- **출석**: 명단 메시지의 **출석** 버튼. 성공 시 채팅에는 남지 않고 명단만 갱신되며, 본인 화면 상단에 잠깐 “출석되었습니다.” 알림이 뜹니다. (실패 시에만 안내)
- `/status`: 현재 **활성** 세션 명단을 다시 보여줌 (출석 버튼 포함)
- `/result`: **오늘**(설정 타임존 기준) 세션의 출석 현황. 세션이 없으면 안내, 있으면 명단·상태(진행 중/완료/종료)
- `/guide`: 출석체크 사용법/시간/안내 문구 보기
- `/stats`: 출석 통계 (최근 4회, 최근 12개월 월별 합계, 월/연 평균)
- `/history week`: 지난주(직전 세션) 출석 현황
- `/history month`: 지난달(이전 달) 출석 현황
- `/top10` / `/top10 month`: 출석 TOP10 (최근 1년 / 최근 30일)
- `/resetdata <비밀번호>`: 모든 출석·세션 데이터 삭제 (배포 전 테스트용). `RESET_PASSWORD` 환경변수를 설정한 경우에만 사용 가능하며, 비밀번호는 환경변수로만 관리되고 외부에 노출되지 않습니다.

## 메시지 동작

- 세션 오픈 시:
  - 오픈 안내 메시지 1개 전송
  - 출석 현황 메시지 1개 전송(인라인 `출석` 버튼 포함)
- 출석이 들어올 때마다:
  - 출석 현황 메시지를 **edit** 해서 최신 상태 유지
- 세션 종료 시:
  - 마지막 출석 현황 메시지에서 **버튼 제거**
  - “출석체크 종료” 안내 메시지 전송

## 데이터 저장 (SQLite)

DB 파일은 기본적으로 `data/attendance.db`에 생성됩니다.

- `sessions`
  - `week_date`: 세션 날짜(YYYY-MM-DD)
  - `status`: `active` / `completed` / `ended`
  - `message_id`: 출석 현황 메시지 id
- `attendances`
  - `(session_id, user_id)` 유니크로 중복 출석 방지
  - `attend_order`로 순서 보장

## 운영 팁

- 출석(버튼)·명령(`/status` 등)은 **1:1 대화가 아니라 `GROUP_CHAT_ID`로 설정한 단체방에서만** 동작합니다.
- 봇을 단체방에 초대 후 **메시지 전송/편집 권한**이 있는지 확인하세요.
- 서버 배포 시 프로세스가 항상 살아있어야 **일요일 지정 시각**에 세션이 자동으로 열리고 닫힙니다. (`/open`·`/close` 수동 명령은 없음.)
- 배포 전 다른 단체방에서 테스트한 뒤, 실제 배포 전에 `/resetdata <비밀번호>`로 데이터를 초기화할 수 있습니다. 비밀번호는 Railway 등에서 `RESET_PASSWORD` 환경변수로만 설정하고, 코드·로그에는 절대 노출되지 않도록 합니다.

## Railway 배포 메모 (SQLite + Volume)

SQLite를 영속적으로 쓰려면 Railway에서 **Volume을 서비스에 Attach** 하고 `DB_PATH`를 mount 경로로 설정하세요.

- **Mount path 예시**: `/data`
- **Variables 예시**: `DB_PATH=/data/attendance.db`
