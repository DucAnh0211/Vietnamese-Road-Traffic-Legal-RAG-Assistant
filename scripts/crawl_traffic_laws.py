from __future__ import annotations

import argparse
import csv
import hashlib
import html
import io
import json
import re
import sys
import time
import unicodedata
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from pypdf import PdfReader

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.chunking.chunker import LegalChunker

VBPL_PAGE_URL = "https://vbpl.vn/van-ban/trung-uong"
VBPL_DETAIL_URL = "https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID={id}"
SEARCH_ACTION = "c529d164f28418e5898a834422629e64c6816af1"
DETAIL_ACTION = "0fb12b3561faa05adec51a82efb3e4f4f427f07b"
DEFAULT_INPUT = Path("data/raw/thuvienphapluat/luat-giao-thong-2025/documents.csv")
DEFAULT_RAW_DIR = Path("data/raw/vbpl/luat-giao-thong-2025")
DEFAULT_CHUNKS = Path("data/processed/luat-giao-thong-2025/legal_chunks.jsonl")

# Có trên danh mục VBPL cũ nhưng đang bị chỉ mục tìm kiếm mới bỏ sót.
KNOWN_VBPL_IDS = {"36/2024/QH15": "170620"}
GOVERNMENT_PDF_SOURCES = {
    "52/2024/TT-BGTVT": {
        "page_url": "https://vanban.chinhphu.vn/?docid=211877&pageid=27160",
        "files": [
            "https://datafiles.chinhphu.vn/cpp/files/vbpq/2024/11/52-bgtvt.pdf",
            "https://datafiles.chinhphu.vn/cpp/files/vbpq/2024/11/52-bgtvt-kem.pdf",
        ],
    },
    "53/2024/TT-BGTVT": {
        "page_url": "https://vanban.chinhphu.vn/?docid=211835&pageid=27160",
        "files": [
            "https://datafiles.chinhphu.vn/cpp/files/vbpq/2024/11/53-bgtvt.pdf",
            "https://datafiles.chinhphu.vn/cpp/files/vbpq/2024/11/pl1nhomchonguoiden8.pdf",
            "https://datafiles.chinhphu.vn/cpp/files/vbpq/2024/11/pl2nhomotochonguoitren8.pdf",
            "https://datafiles.chinhphu.vn/cpp/files/vbpq/2024/11/pl3nhomotochonguoichuyendung.pdf",
            "https://datafiles.chinhphu.vn/cpp/files/vbpq/2024/11/pl4nhomototaithongdung.pdf",
            "https://datafiles.chinhphu.vn/cpp/files/vbpq/2024/11/pl5nhomototaichuyendung.pdf",
            "https://datafiles.chinhphu.vn/cpp/files/vbpq/2024/11/pl6nhomotochuyendung.pdf",
            "https://datafiles.chinhphu.vn/cpp/files/vbpq/2024/11/pl7romooc.pdf",
            "https://datafiles.chinhphu.vn/cpp/files/vbpq/2024/11/pl8somi.pdf",
            "https://datafiles.chinhphu.vn/cpp/files/vbpq/2024/11/pl9xmcd1.pdf",
            "https://datafiles.chinhphu.vn/cpp/files/vbpq/2024/11/pl10xmcd5.pdf",
            "https://datafiles.chinhphu.vn/cpp/files/vbpq/2024/11/pl11xacdinhdientichpickupvan.pdf",
        ],
    },
    "56/2024/TT-BGTVT": {
        "page_url": "https://vanban.chinhphu.vn/?docid=212080&pageid=27160",
        "files": [
            "https://datafiles.chinhphu.vn/cpp/files/vbpq/2024/12/56-bgtvt.pdf",
            "https://datafiles.chinhphu.vn/cpp/files/vbpq/2024/12/qcvn432024bgtvt.pdf",
            "https://datafiles.chinhphu.vn/cpp/files/vbpq/2024/12/qcvn452024bgtvt.pdf",
            "https://datafiles.chinhphu.vn/cpp/files/vbpq/2024/12/qcvn1142024bgtvt.pdf",
            "https://datafiles.chinhphu.vn/cpp/files/vbpq/2024/12/qcvn1162024bgtvt.pdf",
        ],
    },
    "58/2024/TT-BGTVT": {
        "page_url": "https://vanban.chinhphu.vn/?docid=211983&pageid=27160",
        "files": [
            "https://datafiles.chinhphu.vn/cpp/files/vbpq/2024/12/58-bgtvt.pdf",
        ],
    },
}
RSC_TEXT_RE = re.compile(rb"([0-9a-z]+):T([0-9a-f]+),")
BLOCK_TAGS = {
    "address", "article", "blockquote", "br", "div", "footer", "h1", "h2",
    "h3", "h4", "h5", "h6", "header", "hr", "li", "ol", "p", "section",
    "table", "tbody", "td", "th", "tr", "ul",
}


class HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        del attrs
        if tag in {"script", "style"}:
            self.skip_depth += 1
        elif not self.skip_depth and tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self.skip_depth:
            self.skip_depth -= 1
        elif not self.skip_depth and tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.skip_depth:
            self.parts.append(data)

    def text(self) -> str:
        value = html.unescape("".join(self.parts)).replace("\u00a0", " ")
        lines = [re.sub(r"[ \t]+", " ", line).strip() for line in value.splitlines()]
        return "\n".join(line for line in lines if line).strip() + "\n"


def html_to_text(value: str) -> str:
    parser = HTMLTextExtractor()
    parser.feed(value)
    parser.close()
    return parser.text()


def pdf_to_text(value: bytes) -> str:
    reader = PdfReader(io.BytesIO(value))
    pages = [(page.extract_text() or "").replace("\u00a0", " ") for page in reader.pages]
    text = "\n".join(pages)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip() + "\n"


def appendix_chunks(
    text: str,
    *,
    document_id: str,
    document_title: str,
    document_number: str,
    source_url: str,
    file_name: str,
    max_chars: int,
    overlap_chars: int,
) -> list[dict[str, Any]]:
    prefix = f"{document_title}\nPhụ lục/tệp đính kèm: {file_name}"
    budget = max(500, max_chars - len(prefix) - 2)
    overlap = min(overlap_chars, budget // 5)
    parts: list[tuple[str, int, int]] = []
    start = 0
    clean_text = text.strip()
    while start < len(clean_text):
        hard_end = min(len(clean_text), start + budget)
        end = LegalChunker._best_boundary(clean_text, start, hard_end)
        body = clean_text[start:end].strip()
        if body:
            body_start = clean_text.find(body, start, end)
            parts.append((body, body_start, body_start + len(body)))
        if end >= len(clean_text):
            break
        start = max(start + 1, end - overlap)

    result: list[dict[str, Any]] = []
    for index, (body, body_start, body_end) in enumerate(parts):
        digest = hashlib.sha1(
            f"{document_id}|{file_name}|{index}".encode("utf-8")
        ).hexdigest()[:20]
        result.append(
            {
                "chunk_id": digest,
                "text": f"{prefix}\n\n{body}",
                "metadata": {
                    "document_id": document_id,
                    "document_title": document_title,
                    "document_number": document_number,
                    "source_url": source_url,
                    "article_number": "appendix",
                    "unit_type": "appendix",
                    "attachment_file": file_name,
                    "part_index": index,
                    "part_count": len(parts),
                    "body_char_start": body_start,
                    "body_char_end": body_end,
                },
            }
        )
    return result


def normalize_number(value: str | None) -> str:
    if not value:
        return ""
    value = unicodedata.normalize("NFC", value).upper().replace("Ð", "Đ")
    return re.sub(r"[^0-9A-ZĐ]+", "", value)


def safe_name(number: str) -> str:
    return re.sub(r"[^0-9A-Za-z]+", "_", number).strip("_").lower()


def title_query(url: str) -> str:
    slug = re.sub(r"-\d+$", "", Path(urlparse(url).path).stem)
    words = re.sub(r"[^0-9A-Za-z]+", " ", slug).split()
    stop = {
        "luat", "nghi", "dinh", "thong", "tu", "so", "2024", "qh15", "nd",
        "cp", "bca", "bqp", "bgtvt", "byt",
    }
    useful = [word for word in words if word.lower() not in stop and not word.isdigit()]
    return " ".join(useful[:10])


def parse_rsc(raw: bytes) -> dict[str, Any]:
    frames: dict[str, Any] = {}
    position = 0
    while position < len(raw):
        while position < len(raw) and raw[position] in b"\r\n":
            position += 1
        if position >= len(raw):
            break
        match = RSC_TEXT_RE.match(raw, position)
        if match:
            frame_id = match.group(1).decode("ascii")
            length = int(match.group(2), 16)
            start, end = match.end(), match.end() + length
            if end > len(raw):
                raise ValueError("RSC text frame bị cắt thiếu")
            frames[frame_id] = raw[start:end].decode("utf-8")
            position = end
            continue
        colon = raw.find(b":", position)
        if colon == -1:
            break
        frame_id = raw[position:colon].decode("ascii")
        tail = raw[colon + 1 :].decode("utf-8")
        value, consumed = json.JSONDecoder().raw_decode(tail)
        frames[frame_id] = value
        position = colon + 1 + len(tail[:consumed].encode("utf-8"))
    return frames


class VBPLCrawler:
    def __init__(self, timeout: float, delay: float, retries: int) -> None:
        self.delay = delay
        self.retries = retries
        self.client = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={
                "Accept": "text/x-component",
                "User-Agent": "legal-traffic-rag/1.0 (public legal-document research)",
            },
        )

    def close(self) -> None:
        self.client.close()

    def download_pdf(self, url: str) -> bytes:
        response = self.client.get(url, headers={"Accept": "application/pdf"})
        response.raise_for_status()
        if not response.content.startswith(b"%PDF"):
            raise ValueError(f"URL không trả về PDF hợp lệ: {url}")
        if self.delay:
            time.sleep(self.delay)
        return response.content

    def call(self, action: str, argument: Any) -> tuple[dict[str, Any], dict[str, str]]:
        headers = {
            "Next-Action": action,
            "Next-Router-State-Tree": '["",{"children":["__PAGE__",{}]},null,null,true]',
        }
        error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                response = self.client.post(
                    VBPL_PAGE_URL,
                    headers=headers,
                    content=json.dumps([argument], ensure_ascii=False).encode("utf-8"),
                )
                response.raise_for_status()
                frames = parse_rsc(response.content)
                result = frames.get("1")
                if not isinstance(result, dict):
                    raise ValueError("Phản hồi VBPL không có result frame")
                text_frames = {
                    key: value for key, value in frames.items() if isinstance(value, str)
                }
                if self.delay:
                    time.sleep(self.delay)
                return result, text_frames
            except (httpx.HTTPError, ValueError) as exc:
                error = exc
                if attempt < self.retries:
                    time.sleep(max(1.0, self.delay) * (attempt + 1))
        assert error is not None
        raise error

    def search(self, number: str, original_url: str) -> tuple[str | None, str]:
        if number in KNOWN_VBPL_IDS:
            return KNOWN_VBPL_IDS[number], "known-official-id"
        for query, option in ((number, "number"), (title_query(original_url), "title")):
            if not query:
                continue
            result, _ = self.call(
                SEARCH_ACTION,
                {
                    "pageNumber": 1,
                    "pageSize": 100,
                    "keyword": query,
                    "searchMode": "all",
                    "optionDoc": option,
                    "matchMode": "all_words",
                    "score": True,
                    "agencyLevel": "TW",
                },
            )
            for item in result.get("items") or []:
                if normalize_number(item.get("docNum")) == normalize_number(number):
                    return str(item["id"]), f"search-{option}"
        return None, "not-found"

    def detail(self, document_id: str) -> tuple[dict[str, Any], str]:
        detail, frames = self.call(DETAIL_ACTION, document_id)
        document_content = detail.get("documentContent") or {}
        reference = document_content.get("content") if isinstance(document_content, dict) else None
        raw_html = frames.get(reference[1:], "") if isinstance(reference, str) and reference.startswith("$") else reference
        if not isinstance(raw_html, str) or not raw_html.strip():
            raise ValueError(f"Văn bản {document_id} không có HTML toàn văn")
        return detail, raw_html


def load_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file))
    required = {"number", "type", "applies_to", "url"}
    if not rows or not required.issubset(rows[0]):
        raise ValueError(f"CSV phải có các cột: {', '.join(sorted(required))}")
    return rows


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Crawl toàn văn VBPL và chunk theo Điều/Khoản/Điểm."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    parser.add_argument("--chunks", type=Path, default=DEFAULT_CHUNKS)
    parser.add_argument("--max-chars", type=int, default=1600)
    parser.add_argument("--overlap-chars", type=int, default=120)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--delay", type=float, default=0.15)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--limit", type=int)
    return parser.parse_args()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    rows = load_rows(args.input)
    if args.limit is not None:
        rows = rows[: args.limit]
    documents_dir = args.raw_dir / "documents"
    documents_dir.mkdir(parents=True, exist_ok=True)
    args.chunks.parent.mkdir(parents=True, exist_ok=True)
    chunker = LegalChunker(args.max_chars, args.overlap_chars)
    crawler = VBPLCrawler(args.timeout, args.delay, args.retries)
    report: list[dict[str, Any]] = []
    raw_index: list[dict[str, Any]] = []
    chunks: list[dict[str, Any]] = []
    started_at = datetime.now(UTC).isoformat()

    try:
        for position, row in enumerate(rows, start=1):
            number = row["number"].strip()
            print(f"[{position}/{len(rows)}] {number}", flush=True)
            entry: dict[str, Any] = {
                "number": number,
                "type": row["type"],
                "applies_to": row["applies_to"],
                "original_url": row["url"],
            }
            try:
                document_id, resolution = crawler.search(number, row["url"])
                if document_id is None:
                    raise LookupError("Không tìm thấy số hiệu khớp tuyệt đối trên VBPL")
                detail, raw_html = crawler.detail(document_id)
                actual_number = str(detail.get("docNum") or "")
                if normalize_number(actual_number) != normalize_number(number):
                    raise ValueError(f"Số hiệu trả về không khớp: {actual_number}")
                text = html_to_text(raw_html)
                title = str(detail.get("title") or number)
                official_url = VBPL_DETAIL_URL.format(id=document_id)
                source_page_url = official_url
                content_format = "html"
                attachments: list[dict[str, Any]] = []
                if not re.search(r"(?im)^Điều\s+\d+", text):
                    fallback = GOVERNMENT_PDF_SOURCES.get(number)
                    if fallback is None:
                        raise ValueError("Toàn văn không có tiêu đề Điều nhận diện được")
                    for pdf_url in fallback["files"]:
                        pdf_bytes = crawler.download_pdf(pdf_url)
                        pdf_text = pdf_to_text(pdf_bytes)
                        if not pdf_text.strip():
                            raise ValueError(f"Không trích được chữ từ {pdf_url}")
                        attachments.append(
                            {
                                "file_name": Path(urlparse(pdf_url).path).name,
                                "url": pdf_url,
                                "content": pdf_text,
                                "bytes": pdf_bytes,
                            }
                        )
                    text = attachments[0]["content"]
                    if not re.search(r"(?im)^Điều\s+\d+", text):
                        raise ValueError("PDF chính thức không có tiêu đề Điều nhận diện được")
                    official_url = attachments[0]["url"]
                    source_page_url = str(fallback["page_url"])
                    content_format = "pdf"
                    resolution += "+government-pdf-fallback"

                stem = safe_name(number)
                text_path = documents_dir / f"{stem}.txt"
                json_path = documents_dir / f"{stem}.json"
                raw_files: dict[str, Any] = {}
                if content_format == "html":
                    html_path = documents_dir / f"{stem}.html"
                    html_path.write_text(raw_html, encoding="utf-8", newline="\n")
                    raw_files["html_file"] = str(html_path)
                    full_text = text
                else:
                    pdf_dir = documents_dir / f"{stem}_files"
                    pdf_dir.mkdir(parents=True, exist_ok=True)
                    for attachment in attachments:
                        pdf_path = pdf_dir / attachment["file_name"]
                        pdf_path.write_bytes(attachment.pop("bytes"))
                        attachment["local_file"] = str(pdf_path)
                    raw_files["pdf_files"] = [item["local_file"] for item in attachments]
                    full_text = "\n\n".join(
                        f"===== TỆP: {item['file_name']} =====\n{item['content'].strip()}"
                        for item in attachments
                    ) + "\n"
                text_path.write_text(full_text, encoding="utf-8", newline="\n")
                raw_record = {
                    "document_id": document_id,
                    "document_number": actual_number,
                    "document_title": title,
                    "document_type": row["type"],
                    "applies_to": row["applies_to"],
                    "source_url": official_url,
                    "source_page_url": source_page_url,
                    "original_url": row["url"],
                    "retrieved_at": datetime.now(UTC).isoformat(),
                    "content_format": content_format,
                    "content": full_text,
                    "primary_content": text,
                    "attachments": attachments,
                    "vbpl_metadata": detail,
                }
                write_json(json_path, raw_record)
                raw_index.append(raw_record)

                document_chunks = chunker.chunk(
                    text,
                    document_id=document_id,
                    document_title=title,
                    document_number=actual_number,
                    source_url=official_url,
                )
                serialized_chunks = [chunk.to_dict() for chunk in document_chunks]
                for attachment in attachments[1:]:
                    serialized_chunks.extend(
                        appendix_chunks(
                            attachment["content"],
                            document_id=document_id,
                            document_title=title,
                            document_number=actual_number,
                            source_url=attachment["url"],
                            file_name=attachment["file_name"],
                            max_chars=args.max_chars,
                            overlap_chars=args.overlap_chars,
                        )
                    )
                for item in serialized_chunks:
                    item["metadata"].update(
                        {
                            "original_url": row["url"],
                            "document_type": row["type"],
                            "applies_to": row["applies_to"],
                        }
                    )
                    chunks.append(item)
                entry.update(
                    {
                        "status": "ok",
                        "document_id": document_id,
                        "official_url": official_url,
                        "source_page_url": source_page_url,
                        "resolution": resolution,
                        "content_format": content_format,
                        "characters": len(full_text),
                        "chunks": len(serialized_chunks),
                        "text_file": str(text_path),
                        "json_file": str(json_path),
                        **raw_files,
                    }
                )
            except Exception as exc:
                entry.update({"status": "failed", "error": f"{type(exc).__name__}: {exc}"})
                print(f"  FAILED: {entry['error']}", flush=True)
            report.append(entry)
    finally:
        crawler.close()

    index_path = args.raw_dir / "full_text.jsonl"
    with index_path.open("w", encoding="utf-8", newline="\n") as file:
        for record in raw_index:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")
    with args.chunks.open("w", encoding="utf-8", newline="\n") as file:
        for chunk in chunks:
            file.write(json.dumps(chunk, ensure_ascii=False) + "\n")

    ok_count = sum(item["status"] == "ok" for item in report)
    report_path = args.raw_dir / "crawl_report.json"
    write_json(
        report_path,
        {
            "source_list": str(args.input),
            "official_source": VBPL_PAGE_URL,
            "started_at": started_at,
            "completed_at": datetime.now(UTC).isoformat(),
            "requested_documents": len(rows),
            "successful_documents": ok_count,
            "failed_documents": len(rows) - ok_count,
            "total_chunks": len(chunks),
            "max_chars": args.max_chars,
            "overlap_chars": args.overlap_chars,
            "documents": report,
        },
    )
    print(json.dumps(
        {
            "successful_documents": ok_count,
            "failed_documents": len(rows) - ok_count,
            "chunks": len(chunks),
            "raw_index": str(index_path),
            "chunk_file": str(args.chunks),
            "report": str(report_path),
        },
        ensure_ascii=False,
    ))
    return 0 if ok_count == len(rows) else 2


if __name__ == "__main__":
    raise SystemExit(main())
