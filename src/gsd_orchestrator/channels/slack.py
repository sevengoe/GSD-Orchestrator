import logging
from pathlib import Path
from typing import Callable, Awaitable

from .base import ChannelAdapter
from ..attachment_handler import (
    validate_file, download_file_slack, extract_text,
    build_metadata, cleanup_temp_file,
)
from ..text_cleaner import clean_text

logger = logging.getLogger(__name__)


class SlackAdapter(ChannelAdapter):
    """Slack 채널 어댑터. slack_bolt Socket Mode 기반.

    slack_bolt는 지연 임포트로 처리하여, 미설치 환경에서는
    이 클래스를 인스턴스화할 때만 ImportError가 발생한다.
    """

    def __init__(self, bot_token: str, app_token: str, channel_id: str,
                 attachments_config: dict | None = None):
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
        # 첨부파일 설정
        att = attachments_config or {}
        self._att_allowed_ext: list[str] = att.get("allowed_extensions", ["txt", "md", "pdf"])
        self._att_max_size: int = att.get("max_file_size", 1_048_576)
        self._att_temp_dir: Path = att.get("temp_dir", Path("messages/attachments"))
        self._att_reject_msg: str = att.get("reject_message", "txt, md, pdf 파일만 지원합니다.")

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

        @self._app.event("file_shared")
        async def handle_file_shared(event, say):
            await self._handle_file_shared(event)

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

    async def _handle_file_shared(self, event: dict):
        """file_shared 이벤트 핸들러. 첨부파일 수신 처리."""
        file_id = event.get("file_id", "")
        channel_id = event.get("channel_id", "")

        if channel_id != self._channel_id:
            return

        # files.info API로 파일 메타데이터 조회
        try:
            file_info_resp = await self._app.client.files_info(file=file_id)
        except Exception as e:
            logger.error(f"Slack files.info 호출 실패 [{file_id}]: {e}")
            return

        file_obj = file_info_resp.get("file", {})
        file_name = file_obj.get("name", "unknown")
        file_size = file_obj.get("size", 0)
        url_private = file_obj.get("url_private_download", "")
        user_id = event.get("user_id", "") or file_obj.get("user", "")
        thread_ts = file_obj.get("shares", {}).get("public", {}).get(channel_id, [{}])[0].get("ts")

        # 봇 자신의 파일 skip
        if user_id == self._bot_user_id:
            return

        # 화이트리스트 / 크기 검증
        reject = validate_file(
            file_name, file_size,
            self._att_allowed_ext, self._att_max_size, self._att_reject_msg,
        )
        if reject:
            await self._app.client.chat_postMessage(
                channel=channel_id, text=reject, thread_ts=thread_ts,
            )
            return

        if not url_private:
            await self._app.client.chat_postMessage(
                channel=channel_id,
                text="파일 다운로드 URL을 가져올 수 없습니다.",
                thread_ts=thread_ts,
            )
            return

        # 다운로드 → 텍스트 추출 → 정제
        local_path = None
        try:
            local_path = await download_file_slack(
                self._app.client, url_private, self._att_temp_dir, file_name,
            )
            raw_text = extract_text(local_path, file_name)

            if raw_text.startswith("[오류]"):
                await self._app.client.chat_postMessage(
                    channel=channel_id, text=raw_text, thread_ts=thread_ts,
                )
                return

            ext = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""
            cleaned_text, summary = clean_text(raw_text, ext)

            # 확인 메시지 발송 (스레드)
            initial_comment = file_obj.get("initial_comment", {}).get("comment", "")
            preview = cleaned_text[:1000]
            if len(cleaned_text) > 1000:
                preview += "\n… (이하 생략)"
            confirm_msg = (
                f"📎 {file_name} — 텍스트 추출 완료\n"
                f"(원본 {summary['original_length']:,}자 → 정제 후 {summary['cleaned_length']:,}자)\n\n"
                f"{preview}\n\n"
                f"아래 내용이 맞는지 확인해주세요. 이어서 메시지를 보내시면 해당 내용과 함께 처리됩니다."
            )
            await self._app.client.chat_postMessage(
                channel=channel_id, text=confirm_msg, thread_ts=thread_ts,
            )

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

            # source에 메타데이터 포함하여 콜백 호출
            source = {
                "channel_type": "slack",
                "channel_id": channel_id,
                "user_id": user_id,
                "user_name": user_name,
                "message_id": file_obj.get("timestamp", ""),
                "thread_ts": thread_ts,
                "attachments": [build_metadata(file_name, file_size)],
            }

            text = initial_comment if initial_comment else f"[첨부파일: {file_name}]"
            if self._on_message_callback:
                await self._on_message_callback(
                    source=source, text=text, extracted_text=cleaned_text,
                )
        except Exception as e:
            logger.error(f"첨부파일 처리 실패 [{file_name}]: {e}")
            await self._app.client.chat_postMessage(
                channel=channel_id,
                text=f"첨부파일 처리 중 오류가 발생했습니다: {e}",
                thread_ts=thread_ts,
            )
        finally:
            if local_path:
                cleanup_temp_file(local_path)
