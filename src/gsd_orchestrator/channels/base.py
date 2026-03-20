from abc import ABC, abstractmethod
from typing import Callable, Awaitable


class ChannelAdapter(ABC):
    """채널 어댑터 추상 클래스. Telegram, Slack 등 모든 채널은 이 인터페이스를 구현한다."""

    @property
    @abstractmethod
    def channel_type(self) -> str:
        """채널 타입 식별자 ("telegram" | "slack")"""

    @property
    def max_message_length(self) -> int:
        """채널별 최대 메시지 길이. 기본 4096."""
        return 4096

    @abstractmethod
    async def start(self, on_message: Callable[..., Awaitable[None]]) -> None:
        """메시지 수신을 시작한다. on_message(source, text, mode) 콜백."""

    @abstractmethod
    async def stop(self) -> None:
        """어댑터를 정지한다."""

    @abstractmethod
    async def send_message(self, channel_id: str, text: str,
                           parse_mode: str | None = None) -> bool:
        """메시지를 발송한다. max_message_length 초과 시 내부에서 분할. 성공 시 True."""

    @abstractmethod
    def get_channel_id(self) -> str:
        """이 어댑터의 단일 채널 ID를 반환한다."""
