from __future__ import annotations

import hashlib
from dataclasses import dataclass

from src.chunking.metadata import ChunkMetadata, LegalChunk
from src.ingestion.legal_parser import (
    LegalArticle,
    LegalClause,
    LegalPoint,
    parse_legal_document,
)


@dataclass(frozen=True, slots=True)
class _AtomicUnit:
    article: LegalArticle
    clause: LegalClause | None
    point: LegalPoint | None
    body: str
    unit_type: str


class LegalChunker:
    """Chunk Vietnamese legal text by Điều -> Khoản -> Điểm.

    The deepest available legal unit is kept intact whenever possible. Character
    overlap is only introduced when one unit exceeds max_chars; overlap is
    never copied across two different points, clauses, or articles.
    """

    def __init__(
        self,
        max_chars: int = 1600,
        overlap_chars: int = 120,
        parent_context_chars: int = 280,
    ) -> None:
        if max_chars < 500:
            raise ValueError("max_chars must be at least 500")
        if overlap_chars < 0:
            raise ValueError("overlap_chars cannot be negative")
        if overlap_chars >= max_chars // 3:
            raise ValueError("overlap_chars must be less than one third of max_chars")
        if parent_context_chars < 0:
            raise ValueError("parent_context_chars cannot be negative")
        self.max_chars = max_chars
        self.overlap_chars = overlap_chars
        self.parent_context_chars = parent_context_chars

    def chunk(
        self,
        text: str,
        *,
        document_id: str | None = None,
        document_title: str | None = None,
        document_number: str | None = None,
        source_url: str | None = None,
    ) -> list[LegalChunk]:
        parsed = parse_legal_document(text)
        resolved_id = document_id or document_number or self._content_id(text)
        chunks: list[LegalChunk] = []

        for article in parsed.articles:
            for unit in self._atomic_units(article):
                if not unit.body.strip():
                    continue
                prefix = self._prefix(
                    unit,
                    document_title=document_title,
                )
                body_budget = max(80, self.max_chars - len(prefix) - 2)
                body_parts = self._split_body(unit.body, body_budget)

                for index, (body, start, end) in enumerate(body_parts):
                    chunk_text = f"{prefix}\n\n{body}" if prefix else body
                    metadata = ChunkMetadata(
                        document_id=resolved_id,
                        document_title=document_title,
                        document_number=document_number,
                        source_url=source_url,
                        chapter=article.chapter,
                        section=article.section,
                        article_number=article.number,
                        article_title=article.title or None,
                        clause_number=(
                            unit.clause.number if unit.clause is not None else None
                        ),
                        point_label=(
                            unit.point.label if unit.point is not None else None
                        ),
                        unit_type=unit.unit_type,
                        part_index=index,
                        part_count=len(body_parts),
                        body_char_start=start,
                        body_char_end=end,
                    )
                    chunks.append(
                        LegalChunk(
                            chunk_id=self._chunk_id(metadata),
                            text=chunk_text,
                            metadata=metadata,
                        )
                    )
        return chunks

    def _atomic_units(self, article: LegalArticle) -> list[_AtomicUnit]:
        if not article.clauses:
            return [
                _AtomicUnit(
                    article=article,
                    clause=None,
                    point=None,
                    body=article.lead_text,
                    unit_type="article",
                )
            ]

        units: list[_AtomicUnit] = []
        for clause in article.clauses:
            if clause.points:
                units.extend(
                    _AtomicUnit(
                        article=article,
                        clause=clause,
                        point=point,
                        body=point.text,
                        unit_type="point",
                    )
                    for point in clause.points
                )
            else:
                units.append(
                    _AtomicUnit(
                        article=article,
                        clause=clause,
                        point=None,
                        body=clause.lead_text,
                        unit_type="clause",
                    )
                )
        return units

    def _prefix(
        self,
        unit: _AtomicUnit,
        *,
        document_title: str | None,
    ) -> str:
        article = unit.article
        context_lines: list[str] = []
        if document_title:
            context_lines.append(self._clip(document_title, 220))
        if article.chapter:
            context_lines.append(self._clip(article.chapter, 220))
        if article.section:
            context_lines.append(self._clip(article.section, 220))

        article_label = f"Điều {article.number}."
        if article.title:
            article_label = f"{article_label} {article.title}"

        if article.lead_text and unit.unit_type != "article":
            context_lines.append(
                "Dẫn nhập điều: "
                + self._clip(article.lead_text, self.parent_context_chars)
            )

        locator_lines: list[str] = []
        if unit.clause is not None and unit.clause.number is not None:
            locator_lines.append(f"Khoản {unit.clause.number}.")
        if (
            unit.point is not None
            and unit.clause is not None
            and unit.clause.lead_text
        ):
            context_lines.append(
                "Dẫn nhập khoản: "
                + self._clip(unit.clause.lead_text, self.parent_context_chars)
            )
        if unit.point is not None:
            locator_lines.append(f"Điểm {unit.point.label})")

        limit = self.max_chars - 100
        lower_locators = "\n".join(locator_lines)
        article_budget = max(
            80,
            limit - len(lower_locators) - (1 if lower_locators else 0),
        )
        article_line = self._clip(article_label, article_budget)
        locator_text = "\n".join([article_line, *locator_lines])
        context_budget = limit - len(locator_text) - 1
        if context_budget <= 0 or not context_lines:
            return locator_text
        context_text = self._clip("\n".join(context_lines), context_budget)
        return f"{context_text}\n{locator_text}" if context_text else locator_text

    @staticmethod
    def _clip(text: str, limit: int) -> str:
        text = text.strip()
        if limit <= 0:
            return ""
        if len(text) <= limit:
            return text
        clipped = text[: max(1, limit - 1)]
        if " " in clipped:
            clipped = clipped.rsplit(" ", 1)[0]
        return clipped.rstrip() + "…"

    def _split_body(self, text: str, budget: int) -> list[tuple[str, int, int]]:
        text = text.strip()
        if len(text) <= budget:
            return [(text, 0, len(text))]

        overlap = min(self.overlap_chars, max(0, budget // 5))
        parts: list[tuple[str, int, int]] = []
        start = 0

        while start < len(text):
            hard_end = min(len(text), start + budget)
            end = self._best_boundary(text, start, hard_end)
            raw = text[start:end]
            leading = len(raw) - len(raw.lstrip())
            trailing = len(raw) - len(raw.rstrip())
            clean_start = start + leading
            clean_end = end - trailing
            body = text[clean_start:clean_end]
            if body:
                parts.append((body, clean_start, clean_end))
            if end >= len(text):
                break

            next_start = max(start + 1, clean_end - overlap)
            whitespace = text.find(
                " ", next_start, min(clean_end + 1, next_start + 24)
            )
            if whitespace != -1:
                next_start = whitespace + 1
            if next_start >= clean_end:
                next_start = clean_end
            start = next_start

        return parts

    @staticmethod
    def _best_boundary(text: str, start: int, hard_end: int) -> int:
        if hard_end >= len(text):
            return len(text)
        search_start = start + int((hard_end - start) * 0.7)
        window = text[search_start:hard_end]
        candidates: list[int] = []
        for separator in ("\n", ". ", "; ", ", ", " "):
            position = window.rfind(separator)
            if position != -1:
                candidates.append(search_start + position + len(separator))
        return max(candidates) if candidates else hard_end

    @staticmethod
    def _content_id(text: str) -> str:
        digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]
        return f"document-{digest}"

    @staticmethod
    def _chunk_id(metadata: ChunkMetadata) -> str:
        locator = "|".join(
            [
                metadata.document_id,
                metadata.article_number,
                metadata.clause_number or "",
                metadata.point_label or "",
                str(metadata.part_index),
            ]
        )
        return hashlib.sha1(locator.encode("utf-8")).hexdigest()[:20]
