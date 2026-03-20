import os
import pytest
from pathlib import Path


@pytest.fixture
def tmp_dirs(tmp_path):
    """메시지 디렉토리 세트를 임시 경로에 생성한다."""
    dirs = {
        "inbox": tmp_path / "messages" / "inbox",
        "outbox": tmp_path / "messages" / "outbox",
        "sent": tmp_path / "messages" / "sent",
        "error": tmp_path / "messages" / "error",
        "archive": tmp_path / "messages" / "archive",
    }
    for d in dirs.values():
        d.mkdir(parents=True)
    return dirs


@pytest.fixture
def config_file(tmp_path):
    """테스트용 config.yaml (멀티채널 형식)."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text("""
channels:
  telegram:
    enabled: true
    chat_id: "999888777"
  slack:
    enabled: false
    channel_id: ""

broadcast:
  snippet_length: 500

polling:
  inbox_check_interval: 10
  outbox_interval: 3

archive:
  message_retention_days: 7

log:
  dir: logs
  retention_days: 14

claude:
  mode: headless
  dangerously_skip_permissions: true
  timeout: 300
  cooldown_retry_minutes: 10
  max_session_turns: 20

gsd:
  enabled: true
  timeout: 600000
  progress_check_interval: 30
  auto_mode: true
  auto_classify: true
  classify_model: haiku

paths:
  inbox_dir: messages/inbox
  outbox_dir: messages/outbox
  sent_dir: messages/sent
  error_dir: messages/error
  archive_dir: messages/archive
""")
    return config_path


@pytest.fixture
def legacy_config_file(tmp_path):
    """하위 호환 테스트용 기존 형식 config.yaml (channels 섹션 없음)."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text("""
polling:
  inbox_check_interval: 10
  outbox_interval: 3

archive:
  message_retention_days: 7

log:
  dir: logs
  retention_days: 14

claude:
  mode: headless
  dangerously_skip_permissions: true
  timeout: 300
  cooldown_retry_minutes: 10
  max_session_turns: 20

gsd:
  enabled: true
  timeout: 600000
  progress_check_interval: 30
  auto_mode: true
  auto_classify: true
  classify_model: haiku

paths:
  inbox_dir: messages/inbox
  outbox_dir: messages/outbox
  sent_dir: messages/sent
  error_dir: messages/error
  archive_dir: messages/archive
""")
    return config_path


@pytest.fixture
def env_vars(monkeypatch):
    """테스트용 환경변수를 설정한다."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token-123")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "999888777")
