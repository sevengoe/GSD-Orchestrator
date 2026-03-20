# Changelog

## [0.5.0] - 2026-03-20

### Added
- **멀티채널 아키텍처**: Telegram + Slack 동시 지원 (각각 선택적, 최소 1개 필수)
- `channels/` 패키지: ChannelAdapter ABC, ChannelManager, TelegramAdapter, SlackAdapter
- `orchestrator.py`: 채널 공통 메시지 핸들링 + 백그라운드 태스크 오케스트레이션
- 크로스채널 브로드캐스트: 수신 확인/응답을 모든 채널에 전달
- 응답 귀속 헤더: `[채널][사용자명][요청키워드]` 포맷으로 다중 사용자 구분
- 브로드캐스트 요약본: 상대 채널에는 `snippet_length`(기본 500자) 이내로 발송
- outbox `targets` 배열: 멀티채널 발송 대상 관리 (`is_origin` 구분)
- outbox `retry_count`: 부분 실패 재시도 횟수 제한 (MAX_OUTBOX_RETRIES=3, 초과 시 error/로 이관)
- 봇 메시지 skip: 어댑터 레벨에서 봇 자신의 메시지 원천 차단
- inbox `source` 객체: 채널/사용자 정보 통합 (channel_type, user_id, user_name, thread_ts)
- Slack Socket Mode 지원 (slack_bolt 지연 임포트, optional dependency)
- `config.yaml` 확장: `channels`, `broadcast` 섹션
- `.env` 확장: SLACK_BOT_TOKEN, SLACK_APP_TOKEN (optional)

### Changed
- `bot.py` → `orchestrator.py` + `channels/telegram.py`로 분리
- `outbox_sender.py`: Bot 직접 사용 → ChannelManager 통한 멀티채널 발송
- `inbox_writer.py`: source dict 지원 (하위 호환: chat_id 문자열도 동작)
- `inbox_processor.py`: broadcast targets 조립, 응답 헤더, ChannelManager DI
- 시스템 알림: `[시스템]` 접두어, 전채널 브로드캐스트
- 메시지 분할(Chunking): OutboxSender → 각 ChannelAdapter 내부로 캡슐화

### Removed
- `bot.py` (orchestrator.py + channels/로 대체)

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
