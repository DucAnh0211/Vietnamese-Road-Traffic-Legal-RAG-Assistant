from __future__ import annotations

import re
from dataclasses import dataclass, field


_CHAPTER_RE = re.compile(
    r"^(Chương\s+[IVXLCDM\d]+)\s*[\.:]?\s*(.*)$", re.IGNORECASE
)
_SECTION_RE = re.compile(
    r"^(Mục\s+\d+[A-Za-zĐđ]?)\s*[\.:]?\s*(.*)$", re.IGNORECASE
)
_ARTICLE_RE = re.compile(
    r"^Điều\s+(\d+(?:[A-Za-zĐđ])?)\s*[\.:\)]?\s*(.*)$",
    re.IGNORECASE,
)
_CLAUSE_RE = re.compile(r"^(\d+)\s*[\.\)]\s*(.*)$")
_POINT_RE = re.compile(r"^([A-Za-zĐđ])\s*[\)\.]\s*(.*)$")


def _join_text(current: str, addition: str) -> str:
    if not current:
        return addition
    return f"{current} {addition}"


def _normalize_line(line: str) -> str:
    return re.sub(r"\s+", " ", line.replace("\u00a0", " ")).strip()


@dataclass(slots=True)
class LegalPoint:
    label: str
    text: str = ""


@dataclass(slots=True)
class LegalClause:
    number: str | None
    lead_text: str = ""
    points: list[LegalPoint] = field(default_factory=list)


@dataclass(slots=True)
class LegalArticle:
    number: str
    title: str = ""
    lead_text: str = ""
    chapter: str | None = None
    section: str | None = None
    clauses: list[LegalClause] = field(default_factory=list)


@dataclass(slots=True)
class LegalDocument:
    articles: list[LegalArticle]
    preamble: str = ""


def parse_legal_document(text: str) -> LegalDocument:
    """Parse Vietnamese legal text into article, clause, and point hierarchy.

    The parser is deliberately line based. Legal headings must begin a logical
    line, which prevents most in-sentence numbers from being misclassified.
    Blank lines and PDF line wrapping are normalized during parsing.
    """

    articles: list[LegalArticle] = []
    preamble = ""
    current_chapter: str | None = None
    current_section: str | None = None
    pending_heading: str | None = None
    current_article: LegalArticle | None = None
    current_clause: LegalClause | None = None
    current_point: LegalPoint | None = None

    def finish_article() -> None:
        nonlocal current_article, current_clause, current_point
        if current_article is not None:
            articles.append(current_article)
        current_article = None
        current_clause = None
        current_point = None

    for raw_line in text.splitlines():
        line = _normalize_line(raw_line)
        if not line:
            continue

        chapter_match = _CHAPTER_RE.match(line)
        if chapter_match:
            finish_article()
            label, title = chapter_match.groups()
            current_chapter = label.title()
            if title:
                current_chapter = f"{current_chapter} - {title}"
                pending_heading = None
            else:
                pending_heading = "chapter"
            current_section = None
            continue

        section_match = _SECTION_RE.match(line)
        if section_match:
            finish_article()
            label, title = section_match.groups()
            current_section = label.title()
            if title:
                current_section = f"{current_section} - {title}"
                pending_heading = None
            else:
                pending_heading = "section"
            continue

        article_match = _ARTICLE_RE.match(line)
        if article_match:
            finish_article()
            number, title = article_match.groups()
            current_article = LegalArticle(
                number=number,
                title=title,
                chapter=current_chapter,
                section=current_section,
            )
            pending_heading = None
            continue

        if current_article is None:
            if pending_heading == "chapter" and current_chapter:
                current_chapter = f"{current_chapter} - {line}"
                pending_heading = None
            elif pending_heading == "section" and current_section:
                current_section = f"{current_section} - {line}"
                pending_heading = None
            else:
                preamble = _join_text(preamble, line)
            continue

        clause_match = _CLAUSE_RE.match(line)
        if clause_match:
            number, clause_text = clause_match.groups()
            current_clause = LegalClause(number=number, lead_text=clause_text)
            current_article.clauses.append(current_clause)
            current_point = None
            continue

        point_match = _POINT_RE.match(line)
        if point_match:
            label, point_text = point_match.groups()
            if current_clause is None:
                current_clause = LegalClause(number=None)
                current_article.clauses.append(current_clause)
            current_point = LegalPoint(label=label.lower(), text=point_text)
            current_clause.points.append(current_point)
            continue

        if (
            not current_article.title
            and not current_article.lead_text
            and not current_article.clauses
        ):
            current_article.title = line
            continue

        if current_point is not None:
            current_point.text = _join_text(current_point.text, line)
        elif current_clause is not None:
            current_clause.lead_text = _join_text(current_clause.lead_text, line)
        else:
            current_article.lead_text = _join_text(
                current_article.lead_text, line
            )

    finish_article()
    return LegalDocument(articles=articles, preamble=preamble)
