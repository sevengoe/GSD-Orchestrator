"""Echo app — App Bridge 테스트용 reference 외부 앱.

external_inbox/echo/ 디렉토리를 폴링해서 받은 명령의 args 를 그대로
합쳐 outbox 에 응답한다. 외부 앱이 GSD App Bridge 와 통합하는 최소 패턴.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))


def process_one(inbox_dir: Path, outbox_dir: Path) -> dict | None:
    """inbox 의 가장 오래된 명령 파일 1개를 처리한다.

    Returns:
        처리한 명령 dict 또는 None (대기 중인 명령 없음)
    """
    inbox_dir.mkdir(parents=True, exist_ok=True)
    files = sorted(inbox_dir.glob("*.json"))
    if not files:
        return None

    cmd_file = files[0]
    try:
        cmd = json.loads(cmd_file.read_text())
    except (json.JSONDecodeError, OSError):
        cmd_file.unlink(missing_ok=True)
        return None

    args = cmd.get("command", {}).get("args", [])
    response_text = " ".join(args) if args else "(empty)"

    source = cmd.get("source", {})
    command_id = cmd.get("command_id", "")

    now = datetime.now(KST)
    short_id = uuid.uuid4().hex[:8]
    out_filename = f"{now.strftime('%Y%m%d_%H%M%S')}_{short_id}_echoreply.json"
    targets = [{
        "channel_type": source.get("channel_type", ""),
        "channel_id": source.get("channel_id", ""),
        "is_origin": True,
    }]
    out_data = {
        "id": str(uuid.uuid4()),
        "command_id": command_id,
        "source": source,
        "targets": targets,
        "retry_count": 0,
        "keyword": "echo_reply",
        "request": None,
        "response": {
            "text": response_text,
            "parse_mode": None,
            "timestamp": now.isoformat(),
        },
    }

    outbox_dir.mkdir(parents=True, exist_ok=True)
    tmp = outbox_dir / f".{out_filename}.tmp"
    tmp.write_text(json.dumps(out_data, ensure_ascii=False, indent=2))
    tmp.rename(outbox_dir / out_filename)

    cmd_file.unlink(missing_ok=True)
    return cmd


def run_loop(inbox_dir: Path, outbox_dir: Path,
             interval: float = 0.2, stop_after: float = 5.0) -> int:
    """폴링 루프. stop_after 초가 지나면 종료. 처리 건수 반환."""
    deadline = time.time() + stop_after
    count = 0
    while time.time() < deadline:
        if process_one(inbox_dir, outbox_dir):
            count += 1
        else:
            time.sleep(interval)
    return count
