# App Bridge 연동 정의서 (Integration Specification)

| 항목 | 값 |
|---|---|
| Spec 버전 | **1.0** |
| GSD Orchestrator 호환 | v0.7.0 ~ |
| 작성일 | 2026-05-04 |
| 상태 | Stable |
| 문서 유형 | Formal Specification (구현 계약) |

> 이 문서는 GSD Orchestrator App Bridge 와 통합하려는 외부 앱이 **구현해야 할 계약**을 정의한다. 튜토리얼/예시는 [`외부-앱-통합-가이드.md`](./외부-앱-통합-가이드.md) 참조.
>
> **MUST / SHOULD / MAY** 는 RFC 2119 의미를 따른다.

## 목차

1. [범위](#1-범위)
2. [용어](#2-용어)
3. [통합 모드](#3-통합-모드)
4. [파일 시스템 계약 (FILE 모드)](#4-파일-시스템-계약-file-모드)
5. [원자적 쓰기 프로토콜](#5-원자적-쓰기-프로토콜)
6. [메시지 스키마](#6-메시지-스키마)
7. [Correlation 프로토콜](#7-correlation-프로토콜)
8. [Lifecycle / 상태 전이](#8-lifecycle--상태-전이)
9. [Timeout 의미론](#9-timeout-의미론)
10. [에러 / 거부 케이스](#10-에러--거부-케이스)
11. [API 모드 시그니처 계약](#11-api-모드-시그니처-계약)
12. [등록 (config.yaml)](#12-등록-configyaml)
13. [예약어 / 충돌](#13-예약어--충돌)
14. [버저닝 / 호환성](#14-버저닝--호환성)
15. [Test Vectors](#15-test-vectors)
16. [Conformance Checklist](#16-conformance-checklist)
17. [Appendix A: JSON Schema (Draft 2020-12)](#appendix-a-json-schema-draft-2020-12)

---

## 1. 범위

본 정의서는 다음을 명세한다:
- 사용자 채널(텔레그램/슬랙) → GSD → 외부 앱 → GSD → 사용자 채널 의 단방향 명령 흐름
- 외부 앱이 받는 입력(inbound)과 외부 앱이 작성하는 출력(outbound)의 **파일 형식 / 필드 / 타이밍 계약**
- file 모드(독립 프로세스, 언어 무관) 와 api 모드(in-process Python 임베드)

본 정의서가 다루지 **않는** 것:
- 외부 앱 내부 비즈니스 로직 (자유 구현)
- 외부 앱의 healthcheck/모니터링 (외부 앱 책임)
- GSD 의 내부 구현 (라우팅 알고리즘, 채널 어댑터)

## 2. 용어

| 용어 | 정의 |
|---|---|
| **GSD** | GSD Orchestrator 본체 프로세스 |
| **외부 앱** (External App) | 비즈니스 로직을 처리하는 별도 컴포넌트 |
| **명령** (Command) | 사용자가 채널에 입력한 슬래시 prefix 로 시작하는 텍스트 |
| **prefix** | 명령의 첫 토큰 (`/` 로 시작). 라우팅 키 |
| **command_id** | UUID v4. GSD 가 생성. 명령-응답 correlation 키 |
| **inbound 메시지** | GSD → 외부 앱 (file 모드: external_inbox/{app}/, api 모드: handler 호출) |
| **outbound 메시지** | 외부 앱 → GSD (outbox/) |
| **origin 채널** | 사용자가 명령을 보낸 채널 (응답이 우선 전달되는 채널) |
| **ack** | GSD 가 명령 수신 즉시 사용자에게 발송하는 접수 확인 |
| **whitelist** | 앱별 허용 user_id 목록. 빈 리스트는 전체 허용 |

## 3. 통합 모드

외부 앱은 **FILE 모드** 또는 **API 모드** 중 하나로 등록된다. 두 모드는 동시 사용 가능 (앱 A는 FILE, 앱 B는 API).

| | FILE 모드 | API 모드 |
|---|---|---|
| 프로세스 | 별도 프로세스 (언어 무관) | GSD 와 동일 Python 프로세스 |
| inbound | 파일 폴링 | Python 함수 호출 |
| outbound | 파일 작성 | 함수 반환값 |
| 권장 사용 | 독립 비즈니스 앱 (MMM 등) | GSD 임베드 호스트 앱 |

API 모드 외부 앱은 `gsd_orchestrator` 패키지를 import 할 수 있어야 하며, [§11](#11-api-모드-시그니처-계약) 의 시그니처를 따라야 한다.

## 4. 파일 시스템 계약 (FILE 모드)

### 4.1 디렉토리 레이아웃

GSD `base_dir` 기준 (`config.yaml` 와 같은 경로):

```
{base_dir}/
├── messages/
│   ├── external_inbox/
│   │   └── {app_name}/                ← 외부 앱이 폴링하는 inbox
│   │       ├── 20260504_083000_a1b2c3d4.json
│   │       └── ...
│   └── outbox/                          ← 외부 앱이 응답을 작성하는 위치
│       └── 20260504_083001_e5f6g7h8_{app_name}.json
```

- `external_inbox/{app_name}/` 의 절대 경로는 GSD config (`app_bridge.apps[*].inbox_dir`) 가 결정. **외부 앱은 inbound 메시지의 `reply_to_outbox` 필드에서 outbox 절대 경로를 받아야 한다** (하드코딩 금지).
- 외부 앱은 inbox 디렉토리가 존재하지 않을 수 있으므로 시작 시 `mkdir -p` 해야 한다 (MUST).

### 4.2 파일명 규칙

- inbound 파일명: `{YYYYMMDD}_{HHMMSS}_{8자hex}.json` (외부 앱은 파싱하지 않아도 됨; `*.json` 글로빙으로 충분)
- outbound 파일명: 외부 앱이 자유 결정. **MUST** `.json` 확장자. **SHOULD** 시간 정렬 가능한 prefix (`{YYYYMMDD}_{HHMMSS}_*.json`) 사용. 파일명에 충돌 방지용 random suffix 포함 권장.

### 4.3 폴링 규칙

외부 앱은 다음 의무를 지닌다:

- **MUST** `external_inbox/{app}/*.json` 만 처리. dot-prefix 파일(`.{name}.tmp`) 은 GSD 가 작성 중인 임시 파일이므로 무시.
- **MUST** 처리 시작 전 파일 lock (예: 파일을 다른 이름으로 rename 해서 다른 워커와 경쟁 방지) 또는 단일 워커 정책.
- **MUST** 처리 완료 후 inbound 파일을 삭제 또는 별도 archive 디렉토리로 이동.
- **SHOULD** 폴링 주기 100ms ~ 1s.
- **MUST NOT** GSD 의 다른 디렉토리(`inbox/`, `sent/`, `error/`, `archive/`)를 읽거나 쓰지 않는다.

## 5. 원자적 쓰기 프로토콜

inbound 와 outbound 양방향 모두 **원자적 쓰기 필수** (MUST).

```
1. 같은 디렉토리에 임시 파일을 작성:
   {dir}/.{filename}.tmp
2. fsync() (권장, 강제 아님)
3. rename:
   {dir}/.{filename}.tmp  →  {dir}/{filename}
```

이유: rename 은 같은 파일시스템 내에서 POSIX 원자적 연산. 폴러는 절대 부분 작성된 파일을 보지 않는다.

**금지 사항** (MUST NOT):
- `open(target, 'w')` 로 직접 쓰기 (폴러가 빈 JSON 또는 부분 JSON 을 읽을 수 있음)
- `.tmp` 파일을 다른 디렉토리에 작성한 후 rename (cross-device rename 은 비원자적)

## 6. 메시지 스키마

모든 메시지는 **UTF-8 JSON** (BOM 없음). 들여쓰기는 권장 사항.

### 6.1 Inbound (GSD → 외부 앱)

`external_inbox/{app_name}/*.json`:

```json
{
  "id": "9f3a7e2b-...-uuid-v4",
  "command_id": "11111111-2222-3333-4444-555555555555",
  "timestamp": "2026-05-04T08:30:00+09:00",
  "source": {
    "channel_type": "telegram",
    "channel_id": "123456",
    "user_id": "12345678",
    "user_name": "kbj"
  },
  "command": {
    "raw": "/sell d2 005490 100",
    "prefix": "/sell",
    "args": ["d2", "005490", "100"]
  },
  "target_app": "mmm",
  "reply_to_outbox": "/abs/path/to/messages/outbox"
}
```

| 필드 | 타입 | 필수 | 의미 |
|---|---|---|---|
| `id` | string (UUID v4) | MUST | 메시지 자체 ID. 외부 앱이 무시해도 무방 |
| `command_id` | string (UUID v4) | MUST | **outbound 응답에 그대로 echo 해야 하는 correlation 키** |
| `timestamp` | string (ISO 8601 with offset) | MUST | GSD 가 명령을 라우팅한 시각 (KST `+09:00`) |
| `source.channel_type` | string | MUST | `"telegram"` \| `"slack"` |
| `source.channel_id` | string | MUST | 채널 ID. outbound `targets[].channel_id` 에 그대로 echo |
| `source.user_id` | string | MUST | 사용자 ID (문자열, 채널마다 형식 상이) |
| `source.user_name` | string | MUST | 표시 이름 (로깅/디버깅용, 비어있을 수 있음) |
| `command.raw` | string | MUST | 사용자가 입력한 원문 (앞뒤 공백 trim 됨) |
| `command.prefix` | string | MUST | 첫 토큰. `/` 로 시작 |
| `command.args` | array of string | MUST | prefix 이후 공백 분리 토큰 (빈 배열 가능) |
| `target_app` | string | MUST | 등록된 앱 이름 |
| `reply_to_outbox` | string | MUST | outbox 디렉토리의 절대 경로 |

**검증된 사실** (외부 앱은 신뢰해도 됨):
- `args` 의 합산 길이는 `max_args_length` (config) 이내 — GSD 에서 미리 거부됨
- `args` 에 shell metachar (`;`, `|`, `` ` ``, `$()`) 없음 — 라우터 단계 차단
- `source.user_id` 는 whitelist 통과한 값 — 권한 재검증 불필요

**외부 앱이 추가 검증해야 할 것** (MUST):
- `args` 의 의미적 유효성 (예: `/sell` 의 첫 인자가 거래일 코드인지)
- 비즈니스 로직 권한 (예: 거래 가능 시간대인지)
- 실제 자원 접근 권한

### 6.2 Outbound (외부 앱 → GSD)

`messages/outbox/*.json`:

```json
{
  "id": "outbound-uuid-v4",
  "command_id": "11111111-2222-3333-4444-555555555555",
  "source": {
    "channel_type": "telegram",
    "channel_id": "123456",
    "user_id": "12345678",
    "user_name": "kbj"
  },
  "targets": [
    {
      "channel_type": "telegram",
      "channel_id": "123456",
      "is_origin": true
    }
  ],
  "retry_count": 0,
  "keyword": "mmm_reply",
  "request": null,
  "response": {
    "text": "매도 요청 등록됨, ID=42, status=pending",
    "parse_mode": null,
    "timestamp": "2026-05-04T08:30:01+09:00"
  }
}
```

| 필드 | 타입 | 필수 | 의미 |
|---|---|---|---|
| `id` | string (UUID v4) | MUST | 응답 자체 ID. 새로 생성 |
| `command_id` | string (UUID v4) | MUST | **inbound `command_id` 를 그대로 echo** |
| `source` | object | SHOULD | inbound `source` 그대로 echo (correlator timeout 시 통보 대상) |
| `targets` | array of TargetSpec | MUST | 발송 대상 (1개 이상) |
| `targets[].channel_type` | string | MUST | inbound `source.channel_type` 와 동일 권장 |
| `targets[].channel_id` | string | MUST | inbound `source.channel_id` 와 동일 권장 |
| `targets[].is_origin` | boolean | MUST | `true` 권장 (origin 채널 = 사용자 입력 채널) |
| `retry_count` | integer | MUST | `0` 으로 설정. GSD OutboxSender 가 부분 실패 시 증가 |
| `keyword` | string | SHOULD | 로깅/필터링용 (예: `"mmm_reply"`, `"echo_reply"`) |
| `request` | null | MUST | App Bridge 응답은 `null` |
| `response.text` | string | MUST | 사용자에게 발송할 본문 |
| `response.parse_mode` | string \| null | OPTIONAL | `"HTML"`, `"Markdown"`, 또는 `null` (plain) |
| `response.timestamp` | string (ISO 8601) | MUST | 응답 작성 시각 |

**복수 채널 발송 (브로드캐스트)**:
외부 앱이 origin 외 다른 채널에도 응답을 보내고 싶다면 `targets` 에 항목 추가. `is_origin: false` 로 설정한 채널은 GSD 의 `broadcast.snippet_length` 만큼 잘려서 발송됨.

```json
"targets": [
  {"channel_type": "telegram", "channel_id": "123", "is_origin": true},
  {"channel_type": "slack", "channel_id": "C456", "is_origin": false}
]
```

## 7. Correlation 프로토콜

`command_id` 는 GSD 의 `AppResponseCorrelator` pending dict 의 키. 외부 앱은 다음을 준수해야 한다 (MUST):

1. inbound `command_id` 를 정확히 outbound `command_id` 에 복사
2. 한 inbound 명령에 대해 outbound 응답은 **1개** (다중 응답 금지)

> 중간 진행 상황 통보가 필요하면 별도 발송 채널을 쓰거나(`api.py:ChannelSender`), 응답을 모아서 1개로 합쳐 발송한다.

3. 외부 앱이 응답을 보내지 않으면 GSD 가 `response_timeout_sec` (기본 60초) 후 사용자에게 timeout 통보 발송 — 이후 외부 앱이 늦게라도 응답하면 GSD 가 `[지연 응답]` prefix 를 붙여 best-effort 전달

`command_id` 누락 또는 불일치 시:
- **누락**: GSD 는 일반 outbox 메시지로 처리 (correlator pending 에서 제거 안 됨 → 60초 후 timeout 통보 발생, 사용자가 중복 응답을 받을 수 있음)
- **불일치**: 위와 동일 (다른 명령의 pending 이 그대로 남음)

→ 외부 앱은 반드시 정확히 echo 해야 한다.

## 8. Lifecycle / 상태 전이

```
[GSD]                                [외부 앱]                       [사용자]
  │
  │  사용자 슬래시 명령 수신
  │   ─ AppRouter.route ─ matched
  │   ─ command_id 생성, correlator.register
  │   ─ ack 발송 ─────────────────────────────────────────────────▶ [ack 수신]
  │   ─ inbound 작성 (.tmp → rename) ──▶  external_inbox/{app}/x.json
  │                                          │
  │                                          │ ① 폴링 발견
  │                                          │ ② 내부 lock (옵션)
  │                                          │ ③ 처리 (비즈니스 로직)
  │                                          │ ④ outbound 작성 (.tmp → rename)
  │                                          │ ⑤ inbound 파일 삭제
  │   ◀── outbox/y.json ─────────────────────┘
  │   ─ OutboxSender 폴링 (3초) → correlator.resolve(command_id)
  │   ─ 채널 발송 ─────────────────────────────────────────────────▶ [응답 수신]
  │
  ▼
```

### 상태 전이 표

| 외부 앱 상태 | 트리거 | 다음 상태 |
|---|---|---|
| Idle | inbound 파일 발견 | Processing |
| Processing | 비즈니스 로직 완료 | Writing Response |
| Writing Response | outbound .tmp rename 성공 | Cleanup |
| Cleanup | inbound 파일 삭제 | Idle |
| (any) | 비즈니스 예외 | Error Response → Cleanup |

### 권장 처리 순서 (외부 앱)

```
1. files = sorted(glob("*.json"))   ← 시간순 보장 (파일명 prefix)
2. for f in files:
3.   data = json.loads(f.read())
4.   try:
5.     response_text = handle(data)
6.   except Exception as e:
7.     response_text = f"[error] {e}"
8.   write_outbound(data["command_id"], data["source"], response_text,
9.                   reply_to=data["reply_to_outbox"])
10.  f.unlink()
```

## 9. Timeout 의미론

| 시점 | GSD 동작 |
|---|---|
| `t = 0` (라우팅) | command_id 등록, ack 발송 |
| `t = response_timeout_sec` (기본 60s) | correlator 가 pending → expired 이동, **사용자에게 "응답 없음" 통보 발송** |
| `t > response_timeout_sec`, 외부 앱 응답 도착 | OutboxSender 가 expired dict 에서 제거, 응답 본문에 `[지연 응답] ` prefix 부착 후 사용자에게 발송 |
| `t = response_timeout_sec + 24h` | expired dict 에서 영구 제거, 이후 도착하는 응답은 일반 메시지로 처리 (correlation 없음) |

외부 앱은 `response_timeout_sec` 값을 **알 필요 없다** — 응답을 가능한 빨리 보내기만 하면 GSD 가 알아서 처리한다.

다만 외부 앱 처리 시간이 평균적으로 timeout 에 근접하면, GSD 운영자에게 `response_timeout_sec` 상향 조정을 요청할 것 (SHOULD).

## 10. 에러 / 거부 케이스

### 10.1 GSD 단계에서 거부 (외부 앱 호출 안 됨)

| 거부 사유 | 사용자에게 보내지는 메시지 | 외부 앱 영향 |
|---|---|---|
| prefix 미매칭 | (App Bridge 미적용 — 일반 GSD 흐름으로 진행) | 없음 |
| whitelist 거부 | `[{app_name}] 권한 없음 — 등록되지 않은 사용자입니다.` | 없음 |
| args 길이 초과 | `[{app_name}] 입력이 너무 깁니다 (최대 N자).` | 없음 |
| args 위험 문자 | `[{app_name}] 명령에 허용되지 않는 문자가 포함되어 있습니다.` | 없음 |

### 10.2 외부 앱 단계 에러

외부 앱은 비즈니스 에러를 다음 중 하나로 처리해야 한다:

- **Recoverable**: 일반 응답으로 에러 본문을 보낸다. `response.text` 에 사용자가 읽을 메시지 작성. (권장)
- **Non-recoverable**: 응답을 안 보낸다 → GSD timeout 으로 fallthrough. 단 사용자는 60초 대기 후 generic timeout 메시지를 받음.

**MUST NOT**: 잘못된 JSON 을 outbox 에 작성. → GSD OutboxSender 가 `messages/error/` 로 격리하고 사용자에게 응답이 도달하지 않는다.

### 10.3 GSD 발송 실패

GSD OutboxSender 가 채널 API 실패로 발송 못 하면 자동 재시도(최대 3회). 그 후 `messages/error/` 로 격리. **이는 외부 앱과 무관** — 외부 앱은 outbound 작성 후 결과를 신경 쓰지 않는다.

## 11. API 모드 시그니처 계약

### 11.1 등록

```python
# Python 3.11+
from typing import Awaitable, Callable

# 핸들러 시그니처 (둘 중 하나)
HandlerSync  = Callable[[dict], str]
HandlerAsync = Callable[[dict], Awaitable[str]]

orchestrator.app_bridge.register(app_name: str, handler: HandlerSync | HandlerAsync) -> None
```

- `app_name` 은 `config.yaml` `app_bridge.apps[*].name` 와 정확히 일치해야 한다 (MUST). 불일치 시 디스패치 실패.
- 핸들러 등록은 `orchestrator.start()` 또는 `orchestrator.run()` **호출 전**에 해야 한다 (SHOULD). 이후 등록도 동작하지만 등록 전 도착 명령은 "핸들러 없음" 에러 응답.

### 11.2 핸들러 입력 (payload)

```python
{
  "command_id": "uuid-v4",
  "command": {
    "raw": "/cmd a b",
    "prefix": "/cmd",
    "args": ["a", "b"],
  },
  "source": {
    "channel_type": "telegram",
    "channel_id": "...",
    "user_id": "...",
    "user_name": "...",
    # ... 기타 채널 메타 (message_id, thread_ts 등 채널별 키 포함 가능)
  },
}
```

### 11.3 핸들러 출력

- 반환 타입: `str`
- `None` 반환 시 빈 문자열로 변환됨
- async 핸들러는 코루틴 반환 → GSD 가 await
- 예외 발생 시 GSD 가 catch 해서 `"[{app_name}] 핸들러 예외: {ExcType}: {msg}"` 를 사용자에게 발송

### 11.4 다중 등록

같은 `app_name` 에 두 번 `register` 호출하면 **마지막 등록이 우선**한다 (덮어쓰기). `unregister(app_name)` 로 제거 가능.

### 11.5 응답 발송 경로

API 모드 핸들러 응답은 GSD 가 자동으로 `outbox/*_appbridge.json` 에 enqueue 한다 — 외부 앱이 직접 outbox 에 쓸 필요 없다 (FILE 모드와 차이점).

## 12. 등록 (config.yaml)

```yaml
app_bridge:
  enabled: true                        # MUST true 로 설정해야 라우팅 활성화
  external_inbox_base: messages/external_inbox
  response_timeout_sec: 60              # 외부 앱 응답 대기 한계
  ack_timeout_sec: 5                    # ack 발송 SLA
  max_args_length: 1024                 # 보안 한계
  apps:
    - name: my_app                      # MUST: 영문 식별자
      mode: file                         # MUST: "file" | "api"
      inbox_dir: messages/external_inbox/my_app   # FILE only, 생략 시 기본값
      command_prefix: ["/foo", "/bar"]   # MUST: 1개 이상, 모두 "/" 시작
      whitelist_user_ids: ["12345"]      # 빈 리스트 = 전체 허용
      ack_message: "[my_app] ack {id}"   # SHOULD: {id} placeholder 지원 (8자 hex)
```

### 시작 시 검증 (GSD 가 강제)

| 위반 | 시작 결과 |
|---|---|
| `mode` 가 `"file"`, `"api"` 외 값 | `ValueError`, GSD 시작 실패 |
| `command_prefix` 가 `/` 로 시작하지 않음 | `ValueError` |
| `name` 누락 | `ValueError` |
| `command_prefix` 비어있음 | `ValueError` |
| 두 앱이 같은 prefix 등록 | `ValueError` (충돌 메시지에 두 앱 이름 포함) |

## 13. 예약어 / 충돌

다음 prefix 는 **GSD 메타 명령 예약어** 로 외부 앱이 등록할 수 없다 (MUST NOT):

| 예약 prefix | 용도 |
|---|---|
| `/help`, `/start` | 도움말 |
| `/gsd` | GSD 워크플로우 실행 |
| `/status` | 상태 확인 |
| `/reset` | 새 세션 |
| `/resume` | 쿨다운 해제 |
| `/retry` | 발송 실패 재시도 |

이 prefix 는 채널 어댑터(`telegram.py`, `slack.py`)의 CommandHandler 가 우선 처리하므로 AppRouter 에 도달하지 않는다. config 에 등록해도 효력 없음.

**향후 예약 가능성**: `/gsd:*`, `/admin*` 은 향후 GSD 에서 사용할 수 있으므로 외부 앱이 사용하지 말 것 (SHOULD NOT).

## 14. 버저닝 / 호환성

### 14.1 Spec 버저닝

본 정의서는 SemVer 를 따른다.

- **MAJOR** (2.0): 기존 외부 앱 코드 수정 필요한 변경 (필드 제거, 의미 변경)
- **MINOR** (1.1): 하위 호환 추가 (선택 필드 신설)
- **PATCH** (1.0.1): 문서 명료화, 버그 수정

### 14.2 GSD 버전 호환성

| Spec 버전 | GSD 호환 범위 |
|---|---|
| 1.0 | v0.7.x ~ v0.x |

### 14.3 신규 필드 정책

GSD 는 **inbound 메시지에 새 필드를 추가**할 수 있다 (MINOR 변경). 외부 앱은 알지 못하는 필드를 무시해야 한다 (MUST — forward-compat).

외부 앱은 outbound 메시지에 자유롭게 추가 필드를 넣을 수 있다 (예: `metadata`, `app_version`). GSD 는 명세된 필드만 읽는다.

### 14.4 deprecation

deprecated 필드는 최소 1 MINOR 버전 동안 동작 유지. 제거는 다음 MAJOR 에서.

## 15. Test Vectors

외부 앱 구현이 본 정의서에 적합한지 검증하기 위한 reference 테스트 벡터.

### 15.1 Inbound 예시 1 — 기본

GSD 가 작성하는 inbound:
```json
{
  "id": "00000000-0000-0000-0000-000000000001",
  "command_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
  "timestamp": "2026-05-04T12:00:00+09:00",
  "source": {
    "channel_type": "telegram",
    "channel_id": "999",
    "user_id": "12345",
    "user_name": "tester"
  },
  "command": {
    "raw": "/echo hello world",
    "prefix": "/echo",
    "args": ["hello", "world"]
  },
  "target_app": "echo",
  "reply_to_outbox": "/tmp/messages/outbox"
}
```

기대 outbound (echo 앱이 작성):
```json
{
  "id": "11111111-1111-1111-1111-111111111111",
  "command_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
  "source": {
    "channel_type": "telegram",
    "channel_id": "999",
    "user_id": "12345",
    "user_name": "tester"
  },
  "targets": [
    {"channel_type": "telegram", "channel_id": "999", "is_origin": true}
  ],
  "retry_count": 0,
  "keyword": "echo_reply",
  "request": null,
  "response": {
    "text": "hello world",
    "parse_mode": null,
    "timestamp": "2026-05-04T12:00:00+09:00"
  }
}
```

### 15.2 Inbound 예시 2 — 빈 args

```json
{
  "command_id": "...",
  "command": {"raw": "/echo", "prefix": "/echo", "args": []},
  "...": "..."
}
```

외부 앱은 `args == []` 를 정상 케이스로 처리해야 한다 (MUST).

### 15.3 Inbound 예시 3 — 한글 / 유니코드

```json
{
  "command": {
    "raw": "/sell d2 한글종목 100",
    "prefix": "/sell",
    "args": ["d2", "한글종목", "100"]
  },
  "...": "..."
}
```

외부 앱은 UTF-8 인코딩을 가정해야 한다 (MUST).

### 15.4 Outbound 거부 — 잘못된 JSON

외부 앱이 다음을 작성:
```
{this is not json
```

GSD 동작: `messages/error/` 로 격리, 사용자에게 응답 도달 안 함, 60초 후 timeout 통보. → 외부 앱은 절대 잘못된 JSON 을 작성하지 않을 것 (MUST NOT).

### 15.5 Spec compliance 자동 검증

`tests/test_app_bridge.py::test_echo_app_round_trip` 가 reference 검증 케이스다. 외부 앱은 자체 round-trip 테스트를 두는 것이 권장된다 (SHOULD).

## 16. Conformance Checklist

외부 앱 구현체가 본 정의서를 만족하는지 자가 점검 목록:

### 일반 (MUST)
- [ ] inbound 디렉토리를 시작 시 `mkdir -p` 한다
- [ ] `*.json` 글로빙으로 파일 발견 (dot-prefix 무시)
- [ ] inbound JSON 파싱 실패 시 파일 삭제 또는 격리 (재시도 루프 방지)
- [ ] outbound 작성은 `.tmp` → rename 의 원자적 프로토콜 사용
- [ ] outbound 의 `command_id` 는 inbound 와 정확히 일치
- [ ] outbound 의 `id` 는 새로 생성된 UUID v4
- [ ] outbound `targets` 에 1개 이상 항목, `is_origin: true` 포함
- [ ] outbound `response.text` 는 비어 있지 않은 문자열
- [ ] inbound 처리 후 inbound 파일 삭제 또는 archive 이동
- [ ] UTF-8 인코딩 (no BOM)
- [ ] 알지 못하는 inbound 필드는 무시 (forward-compat)
- [ ] GSD 의 다른 디렉토리(`inbox/`, `sent/`, `error/`)는 건드리지 않는다

### 권장 (SHOULD)
- [ ] outbound 파일명에 시간 정렬 가능 prefix
- [ ] 폴링 주기 100ms ~ 1s
- [ ] 비즈니스 예외를 일반 응답에 담아 반환 (사용자 가독성)
- [ ] `keyword` 필드 채움 (로깅용)
- [ ] 자체 round-trip 테스트 보유

### 금지 (MUST NOT)
- [ ] cross-device rename 사용
- [ ] target 디렉토리에 `open(target, 'w')` 직접 쓰기
- [ ] 한 inbound 에 다중 outbound 응답
- [ ] GSD 메타 명령 prefix 등록 (`/help`, `/gsd`, `/status`, `/reset`, `/resume`, `/retry`, `/start`)
- [ ] 잘못된 JSON outbound 작성

---

## Appendix A: JSON Schema (Draft 2020-12)

### A.1 Inbound (외부 앱 입력)

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://gsd-orchestrator/spec/v1/app-bridge-inbound.json",
  "title": "AppBridgeInbound",
  "type": "object",
  "required": ["id", "command_id", "timestamp", "source", "command",
               "target_app", "reply_to_outbox"],
  "additionalProperties": true,
  "properties": {
    "id": {"type": "string", "format": "uuid"},
    "command_id": {"type": "string", "format": "uuid"},
    "timestamp": {"type": "string", "format": "date-time"},
    "source": {
      "type": "object",
      "required": ["channel_type", "channel_id", "user_id", "user_name"],
      "properties": {
        "channel_type": {"type": "string", "enum": ["telegram", "slack"]},
        "channel_id": {"type": "string"},
        "user_id": {"type": "string"},
        "user_name": {"type": "string"}
      }
    },
    "command": {
      "type": "object",
      "required": ["raw", "prefix", "args"],
      "properties": {
        "raw": {"type": "string"},
        "prefix": {"type": "string", "pattern": "^/"},
        "args": {"type": "array", "items": {"type": "string"}}
      }
    },
    "target_app": {"type": "string", "minLength": 1},
    "reply_to_outbox": {"type": "string", "minLength": 1}
  }
}
```

### A.2 Outbound (외부 앱 출력)

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://gsd-orchestrator/spec/v1/app-bridge-outbound.json",
  "title": "AppBridgeOutbound",
  "type": "object",
  "required": ["id", "command_id", "targets", "retry_count",
               "request", "response"],
  "additionalProperties": true,
  "properties": {
    "id": {"type": "string", "format": "uuid"},
    "command_id": {"type": "string", "format": "uuid"},
    "source": {"$ref": "#/$defs/source"},
    "targets": {
      "type": "array",
      "minItems": 1,
      "items": {
        "type": "object",
        "required": ["channel_type", "channel_id", "is_origin"],
        "properties": {
          "channel_type": {"type": "string"},
          "channel_id": {"type": "string"},
          "is_origin": {"type": "boolean"}
        }
      }
    },
    "retry_count": {"type": "integer", "minimum": 0},
    "keyword": {"type": "string"},
    "request": {"type": "null"},
    "response": {
      "type": "object",
      "required": ["text", "timestamp"],
      "properties": {
        "text": {"type": "string", "minLength": 1},
        "parse_mode": {
          "anyOf": [
            {"type": "string", "enum": ["HTML", "Markdown", "MarkdownV2"]},
            {"type": "null"}
          ]
        },
        "timestamp": {"type": "string", "format": "date-time"}
      }
    }
  },
  "$defs": {
    "source": {
      "type": "object",
      "properties": {
        "channel_type": {"type": "string"},
        "channel_id": {"type": "string"},
        "user_id": {"type": "string"},
        "user_name": {"type": "string"}
      }
    }
  }
}
```

---

## 변경 이력

| 버전 | 일자 | 변경 |
|---|---|---|
| 1.0 | 2026-05-04 | 최초 발행 (GSD v0.7.0 기준) |
