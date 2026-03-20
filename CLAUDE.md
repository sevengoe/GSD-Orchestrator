# GSD Orchestrator — 프로젝트 지시

## 프로젝트 개요

Telegram/Slack ↔ Claude Code ↔ GSD 프레임워크 멀티채널 원격 제어 오케스트레이터. 단일 Python 프로세스로 동작. v0.5.0.

## 작업 규칙

- 모든 파일 쓰기는 원자적으로 수행: `.tmp` → `rename`
- 메시지 파일 형식은 단일 파일(request + response), 디렉토리 이동으로 상태 관리
- 채널 발송은 반드시 `messages/outbox/` 경유 (발송 일원화)
- 중복 처리 방지: inbox 파일을 `.processing`으로 rename 후 처리
- 중복 발송 방지: outbox 파일을 `.sending`으로 rename 후 발송
- DB 사용 금지 — 완전 파일 기반 아키텍처
- Claude Code headless 모드 전용 (`claude -p --output-format json --dangerously-skip-permissions`)
- 분류 호출은 `--no-session-persistence`로 세션 오염 방지
- 봇 자신의 메시지는 어댑터 레벨에서 skip (무한 루프 방지)
- 모든 응답에 `[채널][사용자][키워드]` 귀속 헤더 적용
- 상대 채널 브로드캐스트는 snippet_length 이내 요약본 발송

## 구조

```
GSD-Orchestrator/
├── src/gsd_orchestrator/         ← 메인 패키지 (단일 프로세스)
│   ├── __main__.py               ← 진입점 (main 함수, 로그 설정)
│   ├── config.py                 ← config.yaml + .env 로딩 (채널/브로드캐스트 포함)
│   ├── orchestrator.py           ← 메시지 핸들링 + 백그라운드 태스크 오케스트레이션
│   ├── channels/                 ← 채널 어댑터 패키지
│   │   ├── __init__.py           ← ChannelAdapter, ChannelManager export
│   │   ├── base.py               ← ChannelAdapter ABC (분할 발송 캡슐화)
│   │   ├── manager.py            ← ChannelManager (브로드캐스트, targets 빌드)
│   │   ├── telegram.py           ← TelegramAdapter (슬래시 커맨드 포함)
│   │   └── slack.py              ← SlackAdapter (지연 임포트, Socket Mode)
│   ├── inbox_writer.py           ← 메시지 → inbox JSON (원자적 쓰기, source 객체)
│   ├── inbox_processor.py        ← inbox 폴링 → 분류 → Claude/GSD → outbox 조립
│   ├── outbox_sender.py          ← outbox 폴링 → 멀티채널 발송 (retry, snippet)
│   └── archiver.py               ← sent/ → archive/ 이동 + 만료 삭제
├── tests/                        ← 단위 테스트 (pytest, 45개)
├── config.yaml                   ← 일반 설정 (채널, 브로드캐스트, 폴링, Claude, GSD)
├── .env                          ← 민감 정보 (BOT_TOKEN, CHAT_ID, SLACK_TOKEN 등)
├── CHANGELOG.md                  ← 릴리즈 이력
├── setup.sh                      ← 초기 설정 (venv, 의존성, 디렉토리)
├── start.sh / stop.sh / restart.sh ← 실행/중지/재시작
├── logs.sh                       ← 로그 실시간 확인
└── docs/                         ← 문서
    ├── 아키텍처.md                ← 시스템 설계, 멀티채널, 메시지 라이프사이클
    ├── 사용자-가이드.md            ← 설치, 설정, 명령어, 트러블슈팅
    └── GSD-워크플로우-가이드.md    ← GSD 프레임워크 연동 흐름
```

## 설정 파일

- `.env` — 민감 정보만 (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, SLACK_BOT_TOKEN, SLACK_APP_TOKEN)
- `config.yaml` — 채널 설정(telegram/slack), 브로드캐스트(snippet_length), 폴링 간격, 보관 기간, Claude timeout/cooldown, GSD 설정

## 실행

```bash
./setup.sh        # 최초 1회 (Slack: ./setup.sh --slack)
./start.sh        # 시작
./stop.sh         # 중지
./restart.sh      # 재시작
./logs.sh         # 로그 실시간 확인
```
