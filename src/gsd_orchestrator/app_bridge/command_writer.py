"""AppCommandWriter — file 모드 외부 앱 inbox 작성기.

external_inbox/{app}/*.json 에 원자적으로 명령 파일을 떨군다.
외부 앱은 이 디렉토리를 폴링해서 명령을 수신하고, 결과는
GSD outbox/ 에 command_id 를 포함한 JSON 으로 응답한다.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))


class AppCommandWriter:
    """외부 앱 inbox 디렉토리에 명령을 원자적으로 기록한다."""

    def __init__(self, outbox_dir: Path):
        """
        Args:
            outbox_dir: 외부 앱이 응답을 떨굴 GSD outbox 경로
                (`reply_to_outbox` 필드에 절대 경로로 명시)
        """
        self._outbox_dir = outbox_dir

    def write(self, *,
              app_inbox_dir: Path,
              app_name: str,
              command_id: str,
              prefix: str,
              raw_command: str,
              args: list[str],
              source: dict) -> Path:
        """external_inbox/{app}/ 에 명령 JSON 파일을 작성한다.

        Returns:
            작성된 파일 경로
        """
        app_inbox_dir.mkdir(parents=True, exist_ok=True)

        now = datetime.now(KST)
        short_id = uuid.uuid4().hex[:8]
        filename = f"{now.strftime('%Y%m%d_%H%M%S')}_{short_id}.json"

        data = {
            "id": str(uuid.uuid4()),
            "command_id": command_id,
            "timestamp": now.isoformat(),
            "source": {
                "channel_type": source.get("channel_type", ""),
                "channel_id": source.get("channel_id", ""),
                "user_id": str(source.get("user_id", "")),
                "user_name": source.get("user_name", ""),
            },
            "command": {
                "raw": raw_command,
                "prefix": prefix,
                "args": list(args),
            },
            "target_app": app_name,
            "reply_to_outbox": str(self._outbox_dir),
        }

        tmp = app_inbox_dir / f".{filename}.tmp"
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        target = app_inbox_dir / filename
        tmp.rename(target)
        logger.info(
            f"[app_bridge] file 모드 명령 기록 — app={app_name} "
            f"command_id={command_id[:8]} prefix={prefix} → {target}"
        )
        return target
