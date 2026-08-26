from __future__ import annotations

from src.chunking.chunker import LegalChunker
from src.ingestion.legal_parser import parse_legal_document


SAMPLE_TEXT = """
LUẬT MẪU
Chương I
QUY ĐỊNH CHUNG
Mục 1
PHẠM VI ÁP DỤNG
Điều 1. Phạm vi điều chỉnh
1. Người tham gia giao thông có các nghĩa vụ sau:
a) Tuân thủ quy tắc giao thông đường bộ;
b) Chấp hành hiệu lệnh của người điều khiển giao thông.
2. Cơ quan có thẩm quyền tổ chức thực hiện quy định này.
Điều 2. Đối tượng áp dụng
Quy định này áp dụng đối với cơ quan, tổ chức và cá nhân có liên quan.
"""


def test_parser_builds_article_clause_point_hierarchy() -> None:
    document = parse_legal_document(SAMPLE_TEXT)

    assert len(document.articles) == 2
    first = document.articles[0]
    assert first.number == "1"
    assert first.title == "Phạm vi điều chỉnh"
    assert first.chapter == "Chương I - QUY ĐỊNH CHUNG"
    assert first.section == "Mục 1 - PHẠM VI ÁP DỤNG"
    assert [clause.number for clause in first.clauses] == ["1", "2"]
    assert [point.label for point in first.clauses[0].points] == ["a", "b"]


def test_chunker_uses_deepest_legal_unit_and_keeps_parent_context() -> None:
    chunks = LegalChunker().chunk(
        SAMPLE_TEXT,
        document_id="law-sample",
        document_title="Luật mẫu",
        document_number="01/2026/QH",
        source_url="https://example.test/law",
    )

    assert len(chunks) == 4
    assert [chunk.metadata.unit_type for chunk in chunks] == [
        "point",
        "point",
        "clause",
        "article",
    ]
    point = chunks[0]
    assert point.metadata.article_number == "1"
    assert point.metadata.clause_number == "1"
    assert point.metadata.point_label == "a"
    assert "Điều 1. Phạm vi điều chỉnh" in point.text
    assert "Khoản 1." in point.text
    assert "Dẫn nhập khoản:" in point.text
    assert "Điểm a)" in point.text
    assert point.metadata.source_url == "https://example.test/law"


def test_overlap_is_only_used_inside_an_oversized_clause() -> None:
    sentence = (
        "Người điều khiển phương tiện phải quan sát và bảo đảm an toàn "
        "trước khi chuyển hướng. "
    )
    text = "Điều 9. Quy tắc chuyển hướng\n1. " + sentence * 20
    chunker = LegalChunker(max_chars=520, overlap_chars=60)

    chunks = chunker.chunk(text, document_id="long-clause")

    assert len(chunks) > 1
    assert all(len(chunk.text) <= 520 for chunk in chunks)
    assert all(chunk.metadata.unit_type == "clause" for chunk in chunks)
    assert all(chunk.metadata.article_number == "9" for chunk in chunks)
    assert all(chunk.metadata.clause_number == "1" for chunk in chunks)
    assert [chunk.metadata.part_index for chunk in chunks] == list(
        range(len(chunks))
    )
    assert all(chunk.metadata.part_count == len(chunks) for chunk in chunks)
    for previous, current in zip(chunks, chunks[1:]):
        assert current.metadata.body_char_start < previous.metadata.body_char_end
        overlap = (
            previous.metadata.body_char_end
            - current.metadata.body_char_start
        )
        assert 0 < overlap <= 60


def test_overlap_never_crosses_point_boundary() -> None:
    text = """
Điều 3. Hành vi bị nghiêm cấm
1. Các hành vi sau đây bị nghiêm cấm:
a) Hành vi thứ nhất.
b) Hành vi thứ hai.
"""
    chunks = LegalChunker().chunk(text, document_id="boundaries")

    assert len(chunks) == 2
    assert chunks[0].metadata.point_label == "a"
    assert chunks[1].metadata.point_label == "b"
    assert "Hành vi thứ hai" not in chunks[0].text
    assert "Hành vi thứ nhất" not in chunks[1].text


def test_article_title_can_be_on_the_next_line() -> None:
    document = parse_legal_document(
        "Điều 10.\nTrách nhiệm thi hành\nCơ quan có trách nhiệm thi hành."
    )

    assert document.articles[0].title == "Trách nhiệm thi hành"
    assert (
        document.articles[0].lead_text
        == "Cơ quan có trách nhiệm thi hành."
    )


def test_untitled_article_can_start_directly_with_a_clause() -> None:
    document = parse_legal_document(
        "Điều 11.\n1. Nội dung của khoản thứ nhất."
    )

    article = document.articles[0]
    assert article.title == ""
    assert article.clauses[0].number == "1"
    assert article.clauses[0].lead_text == "Nội dung của khoản thứ nhất."


def test_long_parent_context_does_not_remove_legal_locator() -> None:
    long_context = "Nội dung dẫn nhập rất dài " * 30
    text = (
        "Điều 12. Tiêu đề điều rất dài "
        + ("và cần được rút gọn " * 20)
        + "\n1. "
        + long_context
        + "\na) Nội dung của điểm."
    )
    chunks = LegalChunker(max_chars=500, overlap_chars=40).chunk(
        text,
        document_id="long-context",
        document_title="Tên văn bản " * 30,
    )

    assert len(chunks) == 1
    assert len(chunks[0].text) <= 500
    assert "Điều 12." in chunks[0].text
    assert "Khoản 1." in chunks[0].text
    assert "Điểm a)" in chunks[0].text
