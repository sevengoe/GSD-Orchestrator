import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))


class Archiver:
    def __init__(self, sent_dir: Path, archive_dir: Path, retention_days: int = 30):
        self._sent_dir = sent_dir
        self._archive_dir = archive_dir
        self._retention_days = retention_days

    def run(self):
        """발송 완료된 파일을 일자별 아카이브로 이동하고, 만료된 아카이브를 삭제한다."""
        today = datetime.now(KST).strftime("%Y-%m-%d")
        self._archive_files(self._sent_dir, self._archive_dir / today)
        self._cleanup_expired()

    def _archive_files(self, src_dir: Path, dest_dir: Path):
        if not src_dir.exists():
            return
        files = list(src_dir.glob("*.json"))
        if not files:
            return
        dest_dir.mkdir(parents=True, exist_ok=True)
        for f in files:
            f.rename(dest_dir / f.name)

    def _cleanup_expired(self):
        if not self._archive_dir.exists():
            return
        cutoff = datetime.now(KST) - timedelta(days=self._retention_days)
        for date_dir in sorted(self._archive_dir.iterdir()):
            if not date_dir.is_dir():
                continue
            try:
                dir_date = datetime.strptime(date_dir.name, "%Y-%m-%d")
                dir_date = dir_date.replace(tzinfo=KST)
                if dir_date < cutoff:
                    shutil.rmtree(date_dir)
            except ValueError:
                continue
