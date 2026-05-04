import hashlib
import os
from pathlib import Path
from dataclasses import dataclass, field

import yaml
from dotenv import load_dotenv

load_dotenv()


def _make_instance_id(base_dir: Path) -> str:
    """프로젝트 경로에서 인스턴스 고유 해시(8자)를 생성한다."""
    return hashlib.md5(str(base_dir.resolve()).encode()).hexdigest()[:8]


def _normalize_app_bridge_apps(apps_cfg: list, base_dir: Path) -> list:
    """app_bridge.apps 항목을 정규화한다. prefix 충돌 검증 포함.

    각 항목 dict 키:
      name (str), mode ("file"|"api"), inbox_dir (Path, file 모드만 의미),
      command_prefix (list[str]), whitelist_user_ids (list[str]),
      ack_message (str)
    """
    normalized: list[dict] = []
    seen_prefixes: dict[str, str] = {}  # prefix -> app_name

    for raw in apps_cfg:
        if not isinstance(raw, dict):
            continue
        name = raw.get("name", "").strip()
        if not name:
            raise ValueError(f"app_bridge.apps: 'name' 누락 — {raw}")
        mode = raw.get("mode", "file").strip().lower()
        if mode not in ("file", "api"):
            raise ValueError(
                f"app_bridge.apps[{name}]: 'mode' 는 file 또는 api 여야 함 (got: {mode})")

        prefixes = raw.get("command_prefix", []) or []
        if isinstance(prefixes, str):
            prefixes = [prefixes]
        prefixes = [p.strip() for p in prefixes if p and p.strip()]
        if not prefixes:
            raise ValueError(f"app_bridge.apps[{name}]: 'command_prefix' 비어있음")

        for p in prefixes:
            if not p.startswith("/"):
                raise ValueError(
                    f"app_bridge.apps[{name}]: command_prefix '{p}' 는 '/' 로 시작해야 함")
            if p in seen_prefixes and seen_prefixes[p] != name:
                raise ValueError(
                    f"app_bridge.apps: command_prefix '{p}' 가 "
                    f"앱 '{seen_prefixes[p]}' 와 '{name}' 에서 중복 등록됨")
            seen_prefixes[p] = name

        whitelist = raw.get("whitelist_user_ids", []) or []
        whitelist = [str(uid) for uid in whitelist]

        inbox_dir_raw = raw.get("inbox_dir", "")
        if mode == "file":
            inbox_dir = base_dir / (inbox_dir_raw or f"messages/external_inbox/{name}")
        else:
            inbox_dir = None  # api 모드는 inbox_dir 사용 안 함

        ack_message = raw.get("ack_message",
                              f"[{name}] 명령 접수 (ID={{id}})")

        normalized.append({
            "name": name,
            "mode": mode,
            "inbox_dir": inbox_dir,
            "command_prefix": prefixes,
            "whitelist_user_ids": whitelist,
            "ack_message": ack_message,
        })

    return normalized


@dataclass
class Config:
    # channels — telegram
    telegram_enabled: bool
    telegram_bot_token: str
    telegram_chat_id: str

    # channels — slack
    slack_enabled: bool
    slack_bot_token: str
    slack_app_token: str
    slack_channel_id: str

    # broadcast
    broadcast_snippet_length: int

    # polling
    inbox_check_interval: int
    outbox_interval: int
    progress_interval: int

    # archive
    message_retention_days: int

    # log
    log_dir: Path
    log_retention_days: int

    # claude
    claude_timeout: int
    claude_cooldown_retry_minutes: int
    claude_max_session_turns: int
    claude_working_dir: str

    # gsd
    gsd_enabled: bool
    gsd_timeout: int
    gsd_progress_check_interval: int
    gsd_auto_mode: bool
    gsd_auto_classify: bool
    gsd_classify_model: str
    gsd_session_timeout_minutes: int
    gsd_history_max_messages: int

    # mcp
    mcp_enabled: bool
    mcp_config_path: str

    # alert
    alert_quiet_start: int  # 알림 무음 시작 시각 (0~23, -1이면 비활성)
    alert_quiet_end: int    # 알림 무음 종료 시각 (0~23)

    # paths
    inbox_dir: Path
    outbox_dir: Path
    sent_dir: Path
    error_dir: Path
    archive_dir: Path
    workqueue_dir: Path
    plan_dir: Path

    # instance
    instance_id: str = ""

    # attachments
    claude_additional_dirs: list = field(default_factory=list)
    attachments_allowed_extensions: list = field(default_factory=lambda: ["txt", "md", "pdf"])
    attachments_max_file_size: int = 1_048_576  # 1MB
    attachments_temp_dir: Path = Path("messages/attachments")
    attachments_reject_message: str = "txt, md, pdf 파일만 지원합니다."

    # app_bridge — 외부 앱 통합 (file 모드 + api 모드)
    app_bridge_enabled: bool = False
    app_bridge_external_inbox_base: Path = Path("messages/external_inbox")
    app_bridge_response_timeout_sec: int = 60
    app_bridge_ack_timeout_sec: int = 5
    app_bridge_max_args_length: int = 1024
    app_bridge_apps: list = field(default_factory=list)  # list[dict]: name, mode, inbox_dir, command_prefix, whitelist_user_ids, ack_message

    def runtime_path(self, name: str) -> Path:
        """인스턴스별 런타임 파일 경로를 반환한다. /tmp/gsd-orchestrator-{hash}.{name}"""
        return Path(f"/tmp/gsd-orchestrator-{self.instance_id}.{name}")

    @classmethod
    def load(cls, config_path: str = "config.yaml") -> "Config":
        with open(config_path) as f:
            cfg = yaml.safe_load(f)

        base_dir = Path(config_path).parent.resolve()
        instance_id = _make_instance_id(base_dir)

        # ── 채널 설정 (하위 호환: channels 섹션 없으면 .env fallback) ──
        channels = cfg.get("channels", {})
        tg_cfg = channels.get("telegram", {})
        slack_cfg = channels.get("slack", {})

        telegram_enabled = tg_cfg.get("enabled", True) if channels else True
        telegram_chat_id = tg_cfg.get("chat_id", "") or os.environ.get("TELEGRAM_CHAT_ID", "")
        telegram_bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")

        slack_enabled = slack_cfg.get("enabled", False)
        slack_channel_id = slack_cfg.get("channel_id", "") or os.environ.get("SLACK_CHANNEL_ID", "")

        # 최소 하나의 채널 활성화 검증은 Orchestrator에서 수행

        return cls(
            # channels
            telegram_enabled=telegram_enabled,
            telegram_bot_token=telegram_bot_token,
            telegram_chat_id=telegram_chat_id,
            slack_enabled=slack_enabled,
            slack_bot_token=os.environ.get("SLACK_BOT_TOKEN", ""),
            slack_app_token=os.environ.get("SLACK_APP_TOKEN", ""),
            slack_channel_id=slack_channel_id,
            # broadcast
            broadcast_snippet_length=cfg.get("broadcast", {}).get("snippet_length", 500),
            # polling
            inbox_check_interval=cfg["polling"]["inbox_check_interval"],
            outbox_interval=cfg["polling"]["outbox_interval"],
            progress_interval=cfg["polling"].get("progress_interval", 30),
            # archive
            message_retention_days=cfg["archive"].get("message_retention_days", 30),
            # log
            log_dir=base_dir / cfg.get("log", {}).get("dir", "logs"),
            log_retention_days=cfg.get("log", {}).get("retention_days", 14),
            # claude
            claude_timeout=cfg["claude"]["timeout"],
            claude_cooldown_retry_minutes=cfg["claude"].get("cooldown_retry_minutes", 10),
            claude_max_session_turns=cfg["claude"]["max_session_turns"],
            claude_working_dir=str(base_dir / cfg["claude"].get("working_dir", "workspace")),
            claude_additional_dirs=[str(base_dir / d) for d in cfg["claude"].get("additional_dirs", [])],
            # gsd
            gsd_enabled=cfg.get("gsd", {}).get("enabled", False),
            gsd_timeout=cfg.get("gsd", {}).get("timeout", 600000),
            gsd_progress_check_interval=cfg.get("gsd", {}).get("progress_check_interval", 30),
            gsd_auto_mode=cfg.get("gsd", {}).get("auto_mode", True),
            gsd_auto_classify=cfg.get("gsd", {}).get("auto_classify", False),
            gsd_classify_model=cfg.get("gsd", {}).get("classify_model", "haiku"),
            gsd_session_timeout_minutes=cfg.get("gsd", {}).get("session_timeout_minutes", 30),
            gsd_history_max_messages=cfg.get("gsd", {}).get("history_max_messages", 3),
            # mcp
            mcp_enabled=cfg.get("mcp", {}).get("enabled", False),
            mcp_config_path=str(base_dir / "mcp-config.json"),
            # attachments
            attachments_allowed_extensions=cfg.get("attachments", {}).get("allowed_extensions", ["txt", "md", "pdf"]),
            attachments_max_file_size=cfg.get("attachments", {}).get("max_file_size", 1_048_576),
            attachments_temp_dir=base_dir / cfg.get("attachments", {}).get("temp_dir", "messages/attachments"),
            attachments_reject_message=cfg.get("attachments", {}).get("reject_message", "txt, md, pdf 파일만 지원합니다."),
            # alert
            alert_quiet_start=cfg.get("alert", {}).get("quiet_start", -1),
            alert_quiet_end=cfg.get("alert", {}).get("quiet_end", -1),
            # paths
            inbox_dir=base_dir / cfg["paths"]["inbox_dir"],
            outbox_dir=base_dir / cfg["paths"]["outbox_dir"],
            sent_dir=base_dir / cfg["paths"]["sent_dir"],
            error_dir=base_dir / cfg["paths"]["error_dir"],
            archive_dir=base_dir / cfg["paths"]["archive_dir"],
            workqueue_dir=base_dir / cfg["paths"].get("workqueue_dir", "messages/workqueue"),
            plan_dir=base_dir / cfg["paths"].get("plan_dir", "messages/plan"),
            # app_bridge
            app_bridge_enabled=cfg.get("app_bridge", {}).get("enabled", False),
            app_bridge_external_inbox_base=base_dir / cfg.get("app_bridge", {}).get(
                "external_inbox_base", "messages/external_inbox"),
            app_bridge_response_timeout_sec=cfg.get("app_bridge", {}).get("response_timeout_sec", 60),
            app_bridge_ack_timeout_sec=cfg.get("app_bridge", {}).get("ack_timeout_sec", 5),
            app_bridge_max_args_length=cfg.get("app_bridge", {}).get("max_args_length", 1024),
            app_bridge_apps=_normalize_app_bridge_apps(
                cfg.get("app_bridge", {}).get("apps", []) or [], base_dir),
            # instance
            instance_id=instance_id,
        )
