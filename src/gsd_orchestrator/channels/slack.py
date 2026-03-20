import logging
from typing import Callable, Awaitable

from .base import ChannelAdapter

logger = logging.getLogger(__name__)


class SlackAdapter(ChannelAdapter):
    """Slack 채널 어댑터. slack_bolt Socket Mode 기반.

    slack_bolt는 지연 임포트로 처리하여, 미설치 환경에서는
    이 클래스를 인스턴스화할 때만 ImportError가 발생한다.
    """

    def __init__(self, bot_token: str, app_token: str, channel_id: str):
        try:
            from slack_bolt.async_app import AsyncApp
        except ImportError:
            raise ImportError(
                "Slack 어댑터를 사용하려면 slack-bolt 패키지가 필요합니다.\n"
                "pip install 'gsd-orchestrator[slack]' 또는 pip install slack-bolt 로 설치해주세요."
            )

        self._bot_token = bot_token
        self._app_token = app_token
        self._channel_id = channel_id
        self._app = AsyncApp(token=bot_token)
        self._handler = None
        self._bot_user_id: str = ""
        self._on_message_callback: Callable[..., Awaitable[None]] | None = None

    @property
    def channel_type(self) -> str:
        return "slack"

    @property
    def max_message_length(self) -> int:
        return 4000

    def get_channel_id(self) -> str:
        return self._channel_id

    async def start(self, on_message: Callable[..., Awaitable[None]]) -> None:
        from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler

        self._on_message_callback = on_message

        # 봇 정보 조회
        auth_result = await self._app.client.auth_test()
        self._bot_user_id = auth_result.get("user_id", "")
        logger.info(f"SlackAdapter 시작 (bot_user_id: {self._bot_user_id}, channel: {self._channel_id})")

        @self._app.event("message")
        async def handle_message(event, say):
            await self._handle_message(event)

        self._handler = AsyncSocketModeHandler(self._app, self._app_token)
        await self._handler.start_async()

    async def stop(self) -> None:
        if self._handler:
            await self._handler.close_async()

    async def send_message(self, channel_id: str, text: str,
                           parse_mode: str | None = None) -> bool:
        """메시지 발송. 4000자 초과 시 내부 분할."""
        try:
            max_len = self.max_message_length
            if len(text) <= max_len:
                await self._app.client.chat_postMessage(
                    channel=channel_id, text=text,
                )
            else:
                for i in range(0, len(text), max_len):
                    chunk = text[i:i + max_len]
                    await self._app.client.chat_postMessage(
                        channel=channel_id, text=chunk,
                    )
            return True
        except Exception as e:
            logger.error(f"Slack 발송 실패 [{channel_id}]: {e}")
            return False

    async def _handle_message(self, event: dict):
        # 봇 자신의 메시지 skip
        if event.get("bot_id") or event.get("subtype") == "bot_message":
            return

        channel_id = event.get("channel", "")
        if channel_id != self._channel_id:
            return

        user_id = event.get("user", "")
        text = event.get("text", "")

        # 사용자 이름 조회
        user_name = user_id
        try:
            user_info = await self._app.client.users_info(user=user_id)
            profile = user_info.get("user", {}).get("profile", {})
            user_name = (
                profile.get("display_name")
                or profile.get("real_name")
                or user_id
            )
        except Exception:
            pass

        source = {
            "channel_type": "slack",
            "channel_id": channel_id,
            "user_id": user_id,
            "user_name": user_name,
            "message_id": event.get("ts", ""),
            "thread_ts": event.get("thread_ts"),
        }

        if self._on_message_callback:
            await self._on_message_callback(source=source, text=text)
