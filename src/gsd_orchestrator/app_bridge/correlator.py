"""AppResponseCorrelator — 외부 앱 명령 ID 추적 및 timeout 알림.

라우팅된 모든 명령은 register() 로 등록된다. OutboxSender 가 응답을 발송할
때 resolve(command_id) 를 호출해서 pending 에서 제거한다. 백그라운드 태스크가
주기적으로 만료된 항목을 검사해서 사용자에게 timeout 알림을 발송한다.

best-effort 전달: timeout 후 도착한 응답도 사용자에게 전달하되 "[지연 응답]"
prefix 를 붙인다 — `mark_late()` 으로 식별.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))

# 만료 후에도 보관해서 best-effort 전달용. 24시간 후 정리.
_EXPIRED_RETENTION_SEC = 24 * 3600


@dataclass
class PendingCommand:
    command_id: str
    app_name: str
    source: dict           # ack/timeout 통보 대상
    sent_at: float
    timeout_sec: int
    raw_command: str = ""


class AppResponseCorrelator:
    """명령 ID ↔ 응답 매칭 + timeout 추적."""

    def __init__(self, outbox_dir: Path,
                 default_timeout_sec: int = 60,
                 check_interval_sec: int = 5):
        self._outbox_dir = outbox_dir
        self._default_timeout_sec = default_timeout_sec
        self._check_interval_sec = check_interval_sec
        self._pending: dict[str, PendingCommand] = {}
        # 만료된 항목 — 늦은 응답이 도착하면 best-effort 로 전달
        self._expired: dict[str, tuple[PendingCommand, float]] = {}
        self._lock = Lock()
        self._task: asyncio.Task | None = None

    def register(self, *, command_id: str, app_name: str, source: dict,
                 raw_command: str = "",
                 timeout_sec: int | None = None) -> None:
        """라우팅 직후 호출. pending dict 에 등록."""
        pc = PendingCommand(
            command_id=command_id,
            app_name=app_name,
            source=dict(source),
            sent_at=time.time(),
            timeout_sec=timeout_sec or self._default_timeout_sec,
            raw_command=raw_command,
        )
        with self._lock:
            self._pending[command_id] = pc

    def resolve(self, command_id: str) -> PendingCommand | None:
        """outbox 응답 발송 직전 호출. pending 에서 제거 후 반환.

        만료 dict 에 있으면 (지연 응답) 거기서 제거 후 반환. 없으면 None.
        """
        if not command_id:
            return None
        with self._lock:
            pc = self._pending.pop(command_id, None)
            if pc:
                return pc
            entry = self._expired.pop(command_id, None)
            if entry:
                return entry[0]
        return None

    def is_expired(self, command_id: str) -> bool:
        """이 command_id 가 timeout 만료 후 도착한 응답인지 판별."""
        if not command_id:
            return False
        with self._lock:
            return command_id in self._expired or (
                command_id not in self._pending
                and command_id in self._expired
            )

    def has_pending(self, command_id: str) -> bool:
        with self._lock:
            return command_id in self._pending

    async def run(self):
        """백그라운드 만료 검사 루프."""
        while True:
            try:
                self._check_expired()
            except Exception as e:
                logger.exception(f"[correlator] 만료 검사 에러: {e}")
            await asyncio.sleep(self._check_interval_sec)

    def _check_expired(self) -> None:
        """timeout 초과 항목을 expired 로 옮기고 사용자에게 알림."""
        now = time.time()
        moved: list[PendingCommand] = []
        with self._lock:
            for cid, pc in list(self._pending.items()):
                if now - pc.sent_at >= pc.timeout_sec:
                    moved.append(pc)
                    self._pending.pop(cid, None)
                    self._expired[cid] = (pc, now)
            # expired 보관 정리
            for cid, (_, expired_at) in list(self._expired.items()):
                if now - expired_at >= _EXPIRED_RETENTION_SEC:
                    self._expired.pop(cid, None)

        for pc in moved:
            self._notify_timeout(pc)

    def _notify_timeout(self, pc: PendingCommand) -> None:
        """timeout 메시지를 outbox 에 작성."""
        text = (
            f"[{pc.app_name}] 외부 앱 응답 없음 "
            f"({pc.timeout_sec}초 초과). 다시 시도해 주세요."
        )
        ch_type = pc.source.get("channel_type", "")
        ch_id = pc.source.get("channel_id", "")
        targets = (
            [{"channel_type": ch_type, "channel_id": ch_id, "is_origin": True}]
            if ch_type and ch_id else []
        )
        if not targets:
            logger.warning(
                f"[correlator] timeout 통보 대상 없음 — command_id={pc.command_id[:8]}")
            return

        now = datetime.now(KST)
        short_id = uuid.uuid4().hex[:8]
        filename = f"{now.strftime('%Y%m%d_%H%M%S')}_{short_id}_appbridge_timeout.json"
        data = {
            "id": str(uuid.uuid4()),
            "command_id": pc.command_id,
            "source": pc.source,
            "targets": targets,
            "retry_count": 0,
            "keyword": "appbridge_timeout",
            "request": None,
            "response": {
                "text": text,
                "parse_mode": None,
                "timestamp": now.isoformat(),
            },
        }
        try:
            self._outbox_dir.mkdir(parents=True, exist_ok=True)
            tmp = self._outbox_dir / f".{filename}.tmp"
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
            tmp.rename(self._outbox_dir / filename)
            logger.warning(
                f"[correlator] timeout 알림 — app={pc.app_name} "
                f"command_id={pc.command_id[:8]}")
        except OSError as e:
            logger.error(f"[correlator] timeout 파일 작성 실패: {e}")
