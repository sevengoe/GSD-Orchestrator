"""변환된 텍스트 정제 모듈.

불필요한 공백, 헤더/푸터, 페이지번호를 제거하고
PDF 특화 정제(페이지 구분자, 깨진 문자, 하이픈 줄바꿈 병합)를 수행한다.
"""

import re

# ── 공통 패턴 ──────────────────────────────────────────────

# 연속 빈줄 (3줄 이상 → 2줄로)
_RE_MULTI_BLANK = re.compile(r"\n{3,}")
# 줄 끝 공백
_RE_TRAILING_SPACE = re.compile(r"[ \t]+$", re.MULTILINE)
# 줄 앞 탭 → 스페이스 변환용이 아닌, 순수 탭+스페이스만으로 된 줄
_RE_WHITESPACE_ONLY_LINE = re.compile(r"^[ \t]+$", re.MULTILINE)

# ── PDF 특화 패턴 ──────────────────────────────────────────

# 페이지 번호 패턴: 줄 전체가 숫자(또는 - 1 -, Page 1 등)
_RE_PAGE_NUMBER = re.compile(
    r"^(?:"
    r"\s*-?\s*\d{1,4}\s*-?\s*"       # 1, -1-, - 1 -
    r"|page\s*\d{1,4}"                # Page 1, page12
    r"|p\.\s*\d{1,4}"                 # p. 3
    r")$",
    re.IGNORECASE | re.MULTILINE,
)
# 페이지 구분자: form feed (\f)
_RE_FORM_FEED = re.compile(r"\f")
# 하이픈 줄바꿈 병합: 단어-\n단어 → 단어단어
_RE_HYPHEN_LINEBREAK = re.compile(r"(\w)-\n(\w)")
# 깨진 문자: 제어 문자 (탭/개행/캐리지리턴 제외)
_RE_BROKEN_CHARS = re.compile(r"[\x00-\x08\x0b\x0e-\x1f\x7f-\x9f]")
# 반복 헤더/푸터 (동일 텍스트가 여러 페이지에 반복되는 경우는
# 단순 정규식으로 처리 어려우므로, 흔한 패턴만 제거)
_RE_COMMON_HEADER_FOOTER = re.compile(
    r"^(?:confidential|draft|all rights reserved|copyright\s*©?).*$",
    re.IGNORECASE | re.MULTILINE,
)


def clean_text(raw_text: str, file_type: str) -> tuple[str, dict]:
    """텍스트 정제 후 (정제된 텍스트, 요약 dict)를 반환한다.

    Args:
        raw_text: 추출된 원본 텍스트.
        file_type: 파일 확장자 (txt, md, pdf).

    Returns:
        (cleaned_text, summary) 튜플.
        summary 키: original_length, cleaned_length, removed_items.
    """
    original_length = len(raw_text)
    removed_items: list[str] = []
    text = raw_text

    # PDF 특화 정제
    if file_type == "pdf":
        text, pdf_removed = _clean_pdf(text)
        removed_items.extend(pdf_removed)

    # 공통 정제
    text, common_removed = _clean_common(text)
    removed_items.extend(common_removed)

    summary = {
        "original_length": original_length,
        "cleaned_length": len(text),
        "removed_items": removed_items,
    }
    return text, summary


# ── 내부 헬퍼 ──────────────────────────────────────────────


def _clean_pdf(text: str) -> tuple[str, list[str]]:
    """PDF 특화 정제를 수행한다."""
    removed: list[str] = []

    # 깨진 문자 제거
    cleaned = _RE_BROKEN_CHARS.sub("", text)
    if cleaned != text:
        removed.append("깨진 문자")
        text = cleaned

    # 페이지 구분자(form feed) 정리 → 빈줄로 대체
    cleaned = _RE_FORM_FEED.sub("\n", text)
    if cleaned != text:
        removed.append("페이지 구분자")
        text = cleaned

    # 하이픈 줄바꿈 병합
    cleaned = _RE_HYPHEN_LINEBREAK.sub(r"\1\2", text)
    if cleaned != text:
        removed.append("하이픈 줄바꿈")
        text = cleaned

    # 페이지 번호 제거
    cleaned = _RE_PAGE_NUMBER.sub("", text)
    if cleaned != text:
        removed.append("페이지 번호")
        text = cleaned

    # 흔한 헤더/푸터 제거
    cleaned = _RE_COMMON_HEADER_FOOTER.sub("", text)
    if cleaned != text:
        removed.append("헤더/푸터")
        text = cleaned

    return text, removed


def _clean_common(text: str) -> tuple[str, list[str]]:
    """공통 텍스트 정제를 수행한다."""
    removed: list[str] = []

    # 공백만 있는 줄 → 빈줄로
    cleaned = _RE_WHITESPACE_ONLY_LINE.sub("", text)
    if cleaned != text:
        text = cleaned

    # 줄 끝 공백 제거
    cleaned = _RE_TRAILING_SPACE.sub("", text)
    if cleaned != text:
        removed.append("줄 끝 공백")
        text = cleaned

    # 연속 빈줄 압축 (3줄 이상 → 2줄)
    cleaned = _RE_MULTI_BLANK.sub("\n\n", text)
    if cleaned != text:
        removed.append("연속 빈줄")
        text = cleaned

    # 앞뒤 공백 제거
    stripped = text.strip()
    if len(stripped) != len(text):
        text = stripped

    return text, removed
