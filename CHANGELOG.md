# Changelog

## [0.7.0] - 2026-05-04

### Added — App Bridge (외부 비즈니스 앱 통합 인프라)
사용자 → 외부 앱 방향의 슬래시 명령 라우팅 지원. GSD 본체는 명령 의미를 모르고, prefix 매칭·whitelist·correlation·timeout 만 처리. **두 가지 통합 모드 제공: file (독립 프로세스) + api (in-process)**.

- `app_bridge/router.py`: `AppRouter` — 슬래시 명령 prefix 매칭, whitelist 검증, args 길이/위험 문자 차단
- `app_bridge/command_writer.py`: `AppCommandWriter` — file 모드 외부 앱 inbox 원자적 작성 (`.tmp` → rename)
- `app_bridge/correlator.py`: `AppResponseCorrelator` — `command_id` pending 추적, 60초 timeout 알림 백그라운드 태스크, 지연 응답 best-effort 전달 (`[지연 응답]` prefix)
- `api.py::AppBridge`: api 모드 in-process Python 핸들러 등록/디스패치 (`register(name, handler)`, sync/async 모두 지원)
- `outbox_sender.py`: `command_id` 가 있으면 발송 직전 `correlator.resolve()` 호출, 만료 응답에 `[지연 응답]` prefix 자동 부착
- `orchestrator.py`: `_try_app_bridge_route()` 에서 라우팅 분기 (default 모드 + 슬래시 명령만), `app_bridge` 프로퍼티로 호스트 앱이 핸들러 등록 가능
- `channels/telegram.py`: 미등록 슬래시 명령(예: `/sell`)을 `_on_message` 로 위임. `filters.COMMAND` 에서 GSD 메타 명령(`/help`, `/gsd`, `/status`, `/reset`, `/resume`, `/retry`)은 정규식으로 제외하여 중복 처리 방지
- `channels/slack.py`: **변경 0줄** — Slack Socket Mode `event("message")` 가 슬래시 텍스트(`/echo hi`)를 일반 메시지로 전달하므로 기존 `_handle_message` → `_on_message_callback` 경로로 자동 라우팅됨. AppRouter 가 channel_type 무관하게 매칭하여 통합 동작. ⚠️ Slack workspace 가 동일 prefix 를 native slash command 로 등록 시 webhook 으로 분기되어 GSD 미도달 — 정의서 §12.A 참고. 운영상 Slack 사용 시 prefix 충돌 회피 책임은 운영자에게 있음 (장기 검증 항목)
- `config.yaml` `app_bridge` 섹션: `enabled`, `external_inbox_base`, `response_timeout_sec`, `ack_timeout_sec`, `max_args_length`, `apps[*]` (name, mode, inbox_dir, command_prefix, whitelist_user_ids, ack_message)
- `config.py::_normalize_app_bridge_apps`: 시작 시 prefix 충돌·잘못된 mode·`/` 누락 검증 (`ValueError` 로 startup fail)
- `tests/test_app_bridge.py`: 31개 단위 테스트 (config 정규화, 라우팅, command_writer, correlator, echo round-trip, api 모드 sync/async, 핸들러 예외 처리, OutboxSender 통합)
- `tests/fixtures/echo_app.py`: file 모드 reference 외부 앱 (다른 비즈니스 앱이 따라할 폴링 패턴 예제)
- `docs/외부-앱-통합-가이드.md`: 신규 — file/api 두 모드 통합 튜토리얼, 코드 예시
- `docs/App-Bridge-연동-정의서.md`: 신규 — 공식 사양 (Spec v1.0). 다른 언어/팀이 구현할 수 있도록 RFC 2119 키워드(MUST/SHOULD/MAY)로 필드 단위 명세, 상태 전이, JSON Schema (Draft 2020-12), test vectors, conformance checklist
- `docs/아키텍처.md` / `docs/사용자-가이드.md`: App Bridge 섹션 추가

### Design notes

- GSD 가 v0.8 → v1.x 업그레이드 시 비즈니스 명령 코드 충돌 방지 — 외부 앱은 GSD 코드 수정 없이 config 한 줄로 등록
- 두 모드 동시 사용 가능 (앱 A는 file, 앱 B는 api)
- 응답 timeout 후 도착해도 best-effort 로 사용자에게 전달 (24시간 보관)
- 외부 앱 healthcheck 는 외부 앱 책임 (GSD 는 모니터링 안 함)
- MCP 서버와는 별개 메커니즘 (App Bridge 는 채널 슬래시 명령, MCP 는 Claude tool 호출)

---

## [0.6.1] - 2026-03-29

### Fixed
- **GSD 세션 활성 중 새 요청이 `/gsd:next`로 강제 변환되는 문제**: "문서로 만들어줘" 같은 독립 요청이 gsd-resume으로 잘못 분류되어 "GSD 미초기화" 응답만 반복하던 버그 수정. 의도 기반 분류(`_is_gsd_continuation`) 도입 — 계속 패턴("진행해주세요", "네", "승인" 등)만 gsd-resume, 나머지는 default로 처리
- **GSD 세션 타임아웃 후 컨텍스트 소실**: "진행해주세요"가 30분 타임아웃 이후 도착하면 맥락 없이 처리되던 문제 수정. 계획서(`.gsd-plan.md`)가 존재하면 세션 만료와 무관하게 gsd-resume으로 연결
- **gsd-resume 프롬프트에서 `/gsd:next` 강제 실행 제거**: `.planning/` 디렉토리가 없는 프로젝트에서 "GSD 프로젝트 미초기화" 반복 응답 유발하던 문제 수정. 유연한 프롬프트로 변경하여 Claude가 맥락에 맞게 판단
- **네트워크 지연으로 인한 중복 메시지 처리**: 같은 `message_id`가 2회 수신되면 2회 처리되어 시간 낭비(최대 99분)하던 문제 수정. `message_id` 기반 in-memory dedup 추가
- **새 세션 첫 메시지에서 이전 대화 맥락 미참조**: `turn_count=0`(첫 메시지)일 때도 sent/archive에서 이전 대화 이력을 주입하도록 개선

### Added
- `conversation_id` 필드: 메시지 JSON에 대화 스레드 추적 ID 추가. 계속 패턴 시 이전 대화의 ID를 계승, 새 요청 시 신규 생성. `_build_context_from_history()`에서 같은 대화 메시지를 우선 수집
- `has_pending_plan()`: 승인 대기 중인 계획서 존재 여부를 Orchestrator에서 확인 가능
- `_is_gsd_continuation()`: 경량 패턴 매칭으로 GSD 계속 의도 판별 (Claude 호출 없음, 지연 0ms)
- `_is_duplicate_message()`: `message_id` 기반 중복 수신 감지 (최대 100건 유지)
- `_resolve_conversation_id()` / `_find_recent_conversation_id()`: sent/archive에서 대화 스레드 계승 로직
- `test_context_continuity.py`: 26개 단위 테스트 (의도 분류, 타임아웃 방어, 컨텍스트 주입, 중복 방지, conversation_id)

### Changed
- `config.yaml`: `gsd.history_max_messages` 3 → 5 (맥락 복원 범위 확대)
- `_build_context_from_history()`: `conversation_id` 파라미터 추가, 같은 대화 우선 필터링 + channel_id fallback
- `_process_simple()`: 첫 메시지(`turn_count=0`)에도 아카이브 컨텍스트 주입
- `_process_gsd()` gsd-resume: `/gsd:next` 강제 대신 "이전 작업을 이어서 처리해주세요" 유연 프롬프트

---

## [0.6.0] - 2026-03-24

### Added
- **첨부파일 수신 및 텍스트 추출**: 텔레그램/슬랙에서 txt, md, pdf 파일 첨부 시 자동 텍스트 추출
- `attachment_handler.py`: 화이트리스트 검증, 파일 다운로드, 텍스트 추출(pdfminer.six), 메타데이터 생성
- `text_cleaner.py`: 추출 텍스트 정제 (공백/헤더/푸터/페이지번호 제거, PDF 특화 정제)
- `config.yaml` `attachments` 섹션: 허용 확장자, 최대 파일 크기(1MB), 임시 경로, 거부 메시지 템플릿
- 텔레그램 어댑터: Document 핸들러(`_on_document`) 추가
- 슬랙 어댑터: `file_shared` 이벤트 핸들러 추가
- `inbox_writer`: `attachments[]` 메타데이터 + `extracted_text` 필드 지원
- `inbox_processor`: extracted_text가 있으면 Claude 프롬프트에 `[첨부파일: {파일명}]` 형태로 삽입
- PDF 실패 유형별 안내: 암호화 PDF, 이미지 전용 PDF, 구문 오류, pdfminer 미설치
- `pdfminer.six>=20231228` 의존성 추가
- `test_attachment_handler.py`: 28개 단위 테스트 (화이트리스트, PDF 추출 성공/실패, 메타데이터, 파일 정리)
- `test_text_cleaner.py`: 18개 단위 테스트 (공통 정제, PDF 특화 정제, 파일 타입 격리 검증)

---

## [0.5.0] - 2026-03-21

### Added
- **멀티채널 아키텍처**: Telegram + Slack 동시 지원 (각각 선택적, 최소 1개 필수)
- `channels/` 패키지: ChannelAdapter ABC, ChannelManager, TelegramAdapter, SlackAdapter
- `orchestrator.py`: 채널 공통 메시지 핸들링 + 백그라운드 태스크 오케스트레이션
- `api.py`: 외부 연동 인터페이스 (ChannelSender, on_result 콜백)
- 크로스채널 브로드캐스트: 수신 확인/응답을 모든 채널에 전달
- 응답 귀속 헤더: `[채널][사용자명][요청키워드]` 포맷으로 다중 사용자 구분
- 브로드캐스트 요약본: 상대 채널에는 `snippet_length`(기본 500자) 이내로 발송
- outbox `targets` 배열: 멀티채널 발송 대상 관리 (`is_origin` 구분)
- outbox `retry_count`: 부분 실패 재시도 횟수 제한 (MAX_OUTBOX_RETRIES=3, 초과 시 error/로 이관)
- 봇 메시지 skip: 어댑터 레벨에서 봇 자신의 메시지 원천 차단
- inbox `source` 객체: 채널/사용자 정보 통합 (channel_type, user_id, user_name, thread_ts)
- Slack Socket Mode 지원 (slack_bolt 지연 임포트, optional dependency)
- `.env` 확장: SLACK_BOT_TOKEN, SLACK_APP_TOKEN, SLACK_CHANNEL_ID (optional)
- **외부 연동 인터페이스**: `ChannelSender` (send/enqueue) + `on_result` 콜백 (success/error/blocked)
- 비동기 진입점: `orchestrator.start()` / `stop()` (호스트 앱의 이벤트 루프에 추가 가능)
- `/help`, `/start` 커맨드 추가
- `.processing` 잔류 파일 자동 복원 (failcount 기반, 3회 초과 시 error/로 격리 + 채널 알림)
- `.sending` 잔류 파일 자동 복원 (시작 시)
- progress 알림 정리: Claude 처리 완료 시 outbox에 남아있는 "처리 중..." 알림 파일 자동 삭제
- GSD 실행 룰: 계획 수립 → 사용자 확인 → 분류 단위별 분석/설계/구현/단위테스트
- 지능형 라우터 강화: 여러 파일 수정, 단계가 많은 작업, 5분 이상 소요 예상 → GSD Track
- `setup.sh`: 초기 설정 스크립트 (venv, 의존성, .env, 디렉토리 생성, --slack 옵션)
- `restart.sh`: 재시작 스크립트
- `logs.sh`: 로그 실시간 확인 스크립트

### Changed
- `bot.py` → `orchestrator.py` + `channels/telegram.py`로 분리
- `outbox_sender.py`: Bot 직접 사용 → ChannelManager 통한 멀티채널 발송
- `inbox_writer.py`: source dict 지원 (하위 호환: chat_id 문자열도 동작)
- `inbox_processor.py`: broadcast targets 조립, 응답 헤더, ChannelManager DI, result_callback
- `inbox_processor._run_claude()`: `subprocess.run` + `to_thread` → `asyncio.create_subprocess_exec` (네이티브 비동기)
- 시스템 알림: `[시스템]` 접두어, 전채널 브로드캐스트
- 메시지 분할(Chunking): OutboxSender → 각 ChannelAdapter 내부로 캡슐화
- 백그라운드 태스크를 채널 어댑터보다 먼저 시작 (Telegram long-polling 블로킹 방지)
- 키워드 추출 길이: 10자 → 20자
- `start.sh`: 설치 로직을 `setup.sh`로 분리, PYTHONPATH 설정 추가
- `stop.sh`: 프로세스 트리 종료 (자식 프로세스인 claude 등도 함께 종료)
- `config.yaml`: `channels`, `broadcast` 섹션 추가, `claude.timeout` 300→600
- `.gitignore`: messages/, logs/, workspace/ 전체 무시 (.gitkeep 불필요)

### Removed
- `bot.py` (orchestrator.py + channels/로 대체)
- `mcp_server/` (불필요 — Claude Code 기본 도구와 중복)

### Backward Compatibility
- `source` 없는 기존 inbox 메시지: `chat_id` 필드로 telegram source 자동 생성
- `targets` 없는 기존 outbox 메시지: source 기반 단일 타겟 자동 생성
- `channels` 섹션 없는 config.yaml: `.env`의 TELEGRAM_CHAT_ID로 단일 채널 동작

---

## [0.1.0] - 2026-03-19

### Added
- 초기 릴리즈: Telegram 단일 채널 오케스트레이터
- Two-Track 라우팅: Simple Track (claude -p) + GSD Track (/gsd:do)
- 경량 모델 자동 분류 (Haiku 기반 지능형 라우터)
- 파일 기반 메시지 큐: inbox → outbox → sent → archive
- 원자적 파일 쓰기 (.tmp → rename)
- 중복 처리/발송 방지 (.processing, .sending rename lock)
- 토큰 사용량 누적 추적 + /status 명령
- 실패 감지 + 자동 쿨다운 + /resume 즉시 해제
- GSD 블로킹 → 사용자 응답 → 자동 재개
- 세션 관리: --continue, /reset, 자동 리셋 (max_session_turns)
- 슬래시 커맨드: /gsd, /status, /reset, /resume
- 매시간 아카이빙 + 만료 자동 삭제
