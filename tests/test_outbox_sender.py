import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock

from gsd_orchestrator.channels.base import ChannelAdapter
from gsd_orchestrator.channels.manager import ChannelManager
from gsd_orchestrator.outbox_sender import OutboxSender


class FakeAdapter(ChannelAdapter):
    """테스트용 가짜 어댑터."""

    def __init__(self, ch_type: str, ch_id: str, send_ok: bool = True):
        self._type = ch_type
        self._id = ch_id
        self._send_ok = send_ok
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
        return self._send_ok


def _make_outbox_file(outbox_dir: Path, filename: str, data: dict) -> Path:
    path = outbox_dir / filename
    path.write_text(json.dumps(data, ensure_ascii=False))
    return path


def _valid_message_data(text="테스트 응답"):
    return {
        "id": "test-uuid",
        "source": {
            "channel_type": "telegram",
            "channel_id": "123",
            "user_id": "U1",
            "user_name": "김철수",
            "message_id": 1,
            "thread_ts": None,
        },
        "targets": [
            {"channel_type": "telegram", "channel_id": "123", "is_origin": True},
        ],
        "retry_count": 0,
        "keyword": "테스트",
        "mode": "default",
        "request": {"text": "테스트 요청", "timestamp": "2026-03-19T14:30:00+09:00"},
        "response": {"text": text, "parse_mode": "HTML", "timestamp": "2026-03-19T14:31:00+09:00"},
    }


@pytest.fixture
def tg_adapter():
    return FakeAdapter("telegram", "123")


@pytest.fixture
def sender(tg_adapter, tmp_dirs):
    manager = ChannelManager([tg_adapter])
    return OutboxSender(
        channel_manager=manager,
        outbox_dir=tmp_dirs["outbox"],
        sent_dir=tmp_dirs["sent"],
        error_dir=tmp_dirs["error"],
        interval=1,
        snippet_length=500,
    )


class TestOutboxSender:
    @pytest.mark.asyncio
    async def test_send_success(self, sender, tg_adapter, tmp_dirs):
        data = _valid_message_data()
        _make_outbox_file(tmp_dirs["outbox"], "test.json", data)

        await sender._process_outbox()

        assert len(tg_adapter.sent_messages) == 1
        assert (tmp_dirs["sent"] / "test.json").exists()
        assert not (tmp_dirs["outbox"] / "test.json").exists()

    @pytest.mark.asyncio
    async def test_json_parse_error_moves_to_error(self, sender, tmp_dirs):
        (tmp_dirs["outbox"] / "bad.json").write_text("{invalid json")

        await sender._process_outbox()

        assert (tmp_dirs["error"] / "bad.json").exists()

    @pytest.mark.asyncio
    async def test_missing_response_moves_to_error(self, sender, tmp_dirs):
        data = _valid_message_data()
        data["response"] = None
        _make_outbox_file(tmp_dirs["outbox"], "no_resp.json", data)

        await sender._process_outbox()

        assert (tmp_dirs["error"] / "no_resp.json").exists()

    @pytest.mark.asyncio
    async def test_empty_response_text_moves_to_error(self, sender, tmp_dirs):
        data = _valid_message_data()
        data["response"]["text"] = ""
        _make_outbox_file(tmp_dirs["outbox"], "empty.json", data)

        await sender._process_outbox()

        assert (tmp_dirs["error"] / "empty.json").exists()

    @pytest.mark.asyncio
    async def test_send_failure_retries(self, tmp_dirs):
        """발송 실패 시 retry_count 증가 후 outbox 복원."""
        fail_adapter = FakeAdapter("telegram", "123", send_ok=False)
        manager = ChannelManager([fail_adapter])
        sender = OutboxSender(
            channel_manager=manager,
            outbox_dir=tmp_dirs["outbox"],
            sent_dir=tmp_dirs["sent"],
            error_dir=tmp_dirs["error"],
            interval=1,
        )

        data = _valid_message_data()
        _make_outbox_file(tmp_dirs["outbox"], "retry.json", data)

        await sender._process_outbox()

        # outbox에 복원되어야 함
        assert (tmp_dirs["outbox"] / "retry.json").exists()
        restored = json.loads((tmp_dirs["outbox"] / "retry.json").read_text())
        assert restored["retry_count"] == 1

    @pytest.mark.asyncio
    async def test_max_retries_moves_to_error(self, tmp_dirs):
        """최대 재시도 초과 시 error/로 이관."""
        fail_adapter = FakeAdapter("telegram", "123", send_ok=False)
        manager = ChannelManager([fail_adapter])
        sender = OutboxSender(
            channel_manager=manager,
            outbox_dir=tmp_dirs["outbox"],
            sent_dir=tmp_dirs["sent"],
            error_dir=tmp_dirs["error"],
            interval=1,
        )

        data = _valid_message_data()
        data["retry_count"] = 3  # MAX_OUTBOX_RETRIES
        _make_outbox_file(tmp_dirs["outbox"], "maxretry.json", data)

        await sender._process_outbox()

        assert (tmp_dirs["error"] / "maxretry.json").exists()
        assert not (tmp_dirs["outbox"] / "maxretry.json").exists()

    @pytest.mark.asyncio
    async def test_broadcast_snippet(self, tmp_dirs):
        """상대 채널 브로드캐스트는 snippet_length로 잘림."""
        tg = FakeAdapter("telegram", "123")
        slack = FakeAdapter("slack", "C456")
        manager = ChannelManager([tg, slack])
        sender = OutboxSender(
            channel_manager=manager,
            outbox_dir=tmp_dirs["outbox"],
            sent_dir=tmp_dirs["sent"],
            error_dir=tmp_dirs["error"],
            interval=1,
            snippet_length=20,
        )

        long_text = "A" * 100
        data = _valid_message_data(text=long_text)
        data["targets"] = [
            {"channel_type": "telegram", "channel_id": "123", "is_origin": True},
            {"channel_type": "slack", "channel_id": "C456", "is_origin": False},
        ]
        _make_outbox_file(tmp_dirs["outbox"], "broadcast.json", data)

        await sender._process_outbox()

        # telegram (origin): 전체 텍스트
        assert tg.sent_messages[0][1] == long_text
        # slack (broadcast): 20자 + snippet 접미어
        slack_text = slack.sent_messages[0][1]
        assert slack_text.startswith("A" * 20)
        assert "전체 내용은 telegram에서 확인하세요" in slack_text

    @pytest.mark.asyncio
    async def test_backward_compat_no_targets(self, sender, tg_adapter, tmp_dirs):
        """targets 없는 기존 포맷도 정상 발송."""
        data = {
            "id": "old-uuid",
            "chat_id": "123",
            "message_id": 1,
            "keyword": "test",
            "mode": "default",
            "request": {"text": "req", "timestamp": "2026-03-19T14:30:00+09:00"},
            "response": {"text": "old response", "parse_mode": "HTML",
                         "timestamp": "2026-03-19T14:31:00+09:00"},
        }
        _make_outbox_file(tmp_dirs["outbox"], "old.json", data)

        await sender._process_outbox()

        assert (tmp_dirs["sent"] / "old.json").exists()
        assert len(tg_adapter.sent_messages) == 1

    @pytest.mark.asyncio
    async def test_processes_files_in_order(self, sender, tg_adapter, tmp_dirs):
        for name in ["c.json", "a.json", "b.json"]:
            _make_outbox_file(tmp_dirs["outbox"], name, _valid_message_data(text=f"msg-{name}"))

        await sender._process_outbox()

        texts = [msg[1] for msg in tg_adapter.sent_messages]
        assert texts == ["msg-a.json", "msg-b.json", "msg-c.json"]

    @pytest.mark.asyncio
    async def test_empty_outbox(self, sender, tg_adapter):
        await sender._process_outbox()
        assert len(tg_adapter.sent_messages) == 0
