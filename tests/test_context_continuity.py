"""메시지 컨텍스트 연결성 및 타임아웃 방어 테스트.

context-continuity-design.md 설계서의 시나리오를 검증한다.
"""

import json
import os
import time
import pytest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch
from collections import OrderedDict

from gsd_orchestrator.channels.base import ChannelAdapter
from gsd_orchestrator.channels.manager import ChannelManager
from gsd_orchestrator.config import Config
from gsd_orchestrator.inbox_processor import InboxProcessor
from gsd_orchestrator.inbox_writer import InboxWriter
from gsd_orchestrator.orchestrator import Orchestrator, _GSD_CONTINUE_PATTERNS

KST = timezone(timedelta(hours=9))


# ── 공통 헬퍼 ───────────────────────────────────────────────


class FakeAdapter(ChannelAdapter):
    def __init__(self, ch_type="telegram", ch_id="123"):
        self._type = ch_type
        self._id = ch_id
        self.sent_messages: list[tuple[str, str]] = []

    @property
    def channel_type(self) -> str:
        return self._type

    def get_channel_id(self) -> str:
        return self._id

    async def start(self, on_message):
        pass

    async def stop(self):
        pass

    async def send_message(self, channel_id, text, parse_mode=None):
        self.sent_messages.append((channel_id, text))
        return True


def _make_source(channel_id="123", message_id=1, user_name="김철수"):
    return {
        "channel_type": "telegram", "channel_id": channel_id,
        "user_id": "U1", "user_name": user_name,
        "message_id": message_id, "message_ts": int(time.time()),
        "thread_ts": None,
    }


def _make_inbox_file(inbox_dir: Path, filename: str = "test.json",
                     text: str = "테스트", mode: str = "default",
                     conversation_id: str = "") -> Path:
    data = {
        "id": "test-uuid",
        "conversation_id": conversation_id,
        "source": {
            "channel_type": "telegram", "channel_id": "123",
            "user_id": "U1", "user_name": "김철수",
            "message_id": 1, "thread_ts": None,
        },
        "chat_id": "123", "message_id": 1,
        "keyword": "테스트", "mode": mode,
        "request": {"text": text, "timestamp": "2026-03-29T14:30:00+09:00"},
        "response": None,
    }
    path = inbox_dir / filename
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    return path


def _make_sent_file(sent_dir: Path, filename: str,
                    request_text: str, response_text: str,
                    channel_id: str = "123",
                    conversation_id: str = "") -> Path:
    data = {
        "id": "sent-uuid",
        "conversation_id": conversation_id,
        "source": {
            "channel_type": "telegram", "channel_id": channel_id,
            "user_id": "U1", "user_name": "김철수",
            "message_id": 1, "thread_ts": None,
        },
        "keyword": "테스트",
        "request": {"text": request_text, "timestamp": "2026-03-29T14:30:00+09:00"},
        "response": {
            "text": f"[telegram][김철수][테스트] 처리결과\n\n{response_text}",
            "parse_mode": "HTML",
            "timestamp": "2026-03-29T14:31:00+09:00",
        },
        "targets": [{"channel_type": "telegram", "channel_id": channel_id, "is_origin": True}],
        "retry_count": 0,
    }
    path = sent_dir / filename
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    return path


def _claude_success(text="처리 완료"):
    return {
        "type": "result", "subtype": "success", "result": text,
        "usage": {"input_tokens": 100, "output_tokens": 50,
                  "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
        "modelUsage": {"claude-sonnet-4-20250514": {}},
        "total_cost_usd": 0.001, "duration_ms": 1000, "stop_reason": "end_turn",
    }


SAMPLE_PLAN = """# GSD 작업 계획
## 원본 요청
코인 시뮬레이션 해주세요

## 단위 작업
- [ ] Unit 1: 시뮬레이션 엔진 구축
  - 범위: 백테스팅 엔진 구현
  - 대상: backtest/simulator.py
- [ ] Unit 2: 전 종목 스크리닝
  - 범위: 상위 50개 종목 시뮬레이션
  - 대상: backtest/screener.py
"""


@pytest.fixture
def setup(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("""
channels:
  telegram:
    enabled: true
    chat_id: "123"
  slack:
    enabled: false
    channel_id: ""

broadcast:
  snippet_length: 500

polling:
  inbox_check_interval: 1
  outbox_interval: 1
  progress_interval: 0

archive:
  message_retention_days: 7

log:
  dir: logs
  retention_days: 14

claude:
  timeout: 5
  cooldown_retry_minutes: 1
  max_session_turns: 20
  working_dir: workspace

gsd:
  enabled: true
  timeout: 600000
  progress_check_interval: 30
  auto_mode: true
  auto_classify: false
  classify_model: haiku
  session_timeout_minutes: 30
  history_max_messages: 5

paths:
  inbox_dir: messages/inbox
  outbox_dir: messages/outbox
  sent_dir: messages/sent
  error_dir: messages/error
  archive_dir: messages/archive
""")

    for d in ["messages/inbox", "messages/outbox", "messages/sent",
              "messages/error", "messages/archive", "workspace"]:
        (tmp_path / d).mkdir(parents=True, exist_ok=True)

    with patch.dict("os.environ", {
        "TELEGRAM_BOT_TOKEN": "test-token",
        "TELEGRAM_CHAT_ID": "123",
    }):
        config = Config.load(str(config_path))

    adapter = FakeAdapter()
    manager = ChannelManager([adapter])
    processor = InboxProcessor(config, manager)

    return {
        "processor": processor,
        "config": config,
        "adapter": adapter,
        "inbox": config.inbox_dir,
        "outbox": config.outbox_dir,
        "sent": config.sent_dir,
        "error": config.error_dir,
        "archive": config.archive_dir,
        "tmp_path": tmp_path,
    }


@pytest.fixture(autouse=True)
def cleanup_runtime(setup):
    yield
    processor = setup["processor"]
    for attr in ["_gsd_active_file", "_cooldown_file", "_fail_count_file",
                 "_cooldown_alert_file", "_blocked_file", "_token_track_file",
                 "_reset_file", "_turn_count_file", "_active_file"]:
        f = getattr(processor, attr, None)
        if f and f.exists():
            f.unlink(missing_ok=True)


# ===================================================================
# 시나리오 1: GSD 활성 + 새로운 요청 → default로 분류
# ===================================================================


class TestGsdActiveNewRequest:
    """GSD 세션 활성 중 새로운 요청("문서로 만들어줘")이 들어오면
    gsd-resume가 아닌 default로 분류해야 한다."""

    @pytest.mark.asyncio
    async def test_new_request_during_gsd_goes_default(self, setup):
        """GSD 활성 + '문서로 만들어줘' → default 모드, /gsd:next 안 함."""
        processor = setup["processor"]
        inbox = setup["inbox"]

        # GSD 세션 활성 상태
        processor._gsd_active_file.write_text(str(os.getpid()))

        _make_inbox_file(inbox, "doc.json",
                         text="정리해서 문서로 만들어줘", mode="default")

        captured_prompt = None

        async def capture_run(prompt, **kwargs):
            nonlocal captured_prompt
            captured_prompt = prompt
            return _claude_success("문서 작성 완료")

        with patch.object(processor, "_run_claude", side_effect=capture_run):
            await processor._process_inbox()

        assert captured_prompt is not None
        # /gsd:next 가 프롬프트에 없어야 함
        assert "/gsd:next" not in captured_prompt
        # 문서 작성 요청이 그대로 전달되어야 함
        assert "문서로 만들어줘" in captured_prompt

    @pytest.mark.asyncio
    async def test_status_query_during_gsd_goes_default(self, setup):
        """GSD 활성 + '코인 현황' → default 모드."""
        processor = setup["processor"]
        inbox = setup["inbox"]

        processor._gsd_active_file.write_text(str(os.getpid()))

        _make_inbox_file(inbox, "status.json",
                         text="코인 현황", mode="default")

        captured_prompt = None

        async def capture_run(prompt, **kwargs):
            nonlocal captured_prompt
            captured_prompt = prompt
            return _claude_success("코인 현황 조회 완료")

        with patch.object(processor, "_run_claude", side_effect=capture_run):
            await processor._process_inbox()

        assert captured_prompt is not None
        assert "/gsd:next" not in captured_prompt
        assert "코인 현황" in captured_prompt


# ===================================================================
# 시나리오 2: GSD 만료 + 계획서 존재 + "진행해주세요" → gsd-resume
# ===================================================================


class TestTimeoutDefenseWithPlan:
    """GSD 세션이 타임아웃되었지만 계획서가 남아있을 때,
    '진행해주세요' 같은 계속 패턴이 오면 gsd-resume로 처리해야 한다."""

    @pytest.mark.asyncio
    async def test_continuation_with_expired_session_and_plan(self, setup):
        """GSD 만료 + 계획서 + '진행해주세요' → gsd-resume → 작업 큐 생성."""
        processor = setup["processor"]
        inbox = setup["inbox"]

        # GSD 세션 만료 (파일 없음) + 계획서 존재
        processor._plan_file.write_text(SAMPLE_PLAN)

        _make_inbox_file(inbox, "proceed.json",
                         text="진행해주세요", mode="gsd-resume")

        with patch.object(processor, "_run_claude", new_callable=AsyncMock,
                          return_value=_claude_success("작업 시작")), \
             patch.object(processor, "_check_gsd_blockers", return_value=""):
            await processor._process_inbox()

        # 작업 큐가 생성되어야 함
        wq_files = list(processor._workqueue_dir.glob("*.json"))
        assert len(wq_files) >= 1

        # outbox에 큐 생성 완료 메시지
        outbox_files = list(setup["outbox"].glob("*.json"))
        assert len(outbox_files) == 1
        out_data = json.loads(outbox_files[0].read_text())
        assert "작업 큐 생성 완료" in out_data["response"]["text"]

    @pytest.mark.asyncio
    async def test_continuation_without_plan_uses_context(self, setup):
        """GSD 만료 + 계획서 없음 + '진행해주세요' → gsd-resume + 아카이브 컨텍스트."""
        processor = setup["processor"]
        inbox = setup["inbox"]
        sent = setup["sent"]

        # 계획서 없음, sent/에 이전 이력 존재
        _make_sent_file(sent, "001_prev.json",
                        request_text="코인 시뮬레이션 해줘",
                        response_text="계획서를 작성했습니다. 진행할까요?")

        _make_inbox_file(inbox, "proceed.json",
                         text="진행해주세요", mode="gsd-resume")

        captured_prompt = None

        async def capture_run(prompt, **kwargs):
            nonlocal captured_prompt
            captured_prompt = prompt
            return _claude_success("작업 진행")

        with patch.object(processor, "_run_claude", side_effect=capture_run), \
             patch.object(processor, "_check_gsd_blockers", return_value=""):
            await processor._process_inbox()

        assert captured_prompt is not None
        # 아카이브 컨텍스트가 포함되어야 함
        assert "이전 대화" in captured_prompt
        assert "코인 시뮬레이션" in captured_prompt
        # /gsd:next 강제가 없어야 함
        assert "/gsd:next" not in captured_prompt


# ===================================================================
# 시나리오 3: GSD 활성 + 계속 패턴 → gsd-resume (유연 프롬프트)
# ===================================================================


class TestGsdContinuationPrompt:
    """GSD 세션 활성 + "진행해주세요" → gsd-resume이지만 /gsd:next 없이 유연한 프롬프트."""

    @pytest.mark.asyncio
    async def test_resume_prompt_no_forced_gsd_next(self, setup):
        """gsd-resume 세션 유지 시 /gsd:next 강제 없음."""
        processor = setup["processor"]
        inbox = setup["inbox"]

        processor._gsd_active_file.write_text(str(os.getpid()))

        _make_inbox_file(inbox, "resume.json",
                         text="네 진행해주세요", mode="gsd-resume")

        captured_prompt = None

        async def capture_run(prompt, **kwargs):
            nonlocal captured_prompt
            captured_prompt = prompt
            return _claude_success("작업 계속 진행")

        with patch.object(processor, "_run_claude", side_effect=capture_run), \
             patch.object(processor, "_check_gsd_blockers", return_value=""):
            await processor._process_inbox()

        assert captured_prompt is not None
        assert "/gsd:next" not in captured_prompt
        assert "이어서 처리" in captured_prompt

    @pytest.mark.asyncio
    async def test_resume_session_lost_includes_context(self, setup):
        """gsd-resume 세션 유실 시 아카이브 컨텍스트 + /gsd:next 없음."""
        processor = setup["processor"]
        inbox = setup["inbox"]
        sent = setup["sent"]

        # 다른 PID (세션 유실)
        processor._gsd_active_file.write_text("99999")

        _make_sent_file(sent, "prev.json",
                        request_text="시뮬레이션 해줘",
                        response_text="Module 1~5 계획")

        _make_inbox_file(inbox, "resume.json",
                         text="진행해주세요", mode="gsd-resume")

        captured_prompt = None

        async def capture_run(prompt, **kwargs):
            nonlocal captured_prompt
            captured_prompt = prompt
            return _claude_success("작업 진행")

        with patch.object(processor, "_run_claude", side_effect=capture_run), \
             patch.object(processor, "_check_gsd_blockers", return_value=""):
            await processor._process_inbox()

        assert captured_prompt is not None
        assert "/gsd:next" not in captured_prompt
        assert "이전 대화" in captured_prompt
        assert "시뮬레이션" in captured_prompt


# ===================================================================
# 시나리오 4: 첫 메시지 시 아카이브 컨텍스트 주입
# ===================================================================


class TestFirstMessageContextInjection:
    """세션의 첫 메시지(turn_count=0)에도 아카이브 컨텍스트가 주입되어야 한다."""

    @pytest.mark.asyncio
    async def test_first_message_gets_archive_context(self, setup):
        """turn_count=0 + sent/ 이력 → 프롬프트에 컨텍스트 주입."""
        processor = setup["processor"]
        inbox = setup["inbox"]
        sent = setup["sent"]

        # turn_count = 0 (첫 메시지)
        processor._write_int_file(processor._turn_count_file, 0)

        _make_sent_file(sent, "001_prev.json",
                        request_text="이전 작업 요청",
                        response_text="이전 작업 결과")

        _make_inbox_file(inbox, "first.json", text="이어서 해줘")

        captured_prompt = None

        async def capture_run(prompt, **kwargs):
            nonlocal captured_prompt
            captured_prompt = prompt
            return _claude_success("응답")

        with patch.object(processor, "_run_claude", side_effect=capture_run):
            await processor._process_inbox()

        assert captured_prompt is not None
        assert "이전 대화" in captured_prompt
        assert "이전 작업 요청" in captured_prompt
        assert "이전 작업 결과" in captured_prompt

    @pytest.mark.asyncio
    async def test_second_message_no_extra_context(self, setup):
        """turn_count > 0이고 세션 유지 중이면 추가 컨텍스트 주입 없음."""
        processor = setup["processor"]
        inbox = setup["inbox"]
        sent = setup["sent"]

        # turn_count = 5 (진행 중인 세션)
        processor._write_int_file(processor._turn_count_file, 5)

        _make_sent_file(sent, "001_prev.json",
                        request_text="이전 요청",
                        response_text="이전 응답")

        _make_inbox_file(inbox, "mid.json", text="추가 질문")

        captured_prompt = None

        async def capture_run(prompt, **kwargs):
            nonlocal captured_prompt
            captured_prompt = prompt
            return _claude_success("응답")

        with patch.object(processor, "_run_claude", side_effect=capture_run):
            await processor._process_inbox()

        # 세션 중간이므로 컨텍스트 주입 없이 원본 텍스트만
        assert captured_prompt == "추가 질문"


# ===================================================================
# 시나리오 5: 중복 메시지 방지
# ===================================================================


class TestDuplicateMessagePrevention:
    """같은 message_id가 2번 수신되면 2번째는 skip."""

    def test_is_gsd_continuation_patterns(self):
        """계속 패턴이 올바르게 인식된다."""
        assert Orchestrator._is_gsd_continuation("진행해주세요")
        assert Orchestrator._is_gsd_continuation("네")
        assert Orchestrator._is_gsd_continuation("ㅇㅇ")
        assert Orchestrator._is_gsd_continuation("ok")
        assert Orchestrator._is_gsd_continuation("YES")
        assert Orchestrator._is_gsd_continuation("승인")
        assert Orchestrator._is_gsd_continuation("  진행  ")
        assert Orchestrator._is_gsd_continuation("확인.")

    def test_non_continuation_patterns(self):
        """일반 요청은 계속 패턴이 아니다."""
        assert not Orchestrator._is_gsd_continuation("문서로 만들어줘")
        assert not Orchestrator._is_gsd_continuation("코인 현황")
        assert not Orchestrator._is_gsd_continuation("작업을 하고 있나요?")
        assert not Orchestrator._is_gsd_continuation("정리해서 문서로 만들어줘")
        assert not Orchestrator._is_gsd_continuation("이 작업의 결과를 보여줘")

    def test_duplicate_message_detection(self):
        """message_id 중복 감지 동작 확인."""
        # Orchestrator 없이 메서드 직접 테스트
        orch = type('MockOrch', (), {
            '_recent_message_ids': OrderedDict(),
            '_is_duplicate_message': Orchestrator._is_duplicate_message,
        })()

        source1 = {"channel_type": "telegram", "message_id": 100}
        source2 = {"channel_type": "telegram", "message_id": 100}
        source3 = {"channel_type": "telegram", "message_id": 101}

        assert orch._is_duplicate_message(source1) is False  # 첫 수신
        assert orch._is_duplicate_message(source2) is True   # 중복
        assert orch._is_duplicate_message(source3) is False  # 다른 메시지

    def test_duplicate_detection_max_capacity(self):
        """dedup 세트가 100건 초과 시 오래된 것부터 제거."""
        orch = type('MockOrch', (), {
            '_recent_message_ids': OrderedDict(),
            '_is_duplicate_message': Orchestrator._is_duplicate_message,
        })()

        # 150개 메시지 등록
        for i in range(150):
            orch._is_duplicate_message({"channel_type": "tg", "message_id": i})

        assert len(orch._recent_message_ids) <= 100


# ===================================================================
# 시나리오 6: conversation_id 계승
# ===================================================================


class TestConversationIdInheritance:
    """conversation_id가 같은 대화의 이력만 필터링한다."""

    def test_context_filters_by_conversation_id(self, setup):
        """conversation_id 지정 시 같은 대화 메시지만 수집."""
        processor = setup["processor"]
        sent = setup["sent"]

        # 대화 A
        _make_sent_file(sent, "a1.json",
                        request_text="대화A 요청1", response_text="대화A 응답1",
                        conversation_id="conv-A")
        _make_sent_file(sent, "a2.json",
                        request_text="대화A 요청2", response_text="대화A 응답2",
                        conversation_id="conv-A")
        # 대화 B
        _make_sent_file(sent, "b1.json",
                        request_text="대화B 요청", response_text="대화B 응답",
                        conversation_id="conv-B")

        context = processor._build_context_from_history("123", "conv-A")

        assert "대화A 요청1" in context
        assert "대화A 요청2" in context
        assert "대화B 요청" not in context

    def test_context_fallback_to_channel_id(self, setup):
        """conversation_id 불일치 시 channel_id 기반 fallback."""
        processor = setup["processor"]
        sent = setup["sent"]

        # conversation_id 없는 기존 메시지
        _make_sent_file(sent, "old.json",
                        request_text="오래된 요청", response_text="오래된 응답")

        # 존재하지 않는 conversation_id로 검색
        context = processor._build_context_from_history("123", "conv-nonexistent")

        # fallback으로 channel_id 기반 수집
        assert "오래된 요청" in context

    def test_context_without_conversation_id(self, setup):
        """conversation_id 미지정 시 기존 channel_id 기반 동작."""
        processor = setup["processor"]
        sent = setup["sent"]

        _make_sent_file(sent, "msg.json",
                        request_text="일반 요청", response_text="일반 응답")

        context = processor._build_context_from_history("123")

        assert "일반 요청" in context


# ===================================================================
# 시나리오 7: has_pending_plan 동작
# ===================================================================


class TestHasPendingPlan:
    def test_has_plan(self, setup):
        """계획서가 있으면 True."""
        processor = setup["processor"]
        processor._plan_file.write_text(SAMPLE_PLAN)
        assert processor.has_pending_plan() is True

    def test_no_plan(self, setup):
        """계획서가 없으면 False."""
        processor = setup["processor"]
        processor._plan_file.unlink(missing_ok=True)
        assert processor.has_pending_plan() is False


# ===================================================================
# 시나리오 8: InboxWriter conversation_id 필드
# ===================================================================


class TestInboxWriterConversationId:
    def test_conversation_id_written(self, tmp_path):
        """conversation_id가 inbox JSON에 포함된다."""
        inbox_dir = tmp_path / "inbox"
        inbox_dir.mkdir()
        writer = InboxWriter(inbox_dir)

        source = _make_source()
        path = writer.write(source, "테스트", conversation_id="conv-123")

        data = json.loads(path.read_text())
        assert data["conversation_id"] == "conv-123"

    def test_conversation_id_empty_default(self, tmp_path):
        """conversation_id 미지정 시 빈 문자열."""
        inbox_dir = tmp_path / "inbox"
        inbox_dir.mkdir()
        writer = InboxWriter(inbox_dir)

        source = _make_source()
        path = writer.write(source, "테스트")

        data = json.loads(path.read_text())
        assert data["conversation_id"] == ""

    def test_backward_compat_no_conversation_id(self, tmp_path):
        """하위 호환: 기존 형식 호출도 정상 동작."""
        inbox_dir = tmp_path / "inbox"
        inbox_dir.mkdir()
        writer = InboxWriter(inbox_dir)

        path = writer.write("123456", 42, "텍스트")
        data = json.loads(path.read_text())
        assert "conversation_id" in data
        assert data["conversation_id"] == ""


# ===================================================================
# 시나리오: Orchestrator 의도 분류 통합 테스트
# ===================================================================


class TestOrchestratorIntentClassification:
    """Orchestrator._on_channel_message()에서 의도 분류가 올바르게 작동하는지 확인."""

    @pytest.fixture
    def orch_setup(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text("""
channels:
  telegram:
    enabled: true
    chat_id: "123"
  slack:
    enabled: false
    channel_id: ""

broadcast:
  snippet_length: 500

polling:
  inbox_check_interval: 1
  outbox_interval: 1
  progress_interval: 0

archive:
  message_retention_days: 7

log:
  dir: logs
  retention_days: 14

claude:
  timeout: 5
  cooldown_retry_minutes: 1
  max_session_turns: 20
  working_dir: workspace

gsd:
  enabled: true
  timeout: 600000
  progress_check_interval: 30
  auto_mode: true
  auto_classify: false
  classify_model: haiku
  session_timeout_minutes: 30
  history_max_messages: 5

paths:
  inbox_dir: messages/inbox
  outbox_dir: messages/outbox
  sent_dir: messages/sent
  error_dir: messages/error
  archive_dir: messages/archive
""")

        for d in ["messages/inbox", "messages/outbox", "messages/sent",
                  "messages/error", "messages/archive", "workspace"]:
            (tmp_path / d).mkdir(parents=True, exist_ok=True)

        with patch.dict("os.environ", {
            "TELEGRAM_BOT_TOKEN": "test-token",
            "TELEGRAM_CHAT_ID": "123",
        }):
            orch = Orchestrator(Config.load(str(config_path)))

        return {
            "orch": orch,
            "inbox": orch._config.inbox_dir,
            "sent": orch._config.sent_dir,
        }

    @pytest.mark.asyncio
    async def test_gsd_active_continuation_goes_resume(self, orch_setup):
        """GSD 활성 + '진행해주세요' → inbox에 gsd-resume로 저장."""
        orch = orch_setup["orch"]
        inbox = orch_setup["inbox"]

        orch._inbox_processor._gsd_active_file.write_text(str(os.getpid()))

        source = _make_source(message_id=100)
        await orch._on_channel_message(source, "진행해주세요")

        files = list(inbox.glob("*.json"))
        assert len(files) == 1
        data = json.loads(files[0].read_text())
        assert data["mode"] == "gsd-resume"

        orch._inbox_processor._gsd_active_file.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_gsd_active_new_request_goes_default(self, orch_setup):
        """GSD 활성 + '문서로 만들어줘' → inbox에 default로 저장, GSD 세션 종료."""
        orch = orch_setup["orch"]
        inbox = orch_setup["inbox"]

        orch._inbox_processor._gsd_active_file.write_text(str(os.getpid()))

        source = _make_source(message_id=200)
        await orch._on_channel_message(source, "정리해서 문서로 만들어줘")

        files = list(inbox.glob("*.json"))
        assert len(files) == 1
        data = json.loads(files[0].read_text())
        assert data["mode"] == "default"

        # GSD 세션이 종료되어야 함
        assert not orch._inbox_processor._gsd_active_file.exists()

    @pytest.mark.asyncio
    async def test_expired_gsd_with_plan_continuation(self, orch_setup):
        """GSD 만료 + 계획서 + '진행해주세요' → gsd-resume으로 저장."""
        orch = orch_setup["orch"]
        inbox = orch_setup["inbox"]

        # GSD 세션 없음 (만료), 계획서 존재
        orch._inbox_processor._plan_file.write_text(SAMPLE_PLAN)

        source = _make_source(message_id=300)
        await orch._on_channel_message(source, "진행해주세요")

        files = list(inbox.glob("*.json"))
        assert len(files) == 1
        data = json.loads(files[0].read_text())
        assert data["mode"] == "gsd-resume"

        orch._inbox_processor._plan_file.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_duplicate_message_skipped(self, orch_setup):
        """같은 message_id 2번 수신 → 1건만 inbox에 저장."""
        orch = orch_setup["orch"]
        inbox = orch_setup["inbox"]

        source = _make_source(message_id=400)
        await orch._on_channel_message(source, "첫 번째 수신")
        await orch._on_channel_message(source, "첫 번째 수신")  # 중복

        files = list(inbox.glob("*.json"))
        assert len(files) == 1

    @pytest.mark.asyncio
    async def test_conversation_id_generated(self, orch_setup):
        """새 요청에 conversation_id가 생성된다."""
        orch = orch_setup["orch"]
        inbox = orch_setup["inbox"]

        source = _make_source(message_id=500)
        await orch._on_channel_message(source, "새로운 질문")

        files = list(inbox.glob("*.json"))
        assert len(files) == 1
        data = json.loads(files[0].read_text())
        assert data["conversation_id"].startswith("conv-")
        assert len(data["conversation_id"]) > 10

    @pytest.mark.asyncio
    async def test_conversation_id_inherited(self, orch_setup):
        """계속 패턴 시 이전 conversation_id를 계승한다."""
        orch = orch_setup["orch"]
        inbox = orch_setup["inbox"]
        sent = orch_setup["sent"]

        # sent/에 이전 메시지 (conversation_id 포함)
        _make_sent_file(sent, "prev.json",
                        request_text="이전 요청", response_text="이전 응답",
                        conversation_id="conv-existing-123")

        # GSD 활성 + 계속 패턴
        orch._inbox_processor._gsd_active_file.write_text(str(os.getpid()))

        source = _make_source(message_id=600)
        await orch._on_channel_message(source, "진행해주세요")

        files = list(inbox.glob("*.json"))
        assert len(files) == 1
        data = json.loads(files[0].read_text())
        assert data["conversation_id"] == "conv-existing-123"

        orch._inbox_processor._gsd_active_file.unlink(missing_ok=True)
