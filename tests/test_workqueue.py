"""GSD 작업 큐 테스트.

GSD-작업큐-설계서.md의 시나리오를 검증한다.
복잡한 작업을 단위별로 분할하여 순차 실행하는 흐름을 테스트한다.
"""

import asyncio
import json
import os
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from gsd_orchestrator.channels.base import ChannelAdapter
from gsd_orchestrator.channels.manager import ChannelManager
from gsd_orchestrator.config import Config
from gsd_orchestrator.inbox_processor import InboxProcessor, MAX_FILE_FAILURES


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


def _claude_success(text="완료"):
    return {
        "type": "result", "subtype": "success", "result": text,
        "usage": {"input_tokens": 100, "output_tokens": 50,
                  "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
        "modelUsage": {"claude-sonnet-4-20250514": {}},
        "total_cost_usd": 0.001, "duration_ms": 1000, "stop_reason": "end_turn",
    }


SAMPLE_PLAN = """# GSD 작업 계획
## 원본 요청
kospi 시뮬레이션 해주세요

## 단위 작업
- [ ] Unit 1: 시뮬레이션 엔진 구현
  - 범위: daily_ohlcv 기반 일중 매매 시뮬레이션
  - 대상: src/backtest/daily_engine.py
- [ ] Unit 2: 수익 모델 정의
  - 범위: 4가지 전략 구현 (수급추종, Mean Reversion, Momentum, Multi-Factor)
  - 대상: src/backtest/strategies/
- [ ] Unit 3: 시뮬레이션 실행
  - 범위: 4개 모델 비교 실행
  - 대상: scripts/run_simulation.py
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
  history_max_messages: 3

paths:
  inbox_dir: messages/inbox
  outbox_dir: messages/outbox
  sent_dir: messages/sent
  error_dir: messages/error
  archive_dir: messages/archive
  workqueue_dir: messages/workqueue
  plan_dir: messages/plan
""")

    for d in ["messages/inbox", "messages/outbox", "messages/sent",
              "messages/error", "messages/archive", "messages/workqueue",
              "messages/plan", "workspace"]:
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
        "workqueue": config.workqueue_dir,
        "workspace": Path(config.claude_working_dir),
    }


# ── 계획서 파싱 ──────────────────────────────────────────────


class TestParsePlan:
    def test_parses_plan_units(self, setup):
        """계획서에서 Unit 목록을 올바르게 추출한다."""
        processor = setup["processor"]
        processor._plan_file.write_text(SAMPLE_PLAN)

        units = processor._parse_plan()

        assert len(units) == 3
        assert units[0]["unit_number"] == 1
        assert units[0]["title"] == "시뮬레이션 엔진 구현"
        assert "daily_ohlcv" in units[0]["description"]
        assert units[1]["unit_number"] == 2
        assert units[2]["unit_number"] == 3

    def test_parses_simple_format(self, setup):
        """범위/대상 없는 간단한 형식도 파싱한다."""
        processor = setup["processor"]
        processor._plan_file.write_text(
            "# GSD 작업 계획\n"
            "## 단위 작업\n"
            "- [ ] Unit 1: 첫 번째 작업\n"
            "- [ ] Unit 2: 두 번째 작업\n"
        )

        units = processor._parse_plan()

        assert len(units) == 2
        assert units[0]["title"] == "첫 번째 작업"
        assert units[1]["title"] == "두 번째 작업"

    def test_skips_completed_units(self, setup):
        """완료된 Unit([x])은 건너뛴다."""
        processor = setup["processor"]
        processor._plan_file.write_text(
            "- [x] Unit 1: 완료된 작업\n"
            "- [ ] Unit 2: 미완료 작업\n"
        )

        units = processor._parse_plan()

        assert len(units) == 1
        assert units[0]["title"] == "미완료 작업"

    def test_empty_plan_returns_empty(self, setup):
        """빈 계획서는 빈 리스트를 반환한다."""
        processor = setup["processor"]
        processor._plan_file.write_text("# 빈 계획서\n")

        units = processor._parse_plan()
        assert len(units) == 0

    def test_no_plan_file_returns_empty(self, setup):
        """계획서 파일이 없으면 빈 리스트를 반환한다."""
        processor = setup["processor"]
        assert processor._parse_plan() == []


# ── 작업 큐 생성 ─────────────────────────────────────────────


class TestEnqueuePlanUnits:
    def test_creates_workqueue_files(self, setup):
        """Unit 목록으로부터 workqueue/ 파일을 생성한다."""
        processor = setup["processor"]
        workqueue = setup["workqueue"]

        units = [
            {"unit_number": 1, "title": "엔진", "description": "구현", "target_files": "a.py"},
            {"unit_number": 2, "title": "모델", "description": "정의", "target_files": "b.py"},
        ]
        source = {"channel_type": "telegram", "channel_id": "123"}
        data = {"keyword": "테스트"}

        processor._enqueue_plan_units(units, source, data)

        files = sorted(workqueue.glob("*.json"))
        assert len(files) == 2
        assert files[0].name == "001_unit1.json"
        assert files[1].name == "002_unit2.json"

        item = json.loads(files[0].read_text())
        assert item["unit_number"] == 1
        assert item["title"] == "엔진"
        assert item["total_units"] == 2
        assert item["status"] == "pending"


# ── 작업 큐 실행 ─────────────────────────────────────────────


class TestWorkqueueExecution:
    @pytest.mark.asyncio
    async def test_executes_unit_sequentially(self, setup):
        """작업 큐에서 순차적으로 Unit을 실행한다."""
        processor = setup["processor"]
        workqueue = setup["workqueue"]
        outbox = setup["outbox"]

        # 계획서 생성
        processor._plan_file.write_text(SAMPLE_PLAN)

        # workqueue 파일 생성
        units = processor._parse_plan()
        source = {"channel_type": "telegram", "channel_id": "123",
                  "user_id": "U1", "user_name": "김철수"}
        processor._enqueue_plan_units(units, source, {"keyword": "시뮬레이션"})

        with patch.object(processor, "_run_claude", new_callable=AsyncMock,
                          return_value=_claude_success("Unit 1 완료")):
            await processor._process_workqueue()

        # Unit 1 처리됨, 2개 남음
        remaining = list(workqueue.glob("*.json"))
        assert len(remaining) == 2  # Unit 2, 3

        # outbox에 결과 발송
        result_files = list(outbox.glob("*_wq-result.json"))
        assert len(result_files) == 1
        result = json.loads(result_files[0].read_text())
        assert "Unit 1/3 완료" in result["response"]["text"]

    @pytest.mark.asyncio
    async def test_all_units_complete(self, setup):
        """모든 Unit 완료 시 최종 보고 + 정리."""
        processor = setup["processor"]
        workqueue = setup["workqueue"]
        outbox = setup["outbox"]

        processor._plan_file.write_text(SAMPLE_PLAN)

        # 단일 Unit만 큐에 넣기
        units = [{"unit_number": 1, "title": "마지막 작업",
                  "description": "완료", "target_files": ""}]
        source = {"channel_type": "telegram", "channel_id": "123"}
        processor._enqueue_plan_units(units, source, {"keyword": "완료"})

        with patch.object(processor, "_run_claude", new_callable=AsyncMock,
                          return_value=_claude_success("완료")):
            await processor._process_workqueue()

        # workqueue 비었음
        assert len(list(workqueue.glob("*.json"))) == 0

        # 최종 보고 메시지에 "전체 완료" 포함
        result_files = list(outbox.glob("*_wq-result.json"))
        result = json.loads(result_files[0].read_text())
        assert "전체 완료" in result["response"]["text"]

        # 계획서 삭제
        assert not processor._plan_file.exists()

    @pytest.mark.asyncio
    async def test_unit_failure_retries(self, setup):
        """Unit 실패 시 재시도 대기."""
        processor = setup["processor"]
        workqueue = setup["workqueue"]

        processor._plan_file.write_text(SAMPLE_PLAN)
        units = [{"unit_number": 1, "title": "실패 작업",
                  "description": "", "target_files": ""}]
        source = {"channel_type": "telegram", "channel_id": "123"}
        processor._enqueue_plan_units(units, source, {"keyword": "실패"})

        with patch.object(processor, "_run_claude", new_callable=AsyncMock,
                          return_value=None):
            await processor._process_workqueue()

        # 큐에 여전히 존재 (재시도 대기)
        assert len(list(workqueue.glob("*.json"))) == 1
        # failcount 생성
        fc_files = list(workqueue.glob("*.failcount"))
        assert len(fc_files) == 1

    @pytest.mark.asyncio
    async def test_unit_failure_skip_after_max(self, setup):
        """Unit 3회 실패 시 skip + 알림."""
        processor = setup["processor"]
        workqueue = setup["workqueue"]
        outbox = setup["outbox"]

        processor._plan_file.write_text(SAMPLE_PLAN)
        units = [{"unit_number": 1, "title": "불가능한 작업",
                  "description": "", "target_files": ""}]
        source = {"channel_type": "telegram", "channel_id": "123",
                  "user_id": "U1", "user_name": "김철수"}
        processor._enqueue_plan_units(units, source, {"keyword": "실패"})

        with patch.object(processor, "_run_claude", new_callable=AsyncMock,
                          return_value=None):
            for _ in range(MAX_FILE_FAILURES):
                await processor._process_workqueue()
                await asyncio.sleep(0)

        # 큐에서 제거됨 (skip)
        assert len(list(workqueue.glob("*.json"))) == 0
        assert len(list(workqueue.glob("*.failcount"))) == 0

        # skip 알림
        alert_files = list(outbox.glob("*_system-alert.json"))
        alert_texts = [json.loads(f.read_text())["response"]["text"]
                       for f in alert_files]
        assert any("건너뛰고" in t for t in alert_texts)

    @pytest.mark.asyncio
    async def test_empty_workqueue_noop(self, setup):
        """빈 작업 큐는 아무것도 하지 않는다."""
        processor = setup["processor"]

        with patch.object(processor, "_run_claude", new_callable=AsyncMock) as mock:
            await processor._process_workqueue()

        mock.assert_not_called()


# ── 계획서 감지 + 작업 큐 트리거 ──────────────────────────────


class TestPlanToWorkqueue:
    @pytest.mark.asyncio
    async def test_plan_triggers_workqueue_on_confirm(self, setup):
        """계획서 존재 + gsd-resume → 작업 큐 생성."""
        processor = setup["processor"]
        inbox = setup["inbox"]
        workqueue = setup["workqueue"]

        # 계획서 생성
        processor._plan_file.write_text(SAMPLE_PLAN)

        # 사용자 확인 메시지
        _make_inbox_file(inbox, "confirm.json",
                         text="네 진행해주세요", mode="gsd-resume")

        with patch.object(processor, "_run_claude", new_callable=AsyncMock,
                          return_value=_claude_success("계획 보고")), \
             patch.object(processor, "_check_gsd_blockers", return_value=""):
            await processor._process_inbox()

        # workqueue 생성 확인
        wq_files = sorted(workqueue.glob("*.json"))
        assert len(wq_files) == 3  # 3개 Unit

    @pytest.mark.asyncio
    async def test_no_plan_uses_normal_resume(self, setup):
        """계획서 없으면 기존 gsd-resume 로직 사용."""
        processor = setup["processor"]
        inbox = setup["inbox"]
        workqueue = setup["workqueue"]

        # 계획서 없음
        assert not processor._plan_file.exists()

        _make_inbox_file(inbox, "resume.json",
                         text="네 진행해주세요", mode="gsd-resume")

        processor._gsd_active_file.write_text(str(os.getpid()))

        with patch.object(processor, "_run_claude", new_callable=AsyncMock,
                          return_value=_claude_success("결과")), \
             patch.object(processor, "_check_gsd_blockers", return_value=""):
            await processor._process_inbox()

        # workqueue 생성 안 됨
        assert len(list(workqueue.glob("*.json"))) == 0


# ── 정리 ─────────────────────────────────────────────────────


class TestWorkqueueCleanup:
    def test_new_gsd_clears_workqueue(self, setup):
        """새 /gsd 명령 시 기존 작업 큐와 계획서가 삭제된다."""
        processor = setup["processor"]
        workqueue = setup["workqueue"]

        # 기존 큐 + 계획서
        processor._plan_file.write_text(SAMPLE_PLAN)
        (workqueue / "001_unit1.json").write_text("{}")
        (workqueue / "002_unit2.json").write_text("{}")

        processor._clear_workqueue()
        processor._plan_file.unlink(missing_ok=True)

        assert len(list(workqueue.glob("*.json"))) == 0
        assert not processor._plan_file.exists()

    def test_clear_gsd_active_also_clears_workqueue(self, setup):
        """clear_gsd_active()가 작업 큐도 정리한다."""
        processor = setup["processor"]
        workqueue = setup["workqueue"]

        processor._plan_file.write_text(SAMPLE_PLAN)
        (workqueue / "001_unit1.json").write_text("{}")
        processor._gsd_active_file.write_text(str(os.getpid()))

        processor.clear_gsd_active()

        assert not processor._gsd_active_file.exists()
        assert len(list(workqueue.glob("*.json"))) == 0
        assert not processor._plan_file.exists()

    @pytest.mark.asyncio
    async def test_workqueue_survives_restart(self, setup):
        """프로세스 재시작 후에도 workqueue 파일이 남아있어 재개 가능."""
        processor = setup["processor"]
        workqueue = setup["workqueue"]

        # workqueue에 파일 직접 생성 (재시작 시뮬레이션)
        item = {
            "unit_number": 2, "total_units": 3,
            "title": "재개 작업", "description": "이어서 진행",
            "target_files": "", "plan_file": str(processor._plan_file),
            "source": {"channel_type": "telegram", "channel_id": "123",
                       "user_id": "U1", "user_name": "김철수"},
            "keyword": "재개", "status": "pending",
        }
        (workqueue / "002_unit2.json").write_text(
            json.dumps(item, ensure_ascii=False))

        processor._plan_file.write_text(SAMPLE_PLAN)

        with patch.object(processor, "_run_claude", new_callable=AsyncMock,
                          return_value=_claude_success("Unit 2 완료")):
            await processor._process_workqueue()

        # 처리됨
        assert not (workqueue / "002_unit2.json").exists()


# ── 정리: 런타임 파일 ────────────────────────────────────────


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
    processor._plan_file.unlink(missing_ok=True)
    processor._clear_workqueue()
