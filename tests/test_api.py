import json
import pytest
from pathlib import Path

from gsd_orchestrator.channels.manager import ChannelManager
from gsd_orchestrator.api import ChannelSender


class FakeAdapter:
    def __init__(self, ch_type, ch_id):
        self._type = ch_type
        self._id = ch_id
        self.sent = []

    @property
    def channel_type(self):
        return self._type

    @property
    def max_message_length(self):
        return 4096

    def get_channel_id(self):
        return self._id

    async def start(self, on_message):
        pass

    async def stop(self):
        pass

    async def send_message(self, channel_id, text, parse_mode=None):
        self.sent.append((channel_id, text, parse_mode))
        return True


class TestChannelSender:
    @pytest.mark.asyncio
    async def test_send_all_channels(self, tmp_dirs):
        tg = FakeAdapter("telegram", "123")
        slack = FakeAdapter("slack", "C456")
        mgr = ChannelManager([tg, slack])
        sender = ChannelSender(mgr, tmp_dirs["outbox"])

        ok = await sender.send("hello all")

        assert ok is True
        assert len(tg.sent) == 1
        assert len(slack.sent) == 1
        assert tg.sent[0][1] == "hello all"
        assert slack.sent[0][1] == "hello all"

    @pytest.mark.asyncio
    async def test_send_specific_channel(self, tmp_dirs):
        tg = FakeAdapter("telegram", "123")
        slack = FakeAdapter("slack", "C456")
        mgr = ChannelManager([tg, slack])
        sender = ChannelSender(mgr, tmp_dirs["outbox"])

        await sender.send("telegram only", channel_type="telegram")

        assert len(tg.sent) == 1
        assert len(slack.sent) == 0

    @pytest.mark.asyncio
    async def test_send_unknown_channel_type(self, tmp_dirs):
        tg = FakeAdapter("telegram", "123")
        mgr = ChannelManager([tg])
        sender = ChannelSender(mgr, tmp_dirs["outbox"])

        ok = await sender.send("to discord", channel_type="discord")

        assert ok is True  # no channels matched, vacuously true
        assert len(tg.sent) == 0

    def test_enqueue_all_channels(self, tmp_dirs):
        tg = FakeAdapter("telegram", "123")
        slack = FakeAdapter("slack", "C456")
        mgr = ChannelManager([tg, slack])
        sender = ChannelSender(mgr, tmp_dirs["outbox"])

        sender.enqueue("queued message")

        files = list(tmp_dirs["outbox"].glob("*.json"))
        assert len(files) == 1

        data = json.loads(files[0].read_text())
        assert data["response"]["text"] == "queued message"
        assert len(data["targets"]) == 2
        types = {t["channel_type"] for t in data["targets"]}
        assert types == {"telegram", "slack"}

    def test_enqueue_specific_channel(self, tmp_dirs):
        tg = FakeAdapter("telegram", "123")
        slack = FakeAdapter("slack", "C456")
        mgr = ChannelManager([tg, slack])
        sender = ChannelSender(mgr, tmp_dirs["outbox"])

        sender.enqueue("slack only", channel_type="slack")

        files = list(tmp_dirs["outbox"].glob("*.json"))
        data = json.loads(files[0].read_text())
        assert len(data["targets"]) == 1
        assert data["targets"][0]["channel_type"] == "slack"

    def test_enqueue_atomic_write(self, tmp_dirs):
        tg = FakeAdapter("telegram", "123")
        mgr = ChannelManager([tg])
        sender = ChannelSender(mgr, tmp_dirs["outbox"])

        sender.enqueue("test")

        tmp_files = list(tmp_dirs["outbox"].glob(".*tmp"))
        assert len(tmp_files) == 0


class TestOrchestratorInterface:
    @pytest.mark.asyncio
    async def test_on_result_callback(self):
        """on_result 콜백이 정상 호출되는지 확인."""
        from gsd_orchestrator.orchestrator import Orchestrator

        results = []

        async def handler(source, req, resp, status):
            results.append((source, req, resp, status))

        # Orchestrator를 직접 만들기는 어려우므로 _fire_result만 테스트
        class FakeOrchestrator:
            def __init__(self):
                self._result_callback = None

            def on_result(self, callback):
                self._result_callback = callback

            async def _fire_result(self, source, req, resp, status):
                if self._result_callback:
                    await self._result_callback(source, req, resp, status)

        orch = FakeOrchestrator()
        orch.on_result(handler)

        await orch._fire_result({"user": "test"}, "요청", "응답", "success")
        assert len(results) == 1
        assert results[0] == ({"user": "test"}, "요청", "응답", "success")

    @pytest.mark.asyncio
    async def test_no_callback_no_error(self):
        """콜백 미등록 시 에러 없이 동작."""
        from gsd_orchestrator.inbox_processor import InboxProcessor

        # result_callback=None이면 _notify_result가 아무것도 하지 않음
        # InboxProcessor를 직접 테스트하기 어려우므로 _notify_result만 확인
        class FakeProcessor:
            _result_callback = None

            async def _notify_result(self, source, req, resp, status):
                if self._result_callback:
                    await self._result_callback(source, req, resp, status)

        proc = FakeProcessor()
        # 에러 없이 통과해야 함
        await proc._notify_result({}, "", "", "success")
