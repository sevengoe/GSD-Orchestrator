# GSD Orchestrator

> **"메신저로 지시하고 일상으로 돌아가세요. 개발은 AI가 백그라운드에서 완료합니다."**
> **비동기 메시징 기반의 자율형 개발 오케스트레이터 (Autonomous Dev Orchestrator)**

[![GSD Framework](https://img.shields.io/badge/Powered%20by-GSD%20Framework-blue.svg)](#) [![Multi-Channel](https://img.shields.io/badge/Multi--Channel-Telegram%20%7C%20Slack-blue.svg)](#) [![Autonomous System](https://img.shields.io/badge/Autonomous-System-success.svg)](#) [![Fire & Forget](https://img.shields.io/badge/Fire%20%26%20Forget-Async-orange.svg)](#)

GSD Orchestrator는 내 PC의 Claude Code와 GSD(Getting Shit Done) 프레임워크를 하나로 연결하는 **무인 자율 실행 엔진**입니다.

과거처럼 AI가 타이핑을 마칠 때까지 화면을 지켜볼 필요가 없습니다. **텔레그램이나 슬랙**으로 "결제 모듈 연동해 줘"라고 무심하게 툭 던지면, 봇이 작업을 인지하고 백그라운드에서 묵묵히 코드를 짭니다. 작업이 완료되면 결과와 요약본을 스마트폰으로 보고받으세요.

---

## 🔥 주요 특장점 (Why GSD Orchestrator?)

### 1️⃣ "Fire & Forget" (비동기 자율 코딩)
사용자가 답변을 기다리며 대기할 필요가 없습니다. 명령을 내리면 시스템이 백그라운드에서 기획 → 작업 분할(WBS) → 병렬 코딩 → 검증 → 커밋까지 알아서 수행하는 **비동기적 감독 구조**입니다.

### 2️⃣ Multi-Channel 팀 협업 (Telegram + Slack)
나 혼자 쓰는 텔레그램 봇을 넘어, 팀 단위의 Slack 연동을 완벽하게 지원합니다. 
- **크로스채널 브로드캐스트 지원**: 어느 채널에서 요청하든, 원본 채널에는 수백 줄의 상세 응답이 가고 상대 채널엔 깔끔한 요약본만 전송되어 채팅방을 오염시키지 않습니다.

### 3️⃣ 죽지 않는 시스템 (Resilient State Machine)
DB나 외부 서비스 의존 없이 오직 '로컬 파일 시스템'만으로 큐(inbox/outbox)를 관리합니다. 컴퓨터가 꺼져도, 프로세스가 죽어도 켜지는 즉시 중단된 시점부터 100% 자동 재개됩니다.

### 4️⃣ API 비용 걱정 없는 무제한 코딩 (Subscription-based Adaptive Routing)
가장 큰 장점은 **값비싼 종량제 API가 아닌 Claude Pro/Max 구독 계정**을 직접 활용한다는 점입니다. 토큰 요금 폭탄 걱정 없이 구독 플랜의 사용량을 한계치까지 알뜰하게 끌어쓰며 개발을 자동화합니다. 또한, 단순 질문은 가벼운 모델로 처리해 일일 사용량 한도(Limit) 소모를 방어하는 **지능형 라우터** 기능도 탑재했습니다.

### 5️⃣ 외부 앱 연동 인터페이스 (2가지 모드)
외부 애플리케이션과 두 가지 방식으로 연동할 수 있습니다. **Library 모드**는 기존 앱(FastAPI, 기타 봇 등)에 패키지로 import하여 같은 프로세스에서 실행하며, `on_result` 콜백과 `ChannelSender`로 Python API를 직접 호출합니다. **독립 프로세스 모드**는 GSD-Orchestrator가 별도 프로세스로 실행 중일 때, 외부 앱이 `inbox/`에 JSON 파일을 작성하고 `sent/`에서 결과를 읽는 파일 기반 연동입니다. 별도 패키지 설치 없이 JSON 읽기/쓰기만으로 동작합니다.

### 6️⃣ 투명한 가시성 (Real-time Visibility)
명령을 내린 후 "현재 큐에 백그라운드 작업이 몇 개나 대기 중인지?", "지금 어떤 WBS 단계를 밟고 있는지?" 궁금할 수 있습니다. 텔레그램이나 슬랙에서 `/status` 명령어를 입력하면 **현재 진행 중인 작업 상태와 대기 건수, 누적 토큰 사용량**을 실시간으로 투명하게 확인할 수 있어, 비동기 시스템의 '깜깜이' 한계를 완벽히 해소합니다.

---

## 🆚 일반 AI 봇과 무엇이 다른가?

| 비교 항목 | 일반 메신저 기반 AI 봇 | GSD Orchestrator |
|------|------|------|
| **목적** | 단순 텍스트 생성 및 단답형 응답 | **소프트웨어 개발 전 과정 자동화** (코딩, 테스트, 커밋) |
| **실행 모델** | 동기식 (답변 시까지 화면 점유 대기) | **비동기 큐 기반** (명령 후 일상 복귀, "Fire & Forget") |
| **지원 채널** | 특정 플랫폼 1개 종속 | **Telegram & Slack** 동시 지원 및 크로스 브로드캐스트 |
| **장애 대응** | 프로세스 종료 시 세션 및 작업 내역 증발 | **파일 기반 트랜잭션**으로 시스템 재시작 시 100% 임무 재개 |
| **문맥 관리** | 대화 누적에 따른 환각 증가 (Context Rot) | 서브 태스크별 **세션 격리 (Fresh Context)** 보장 |

---

## 🚀 사전 요구사항 및 빠른 시작

### 준비물
- **Python 3.11+**
- **Claude Code CLI (`claude`)**: Pro 또는 Max 구독 필요 (API Key 불필요)
- **GSD Framework**: GSD 트랙 사용 시 설치 (`npx get-shit-done-cc --claude --global`)
- **메신저 봇 설정**: Telegram 봇 토큰 발급 및 Slack App Token/Bot Token 세팅 (선택)

### 1분 설치

```bash
git clone git@github.com:sevengoe/GSD-Orchestrator.git
cd GSD-Orchestrator

./setup.sh              # 초기 설정 (Slack 포함: ./setup.sh --slack)
vi .env                 # 봇 토큰 설정
./start.sh              # 실행
```

> 중지할 때는 `./stop.sh`, 실시간 로그를 볼 때는 `tail -f logs/gsd-orchestrator.log`를 사용하세요.

---

## 🛠 아키텍처 흐름 요약

복잡한 cron 데몬 설정이나 무거운 프레임워크 없이, 단일 Python 프로세스로 돌아가는 우아하고 직관적인 구조입니다. 

```text
[수신] 텔레그램/슬랙 → ChannelManager/Orchestrator → "확인 ✓" 응답 + inbox/{ts}.json 생성
[분류] inbox_processor (10초 폴링) → 경량 모델 분류 ("simple" or "gsd")
[처리] claude -p --dangerously-skip-permissions (즉답 또는 GSD 자율 코딩 작업)
[보고] 30초 간격 중간 진행 알림 발송 (텔레그램/슬랙)
[발송] outbox_sender (3초 폴링) → 멀티 타겟 채널별 분할 및 요약 처리 발송 → sent/ 로 이동
[보관] archiver (매시간) → 보관소(archive/) 적재 후 기간 만료 시 영구 삭제
[복구] 실패 시 자동 재시도 → 최대 재시도 초과 시 격리, 쿨다운(Cooldown) 제어
```

---

## ❓ 자주 묻는 질문 (FAQ)

**Q. 멀티 GSD-Orchestrator (다중 인스턴스 시스템)는 지원하지 않나요?**
A. 여러 인스턴스를 동시에 운영할 수 있습니다. 단, **인스턴스 1개 = 봇 토큰 1개** 정책을 지켜야 합니다. 동일한 Telegram 봇 토큰으로 두 인스턴스를 실행하면 Conflict 에러가 발생하고, Slack도 동일 앱 토큰으로 여러 인스턴스를 연결하면 메시지가 랜덤 분산됩니다. 인스턴스마다 BotFather에서 별도 봇을 생성하고, 각각 다른 토큰을 설정하세요.

**Q. AI가 제 PC(서버)를 직접 제어하는 것에 대한 보안 이슈는 없나요?**
A. AI가 터미널 CLI를 직접 실행하므로 권한 제어는 필수적입니다. 기본 레벨의 보호를 위해 바탕화면 등 중요 파일이 있는 관리자(admin/root)가 아닌, **권한이 엄격히 제한된 '일반 사용자(Restricted User)' 계정을 OS에 별도로 생성**하여 실행하는 것을 권장합니다. 더 나아가 엔터프라이즈 환경이나 완벽한 보호가 필요할 경우, **Docker 컨테이너 내에서 격리 실행**하거나 **특정 작업 디렉토리 외부로의 접근을 원천 차단(Sandboxing)**하는 구조를 구축하면 인프라를 안전하게 보호할 수 있습니다.

**Q. Claude 모델만 지원하나요? (OpenAI 등 다른 모델은요?)**
A. 네, 현재 아키텍처는 Claude 모델 전용입니다. 오케스트레이터의 핵심 엔진이 Anthropic의 `claude-code` CLI를 직접 호출(`claude -p`)하는 구조이며, GSD 프레임워크의 슬래시 명령어(`/gsd:do`, `/gsd:next` 등)와 블로커 감지(`gsd-tools.cjs`) 역시 Claude Code 세션 위에서 동작하도록 설계되었기 때문입니다. 채널 어댑터(Telegram/Slack)처럼 LLM 실행 계층을 추상화하면 다른 모델도 이론적으로 연동 가능하지만, 현재는 Claude Code의 세션 관리·도구 사용·headless 모드에 최적화된 단일 엔진 구조입니다.

**Q. "지능형 라우터"가 뭔가요?**
A. 사용자가 보낸 메시지를 경량 모델(Haiku)이 먼저 읽고, "이건 단순 질문이니 가볍게 처리"(Simple Track) 또는 "이건 복잡한 개발 작업이니 GSD로 처리"(GSD Track)를 자동 판단하는 기능입니다. 단순 질문까지 무거운 모델로 처리하면 구독 플랜의 일일 사용량 한도가 빠르게 소진되므로, 요청 복잡도에 따라 처리 경로를 나누어 한도를 아끼면서도 복잡한 작업은 풀파워로 실행합니다. `config.yaml`의 `gsd.auto_classify: true`로 활성화되며, `gsd.auto_classify: false`로 끄면 모든 메시지가 Simple Track으로만 처리됩니다.

**Q. 왜 API 연동 방식을 채택하지 않고 구독형 계정(CLI) 방식을 사용하나요?**
A. 가장 큰 이유는 **비용 폭탄을 방지하기 위함**입니다. GSD 프레임워크의 자율 코딩 과정(요구사항 분석 → 작업 분할 → 병렬 코딩 → 검증 및 디버깅 무한 루프)은 엄청난 양의 토큰을 지속적으로 소모합니다. 이를 종량제 API로 연동할 경우 실무 도입 시 감당하기 힘든 비용이 발생합니다. 따라서 월정액인 Claude Pro/Max **구독 플랜의 일일 사용량 한도를 비용 걱정 없이(Cost-Free) 100% 극한까지 뽑아 쓰며** 강력한 AI 에이전트를 가동할 수 있도록 의도적으로 CLI 기반 엔진으로 설계했습니다.

---

## 📖 문서 가이드

디테일한 운영 및 기술 스펙은 다음 문서를 참고하세요.

- [`docs/사용자-가이드.md`](docs/사용자-가이드.md) — 봇 생성, 토큰 발급, 상세 설정(config), 명령어, 트러블슈팅
- [`docs/아키텍처.md`](docs/아키텍처.md) — 전체 시스템 설계도, 멀티채널 어댑터 계층, 메시지 라이프사이클
- [`docs/외부-연동-가이드.md`](docs/외부-연동-가이드.md) — 외부 앱 연동: Library 모드(Python API) 및 독립 프로세스 모드(파일 기반)

---

> **Keywords for Search**: `GSD Framework`, `GSD 프레임워크`, `AI Agent`, `Autonomous AI Developer`, `AI Auto Coder`, `Claude Code Automation`, `Telegram AI Bot`, `Slack AI Bot`, `Autonomous Dev Orchestrator`, `비동기 자율 코딩`, `자율형 AI 에이전트`, `AI 개발 자동화`
