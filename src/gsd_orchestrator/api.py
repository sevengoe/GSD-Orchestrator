import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Awaitable, Callable

from .channels.manager import ChannelManager

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))


# AppBridge handler signature: (payload: dict) -> str | Awaitable[str]
AppHandler = Callable[[dict], "str | Awaitable[str]"]


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


class AppBridge:
    """In-process 외부 앱 핸들러 등록 및 디스패치 API.

    호스트 앱이 GSD 를 라이브러리로 임베드하거나, 같은 Python 프로세스에서
    비즈니스 로직을 처리할 때 사용한다. 라우팅은 GSD 의 AppRouter 가
    담당하고, 매칭된 명령이 api 모드면 AppBridge.dispatch() 가 핸들러를
    호출한다.

    핸들러 시그니처:
        async def handler(payload: dict) -> str
        또는 def handler(payload: dict) -> str

    payload 구조:
        {
            "command_id": "uuid",
            "command": {"raw": "...", "prefix": "/foo", "args": [...]},
            "source": {"channel_type", "channel_id", "user_id", "user_name"},
        }

    핸들러 응답 문자열은 자동으로 outbox 로 enqueue 되어 사용자에게 발송된다.
    """

    def __init__(self, channel_sender: "ChannelSender",
                 channel_manager: ChannelManager, outbox_dir: Path):
        self._channel_sender = channel_sender
        self._channel_manager = channel_manager
        self._outbox_dir = outbox_dir
        self._handlers: dict[str, AppHandler] = {}

    def register(self, app_name: str, handler: AppHandler) -> None:
        """app_name 에 핸들러 등록. 동일 이름 재등록 시 덮어쓴다."""
        if not callable(handler):
            raise TypeError(f"handler must be callable, got {type(handler)}")
        self._handlers[app_name] = handler
        logger.info(f"[app_bridge] api 핸들러 등록 — app={app_name}")

    def unregister(self, app_name: str) -> None:
        self._handlers.pop(app_name, None)

    def has_handler(self, app_name: str) -> bool:
        return app_name in self._handlers

    async def dispatch(self, *, app_name: str, command_id: str,
                       prefix: str, raw_command: str, args: list[str],
                       source: dict) -> None:
        """등록된 핸들러를 비동기 실행하고 응답을 outbox 로 발송한다.

        핸들러 실행은 asyncio.create_task 로 fire-and-forget. 사용자 채널은
        OutboxSender 가 폴링해서 발송. 핸들러 실패 시 에러 메시지를 outbox
        로 기록한다.
        """
        handler = self._handlers.get(app_name)
        if not handler:
            logger.error(
                f"[app_bridge] api 핸들러 없음 — app={app_name} "
                f"command_id={command_id[:8]}")
            self._enqueue_error(
                command_id=command_id, source=source, app_name=app_name,
                error_text=f"[{app_name}] 등록된 핸들러가 없습니다.",
            )
            return

        payload = {
            "command_id": command_id,
            "command": {
                "raw": raw_command,
                "prefix": prefix,
                "args": list(args),
            },
            "source": dict(source),
        }

        asyncio.create_task(self._run_and_reply(
            handler=handler, payload=payload,
            command_id=command_id, source=source, app_name=app_name,
        ))

    async def _run_and_reply(self, *, handler: AppHandler, payload: dict,
                              command_id: str, source: dict,
                              app_name: str) -> None:
        try:
            result = handler(payload)
            if asyncio.iscoroutine(result):
                response_text = await result
            else:
                response_text = result
            if response_text is None:
                response_text = ""
            response_text = str(response_text)
        except Exception as e:
            logger.exception(
                f"[app_bridge] api 핸들러 예외 — app={app_name} "
                f"command_id={command_id[:8]}: {e}")
            self._enqueue_error(
                command_id=command_id, source=source, app_name=app_name,
                error_text=f"[{app_name}] 핸들러 예외: {type(e).__name__}: {e}",
            )
            return

        self._enqueue_response(
            command_id=command_id, source=source,
            response_text=response_text,
        )

    def _enqueue_response(self, *, command_id: str, source: dict,
                          response_text: str) -> None:
        """outbox 에 origin 채널로만 발송하는 응답 파일 작성."""
        now = datetime.now(KST)
        short_id = uuid.uuid4().hex[:8]
        filename = f"{now.strftime('%Y%m%d_%H%M%S')}_{short_id}_appbridge.json"

        ch_type = source.get("channel_type", "")
        ch_id = source.get("channel_id", "")
        targets = []
        if ch_type and ch_id:
            targets = [{"channel_type": ch_type, "channel_id": ch_id, "is_origin": True}]
        else:
            # source 가 비어 있으면 전 채널 발송 (fallback)
            targets = [
                {"channel_type": ct, "channel_id": cid, "is_origin": True}
                for ct, cid in self._channel_manager.get_all_channels()
            ]

        data = {
            "id": str(uuid.uuid4()),
            "command_id": command_id,
            "source": {
                "channel_type": ch_type,
                "channel_id": ch_id,
                "user_id": str(source.get("user_id", "")),
                "user_name": source.get("user_name", ""),
            },
            "targets": targets,
            "retry_count": 0,
            "keyword": "appbridge",
            "request": None,
            "response": {
                "text": response_text,
                "parse_mode": None,
                "timestamp": now.isoformat(),
            },
        }

        self._outbox_dir.mkdir(parents=True, exist_ok=True)
        tmp = self._outbox_dir / f".{filename}.tmp"
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        tmp.rename(self._outbox_dir / filename)

    def _enqueue_error(self, *, command_id: str, source: dict,
                        app_name: str, error_text: str) -> None:
        self._enqueue_response(
            command_id=command_id, source=source, response_text=error_text)
