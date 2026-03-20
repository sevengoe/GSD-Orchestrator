import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from gsd_orchestrator.archiver import Archiver, KST


def _create_json_file(directory: Path, filename: str) -> Path:
    path = directory / filename
    path.write_text(json.dumps({"id": filename}))
    return path


class TestArchiver:
    def test_archive_moves_files(self, tmp_dirs):
        archiver = Archiver(tmp_dirs["sent"], tmp_dirs["archive"], retention_days=30)

        _create_json_file(tmp_dirs["sent"], "msg1.json")
        _create_json_file(tmp_dirs["sent"], "msg2.json")

        archiver.run()

        # sent/는 비어있어야 함
        assert len(list(tmp_dirs["sent"].glob("*.json"))) == 0

        # archive/YYYY-MM-DD/ 에 파일이 있어야 함
        today = datetime.now(KST).strftime("%Y-%m-%d")
        archive_today = tmp_dirs["archive"] / today
        assert archive_today.exists()
        assert len(list(archive_today.glob("*.json"))) == 2

    def test_archive_creates_date_directory(self, tmp_dirs):
        archiver = Archiver(tmp_dirs["sent"], tmp_dirs["archive"])

        _create_json_file(tmp_dirs["sent"], "test.json")
        archiver.run()

        today = datetime.now(KST).strftime("%Y-%m-%d")
        assert (tmp_dirs["archive"] / today).is_dir()

    def test_archive_empty_sent(self, tmp_dirs):
        archiver = Archiver(tmp_dirs["sent"], tmp_dirs["archive"])
        archiver.run()  # 에러 없이 완료되어야 함

    def test_cleanup_expired(self, tmp_dirs):
        archiver = Archiver(tmp_dirs["sent"], tmp_dirs["archive"], retention_days=7)

        # 10일 전 아카이브 생성
        old_date = (datetime.now(KST) - timedelta(days=10)).strftime("%Y-%m-%d")
        old_dir = tmp_dirs["archive"] / old_date
        old_dir.mkdir(parents=True)
        _create_json_file(old_dir, "old.json")

        # 3일 전 아카이브 생성
        recent_date = (datetime.now(KST) - timedelta(days=3)).strftime("%Y-%m-%d")
        recent_dir = tmp_dirs["archive"] / recent_date
        recent_dir.mkdir(parents=True)
        _create_json_file(recent_dir, "recent.json")

        archiver.run()

        # 10일 전은 삭제, 3일 전은 유지
        assert not old_dir.exists()
        assert recent_dir.exists()

    def test_cleanup_keeps_non_date_dirs(self, tmp_dirs):
        archiver = Archiver(tmp_dirs["sent"], tmp_dirs["archive"], retention_days=1)

        # 날짜 형식이 아닌 디렉토리
        other_dir = tmp_dirs["archive"] / "not-a-date"
        other_dir.mkdir()

        archiver.run()

        assert other_dir.exists()

    def test_cleanup_boundary_day(self, tmp_dirs):
        archiver = Archiver(tmp_dirs["sent"], tmp_dirs["archive"], retention_days=7)

        # 정확히 7일 전 (경계)
        boundary_date = (datetime.now(KST) - timedelta(days=7)).strftime("%Y-%m-%d")
        boundary_dir = tmp_dirs["archive"] / boundary_date
        boundary_dir.mkdir(parents=True)
        _create_json_file(boundary_dir, "boundary.json")

        archiver.run()

        # 7일 전은 cutoff 이전이므로 삭제
        assert not boundary_dir.exists()

    def test_sent_dir_not_exists(self, tmp_path):
        sent = tmp_path / "nonexistent_sent"
        archive = tmp_path / "archive"
        archive.mkdir()

        archiver = Archiver(sent, archive)
        archiver.run()  # 에러 없이 완료

    def test_archive_dir_not_exists_for_cleanup(self, tmp_path):
        sent = tmp_path / "sent"
        sent.mkdir()
        archive = tmp_path / "nonexistent_archive"

        archiver = Archiver(sent, archive)
        archiver.run()  # 에러 없이 완료
