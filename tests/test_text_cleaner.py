"""text_cleaner 모듈 단위 테스트."""

import pytest

from gsd_orchestrator.text_cleaner import clean_text


class TestCleanTextCommon:
    """공통 정제 로직 테스트 (모든 파일 타입)."""

    def test_trailing_spaces_removed(self):
        text, summary = clean_text("hello   \nworld  ", "txt")
        assert "hello\nworld" == text

    def test_consecutive_blank_lines_compressed(self):
        text, summary = clean_text("a\n\n\n\n\nb", "txt")
        assert text == "a\n\nb"
        assert "연속 빈줄" in summary["removed_items"]

    def test_whitespace_only_lines_cleaned(self):
        text, _ = clean_text("a\n   \nb", "txt")
        assert "   " not in text

    def test_leading_trailing_whitespace_stripped(self):
        text, _ = clean_text("  \n\nhello\n\n  ", "txt")
        assert text == "hello"

    def test_summary_lengths(self):
        raw = "hello   \n\n\n\nworld  "
        text, summary = clean_text(raw, "txt")
        assert summary["original_length"] == len(raw)
        assert summary["cleaned_length"] == len(text)

    def test_clean_text_already_clean(self):
        text, summary = clean_text("already clean", "txt")
        assert text == "already clean"
        assert summary["removed_items"] == []

    def test_empty_input(self):
        text, summary = clean_text("", "txt")
        assert text == ""
        assert summary["original_length"] == 0


class TestCleanTextPdf:
    """PDF 특화 정제 로직 테스트."""

    def test_form_feed_removed(self):
        text, summary = clean_text("page1\fpage2", "pdf")
        assert "\f" not in text
        assert "페이지 구분자" in summary["removed_items"]

    def test_hyphen_linebreak_merged(self):
        text, summary = clean_text("docu-\nment", "pdf")
        assert "document" in text
        assert "하이픈 줄바꿈" in summary["removed_items"]

    def test_page_numbers_removed(self):
        text, _ = clean_text("content\n1\nmore content\n2\n", "pdf")
        lines = [l for l in text.split("\n") if l.strip()]
        assert all(not l.strip().isdigit() for l in lines)

    def test_page_number_patterns(self):
        """다양한 페이지 번호 형식 제거."""
        text, summary = clean_text("text\n- 3 -\nPage 12\np. 5\nmore", "pdf")
        assert "- 3 -" not in text
        assert "Page 12" not in text
        assert "p. 5" not in text
        assert "페이지 번호" in summary["removed_items"]

    def test_broken_chars_removed(self):
        text, summary = clean_text("hello\x00\x01world", "pdf")
        assert "\x00" not in text
        assert "\x01" not in text
        assert "깨진 문자" in summary["removed_items"]

    def test_common_header_footer_removed(self):
        text, summary = clean_text("Confidential\ncontent\nAll Rights Reserved", "pdf")
        assert "Confidential" not in text
        assert "All Rights Reserved" not in text
        assert "헤더/푸터" in summary["removed_items"]

    def test_draft_header_removed(self):
        text, _ = clean_text("DRAFT\ncontent here", "pdf")
        assert "DRAFT" not in text

    def test_copyright_footer_removed(self):
        text, _ = clean_text("content\nCopyright © 2024 Company", "pdf")
        assert "Copyright" not in text

    def test_pdf_combined_cleanup(self):
        """여러 정제가 동시에 적용되는 통합 테스트."""
        raw = "\x00Confidential\n\nconte-\nnt\f\n- 1 -\n\n\n\nmore text   "
        text, summary = clean_text(raw, "pdf")
        assert "\x00" not in text
        assert "Confidential" not in text
        assert "\f" not in text
        assert "content" in text
        assert len(summary["removed_items"]) > 0

    def test_pdf_specific_not_applied_to_txt(self):
        """txt 파일에는 PDF 특화 정제가 적용되지 않는다."""
        raw = "text\fmore"
        text, summary = clean_text(raw, "txt")
        # form feed는 PDF 정제에서만 제거 → txt에서는 남아 있음
        assert "\f" in text
        assert "페이지 구분자" not in summary["removed_items"]

    def test_md_no_pdf_cleanup(self):
        """md 파일에도 PDF 특화 정제가 적용되지 않는다."""
        raw = "docu-\nment"
        text, summary = clean_text(raw, "md")
        # 하이픈 줄바꿈 병합은 PDF 전용
        assert "docu-" in text
        assert "하이픈 줄바꿈" not in summary["removed_items"]
