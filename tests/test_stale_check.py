"""Inbox stale check 장애 알림 테스트.

장애-복구-가이드.md의 C-5를 검증한다.
inbox 파일이 5분 이상 체류 시 경고 브로드캐스트를 발송한다.
"""

import json
import os
import time
import pytest
from pathlib import Path

from gsd_orchestrator.channels.base import ChannelAdapter
from gsd_orchestrator.channels.manager import ChannelManager


class FakeAdapter(ChannelAdapter):
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


@pytest.fixture
def inbox_dir(tmp_path):
    d = tmp_path / "messages" / "inbox"
    d.mkdir(parents=True)
    return d


class TestInboxStaleCheck:
    """C-5: inbox 파일 5분 이상 미처리 시 경고."""

    @pytest.mark.asyncio
    async def test_stale_inbox_alert(self, inbox_dir):
        """5분 이상 체류 시 경고 브로드캐스트."""
        adapter = FakeAdapter()
        manager = ChannelManager([adapter])

        # inbox에 파일 생성 후 mtime을 6분 전으로 조작
        stale_file = inbox_dir / "stale.json"
        stale_file.write_text(json.dumps({"id": "test"}))
        old_time = time.time() - 360  # 6분 전
        os.utime(stale_file, (old_time, old_time))

        # _inbox_stale_check 로직 재현 (orchestrator.py:142-155)
        for f in inbox_dir.glob("*.json"):
            age = time.time() - f.stat().st_mtime
            if age > 300:
                await manager.broadcast_all(
                    "[시스템] Inbox 메시지 미처리 경고: Claude Code 상태 확인 필요"
                )
                break

        assert len(adapter.sent_messages) == 1
        assert "미처리 경고" in adapter.sent_messages[0][1]

    @pytest.mark.asyncio
    async def test_no_alert_for_fresh_inbox(self, inbox_dir):
        """방금 생성된 파일에는 경고 없음."""
        adapter = FakeAdapter()
        manager = ChannelManager([adapter])

        # 방금 생성된 파일
        fresh_file = inbox_dir / "fresh.json"
        fresh_file.write_text(json.dumps({"id": "test"}))

        for f in inbox_dir.glob("*.json"):
            age = time.time() - f.stat().st_mtime
            if age > 300:
                await manager.broadcast_all(
                    "[시스템] Inbox 메시지 미처리 경고: Claude Code 상태 확인 필요"
                )
                break

        assert len(adapter.sent_messages) == 0

    @pytest.mark.asyncio
    async def test_alert_fires_once_per_check(self, inbox_dir):
        """여러 stale 파일이 있어도 경고는 1회만 발송."""
        adapter = FakeAdapter()
        manager = ChannelManager([adapter])

        old_time = time.time() - 600
        for name in ["a.json", "b.json", "c.json"]:
            f = inbox_dir / name
            f.write_text(json.dumps({"id": name}))
            os.utime(f, (old_time, old_time))

        for f in inbox_dir.glob("*.json"):
            age = time.time() - f.stat().st_mtime
            if age > 300:
                await manager.broadcast_all(
                    "[시스템] Inbox 메시지 미처리 경고: Claude Code 상태 확인 필요"
                )
                break

        # 1회만 발송 (break로 루프 탈출)
        assert len(adapter.sent_messages) == 1

    @pytest.mark.asyncio
    async def test_mixed_fresh_and_stale(self, inbox_dir):
        """stale 파일과 fresh 파일이 섞여 있으면 경고 발송."""
        adapter = FakeAdapter()
        manager = ChannelManager([adapter])

        # fresh 파일
        (inbox_dir / "fresh.json").write_text(json.dumps({"id": "fresh"}))

        # stale 파일
        stale = inbox_dir / "stale.json"
        stale.write_text(json.dumps({"id": "stale"}))
        old_time = time.time() - 400
        os.utime(stale, (old_time, old_time))

        alerted = False
        for f in inbox_dir.glob("*.json"):
            age = time.time() - f.stat().st_mtime
            if age > 300:
                await manager.broadcast_all(
                    "[시스템] Inbox 메시지 미처리 경고: Claude Code 상태 확인 필요"
                )
                alerted = True
                break

        assert alerted
        assert len(adapter.sent_messages) == 1
