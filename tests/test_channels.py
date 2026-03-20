import pytest

from gsd_orchestrator.channels.base import ChannelAdapter
from gsd_orchestrator.channels.manager import ChannelManager


class FakeAdapter(ChannelAdapter):
    def __init__(self, ch_type: str, ch_id: str):
        self._type = ch_type
        self._id = ch_id
        self.sent: list[tuple[str, str]] = []

    @property
    def channel_type(self):
        return self._type

    def get_channel_id(self):
        return self._id

    async def start(self, on_message):
        pass

    async def stop(self):
        pass

    async def send_message(self, channel_id, text, parse_mode=None):
        self.sent.append((channel_id, text))
        return True


class TestChannelManager:
    def test_get_all_channels_single(self):
        tg = FakeAdapter("telegram", "123")
        mgr = ChannelManager([tg])

        assert mgr.get_all_channels() == [("telegram", "123")]

    def test_get_all_channels_dual(self):
        tg = FakeAdapter("telegram", "123")
        slack = FakeAdapter("slack", "C456")
        mgr = ChannelManager([tg, slack])

        channels = mgr.get_all_channels()
        assert ("telegram", "123") in channels
        assert ("slack", "C456") in channels

    def test_build_broadcast_targets_single_channel(self):
        tg = FakeAdapter("telegram", "123")
        mgr = ChannelManager([tg])

        source = {"channel_type": "telegram", "channel_id": "123"}
        targets = mgr.build_broadcast_targets(source)

        assert len(targets) == 1
        assert targets[0]["is_origin"] is True

    def test_build_broadcast_targets_dual_channel(self):
        tg = FakeAdapter("telegram", "123")
        slack = FakeAdapter("slack", "C456")
        mgr = ChannelManager([tg, slack])

        source = {"channel_type": "telegram", "channel_id": "123"}
        targets = mgr.build_broadcast_targets(source)

        assert len(targets) == 2
        tg_target = next(t for t in targets if t["channel_type"] == "telegram")
        slack_target = next(t for t in targets if t["channel_type"] == "slack")
        assert tg_target["is_origin"] is True
        assert slack_target["is_origin"] is False

    @pytest.mark.asyncio
    async def test_send_to(self):
        tg = FakeAdapter("telegram", "123")
        mgr = ChannelManager([tg])

        ok = await mgr.send_to("telegram", "123", "hello")
        assert ok is True
        assert tg.sent == [("123", "hello")]

    @pytest.mark.asyncio
    async def test_send_to_unknown_type(self):
        tg = FakeAdapter("telegram", "123")
        mgr = ChannelManager([tg])

        ok = await mgr.send_to("slack", "C456", "hello")
        assert ok is False

    @pytest.mark.asyncio
    async def test_broadcast_all(self):
        tg = FakeAdapter("telegram", "123")
        slack = FakeAdapter("slack", "C456")
        mgr = ChannelManager([tg, slack])

        await mgr.broadcast_all("알림 메시지")

        assert ("123", "알림 메시지") in tg.sent
        assert ("C456", "알림 메시지") in slack.sent

    def test_get_adapter(self):
        tg = FakeAdapter("telegram", "123")
        mgr = ChannelManager([tg])

        assert mgr.get_adapter("telegram") is tg
        assert mgr.get_adapter("slack") is None
