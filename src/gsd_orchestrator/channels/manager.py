import logging
from typing import Callable, Awaitable

from .base import ChannelAdapter

logger = logging.getLogger(__name__)


class ChannelManager:
    """채널 어댑터 레지스트리. 멀티채널 발송과 브로드캐스트를 담당한다."""

    def __init__(self, adapters: list[ChannelAdapter]):
        self._adapters: dict[str, ChannelAdapter] = {}
        for adapter in adapters:
            self._adapters[adapter.channel_type] = adapter

    def get_adapter(self, channel_type: str) -> ChannelAdapter | None:
        return self._adapters.get(channel_type)

    async def send_to(self, channel_type: str, channel_id: str,
                      text: str, parse_mode: str | None = None) -> bool:
        """특정 채널에 메시지를 발송한다."""
        adapter = self._adapters.get(channel_type)
        if not adapter:
            logger.warning(f"어댑터 없음: {channel_type}")
            return False
        return await adapter.send_message(channel_id, text, parse_mode)

    def get_all_channels(self) -> list[tuple[str, str]]:
        """모든 (channel_type, channel_id) 쌍을 반환한다."""
        return [
            (ct, adapter.get_channel_id())
            for ct, adapter in self._adapters.items()
        ]

    def build_broadcast_targets(self, source: dict) -> list[dict]:
        """source 기반으로 targets 배열을 생성한다.

        origin 채널: is_origin=True, 나머지: is_origin=False
        """
        src_type = source.get("channel_type", "")
        src_id = source.get("channel_id", "")
        targets = []
        for channel_type, channel_id in self.get_all_channels():
            is_origin = (channel_type == src_type and channel_id == src_id)
            targets.append({
                "channel_type": channel_type,
                "channel_id": channel_id,
                "is_origin": is_origin,
            })
        return targets

    async def broadcast_all(self, text: str) -> None:
        """모든 채널에 동일 메시지를 발송한다."""
        for channel_type, channel_id in self.get_all_channels():
            try:
                await self.send_to(channel_type, channel_id, text)
            except Exception as e:
                logger.error(f"브로드캐스트 실패 [{channel_type}:{channel_id}]: {e}")

    async def start_all(self, on_message: Callable[..., Awaitable[None]]) -> None:
        """모든 어댑터의 메시지 수신을 시작한다."""
        for adapter in self._adapters.values():
            await adapter.start(on_message)

    async def stop_all(self) -> None:
        """모든 어댑터를 정지한다."""
        for adapter in self._adapters.values():
            try:
                await adapter.stop()
            except Exception as e:
                logger.error(f"어댑터 정지 실패 [{adapter.channel_type}]: {e}")
