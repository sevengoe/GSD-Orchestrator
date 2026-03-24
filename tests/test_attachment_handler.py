"""attachment_handler 모듈 단위 테스트."""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from datetime import datetime

from gsd_orchestrator.attachment_handler import (
    validate_file,
    extract_text,
    build_metadata,
    cleanup_temp_file,
    _get_extension,
)

# ── 공통 설정 ──────────────────────────────────────────────

ALLOWED = ["txt", "md", "pdf"]
MAX_SIZE = 1 * 1024 * 1024  # 1MB
REJECT_MSG = "txt, md, pdf 파일만 지원합니다."


# ── validate_file 테스트 ──────────────────────────────────


class TestValidateFile:
    def test_allowed_txt(self):
        assert validate_file("readme.txt", 100, ALLOWED, MAX_SIZE, REJECT_MSG) is None

    def test_allowed_md(self):
        assert validate_file("doc.MD", 100, ALLOWED, MAX_SIZE, REJECT_MSG) is None

    def test_allowed_pdf(self):
        assert validate_file("report.pdf", 100, ALLOWED, MAX_SIZE, REJECT_MSG) is None

    def test_reject_unsupported_extension(self):
        result = validate_file("image.png", 100, ALLOWED, MAX_SIZE, REJECT_MSG)
        assert result == REJECT_MSG

    def test_reject_no_extension(self):
        result = validate_file("Makefile", 100, ALLOWED, MAX_SIZE, REJECT_MSG)
        assert result == REJECT_MSG

    def test_reject_oversized_file(self):
        result = validate_file("big.pdf", MAX_SIZE + 1, ALLOWED, MAX_SIZE, REJECT_MSG)
        assert result is not None
        assert "1MB" in result

    def test_exact_max_size_passes(self):
        assert validate_file("ok.txt", MAX_SIZE, ALLOWED, MAX_SIZE, REJECT_MSG) is None

    def test_reject_exe(self):
        result = validate_file("virus.exe", 100, ALLOWED, MAX_SIZE, REJECT_MSG)
        assert result == REJECT_MSG


# ── _get_extension 테스트 ─────────────────────────────────


class TestGetExtension:
    def test_simple(self):
        assert _get_extension("file.txt") == "txt"

    def test_uppercase(self):
        assert _get_extension("FILE.PDF") == "pdf"

    def test_multiple_dots(self):
        assert _get_extension("my.file.name.md") == "md"

    def test_no_extension(self):
        assert _get_extension("README") == ""

    def test_dot_only(self):
        # os.path.splitext treats .gitignore as name with no extension
        assert _get_extension(".gitignore") == ""


# ── extract_text 테스트 ───────────────────────────────────


class TestExtractText:
    def test_txt_file(self, tmp_path):
        f = tmp_path / "hello.txt"
        f.write_text("Hello World", encoding="utf-8")
        result = extract_text(f, "hello.txt")
        assert result == "Hello World"

    def test_md_file(self, tmp_path):
        f = tmp_path / "doc.md"
        f.write_text("# Title\n\nBody", encoding="utf-8")
        result = extract_text(f, "doc.md")
        assert "# Title" in result

    def test_txt_cp949_encoding(self, tmp_path):
        f = tmp_path / "korean.txt"
        f.write_bytes("한글 테스트".encode("cp949"))
        result = extract_text(f, "korean.txt")
        assert "한글 테스트" in result

    def test_unsupported_extension(self, tmp_path):
        f = tmp_path / "data.csv"
        f.write_text("a,b,c")
        result = extract_text(f, "data.csv")
        assert result.startswith("[오류]")

    def test_pdf_success(self, tmp_path):
        f = tmp_path / "test.pdf"
        f.write_text("dummy")  # 실제로는 mock
        with patch(
            "gsd_orchestrator.attachment_handler.pdfminer_extract",
            return_value="PDF content here",
            create=True,
        ):
            # pdfminer가 _extract_pdf 내부에서 import되므로 해당 경로를 mock
            with patch(
                "pdfminer.high_level.extract_text", return_value="PDF content here"
            ):
                result = extract_text(f, "test.pdf")
                assert result == "PDF content here"

    def test_pdf_password_protected(self, tmp_path):
        f = tmp_path / "locked.pdf"
        f.write_text("dummy")
        from pdfminer.pdfdocument import PDFPasswordIncorrect

        with patch(
            "pdfminer.high_level.extract_text", side_effect=PDFPasswordIncorrect
        ):
            result = extract_text(f, "locked.pdf")
            assert "[오류]" in result
            assert "보호" in result

    def test_pdf_syntax_error(self, tmp_path):
        f = tmp_path / "broken.pdf"
        f.write_text("not a pdf")
        from pdfminer.pdfparser import PDFSyntaxError

        with patch(
            "pdfminer.high_level.extract_text", side_effect=PDFSyntaxError
        ):
            result = extract_text(f, "broken.pdf")
            assert "[오류]" in result
            assert "형식" in result

    def test_pdf_empty_result(self, tmp_path):
        f = tmp_path / "image_only.pdf"
        f.write_text("dummy")
        with patch("pdfminer.high_level.extract_text", return_value="   \n  "):
            result = extract_text(f, "image_only.pdf")
            assert "[오류]" in result
            assert "이미지" in result

    def test_pdf_import_missing(self, tmp_path):
        f = tmp_path / "test.pdf"
        f.write_text("dummy")
        with patch.dict("sys.modules", {"pdfminer": None, "pdfminer.high_level": None}):
            result = extract_text(f, "test.pdf")
            assert "[오류]" in result
            assert "pdfminer" in result.lower() or "설치" in result


# ── build_metadata 테스트 ─────────────────────────────────


class TestBuildMetadata:
    def test_basic_metadata(self):
        meta = build_metadata("report.pdf", 12345)
        assert meta["filename"] == "report.pdf"
        assert meta["extension"] == "pdf"
        assert meta["size_bytes"] == 12345
        assert "uploaded_at" in meta

    def test_uploaded_at_is_iso(self):
        meta = build_metadata("test.txt", 100)
        # ISO 형식이므로 파싱 가능해야 한다
        datetime.fromisoformat(meta["uploaded_at"])

    def test_extension_extracted(self):
        meta = build_metadata("My Document.PDF", 500)
        assert meta["extension"] == "pdf"


# ── cleanup_temp_file 테스트 ──────────────────────────────


class TestCleanupTempFile:
    def test_deletes_existing_file(self, tmp_path):
        f = tmp_path / "temp.txt"
        f.write_text("temp")
        cleanup_temp_file(f)
        assert not f.exists()

    def test_nonexistent_file_no_error(self, tmp_path):
        f = tmp_path / "ghost.txt"
        cleanup_temp_file(f)  # 예외 없이 통과

    def test_permission_error_logs_warning(self, tmp_path):
        f = tmp_path / "locked.txt"
        f.write_text("data")
        with patch.object(Path, "unlink", side_effect=OSError("denied")):
            cleanup_temp_file(f)  # 경고만 남기고 통과
