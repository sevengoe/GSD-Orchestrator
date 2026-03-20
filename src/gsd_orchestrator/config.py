import os
from pathlib import Path
from dataclasses import dataclass

import yaml
from dotenv import load_dotenv

load_dotenv()


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

    # paths
    inbox_dir: Path
    outbox_dir: Path
    sent_dir: Path
    error_dir: Path
    archive_dir: Path

    @classmethod
    def load(cls, config_path: str = "config.yaml") -> "Config":
        with open(config_path) as f:
            cfg = yaml.safe_load(f)

        base_dir = Path(config_path).parent

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
            # gsd
            gsd_enabled=cfg.get("gsd", {}).get("enabled", False),
            gsd_timeout=cfg.get("gsd", {}).get("timeout", 600000),
            gsd_progress_check_interval=cfg.get("gsd", {}).get("progress_check_interval", 30),
            gsd_auto_mode=cfg.get("gsd", {}).get("auto_mode", True),
            gsd_auto_classify=cfg.get("gsd", {}).get("auto_classify", False),
            gsd_classify_model=cfg.get("gsd", {}).get("classify_model", "haiku"),
            # paths
            inbox_dir=base_dir / cfg["paths"]["inbox_dir"],
            outbox_dir=base_dir / cfg["paths"]["outbox_dir"],
            sent_dir=base_dir / cfg["paths"]["sent_dir"],
            error_dir=base_dir / cfg["paths"]["error_dir"],
            archive_dir=base_dir / cfg["paths"]["archive_dir"],
        )
