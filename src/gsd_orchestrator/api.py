import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .channels.manager import ChannelManager

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))


class ChannelSender:
    """호스트 앱이 gsd_orchestrator의 채널로 메시지를 발송하는 API."""

    def __init__(self, channel_manager: ChannelManager, outbox_dir: Path):
        self._channel_manager = channel_manager
        self._outbox_dir = outbox_dir

    async def send(self, text: str, channel_type: str | None = None,
                   parse_mode: str | None = "HTML") -> bool:
        """채널에 메시지 즉시 발송.

        Args:
            text: 발송할 텍스트
            channel_type: 특정 채널 타입 ("telegram", "slack"). None이면 모든 채널.
            parse_mode: HTML, Markdown 등. None이면 plain text.

        Returns:
            True if 모든 발송 성공
        """
        if channel_type:
            channels = [
                (ct, cid) for ct, cid in self._channel_manager.get_all_channels()
                if ct == channel_type
            ]
        else:
            channels = self._channel_manager.get_all_channels()

        all_ok = True
        for ct, cid in channels:
            ok = await self._channel_manager.send_to(ct, cid, text, parse_mode)
            if not ok:
                all_ok = False
        return all_ok

    def enqueue(self, text: str, channel_type: str | None = None,
                parse_mode: str | None = "HTML") -> None:
        """outbox에 파일 작성. OutboxSender가 폴링하여 발송.

        Args:
            text: 발송할 텍스트
            channel_type: 특정 채널 타입. None이면 모든 채널.
            parse_mode: HTML, Markdown 등.
        """
        now = datetime.now(KST)
        short_id = uuid.uuid4().hex[:8]
        filename = f"{now.strftime('%Y%m%d_%H%M%S')}_{short_id}_external.json"

        if channel_type:
            targets = [
                {"channel_type": ct, "channel_id": cid, "is_origin": True}
                for ct, cid in self._channel_manager.get_all_channels()
                if ct == channel_type
            ]
        else:
            targets = [
                {"channel_type": ct, "channel_id": cid, "is_origin": True}
                for ct, cid in self._channel_manager.get_all_channels()
            ]

        data = {
            "id": str(uuid.uuid4()),
            "source": {},
            "targets": targets,
            "retry_count": 0,
            "keyword": "external",
            "request": None,
            "response": {
                "text": text,
                "parse_mode": parse_mode,
                "timestamp": now.isoformat(),
            },
        }

        tmp = self._outbox_dir / f".{filename}.tmp"
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        tmp.rename(self._outbox_dir / filename)
