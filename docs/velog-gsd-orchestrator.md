# GSD-Orchestrator — 메신저로 개발을 지시하고, 일상으로 돌아간다
### Telegram/Slack에서 Claude Code를 원격 제어하는 비동기 개발 자동화

---

## 이런 경험, 없으신가요?

Claude Code로 복잡한 작업을 시키면 10분, 길면 30분을 기다려야 한다. 그 시간 동안 터미널 앞에 앉아서 진행 상황을 지켜보고 있다. 작업이 끝나면 결과를 확인하고, 다음 지시를 내리고, 또 기다린다.

"이걸 메신저로 시키고 나는 다른 일 하면 안 되나?"

GSD-Orchestrator는 그 질문에서 시작했다.

---

## 무엇을 하는 물건인가

**Telegram이나 Slack에서 메시지를 보내면, Claude Code가 백그라운드에서 작업을 수행하고, 결과를 다시 메신저로 보내준다.**

```
나 (Telegram) → "사용자 인증 API 구현해줘"
                    ↓
            GSD-Orchestrator (백그라운드)
                    ↓
            Claude Code 실행 → 코드 작성 → 테스트
                    ↓
나 (Telegram) ← "구현 완료. 변경 파일 3개, 테스트 통과."
```

메시지를 보내고 일상으로 돌아가면 된다. Fire & Forget.

---

## 왜 만들었나

AI-Native Architecting — 한 줄의 코드도 직접 수정하지 않는 방식으로 개발하고 있다. 모든 구현은 AI에게 맡기고, 나는 설계와 품질 관리에 집중한다.

이 방식에서 가장 큰 병목은 **대기 시간**이었다. Claude Code 앞에 앉아 있는 시간이 너무 길었다. 설계와 지시는 5분이면 끝나는데, 실행과 결과 확인을 위해 30분을 기다리는 건 비효율적이었다.

메신저로 지시하고 결과만 받으면, 그 30분 동안 다른 설계를 하거나, 다른 일을 할 수 있다. GSD-Orchestrator는 이 병목을 해결하기 위해 만들었다.

---

## 핵심 특징

### 1. Two-Track 자동 라우팅

메시지를 보내면 경량 모델(Haiku)이 자동으로 분류한다.

- **Simple Track** — 단순 질문, 즉답 ("Python에서 리스트 정렬 어떻게 해?")
- **GSD Track** — 복잡한 개발 작업 (기획 → WBS → 병렬 구현 → 검증)

토큰을 아끼면서도 복잡한 작업은 제대로 처리한다.

### 2. 파일 기반 상태 관리 (DB 없음)

```
inbox/ → processing → outbox/ → sending → sent/ → archive/
```

모든 상태가 파일 시스템 위에서 관리된다. DB가 없으니 설치도 간단하고, 장애 복구도 단순하다. 시스템이 중간에 죽어도 미완료 파일을 찾아서 자동으로 재개한다.

### 3. 멀티채널 동시 지원

Telegram과 Slack을 동시에 연결할 수 있다. Telegram에서 보낸 작업 결과를 Slack에도 요약본으로 브로드캐스트할 수 있다. 채널은 어댑터 패턴으로 설계되어 있어서 확장이 쉽다.

### 4. 첨부파일 처리

PDF, 텍스트, 마크다운 파일을 메신저에서 보내면 자동으로 텍스트를 추출하고 정제해서 Claude에게 전달한다. 실제 파일은 처리 후 즉시 삭제하고 메타데이터만 보관한다.

### 5. MCP 파일시스템 서버

Model Context Protocol 표준으로 12개 파일시스템 도구를 제공한다. 경로 검증, 심링크 차단 등 보안 검증이 내장되어 있다.

---

## 아키텍처

```
┌─────────────┐     ┌─────────────┐
│  Telegram   │     │    Slack    │
│  Adapter    │     │   Adapter   │
└──────┬──────┘     └──────┬──────┘
       │                   │
       └─────────┬─────────┘
                 │
          ┌──────▼──────┐
          │   Inbox     │  ← 원자적 쓰기 (.tmp → rename)
          │   Writer    │
          └──────┬──────┘
                 │
          ┌──────▼──────┐
          │   Inbox     │  ← Haiku 자동 분류
          │  Processor  │     → Simple Track / GSD Track
          └──────┬──────┘
                 │
          ┌──────▼──────┐
          │   Outbox    │  ← 멀티채널 발송 + 브로드캐스트
          │   Sender    │
          └──────┬──────┘
                 │
          ┌──────▼──────┐
          │  Archiver   │  ← 자동 보관 + 만료 삭제
          └─────────────┘
```

---

## 빠르게 시작하기

### 필요한 것
- Python 3.11+
- Claude Code CLI (Pro/Max 구독)
- Telegram Bot Token + Chat ID

### 설치 및 실행

```bash
git clone https://github.com/sevengoe/GSD-Orchestrator.git
cd GSD-Orchestrator

# 초기 설정 (venv, 의존성, 디렉토리 생성)
./setup.sh

# .env에 봇 토큰과 Chat ID 설정
vi .env

# 시작
./start.sh
```

Telegram에서 봇에게 메시지를 보내면 된다.

### Slack도 쓰고 싶다면

```bash
./setup.sh --slack
```

---

## 설정 예시

```yaml
channels:
  telegram:
    enabled: true
  slack:
    enabled: false

broadcast:
  snippet_length: 500      # 상대 채널 요약 길이

claude:
  timeout: 600              # 10분
  cooldown_retry_minutes: 10

gsd:
  enabled: true
  auto_classify: true       # 자동 분류 활성화
```

민감 정보(토큰, Chat ID)는 `.env` 파일에 분리되어 있다.

---

## 슬래시 커맨드

| 명령 | 동작 |
|------|------|
| 일반 메시지 | 자동 분류 → Simple 또는 GSD Track |
| `/gsd <작업>` | GSD Track 강제 라우팅 |
| `/status` | 쿨다운 상태, 작업 현황 확인 |
| `/reset` | 새 세션 시작 |
| `/resume` | 쿨다운 즉시 해제 |

---

## 숫자로 보는 프로젝트

- **v0.6.0** — 3주간 3번의 메이저 릴리즈
- **171개** 단위 테스트
- **12개** MCP 파일시스템 도구
- **27개** 장애 시나리오 복구 가이드
- **0개** 외부 데이터베이스 의존성

---

## 이 프로젝트 자체가 AI-Native Architecting의 산물이다

GSD-Orchestrator의 모든 코드는 AI가 작성했다. 나는 한 줄도 직접 타이핑하지 않았다. 설계하고, 지시하고, 검증하고, 피드백을 줬다.

CLAUDE.md에 작업 규칙을 정의하고, 설계서를 먼저 작성하고, AI에게 구현을 맡기고, 슬롭을 잡아내는 — 그 과정의 결과물이 이 레포지토리다.

AI-Native Architecting에 대해 더 알고 싶다면: [AI-Native Architecting — 한 줄의 코드도 직접 수정하지 않는 아키텍트의 개발 방법론](./ai-native-architecting.md)

---

## 링크

- **GitHub**: [https://github.com/sevengoe/GSD-Orchestrator](https://github.com/sevengoe/GSD-Orchestrator)
- **아키텍처 문서**: [docs/아키텍처.md](https://github.com/sevengoe/GSD-Orchestrator/blob/main/docs/아키텍처.md)
- **사용자 가이드**: [docs/사용자-가이드.md](https://github.com/sevengoe/GSD-Orchestrator/blob/main/docs/사용자-가이드.md)

---

*이 글은 AI(Claude)와의 협업으로 작성되었습니다.*
