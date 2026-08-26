from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.chunking.chunker import LegalChunker


def _iter_documents(path: Path) -> Iterator[dict[str, Any]]:
    if path.suffix.lower() == ".txt":
        yield {
            "document_id": path.stem,
            "document_title": path.stem,
            "content": path.read_text(encoding="utf-8"),
        }
        return

    if path.suffix.lower() != ".jsonl":
        raise ValueError("Input must be a UTF-8 .txt or .jsonl file")

    with path.open(encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Invalid JSON on line {line_number}: {exc}"
                ) from exc
            if not isinstance(item, dict):
                raise ValueError(f"Line {line_number} must contain a JSON object")
            yield item


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Chunk Vietnamese legal text by article, clause, and point."
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/processed/legal_chunks.jsonl"),
    )
    parser.add_argument("--content-field", default="content")
    parser.add_argument("--max-chars", type=int, default=1600)
    parser.add_argument("--overlap-chars", type=int, default=120)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    chunker = LegalChunker(
        max_chars=args.max_chars,
        overlap_chars=args.overlap_chars,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    document_count = 0
    chunk_count = 0

    with args.output.open("w", encoding="utf-8", newline="\n") as output:
        for document in _iter_documents(args.input):
            content = document.get(args.content_field) or document.get("text")
            if not isinstance(content, str) or not content.strip():
                raise ValueError(
                    f"Document {document_count + 1} has no non-empty "
                    f"{args.content_field!r} or 'text' field"
                )
            chunks = chunker.chunk(
                content,
                document_id=document.get("document_id") or document.get("id"),
                document_title=document.get("document_title")
                or document.get("title"),
                document_number=document.get("document_number")
                or document.get("number"),
                source_url=document.get("source_url") or document.get("url"),
            )
            for chunk in chunks:
                output.write(
                    json.dumps(chunk.to_dict(), ensure_ascii=False) + "\n"
                )
            document_count += 1
            chunk_count += len(chunks)

    print(
        json.dumps(
            {
                "documents": document_count,
                "chunks": chunk_count,
                "output": str(args.output),
                "max_chars": args.max_chars,
                "overlap_chars": args.overlap_chars,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
