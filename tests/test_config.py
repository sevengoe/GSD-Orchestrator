import pytest

from gsd_orchestrator.config import Config


class TestConfig:
    def test_load_full_config(self, config_file, env_vars):
        config = Config.load(str(config_file))

        assert config.telegram_enabled is True
        assert config.telegram_bot_token == "test-token-123"
        assert config.telegram_chat_id == "999888777"
        assert config.slack_enabled is False
        assert config.broadcast_snippet_length == 500
        assert config.inbox_check_interval == 10
        assert config.outbox_interval == 3
        assert config.message_retention_days == 7
        assert config.log_dir == config_file.parent / "logs"
        assert config.log_retention_days == 14
        assert config.claude_timeout == 300
        assert config.claude_cooldown_retry_minutes == 10
        assert config.claude_max_session_turns == 20
        assert config.gsd_enabled is True
        assert config.gsd_timeout == 600000
        assert config.gsd_auto_classify is True
        assert config.gsd_classify_model == "haiku"

    def test_paths_relative_to_config(self, config_file, env_vars):
        config = Config.load(str(config_file))

        assert config.inbox_dir == config_file.parent / "messages" / "inbox"
        assert config.outbox_dir == config_file.parent / "messages" / "outbox"
        assert config.sent_dir == config_file.parent / "messages" / "sent"
        assert config.error_dir == config_file.parent / "messages" / "error"
        assert config.archive_dir == config_file.parent / "messages" / "archive"

    def test_legacy_config_fallback(self, legacy_config_file, env_vars):
        """기존 config (channels 섹션 없음)에서 .env fallback."""
        config = Config.load(str(legacy_config_file))

        assert config.telegram_enabled is True
        assert config.telegram_chat_id == "999888777"
        assert config.slack_enabled is False
        assert config.broadcast_snippet_length == 500  # default

    def test_gsd_defaults_when_section_missing(self, tmp_path, env_vars):
        config_path = tmp_path / "config.yaml"
        config_path.write_text("""
polling:
  inbox_check_interval: 10
  outbox_interval: 3
archive:
  message_retention_days: 30
claude:
  timeout: 300
  cooldown_retry_minutes: 10
  max_session_turns: 20
paths:
  inbox_dir: messages/inbox
  outbox_dir: messages/outbox
  sent_dir: messages/sent
  error_dir: messages/error
  archive_dir: messages/archive
""")
        config = Config.load(str(config_path))

        assert config.gsd_enabled is False
        assert config.gsd_timeout == 600000
        assert config.gsd_auto_classify is False
        assert config.gsd_classify_model == "haiku"
