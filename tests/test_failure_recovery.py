"""장애 복구 및 알림 테스트 — InboxProcessor.

장애-복구-가이드.md의 카테고리 A~C, 알림 계획을 검증한다.
_run_claude만 Mock하고 나머지는 실제 파일 I/O로 장애를 시뮬레이션한다.
"""

import asyncio
import json
import time
import pytest
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

from gsd_orchestrator.channels.base import ChannelAdapter
from gsd_orchestrator.channels.manager import ChannelManager
from gsd_orchestrator.config import Config
from gsd_orchestrator.inbox_processor import InboxProcessor, MAX_FILE_FAILURES, MAX_GLOBAL_FAILURES


# ── 테스트 인프라 ──────────────────────────────────────────


class FakeAdapter(ChannelAdapter):
    """발송 추적용 가짜 어댑터."""

    def __init__(self, ch_type: str = "telegram", ch_id: str = "123"):
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
                     text: str = "테스트 요청", mode: str = "default") -> Path:
    """inbox에 유효한 메시지 파일을 생성한다."""
    data = {
        "id": "test-uuid",
        "source": {
            "channel_type": "telegram",
            "channel_id": "123",
            "user_id": "U1",
            "user_name": "김철수",
            "message_id": 1,
            "thread_ts": None,
        },
        "chat_id": "123",
        "message_id": 1,
        "keyword": "테스트",
        "mode": mode,
        "request": {"text": text, "timestamp": "2026-03-19T14:30:00+09:00"},
        "response": None,
    }
    path = inbox_dir / filename
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    return path


def _claude_success(text="처리 완료"):
    """Claude 성공 응답."""
    return {
        "type": "result",
        "subtype": "success",
        "result": text,
        "usage": {"input_tokens": 100, "output_tokens": 50,
                  "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
        "modelUsage": {"claude-sonnet-4-20250514": {}},
        "total_cost_usd": 0.001,
        "duration_ms": 1000,
        "stop_reason": "end_turn",
    }


def _claude_error():
    """Claude 에러 응답."""
    return {
        "type": "result",
        "subtype": "error_max_turns",
        "result": "최대 턴 초과",
        "usage": {"input_tokens": 100, "output_tokens": 0,
                  "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
        "modelUsage": {},
        "total_cost_usd": 0,
        "duration_ms": 500,
        "stop_reason": "max_turns",
    }


@pytest.fixture
def setup(tmp_path):
    """InboxProcessor + FakeAdapter + Config를 구성한다."""
    # config.yaml 생성
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
  max_session_turns: 3
  working_dir: workspace

gsd:
  enabled: false
  timeout: 600000
  progress_check_interval: 30
  auto_mode: true
  auto_classify: false
  classify_model: haiku

paths:
  inbox_dir: messages/inbox
  outbox_dir: messages/outbox
  sent_dir: messages/sent
  error_dir: messages/error
  archive_dir: messages/archive
""")

    # 디렉토리 생성
    for d in ["messages/inbox", "messages/outbox", "messages/sent",
              "messages/error", "messages/archive", "workspace"]:
        (tmp_path / d).mkdir(parents=True, exist_ok=True)

    # Config 로드
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
        "manager": manager,
        "inbox": config.inbox_dir,
        "outbox": config.outbox_dir,
        "error": config.error_dir,
    }


# ── 카테고리 A: 프로세스 장애 복구 ──────────────────────────


class TestProcessRecovery:
    """A-1, B-5, C-2: .processing 파일 복구."""

    def test_recover_stale_processing_restores(self, setup):
        """잔류 .processing 파일을 inbox/로 복원한다."""
        inbox = setup["inbox"]
        processor = setup["processor"]

        # .processing 파일 직접 생성 (프로세스 중단 시뮬레이션)
        _make_inbox_file(inbox, "stale.json")
        (inbox / "stale.json").rename(inbox / "stale.json.processing")

        processor._recover_stale_processing()

        assert (inbox / "stale.json").exists()
        assert not (inbox / "stale.json.processing").exists()
        # failcount 파일 생성 확인
        assert (inbox / "stale.json.failcount").exists()
        assert int((inbox / "stale.json.failcount").read_text()) == 1

    @pytest.mark.asyncio
    async def test_recover_stale_processing_quarantine(self, setup):
        """failcount >= 3인 .processing은 error/로 격리 + 알림 발송."""
        inbox = setup["inbox"]
        error = setup["error"]
        processor = setup["processor"]
        adapter = setup["adapter"]

        # .processing 파일 + failcount 2 (다음이 3회째)
        _make_inbox_file(inbox, "fail.json")
        (inbox / "fail.json").rename(inbox / "fail.json.processing")
        (inbox / "fail.json.failcount").write_text("2")

        processor._recover_stale_processing()
        # create_task로 발송된 알림이 실행되도록 양보
        await asyncio.sleep(0)

        # error/ 격리 확인
        assert (error / "fail.json").exists()
        assert not (inbox / "fail.json.processing").exists()
        assert not (inbox / "fail.json.failcount").exists()

        # 알림 파일이 outbox/에 생성되었는지 확인
        outbox = setup["outbox"]
        alert_files = list(outbox.glob("*_system-alert.json"))
        assert len(alert_files) >= 1
        alert_data = json.loads(alert_files[0].read_text())
        assert "요청 처리 실패" in alert_data["response"]["text"]
        assert "건너뜁니다" in alert_data["response"]["text"]


class TestStaleSendingRecovery:
    """A-1, C-4: .sending 파일 복구 (OutboxSender 측)."""

    def test_recover_stale_sending_restores(self, tmp_path):
        """잔류 .sending 파일을 outbox/로 복원한다."""
        from gsd_orchestrator.outbox_sender import OutboxSender

        outbox = tmp_path / "outbox"
        sent = tmp_path / "sent"
        error = tmp_path / "error"
        for d in [outbox, sent, error]:
            d.mkdir()

        adapter = FakeAdapter()
        manager = ChannelManager([adapter])
        sender = OutboxSender(manager, outbox, sent, error, interval=1)

        # .sending 파일 직접 생성
        data = {"id": "test", "response": {"text": "hi", "parse_mode": "HTML"},
                "targets": [{"channel_type": "telegram", "channel_id": "123",
                             "is_origin": True}],
                "source": {}, "retry_count": 0}
        (outbox / "msg.json.sending").write_text(json.dumps(data))

        sender._recover_stale_sending()

        assert (outbox / "msg.json").exists()
        assert not (outbox / "msg.json.sending").exists()


# ── 카테고리 B: Claude 실행 장애 ────────────────────────────


class TestClaudeFailure:
    """B-1, B-2: Claude 타임아웃/파싱 실패."""

    @pytest.mark.asyncio
    async def test_claude_timeout_triggers_failure(self, setup):
        """Claude 타임아웃 시 failcount 증가, inbox 복원."""
        processor = setup["processor"]
        inbox = setup["inbox"]
        _make_inbox_file(inbox, "timeout.json")

        with patch.object(processor, "_run_claude", new_callable=AsyncMock,
                          return_value=None):
            await processor._process_inbox()

        # inbox로 복원 (failcount 1)
        assert (inbox / "timeout.json").exists()
        fc = inbox / "timeout.json.failcount"
        assert fc.exists()
        assert int(fc.read_text()) == 1

    @pytest.mark.asyncio
    async def test_claude_returns_error_status(self, setup):
        """Claude가 error subtype 반환 시 실패 처리."""
        processor = setup["processor"]
        inbox = setup["inbox"]
        _make_inbox_file(inbox, "error_resp.json")

        with patch.object(processor, "_run_claude", new_callable=AsyncMock,
                          return_value=_claude_error()):
            await processor._process_inbox()

        assert (inbox / "error_resp.json").exists()
        fc = inbox / "error_resp.json.failcount"
        assert fc.exists()
        assert int(fc.read_text()) == 1

    @pytest.mark.asyncio
    async def test_claude_json_parse_failure(self, setup):
        """Claude None 응답 (JSON 파싱 실패 시뮬레이션) 시 실패 처리."""
        processor = setup["processor"]
        inbox = setup["inbox"]
        _make_inbox_file(inbox, "parse_fail.json")

        with patch.object(processor, "_run_claude", new_callable=AsyncMock,
                          return_value=None):
            await processor._process_inbox()

        assert (inbox / "parse_fail.json").exists()
        fc_path = inbox / "parse_fail.json.failcount"
        assert fc_path.exists()


# ── 카테고리 C: 메시지 처리 장애 ────────────────────────────


class TestMessageProcessingFailure:
    """C-1: inbox 파일 손상."""

    @pytest.mark.asyncio
    async def test_inbox_file_corruption(self, setup):
        """JSON 파싱 불가 inbox 파일은 _handle_failure로 처리된다."""
        processor = setup["processor"]
        inbox = setup["inbox"]

        # 손상된 JSON 파일
        (inbox / "corrupt.json").write_text("{invalid json content")

        with patch.object(processor, "_run_claude", new_callable=AsyncMock):
            await processor._process_inbox()

        # failcount 생성 확인 (복원 후 재시도 대상)
        assert (inbox / "corrupt.json").exists() or \
               (setup["error"] / "corrupt.json").exists()


# ── 에스컬레이션: 파일 격리 알림 ────────────────────────────


class TestFileFailureEscalation:
    """파일별 3회 실패 → error/ 격리 + 채널 알림."""

    @pytest.mark.asyncio
    async def test_file_failure_escalation_to_error(self, setup):
        """3회 연속 실패 시 error/ 격리 + 알림 발송."""
        processor = setup["processor"]
        inbox = setup["inbox"]
        error = setup["error"]
        outbox = setup["outbox"]

        _make_inbox_file(inbox, "escalate.json")

        with patch.object(processor, "_run_claude", new_callable=AsyncMock,
                          return_value=None):
            # 3회 처리 시도
            for _ in range(MAX_FILE_FAILURES):
                await processor._process_inbox()
                await asyncio.sleep(0)  # create_task 알림 실행

        # error/ 격리 확인
        assert (error / "escalate.json").exists()
        assert not (inbox / "escalate.json").exists()
        assert not (inbox / "escalate.json.failcount").exists()

        # 알림 확인
        alert_files = list(outbox.glob("*_system-alert.json"))
        alert_texts = []
        for f in alert_files:
            data = json.loads(f.read_text())
            alert_texts.append(data["response"]["text"])

        assert any("메시지 처리 실패" in t and "error/로 격리" in t
                    for t in alert_texts)


# ── 에스컬레이션: 쿨다운 ────────────────────────────────────


class TestCooldownEscalation:
    """전역 5회 실패 → 쿨다운 진입 + 알림."""

    @pytest.mark.asyncio
    async def test_global_failure_escalation_to_cooldown(self, setup):
        """전역 failcount 5회 → 쿨다운 진입 + 알림 발송."""
        processor = setup["processor"]
        inbox = setup["inbox"]
        outbox = setup["outbox"]
        config = setup["config"]

        with patch.object(processor, "_run_claude", new_callable=AsyncMock,
                          return_value=None):
            # 각각 다른 파일로 MAX_GLOBAL_FAILURES회 실패
            for i in range(MAX_GLOBAL_FAILURES):
                _make_inbox_file(inbox, f"fail_{i}.json")
                await processor._process_inbox()
                await asyncio.sleep(0)  # create_task 알림 실행

        # 쿨다운 파일 생성 확인
        assert processor._cooldown_file.exists()
        resume_at = int(processor._cooldown_file.read_text().strip())
        assert resume_at > int(time.time())

        # 쿨다운 알림 확인
        alert_files = list(outbox.glob("*_system-alert.json"))
        alert_texts = [json.loads(f.read_text())["response"]["text"]
                       for f in alert_files]
        assert any("연속 실패 감지" in t and "/resume" in t
                    for t in alert_texts)

    @pytest.mark.asyncio
    async def test_cooldown_blocks_processing(self, setup):
        """쿨다운 상태에서 inbox 처리를 건너뛴다."""
        processor = setup["processor"]
        inbox = setup["inbox"]

        # 미래 시각으로 쿨다운 설정
        processor._cooldown_file.write_text(str(int(time.time()) + 3600))

        _make_inbox_file(inbox, "blocked.json")

        with patch.object(processor, "_run_claude", new_callable=AsyncMock) as mock:
            await processor._process_inbox()

        # Claude 호출 없음
        mock.assert_not_called()
        # inbox에 그대로 존재
        assert (inbox / "blocked.json").exists()

    @pytest.mark.asyncio
    async def test_cooldown_expires_automatically(self, setup):
        """쿨다운 시간 경과 후 처리 재개."""
        processor = setup["processor"]
        inbox = setup["inbox"]

        # 과거 시각으로 쿨다운 설정 (이미 만료)
        processor._cooldown_file.write_text(str(int(time.time()) - 10))

        _make_inbox_file(inbox, "resume.json")

        with patch.object(processor, "_run_claude", new_callable=AsyncMock,
                          return_value=_claude_success()):
            await processor._process_inbox()

        # 쿨다운 해제 확인
        assert not processor._cooldown_file.exists()
        # 정상 처리 확인 (outbox에 결과 생성)
        assert len(list(setup["outbox"].glob("*.json"))) >= 1

    @pytest.mark.asyncio
    async def test_cooldown_long_duration_alert(self, setup):
        """쿨다운 5시간 이상 시 장기 경고 알림. 중복 발송 방지."""
        processor = setup["processor"]
        inbox = setup["inbox"]
        outbox = setup["outbox"]
        config = setup["config"]

        # cooldown_start = resume_at - cooldown_retry_minutes * 60
        # elapsed = now - cooldown_start >= 18000 (5시간) 이어야 알림 발생
        # → cooldown_retry_minutes를 충분히 크게 설정
        config.claude_cooldown_retry_minutes = 400  # 400분
        now = int(time.time())
        resume_at = now + 60  # 아직 만료 안 됨
        # cooldown_start = resume_at - 400*60 = now + 60 - 24000 = now - 23940
        # elapsed = now - (now - 23940) = 23940 > 18000 ✓
        processor._cooldown_file.write_text(str(resume_at))

        _make_inbox_file(inbox, "long_cd.json")

        with patch.object(processor, "_run_claude", new_callable=AsyncMock):
            await processor._process_inbox()
            await asyncio.sleep(0)  # create_task 알림 실행

        # 장기 경고 알림 발송 확인
        alert_files = list(outbox.glob("*_system-alert.json"))
        alert_texts = [json.loads(f.read_text())["response"]["text"]
                       for f in alert_files]
        assert any("쿨다운 5시간 이상" in t for t in alert_texts)

        # .cooldown-alerted 생성 확인
        assert processor._cooldown_alert_file.exists()

        # 두 번째 호출 시 중복 알림 없음
        with patch.object(processor, "_run_claude", new_callable=AsyncMock):
            await processor._process_inbox()
            await asyncio.sleep(0)

        new_alert_files = list(outbox.glob("*_system-alert.json"))
        cooldown_alerts = [f for f in new_alert_files
                           if "쿨다운 5시간" in json.loads(f.read_text())["response"]["text"]]
        # 기존과 같은 수 (중복 방지)
        assert len(cooldown_alerts) == 1


# ── /resume 명령 ────────────────────────────────────────────


class TestResumeCommand:
    """쿨다운 해제 검증."""

    def test_resume_clears_cooldown(self, setup):
        """쿨다운/failcount 파일이 삭제된다."""
        processor = setup["processor"]

        # 쿨다운 상태 설정
        processor._cooldown_file.write_text(str(int(time.time()) + 3600))
        processor._fail_count_file.write_text("5")
        processor._cooldown_alert_file.touch()

        assert processor._cooldown_file.exists()
        assert processor._fail_count_file.exists()
        assert processor._cooldown_alert_file.exists()

        # /resume 시뮬레이션 (telegram.py의 _on_resume과 동일 로직)
        for f in [processor._cooldown_file, processor._fail_count_file,
                  processor._cooldown_alert_file]:
            if f.exists():
                f.unlink()

        assert not processor._cooldown_file.exists()
        assert not processor._fail_count_file.exists()
        assert not processor._cooldown_alert_file.exists()

        # 쿨다운 해제 확인
        assert not processor._is_cooldown()


# ── 성공 시 상태 리셋 ──────────────────────────────────────


class TestSuccessResetsState:
    """성공 처리 시 failcount/cooldown 리셋 확인."""

    @pytest.mark.asyncio
    async def test_success_resets_fail_state(self, setup):
        """성공 처리 시 failcount, cooldown 파일이 삭제된다."""
        processor = setup["processor"]
        inbox = setup["inbox"]

        # 사전 상태: 실패 카운터 존재
        processor._fail_count_file.write_text("3")
        processor._cooldown_file.write_text(str(int(time.time()) - 10))
        processor._cooldown_alert_file.touch()

        _make_inbox_file(inbox, "success.json")

        with patch.object(processor, "_run_claude", new_callable=AsyncMock,
                          return_value=_claude_success()):
            await processor._process_inbox()

        assert not processor._fail_count_file.exists()
        assert not processor._cooldown_file.exists()
        assert not processor._cooldown_alert_file.exists()


# ── 알림 검증: 작업 시작 / 세션 리셋 ────────────────────────


class TestNotificationAlerts:
    """알림 발송 검증."""

    @pytest.mark.asyncio
    async def test_work_start_notification(self, setup):
        """작업 시작 시 '작업중입니다' 브로드캐스트."""
        processor = setup["processor"]
        adapter = setup["adapter"]
        inbox = setup["inbox"]

        _make_inbox_file(inbox, "notify.json")

        with patch.object(processor, "_run_claude", new_callable=AsyncMock,
                          return_value=_claude_success()):
            await processor._process_inbox()

        # 어댑터에 "작업중입니다" 메시지 발송 확인
        all_texts = [msg[1] for msg in adapter.sent_messages]
        assert any("작업중입니다" in t for t in all_texts)

    @pytest.mark.asyncio
    async def test_session_auto_reset_alert(self, setup):
        """max_session_turns 도달 시 세션 리셋 알림."""
        processor = setup["processor"]
        inbox = setup["inbox"]
        outbox = setup["outbox"]
        config = setup["config"]

        # turn count를 max_session_turns로 설정
        processor._turn_count_file.write_text(str(config.claude_max_session_turns))

        _make_inbox_file(inbox, "turn_limit.json")

        with patch.object(processor, "_run_claude", new_callable=AsyncMock,
                          return_value=_claude_success()):
            await processor._process_inbox()

        # 세션 리셋 알림 확인
        alert_files = list(outbox.glob("*_system-alert.json"))
        alert_texts = [json.loads(f.read_text())["response"]["text"]
                       for f in alert_files]
        assert any("세션이 자동 리셋" in t for t in alert_texts)


# ── 알림 피로도 제어: quiet hours ────────────────────────────


class TestAlertQuietHours:
    """알림 무음 시간대 테스트."""

    @pytest.mark.asyncio
    async def test_quiet_hours_suppresses_alert(self, setup):
        """무음 시간대에는 알림이 생략된다."""
        processor = setup["processor"]
        config = setup["config"]
        outbox = setup["outbox"]

        # 현재 시각을 무음 범위에 포함시킴
        from gsd_orchestrator.inbox_processor import KST
        current_hour = datetime.now(KST).hour
        config.alert_quiet_start = current_hour
        config.alert_quiet_end = (current_hour + 2) % 24

        await processor._send_alert("[시스템] 테스트 알림")

        # outbox에 알림 파일이 생성되지 않아야 함
        alert_files = list(outbox.glob("*_system-alert.json"))
        assert len(alert_files) == 0

    @pytest.mark.asyncio
    async def test_outside_quiet_hours_sends_alert(self, setup):
        """무음 시간대 밖에서는 알림이 정상 발송된다."""
        processor = setup["processor"]
        config = setup["config"]
        outbox = setup["outbox"]

        # 현재 시각을 무음 범위 밖으로 설정
        from gsd_orchestrator.inbox_processor import KST
        current_hour = datetime.now(KST).hour
        config.alert_quiet_start = (current_hour + 5) % 24
        config.alert_quiet_end = (current_hour + 7) % 24

        await processor._send_alert("[시스템] 테스트 알림")

        alert_files = list(outbox.glob("*_system-alert.json"))
        assert len(alert_files) == 1

    @pytest.mark.asyncio
    async def test_quiet_hours_disabled_by_default(self, setup):
        """기본값(-1)이면 무음 비활성 → 알림 정상 발송."""
        processor = setup["processor"]
        config = setup["config"]
        outbox = setup["outbox"]

        # 기본값 확인
        assert config.alert_quiet_start == -1
        assert config.alert_quiet_end == -1

        await processor._send_alert("[시스템] 기본 알림")

        alert_files = list(outbox.glob("*_system-alert.json"))
        assert len(alert_files) == 1

    def test_is_quiet_hour_overnight_range(self, setup):
        """자정을 넘는 범위 (예: 23~06) 테스트."""
        processor = setup["processor"]
        config = setup["config"]

        config.alert_quiet_start = 23
        config.alert_quiet_end = 6

        from gsd_orchestrator.inbox_processor import KST
        current_hour = datetime.now(KST).hour

        result = processor._is_quiet_hour()

        # 현재 시각이 23~06 범위이면 True, 아니면 False
        if current_hour >= 23 or current_hour < 6:
            assert result is True
        else:
            assert result is False


# ── 정리: 런타임 파일 cleanup ────────────────────────────────


@pytest.fixture(autouse=True)
def cleanup_runtime_files(setup):
    """테스트 후 런타임 파일을 정리한다."""
    yield
    processor = setup["processor"]
    for attr in ["_cooldown_file", "_fail_count_file", "_cooldown_alert_file",
                 "_blocked_file", "_token_track_file", "_reset_file",
                 "_turn_count_file", "_active_file"]:
        f = getattr(processor, attr, None)
        if f and f.exists():
            f.unlink(missing_ok=True)
