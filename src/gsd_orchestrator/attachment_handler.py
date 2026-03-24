"""첨부파일 처리 전담 모듈.

화이트리스트 검증, 파일 다운로드, 텍스트 추출, 메타데이터 생성을 담당한다.
"""

import logging
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))


def validate_file(
    filename: str,
    size: int,
    allowed_extensions: list[str],
    max_file_size: int,
    reject_message: str,
) -> str | None:
    """화이트리스트/크기 체크. 거부 시 안내 메시지 반환, 통과 시 None."""
    ext = _get_extension(filename)
    if ext not in allowed_extensions:
        return reject_message
    if size > max_file_size:
        max_mb = max_file_size / (1024 * 1024)
        return f"파일 크기가 {max_mb:.0f}MB를 초과합니다."
    return None


async def download_file_telegram(bot, file_id: str, dest_dir: Path) -> Path:
    """텔레그램 Bot API로 파일 다운로드 → dest_dir에 임시 저장."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    tg_file = await bot.get_file(file_id)
    local_path = dest_dir / f"tg_{file_id}"
    await tg_file.download_to_drive(custom_path=str(local_path))
    return local_path


async def download_file_slack(client, url_private: str, dest_dir: Path, filename: str) -> Path:
    """슬랙 url_private_download로 파일 다운로드 → dest_dir에 임시 저장."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    import aiohttp
    token = client.token
    local_path = dest_dir / f"slack_{filename}"
    async with aiohttp.ClientSession() as session:
        async with session.get(
            url_private,
            headers={"Authorization": f"Bearer {token}"},
        ) as resp:
            resp.raise_for_status()
            local_path.write_bytes(await resp.read())
    return local_path


def extract_text(filepath: Path, original_filename: str) -> str:
    """txt/md는 직접 읽기, pdf는 pdfminer.six로 텍스트 추출.

    실패 시 사용자 안내 메시지를 반환한다 (접두사 "[오류]").
    """
    ext = _get_extension(original_filename)

    if ext in ("txt", "md"):
        return _read_text_file(filepath)
    if ext == "pdf":
        return _extract_pdf(filepath)

    return f"[오류] 지원하지 않는 파일 형식입니다: {ext}"


def build_metadata(filename: str, size: int) -> dict:
    """메타데이터 dict 생성 (파일명, 확장자, 업로드일자, 용량)."""
    return {
        "filename": filename,
        "extension": _get_extension(filename),
        "uploaded_at": datetime.now(KST).isoformat(),
        "size_bytes": size,
    }


def cleanup_temp_file(filepath: Path) -> None:
    """임시 파일 삭제. 실패 시 경고 로그만 남긴다."""
    try:
        if filepath.exists():
            filepath.unlink()
    except OSError as e:
        logger.warning(f"임시 파일 삭제 실패: {filepath} — {e}")


# ── 내부 헬퍼 ──────────────────────────────────────────────


def _get_extension(filename: str) -> str:
    """파일명에서 확장자를 소문자로 추출한다."""
    _, ext = os.path.splitext(filename)
    return ext.lstrip(".").lower()


def _read_text_file(filepath: Path) -> str:
    """텍스트 파일 읽기. 인코딩 감지 시 utf-8 → cp949 순으로 시도."""
    for encoding in ("utf-8", "cp949", "latin-1"):
        try:
            return filepath.read_text(encoding=encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return "[오류] 파일 인코딩을 인식할 수 없습니다."


def _extract_pdf(filepath: Path) -> str:
    """pdfminer.six로 PDF 텍스트 추출. 실패 유형별 안내 메시지 반환."""
    try:
        from pdfminer.high_level import extract_text as pdfminer_extract
        from pdfminer.pdfparser import PDFSyntaxError
        from pdfminer.pdfdocument import PDFPasswordIncorrect
    except ImportError:
        return "[오류] PDF 처리 모듈(pdfminer.six)이 설치되지 않았습니다."

    try:
        text = pdfminer_extract(str(filepath))
    except PDFPasswordIncorrect:
        return "[오류] 이 PDF는 보호되어 열 수 없습니다."
    except PDFSyntaxError:
        return "[오류] PDF 파일 형식이 올바르지 않습니다."
    except Exception as e:
        logger.error(f"PDF 추출 실패: {filepath} — {e}")
        return f"[오류] PDF 텍스트 추출에 실패했습니다: {e}"

    if not text or not text.strip():
        return "[오류] 이 PDF는 이미지로 구성되어 텍스트 추출이 불가합니다."

    return text
