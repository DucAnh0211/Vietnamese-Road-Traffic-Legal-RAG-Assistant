from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ChunkMetadata:
    """Metadata needed for hierarchical legal retrieval and citation."""

    document_id: str
    document_title: str | None
    document_number: str | None
    source_url: str | None
    chapter: str | None
    section: str | None
    article_number: str
    article_title: str | None
    clause_number: str | None
    point_label: str | None
    unit_type: str
    part_index: int
    part_count: int
    body_char_start: int
    body_char_end: int

    def to_dict(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in asdict(self).items()
            if value is not None
        }


@dataclass(frozen=True, slots=True)
class LegalChunk:
    """A chunk ready to serialize or send to an embedding model."""

    chunk_id: str
    text: str
    metadata: ChunkMetadata

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "text": self.text,
            "metadata": self.metadata.to_dict(),
        }
