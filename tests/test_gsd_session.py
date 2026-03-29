"""GSD 세션 유지 및 이력 복원 테스트.

GSD-세션-유지-설계서.md의 시나리오를 검증한다.
"""

import asyncio
import json
import os
import time
import pytest
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

from gsd_orchestrator.channels.base import ChannelAdapter
from gsd_orchestrator.channels.manager import ChannelManager
from gsd_orchestrator.config import Config
from gsd_orchestrator.inbox_processor import InboxProcessor


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


def _make_inbox_file(inbox_dir: Path, filename: str = "test.json",
                     text: str = "테스트", mode: str = "default") -> Path:
    data = {
        "id": "test-uuid",
        "source": {
            "channel_type": "telegram", "channel_id": "123",
            "user_id": "U1", "user_name": "김철수",
            "message_id": 1, "thread_ts": None,
        },
        "chat_id": "123", "message_id": 1,
        "keyword": "테스트", "mode": mode,
        "request": {"text": text, "timestamp": "2026-03-21T14:30:00+09:00"},
        "response": None,
    }
    path = inbox_dir / filename
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    return path


def _make_sent_file(sent_dir: Path, filename: str,
                    request_text: str, response_text: str,
                    channel_id: str = "123") -> Path:
    """sent/ 디렉토리에 완료된 메시지 파일을 생성한다."""
    data = {
        "id": "sent-uuid",
        "source": {
            "channel_type": "telegram", "channel_id": channel_id,
            "user_id": "U1", "user_name": "김철수",
            "message_id": 1, "thread_ts": None,
        },
        "keyword": "테스트",
        "request": {"text": request_text, "timestamp": "2026-03-21T14:30:00+09:00"},
        "response": {
            "text": f"[telegram][김철수][테스트] 처리결과\n\n{response_text}",
            "parse_mode": "HTML",
            "timestamp": "2026-03-21T14:31:00+09:00",
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
  history_max_messages: 3

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
    }


# ── GSD 성공 시 .gsd-active 생성 ────────────────────────────


class TestGsdActiveCreation:
    @pytest.mark.asyncio
    async def test_gsd_success_creates_active(self, setup):
        """GSD 성공 완료 시 .gsd-active 파일에 PID가 기록된다."""
        processor = setup["processor"]
        inbox = setup["inbox"]

        _make_inbox_file(inbox, "gsd.json", text="시뮬레이션 해줘", mode="gsd")

        with patch.object(processor, "_run_claude", new_callable=AsyncMock,
                          return_value=_claude_success("계획 완료. 진행할까요?")), \
             patch.object(processor, "_check_gsd_blockers", return_value=""):
            await processor._process_inbox()

        assert processor._gsd_active_file.exists()
        stored_pid = int(processor._gsd_active_file.read_text().strip())
        assert stored_pid == os.getpid()

    @pytest.mark.asyncio
    async def test_gsd_failure_clears_active(self, setup):
        """GSD 실패 시 .gsd-active가 삭제된다."""
        processor = setup["processor"]
        inbox = setup["inbox"]

        # 사전에 .gsd-active 존재
        processor._gsd_active_file.write_text(str(os.getpid()))

        _make_inbox_file(inbox, "fail.json", text="실패 작업", mode="gsd")

        with patch.object(processor, "_run_claude", new_callable=AsyncMock,
                          return_value=None), \
             patch.object(processor, "_check_gsd_blockers", return_value=""):
            await processor._process_inbox()

        assert not processor._gsd_active_file.exists()

    @pytest.mark.asyncio
    async def test_new_gsd_clears_previous_active(self, setup):
        """새 /gsd 명령은 이전 .gsd-active를 삭제한다."""
        processor = setup["processor"]
        inbox = setup["inbox"]

        # 이전 세션의 .gsd-active
        processor._gsd_active_file.write_text("99999")

        _make_inbox_file(inbox, "new_gsd.json", text="새 작업", mode="gsd")

        with patch.object(processor, "_run_claude", new_callable=AsyncMock,
                          return_value=_claude_success("새 계획")), \
             patch.object(processor, "_check_gsd_blockers", return_value=""):
            await processor._process_inbox()

        # 새 PID로 갱신
        stored_pid = int(processor._gsd_active_file.read_text().strip())
        assert stored_pid == os.getpid()


# ── .gsd-active 상태 감지 ────────────────────────────────────


class TestGsdActiveDetection:
    def test_is_gsd_active_true(self, setup):
        """유효한 .gsd-active가 있으면 True."""
        processor = setup["processor"]
        processor._gsd_active_file.write_text(str(os.getpid()))
        assert processor.is_gsd_active() is True

    def test_is_gsd_active_false_no_file(self, setup):
        """.gsd-active 없으면 False."""
        processor = setup["processor"]
        assert processor.is_gsd_active() is False

    def test_is_gsd_active_timeout_expires(self, setup):
        """타임아웃 초과 시 False + 파일 삭제."""
        processor = setup["processor"]
        processor._gsd_active_file.write_text(str(os.getpid()))
        # mtime을 31분 전으로 조작
        old_time = time.time() - 31 * 60
        os.utime(processor._gsd_active_file, (old_time, old_time))

        assert processor.is_gsd_active() is False
        assert not processor._gsd_active_file.exists()

    def test_is_gsd_active_disabled_when_timeout_zero(self, setup):
        """session_timeout_minutes=0이면 항상 False."""
        processor = setup["processor"]
        setup["config"].gsd_session_timeout_minutes = 0
        processor._gsd_active_file.write_text(str(os.getpid()))

        assert processor.is_gsd_active() is False

    def test_blocked_takes_priority(self, setup):
        """.blocked와 .gsd-active 동시 존재 시 .blocked 우선."""
        processor = setup["processor"]
        processor._gsd_active_file.write_text(str(os.getpid()))
        processor._blocked_file.write_text("some_file.json")

        # blocked가 있으면 gsd-active보다 우선 (orchestrator에서 처리)
        # is_gsd_active 자체는 여전히 True
        assert processor.is_gsd_active() is True
        assert processor._blocked_file.exists()


# ── 세션 유실 판단 ────────────────────────────────────────────


class TestSessionAliveDetection:
    def test_session_alive_same_pid(self, setup):
        """동일 PID → 세션 유지."""
        processor = setup["processor"]
        processor._gsd_active_file.write_text(str(os.getpid()))
        assert processor._is_gsd_session_alive() is True

    def test_session_dead_different_pid(self, setup):
        """다른 PID → 세션 유실."""
        processor = setup["processor"]
        processor._gsd_active_file.write_text("99999")
        assert processor._is_gsd_session_alive() is False

    def test_session_dead_no_file(self, setup):
        """파일 없음 → 세션 유실."""
        processor = setup["processor"]
        assert processor._is_gsd_session_alive() is False


# ── GSD resume: --continue vs 이력 복원 ──────────────────────


class TestGsdResume:
    @pytest.mark.asyncio
    async def test_resume_with_continue(self, setup):
        """동일 PID에서 resume → --continue 사용."""
        processor = setup["processor"]
        inbox = setup["inbox"]

        # 현재 PID로 .gsd-active 설정
        processor._gsd_active_file.write_text(str(os.getpid()))

        _make_inbox_file(inbox, "resume.json", text="네 진행해주세요", mode="gsd-resume")

        with patch.object(processor, "_run_claude", new_callable=AsyncMock,
                          return_value=_claude_success("작업 진행 중")) as mock_claude, \
             patch.object(processor, "_check_gsd_blockers", return_value=""):
            await processor._process_inbox()

        # --continue로 호출 확인
        mock_claude.assert_called_once()
        call_kwargs = mock_claude.call_args
        assert call_kwargs.kwargs.get("continue_session") is True

    @pytest.mark.asyncio
    async def test_resume_with_history_fallback(self, setup):
        """PID 불일치 시 sent/ 이력으로 맥락 복원."""
        processor = setup["processor"]
        inbox = setup["inbox"]
        sent = setup["sent"]

        # 다른 PID로 .gsd-active (세션 유실)
        processor._gsd_active_file.write_text("99999")

        # sent/ 이력 생성
        _make_sent_file(sent, "prev.json",
                        request_text="kospi 시뮬레이션 해줘",
                        response_text="Module 1~5 계획. 진행할까요?")

        _make_inbox_file(inbox, "resume.json", text="네 진행해주세요", mode="gsd-resume")

        with patch.object(processor, "_run_claude", new_callable=AsyncMock,
                          return_value=_claude_success("작업 진행")) as mock_claude, \
             patch.object(processor, "_check_gsd_blockers", return_value=""):
            await processor._process_inbox()

        # --continue 없이 호출 (fresh session with context)
        mock_claude.assert_called_once()
        call_kwargs = mock_claude.call_args
        assert call_kwargs.kwargs.get("continue_session") is not True

        # 프롬프트에 이전 맥락 포함 확인
        prompt = call_kwargs.args[0] if call_kwargs.args else call_kwargs.kwargs.get("prompt", "")
        assert "이전 대화 맥락" in prompt
        assert "kospi 시뮬레이션" in prompt
        assert "Module 1~5 계획" in prompt


# ── 이력 복원 함수 ────────────────────────────────────────────


class TestBuildContextFromHistory:
    def test_builds_context_from_sent(self, setup):
        """sent/ 파일에서 맥락을 올바르게 조립한다."""
        processor = setup["processor"]
        sent = setup["sent"]

        _make_sent_file(sent, "a.json", "요청 A", "응답 A")
        _make_sent_file(sent, "b.json", "요청 B", "응답 B")

        context = processor._build_context_from_history("123")

        assert "이전 대화" in context
        assert "요청 A" in context
        assert "응답 A" in context
        assert "요청 B" in context

    def test_skips_system_alerts(self, setup):
        """system-alert 파일은 제외한다."""
        processor = setup["processor"]
        sent = setup["sent"]

        _make_sent_file(sent, "msg.json", "요청", "응답")
        # system-alert 파일
        alert = sent / "alert_system-alert.json"
        alert.write_text(json.dumps({
            "source": {}, "request": None,
            "response": {"text": "[시스템] 알림", "timestamp": ""},
        }))

        context = processor._build_context_from_history("123")

        assert "요청" in context
        assert "[시스템] 알림" not in context

    def test_filters_by_channel_id(self, setup):
        """다른 사용자의 메시지는 제외한다."""
        processor = setup["processor"]
        sent = setup["sent"]

        _make_sent_file(sent, "mine.json", "내 요청", "내 응답", channel_id="123")
        _make_sent_file(sent, "other.json", "남의 요청", "남의 응답", channel_id="999")

        context = processor._build_context_from_history("123")

        assert "내 요청" in context
        assert "남의 요청" not in context

    def test_respects_max_messages(self, setup):
        """history_max_messages를 초과하지 않는다."""
        processor = setup["processor"]
        sent = setup["sent"]

        for i in range(10):
            _make_sent_file(sent, f"msg_{i:02d}.json", f"요청{i}", f"응답{i}")

        context = processor._build_context_from_history("123")

        # 기본 max_messages=3이므로 최근 3건만
        assert context.count("[요청]") == 3

    def test_empty_sent_returns_empty(self, setup):
        """sent/가 비어있으면 빈 문자열 반환."""
        processor = setup["processor"]
        context = processor._build_context_from_history("123")
        assert context == ""

    def test_strips_response_header(self, setup):
        """응답에서 '[채널][사용자][키워드] 처리결과' 헤더를 제거한다."""
        processor = setup["processor"]
        sent = setup["sent"]

        _make_sent_file(sent, "hdr.json", "요청", "실제 응답 내용")

        context = processor._build_context_from_history("123")

        assert "실제 응답 내용" in context
        assert "처리결과" not in context

    def test_truncates_long_response(self, setup):
        """2000자 초과 응답은 잘린다."""
        processor = setup["processor"]
        sent = setup["sent"]

        long_text = "A" * 3000
        _make_sent_file(sent, "long.json", "요청", long_text)

        context = processor._build_context_from_history("123")

        assert "이하 생략" in context
        assert len(context) < 3000


# ── clear_gsd_active ─────────────────────────────────────────


class TestClearGsdActive:
    def test_clear_gsd_active(self, setup):
        """clear_gsd_active()로 .gsd-active가 삭제된다."""
        processor = setup["processor"]
        processor._gsd_active_file.write_text(str(os.getpid()))
        assert processor._gsd_active_file.exists()

        processor.clear_gsd_active()
        assert not processor._gsd_active_file.exists()

    def test_clear_gsd_active_no_file(self, setup):
        """파일 없어도 에러 없음."""
        processor = setup["processor"]
        processor.clear_gsd_active()  # should not raise


# ── Simple Track 세션 리셋 맥락 복원 ──────────────────────────


class TestSimpleTrackContextRestore:
    @pytest.mark.asyncio
    async def test_simple_reset_injects_history(self, setup):
        """Simple Track 세션 리셋 시 sent/ 이력이 프롬프트에 주입된다."""
        processor = setup["processor"]
        inbox = setup["inbox"]
        sent = setup["sent"]

        # sent/에 이전 대화 이력 생성
        _make_sent_file(sent, "001_prev.json",
                        request_text="이전 질문", response_text="이전 답변")

        # turn_count를 max(20) 이상으로 설정하여 세션 리셋 유도
        processor._write_int_file(processor._turn_count_file, 20)

        _make_inbox_file(inbox, "reset_test.json", text="새 질문")

        captured_prompt = None
        original_run = processor._run_claude

        async def capture_run(prompt, **kwargs):
            nonlocal captured_prompt
            captured_prompt = prompt
            return _claude_success("응답 완료")

        with patch.object(processor, "_run_claude", side_effect=capture_run):
            await processor._process_inbox()

        assert captured_prompt is not None
        assert "이전 대화 맥락을 참고하여 답변하세요" in captured_prompt
        assert "이전 질문" in captured_prompt
        assert "이전 답변" in captured_prompt
        assert "새 질문" in captured_prompt

    @pytest.mark.asyncio
    async def test_simple_continue_mid_session_no_history(self, setup):
        """Simple Track 세션 중간(turn_count>0) 시 이력 주입 없이 원본 텍스트만 전달된다."""
        processor = setup["processor"]
        inbox = setup["inbox"]
        sent = setup["sent"]

        _make_sent_file(sent, "002_prev.json",
                        request_text="이전 질문", response_text="이전 답변")

        # turn_count > 0이면 세션 진행 중 → 이력 주입 없음
        processor._write_int_file(processor._turn_count_file, 5)

        _make_inbox_file(inbox, "continue_test.json", text="일반 질문")

        captured_prompt = None

        async def capture_run(prompt, **kwargs):
            nonlocal captured_prompt
            captured_prompt = prompt
            return _claude_success("응답 완료")

        with patch.object(processor, "_run_claude", side_effect=capture_run):
            await processor._process_inbox()

        assert captured_prompt == "일반 질문"

    @pytest.mark.asyncio
    async def test_simple_first_message_injects_history(self, setup):
        """Simple Track 첫 메시지(turn_count=0) 시 이력이 주입된다."""
        processor = setup["processor"]
        inbox = setup["inbox"]
        sent = setup["sent"]

        _make_sent_file(sent, "002_prev.json",
                        request_text="이전 질문", response_text="이전 답변")

        # turn_count = 0이면 첫 메시지 → 이력 주입
        processor._write_int_file(processor._turn_count_file, 0)

        _make_inbox_file(inbox, "first_test.json", text="일반 질문")

        captured_prompt = None

        async def capture_run(prompt, **kwargs):
            nonlocal captured_prompt
            captured_prompt = prompt
            return _claude_success("응답 완료")

        with patch.object(processor, "_run_claude", side_effect=capture_run):
            await processor._process_inbox()

        assert "이전 대화" in captured_prompt
        assert "이전 질문" in captured_prompt
        assert "일반 질문" in captured_prompt

    @pytest.mark.asyncio
    async def test_simple_reset_no_history_uses_original(self, setup):
        """Simple Track 세션 리셋이지만 이력이 없으면 원본 텍스트만 전달된다."""
        processor = setup["processor"]
        inbox = setup["inbox"]

        # sent/ 비어있음 → 이력 없음
        processor._write_int_file(processor._turn_count_file, 20)

        _make_inbox_file(inbox, "no_hist.json", text="이력없는 질문")

        captured_prompt = None

        async def capture_run(prompt, **kwargs):
            nonlocal captured_prompt
            captured_prompt = prompt
            return _claude_success("응답 완료")

        with patch.object(processor, "_run_claude", side_effect=capture_run):
            await processor._process_inbox()

        assert captured_prompt == "이력없는 질문"


# ── 정리 ─────────────────────────────────────────────────────


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
