import json
import re
from pathlib import Path

from gsd_orchestrator.inbox_writer import InboxWriter, extract_keyword


class TestExtractKeyword:
    def test_korean_text(self):
        assert extract_keyword("로그인 API 리팩터링해줘") == "로그인API리팩터링해줘"

    def test_english_text(self):
        assert extract_keyword("fix login bug") == "fixloginbug"

    def test_max_length(self):
        result = extract_keyword("abcdefghijklmnop", max_len=5)
        assert result == "abcde"

    def test_special_chars_only(self):
        assert extract_keyword("!@#$%^&*()") == "메시지"

    def test_empty_string(self):
        assert extract_keyword("") == "메시지"

    def test_mixed_text(self):
        result = extract_keyword("결제 모듈 구현")
        assert "결제" in result


class TestInboxWriter:
    def test_write_with_source(self, tmp_dirs):
        """새 형식: source dict로 저장."""
        writer = InboxWriter(tmp_dirs["inbox"])
        source = {
            "channel_type": "telegram",
            "channel_id": "123456",
            "user_id": "U789",
            "user_name": "김철수",
            "message_id": 42,
            "thread_ts": None,
        }
        path = writer.write(source, "로그인 수정해줘")

        assert path.exists()
        data = json.loads(path.read_text())
        assert data["source"]["channel_type"] == "telegram"
        assert data["source"]["channel_id"] == "123456"
        assert data["source"]["user_name"] == "김철수"
        assert data["chat_id"] == "123456"
        assert data["message_id"] == 42
        assert data["request"]["text"] == "로그인 수정해줘"
        assert data["response"] is None

    def test_write_backward_compat(self, tmp_dirs):
        """하위 호환: (chat_id, message_id, text) 형식."""
        writer = InboxWriter(tmp_dirs["inbox"])
        path = writer.write("123456", 42, "로그인 수정해줘")

        data = json.loads(path.read_text())
        assert data["source"]["channel_type"] == "telegram"
        assert data["source"]["channel_id"] == "123456"
        assert data["chat_id"] == "123456"
        assert data["message_id"] == 42
        assert data["keyword"] == "로그인수정해줘"
        assert data["mode"] == "default"
        assert data["request"]["text"] == "로그인 수정해줘"

    def test_write_gsd_mode(self, tmp_dirs):
        writer = InboxWriter(tmp_dirs["inbox"])
        source = {"channel_type": "slack", "channel_id": "C123",
                   "user_id": "U1", "user_name": "test",
                   "message_id": "ts1", "thread_ts": None}
        path = writer.write(source, "결제 모듈 구현", mode="gsd")

        data = json.loads(path.read_text())
        assert data["mode"] == "gsd"
        assert data["source"]["channel_type"] == "slack"

    def test_write_gsd_resume_mode(self, tmp_dirs):
        writer = InboxWriter(tmp_dirs["inbox"])
        path = writer.write("123", 1, "Stripe로 해줘", mode="gsd-resume")

        data = json.loads(path.read_text())
        assert data["mode"] == "gsd-resume"

    def test_filename_format(self, tmp_dirs):
        writer = InboxWriter(tmp_dirs["inbox"])
        path = writer.write("123", 1, "테스트 메시지")

        pattern = r"\d{8}_\d{6}_[a-f0-9]{8}\.json"
        assert re.match(pattern, path.name)

    def test_atomic_write_no_tmp_files(self, tmp_dirs):
        writer = InboxWriter(tmp_dirs["inbox"])
        writer.write("123", 1, "테스트")

        tmp_files = list(tmp_dirs["inbox"].glob(".*tmp"))
        assert len(tmp_files) == 0

    def test_pending_count(self, tmp_dirs):
        writer = InboxWriter(tmp_dirs["inbox"])
        assert writer.pending_count() == 0

        writer.write("123", 1, "첫번째")
        assert writer.pending_count() == 1

        writer.write("123", 2, "두번째")
        assert writer.pending_count() == 2

    def test_unique_filenames(self, tmp_dirs):
        writer = InboxWriter(tmp_dirs["inbox"])
        path1 = writer.write("123", 1, "같은 메시지")
        path2 = writer.write("123", 2, "같은 메시지")

        assert path1.name != path2.name

    def test_creates_directory_if_missing(self, tmp_path):
        inbox_dir = tmp_path / "nonexistent" / "inbox"
        writer = InboxWriter(inbox_dir)

        assert inbox_dir.exists()
