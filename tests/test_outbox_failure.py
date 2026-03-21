"""OutboxSender 장애 복구 테스트.

장애-복구-가이드.md의 카테고리 C-4, D-1~6을 검증한다.
"""

import json
import pytest
from pathlib import Path

from gsd_orchestrator.channels.base import ChannelAdapter
from gsd_orchestrator.channels.manager import ChannelManager
from gsd_orchestrator.outbox_sender import OutboxSender, MAX_OUTBOX_RETRIES


# ── 테스트 인프라 ──────────────────────────────────────────


class FakeAdapter(ChannelAdapter):
    def __init__(self, ch_type: str = "telegram", ch_id: str = "123",
                 send_ok: bool = True):
        self._type = ch_type
        self._id = ch_id
        self._send_ok = send_ok
        self.sent_messages: list[tuple[str, str, str | None]] = []

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
        self.sent_messages.append((channel_id, text, parse_mode))
        return self._send_ok


class HtmlFailAdapter(ChannelAdapter):
    """첫 번째 발송(HTML)은 실패, 두 번째(plain text)는 성공하는 어댑터."""

    def __init__(self, ch_type: str = "telegram", ch_id: str = "123"):
        self._type = ch_type
        self._id = ch_id
        self.sent_messages: list[tuple[str, str, str | None]] = []

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
        self.sent_messages.append((channel_id, text, parse_mode))
        if parse_mode == "HTML":
            return False  # HTML 파싱 에러 시뮬레이션
        return True  # plain text는 성공


def _valid_outbox_data(text="테스트 응답", retry_count=0):
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
        "retry_count": retry_count,
        "keyword": "테스트",
        "request": {"text": "요청", "timestamp": "2026-03-19T14:30:00+09:00"},
        "response": {"text": text, "parse_mode": "HTML",
                     "timestamp": "2026-03-19T14:31:00+09:00"},
    }


@pytest.fixture
def dirs(tmp_path):
    d = {
        "outbox": tmp_path / "outbox",
        "sent": tmp_path / "sent",
        "error": tmp_path / "error",
    }
    for p in d.values():
        p.mkdir()
    return d


# ── C-4: .sending 복구 ─────────────────────────────────────


class TestSendingRecovery:
    """C-4: .sending 파일 정체 복구."""

    def test_recover_stale_sending(self, dirs):
        adapter = FakeAdapter()
        manager = ChannelManager([adapter])
        sender = OutboxSender(manager, dirs["outbox"], dirs["sent"],
                              dirs["error"], interval=1)

        # .sending 파일 직접 생성
        data = _valid_outbox_data()
        (dirs["outbox"] / "msg.json.sending").write_text(json.dumps(data))

        sender._recover_stale_sending()

        assert (dirs["outbox"] / "msg.json").exists()
        assert not (dirs["outbox"] / "msg.json.sending").exists()

    def test_recover_multiple_sending(self, dirs):
        """여러 .sending 파일을 모두 복원한다."""
        adapter = FakeAdapter()
        manager = ChannelManager([adapter])
        sender = OutboxSender(manager, dirs["outbox"], dirs["sent"],
                              dirs["error"], interval=1)

        for name in ["a.json.sending", "b.json.sending", "c.json.sending"]:
            (dirs["outbox"] / name).write_text(
                json.dumps(_valid_outbox_data()))

        sender._recover_stale_sending()

        for name in ["a.json", "b.json", "c.json"]:
            assert (dirs["outbox"] / name).exists()
        assert len(list(dirs["outbox"].glob("*.sending"))) == 0


# ── D-4: HTML 파싱 에러 fallback ────────────────────────────


class TestHtmlParseFallback:
    """D-4: HTML 파싱 실패 시 parse_mode=None으로 재시도."""

    @pytest.mark.asyncio
    async def test_html_parse_fallback_to_plain(self, dirs):
        adapter = HtmlFailAdapter()
        manager = ChannelManager([adapter])
        sender = OutboxSender(manager, dirs["outbox"], dirs["sent"],
                              dirs["error"], interval=1)

        data = _valid_outbox_data(text="<b>Bold</b> 텍스트")
        (dirs["outbox"] / "html.json").write_text(json.dumps(data))

        await sender._process_outbox()

        # sent/로 이동 (plain text fallback 성공)
        assert (dirs["sent"] / "html.json").exists()

        # 발송 내역 확인: HTML 실패 → plain 성공
        assert len(adapter.sent_messages) == 2
        assert adapter.sent_messages[0][2] == "HTML"   # 첫 시도
        assert adapter.sent_messages[1][2] is None      # fallback


# ── D-6: 부분 채널 실패 ─────────────────────────────────────


class TestPartialChannelFailure:
    """D-6: 멀티채널에서 한 채널만 실패."""

    @pytest.mark.asyncio
    async def test_partial_channel_failure_retries_failed_only(self, dirs):
        """성공 채널은 제외, 실패 채널만 재시도."""
        tg = FakeAdapter("telegram", "T123", send_ok=True)
        slack = FakeAdapter("slack", "C456", send_ok=False)
        manager = ChannelManager([tg, slack])
        sender = OutboxSender(manager, dirs["outbox"], dirs["sent"],
                              dirs["error"], interval=1, snippet_length=500)

        data = _valid_outbox_data()
        data["targets"] = [
            {"channel_type": "telegram", "channel_id": "T123", "is_origin": True},
            {"channel_type": "slack", "channel_id": "C456", "is_origin": False},
        ]
        (dirs["outbox"] / "partial.json").write_text(json.dumps(data))

        await sender._process_outbox()

        # outbox에 복원 (retry 대기)
        assert (dirs["outbox"] / "partial.json").exists()
        restored = json.loads((dirs["outbox"] / "partial.json").read_text())

        # slack만 남아있어야 함
        assert len(restored["targets"]) == 1
        assert restored["targets"][0]["channel_type"] == "slack"
        assert restored["retry_count"] == 1

    @pytest.mark.asyncio
    async def test_partial_failure_max_retries_quarantine(self, dirs):
        """부분 실패도 max retry 초과 시 error/ 격리."""
        tg = FakeAdapter("telegram", "T123", send_ok=True)
        slack = FakeAdapter("slack", "C456", send_ok=False)
        manager = ChannelManager([tg, slack])
        sender = OutboxSender(manager, dirs["outbox"], dirs["sent"],
                              dirs["error"], interval=1, snippet_length=500)

        data = _valid_outbox_data()
        data["retry_count"] = MAX_OUTBOX_RETRIES  # 이미 최대
        data["targets"] = [
            {"channel_type": "telegram", "channel_id": "T123", "is_origin": True},
            {"channel_type": "slack", "channel_id": "C456", "is_origin": False},
        ]
        (dirs["outbox"] / "maxpartial.json").write_text(json.dumps(data))

        await sender._process_outbox()

        assert (dirs["error"] / "maxpartial.json").exists()
        assert not (dirs["outbox"] / "maxpartial.json").exists()


# ── D-2: 영구적 발송 실패 ───────────────────────────────────


class TestPermanentFailure:
    """D-2: 모든 발송 실패 → 3회 후 error/ 격리."""

    @pytest.mark.asyncio
    async def test_permanent_failure_quarantine(self, dirs):
        """연속 실패 → retry → error/ 격리 전체 흐름."""
        fail_adapter = FakeAdapter("telegram", "123", send_ok=False)
        manager = ChannelManager([fail_adapter])
        sender = OutboxSender(manager, dirs["outbox"], dirs["sent"],
                              dirs["error"], interval=1)

        data = _valid_outbox_data()
        (dirs["outbox"] / "perm.json").write_text(json.dumps(data))

        # MAX_OUTBOX_RETRIES + 1회 시도 (0, 1, 2, 3 → 3에서 격리)
        for i in range(MAX_OUTBOX_RETRIES + 1):
            await sender._process_outbox()

        assert (dirs["error"] / "perm.json").exists()
        assert not (dirs["outbox"] / "perm.json").exists()

    @pytest.mark.asyncio
    async def test_retry_count_increments(self, dirs):
        """매 실패마다 retry_count가 정확히 증가한다."""
        fail_adapter = FakeAdapter("telegram", "123", send_ok=False)
        manager = ChannelManager([fail_adapter])
        sender = OutboxSender(manager, dirs["outbox"], dirs["sent"],
                              dirs["error"], interval=1)

        data = _valid_outbox_data()
        (dirs["outbox"] / "inc.json").write_text(json.dumps(data))

        for expected_count in range(1, MAX_OUTBOX_RETRIES + 1):
            await sender._process_outbox()
            if (dirs["outbox"] / "inc.json").exists():
                restored = json.loads(
                    (dirs["outbox"] / "inc.json").read_text())
                assert restored["retry_count"] == expected_count
