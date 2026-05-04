"""AppRouter — 슬래시 명령을 외부 앱으로 라우팅한다.

GSD 메타 명령(/help, /gsd, /status, /reset, /resume, /retry)은 채널 어댑터의
CommandHandler 가 우선 처리하므로 여기에 도달하지 않는다. 그 외 슬래시
명령만 AppRouter 가 매칭한다.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# 위험 문자 — args 에 포함되면 거부 (외부 앱이 shell 호출 시 보호)
_DANGEROUS_PATTERN = re.compile(r"[;|`$]|\$\(|\)\s*\$")


@dataclass
class RouteResult:
    """AppRouter.route() 반환 타입.

    matched=True 인 경우 호출자는 ack 메시지를 즉시 발송하고 file 또는 api
    모드별로 명령을 디스패치한다. matched=False 면 기존 GSD 흐름으로 진행.
    rejected=True 면 권한/입력 검증 실패 — 거부 메시지를 사용자에게 발송하고
    종료한다.
    """
    matched: bool
    rejected: bool = False
    app_name: str | None = None
    app_mode: str | None = None
    prefix: str | None = None
    args: list[str] = field(default_factory=list)
    raw_command: str = ""
    command_id: str | None = None
    reason: str | None = None
    ack_message: str | None = None
    reject_message: str | None = None


class AppRouter:
    """슬래시 명령 → 외부 앱 라우팅."""

    def __init__(self, apps: list[dict], max_args_length: int = 1024):
        """
        Args:
            apps: Config.app_bridge_apps (정규화된 dict 리스트)
            max_args_length: command args 결합 길이 상한 (악성 입력 차단)
        """
        self._apps = apps
        self._max_args_length = max_args_length

        # prefix → app dict 인덱스 (빠른 조회)
        self._prefix_index: dict[str, dict] = {}
        for app in apps:
            for prefix in app["command_prefix"]:
                self._prefix_index[prefix] = app

    @property
    def registered_prefixes(self) -> list[str]:
        return sorted(self._prefix_index.keys())

    def route(self, text: str, source: dict) -> RouteResult:
        """텍스트와 source 로 라우팅 결과를 결정한다."""
        if not text:
            return RouteResult(matched=False)

        stripped = text.strip()
        if not stripped.startswith("/"):
            return RouteResult(matched=False)

        # 첫 토큰을 prefix 후보로
        parts = stripped.split()
        prefix = parts[0]
        args = parts[1:]

        app = self._prefix_index.get(prefix)
        if not app:
            return RouteResult(matched=False)

        # ── whitelist 검증 ──
        whitelist = app.get("whitelist_user_ids", [])
        user_id = str(source.get("user_id", ""))
        if whitelist and user_id not in whitelist:
            return RouteResult(
                matched=True,
                rejected=True,
                app_name=app["name"],
                app_mode=app["mode"],
                prefix=prefix,
                args=args,
                raw_command=stripped,
                reason="whitelist",
                reject_message=f"[{app['name']}] 권한 없음 — 등록되지 않은 사용자입니다.",
            )

        # ── args 길이 제한 ──
        joined_args_len = sum(len(a) for a in args) + max(0, len(args) - 1)
        if joined_args_len > self._max_args_length:
            return RouteResult(
                matched=True,
                rejected=True,
                app_name=app["name"],
                app_mode=app["mode"],
                prefix=prefix,
                args=args,
                raw_command=stripped,
                reason="args_too_long",
                reject_message=(
                    f"[{app['name']}] 입력이 너무 깁니다 "
                    f"(최대 {self._max_args_length}자)."),
            )

        # ── args 위험 문자 차단 ──
        for arg in args:
            if _DANGEROUS_PATTERN.search(arg):
                return RouteResult(
                    matched=True,
                    rejected=True,
                    app_name=app["name"],
                    app_mode=app["mode"],
                    prefix=prefix,
                    args=args,
                    raw_command=stripped,
                    reason="dangerous_chars",
                    reject_message=(
                        f"[{app['name']}] 명령에 허용되지 않는 문자가 포함되어 있습니다."),
                )

        # ── 매칭 성공: command_id 생성 ──
        command_id = str(uuid.uuid4())
        ack_template = app.get("ack_message", "")
        try:
            ack_message = ack_template.format(id=command_id[:8], app=app["name"])
        except (KeyError, IndexError):
            ack_message = ack_template

        return RouteResult(
            matched=True,
            rejected=False,
            app_name=app["name"],
            app_mode=app["mode"],
            prefix=prefix,
            args=args,
            raw_command=stripped,
            command_id=command_id,
            ack_message=ack_message or None,
        )

    def get_app(self, app_name: str) -> dict | None:
        """앱 이름으로 설정 dict 조회."""
        for app in self._apps:
            if app["name"] == app_name:
                return app
        return None
