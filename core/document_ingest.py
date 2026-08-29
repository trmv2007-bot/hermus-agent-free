"""Best-effort document ingestion for uploaded attachments.

``/command`` previously inlined only text-like uploads; binary documents
(PDF/DOCX/XLSX/PPTX/ZIP/...) were reduced to filename + content-type + size,
so the model never received usable contents — the opposite of "give it a file
and let it figure everything out".

This module extracts real text from the most common container formats using
only the standard library (zipfile / re / html) plus optional extras when
installed (pypdf for PDFs, Pillow for image metadata):

* **DOCX / ODT / EPUB**  → concatenated ``<w:t>`` / text-node contents
* **XLSX / ODS**         → shared strings + inline strings, sheet by sheet
* **PPTX**               → ``<a:t>`` runs per slide
* **ZIP / JAR / APK / wheel** → entry listing (names/sizes) so the model knows
  what is inside and can ask for specific files to be unpacked
* **PDF**                → pypdf when available; otherwise a naive
  text-operator scrape that only works on uncompressed PDFs (honestly reported)
* **images**             → dimensions/EXIF via Pillow when available; the bytes
  are persisted to the workspace uploads dir so vision/OCR tools can open them
* **anything else**      → utf-8 salvage of printable runs, else a clear note

Every extraction reports its ``method`` and a ``note`` when it fell short, so
the agent (and the user) can tell "extracted the real contents" from
"couldn't read this format".
"""
from __future__ import annotations

import os
import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

# same set the gateway already treats as plain text
TEXT_UPLOAD_EXTS = {
    ".txt", ".md", ".py", ".js", ".ts", ".jsx", ".tsx", ".json", ".csv", ".yaml",
    ".yml", ".html", ".htm", ".css", ".xml", ".log", ".ini", ".cfg", ".toml",
    ".sh", ".sql", ".rst", ".env", ".c", ".cc", ".cpp", ".h", ".hpp", ".java",
    ".go", ".rs", ".rb", ".php", ".kt", ".swift", ".vue", ".svelte",
}

ZIP_TEXT_EXTS = {".docx", ".xlsx", ".pptx", ".odt", ".ods", ".epub", ".odp"}
ARCHIVE_EXTS = {".zip", ".jar", ".apk", ".whl", ".egg", ".ipa"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff", ".svg"}

MAX_TEXT_CHARS = 120_000          # per attachment inlined into the prompt
MAX_ARCHIVE_ENTRIES = 200


@dataclass
class ExtractedDocument:
    filename: str
    content_type: str = ""
    size_bytes: int = 0
    text: Optional[str] = None          # None = could not extract anything
    method: str = "none"                # plain | docx | xlsx | pptx | archive | pdf | image | salvage
    note: Optional[str] = None          # honest limitation, when there is one
    saved_path: Optional[str] = None    # where binary bytes were persisted for tools
    truncated: bool = False

    @property
    def inlined(self) -> bool:
        return bool(self.text)

    def to_dict(self) -> dict[str, Any]:
        return {
            "filename": self.filename,
            "content_type": self.content_type,
            "size_bytes": self.size_bytes,
            "inlined": self.inlined,
            "method": self.method,
            "note": self.note,
            "saved_path": self.saved_path,
            "truncated": self.truncated,
        }


def _clean(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _cap(text: str, limit: int = MAX_TEXT_CHARS) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[:limit], True


def _looks_binary(sample: bytes) -> bool:
    if b"\x00" in sample:
        return True
    try:
        sample.decode("utf-8")
        return False
    except UnicodeDecodeError:
        return True


# ---------------------------------------------------------------- zip formats
_XML_TEXT_TAG = re.compile(
    r"<(?:w:t|a:t|t|text|p)[^>]*>(.*?)</(?:w:t|a:t|t|text|p)>", re.S
)


def _xml_texts(xml: str) -> list[str]:
    out = []
    for m in _XML_TEXT_TAG.finditer(xml):
        chunk = m.group(1)
        chunk = re.sub(r"<[^>]+>", "", chunk)
        chunk = (
            chunk.replace("&amp;", "&").replace("&lt;", "<")
            .replace("&gt;", ">").replace("&quot;", '"').replace("&apos;", "'")
        )
        if chunk.strip():
            out.append(chunk.strip())
    return out


def _paragraph_texts(xml: str, para_tag: str = "w:p", run_tag: str = "w:t") -> list[str]:
    """Text per paragraph: runs inside one paragraph joined, paragraphs on lines.

    When the paragraph contains no run tags at all (e.g. bare ODT ``text:p``),
    the paragraph body is tag-stripped instead, so text is never lost.
    """
    paras = []
    for pm in re.finditer(rf"<{para_tag}[ >].*?</{para_tag}>", xml, re.S):
        body = pm.group(0)
        runs = []
        for rm in re.finditer(rf"<{run_tag}[^>]*>(.*?)</{run_tag}>", body, re.S):
            chunk = (
                rm.group(1)
                .replace("&amp;", "&").replace("&lt;", "<")
                .replace("&gt;", ">").replace("&quot;", '"').replace("&apos;", "'")
            )
            if chunk:
                runs.append(chunk)
        if runs:
            line = "".join(runs).strip()
        else:
            inner = re.sub(rf"</?{para_tag}[^>]*>", "", body)
            line = re.sub(r"<[^>]+>", "", inner).strip()
        if line:
            paras.append(line)
    return paras


def _extract_docx(zf: zipfile.ZipFile) -> str:
    parts = []
    for name in ("word/document.xml", "word/header1.xml", "word/footer1.xml"):
        try:
            xml = zf.read(name).decode("utf-8", errors="ignore")
        except KeyError:
            continue
        parts.extend(_paragraph_texts(xml))
    try:  # ODT/ODF body
        xml = zf.read("content.xml").decode("utf-8", errors="ignore")
        parts.extend(_paragraph_texts(xml, para_tag="text:p", run_tag="text:span"))
    except KeyError:
        pass
    return _clean("\n".join(parts))


def _extract_pptx(zf: zipfile.ZipFile) -> str:
    slides = sorted(
        n for n in zf.namelist()
        if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)
    )
    out = []
    for i, name in enumerate(slides, start=1):
        xml = zf.read(name).decode("utf-8", errors="ignore")
        paras = _paragraph_texts(xml, para_tag="a:p", run_tag="a:t")
        if paras:
            out.append(f"[slide {i}] " + " / ".join(paras))
    return _clean("\n".join(out))


def _extract_xlsx(zf: zipfile.ZipFile) -> str:
    # shared strings table: each <si> block is one shared string
    shared: list[str] = []
    try:
        ss = zf.read("xl/sharedStrings.xml").decode("utf-8", errors="ignore")
        for si in re.finditer(r"<si>(.*?)</si>", ss, re.S):
            runs = _xml_texts(si.group(1))
            if runs:
                shared.append("".join(runs))
    except KeyError:
        pass
    out = []
    sheet_names = sorted(
        n for n in zf.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n)
    )
    for name in sheet_names:
        xml = zf.read(name).decode("utf-8", errors="ignore")
        rows = []
        for row_m in re.finditer(r"<row[^>]*>(.*?)</row>", xml, re.S):
            cells = []
            for cell_m in re.finditer(r"<c([^>]*)>(.*?)</c>", row_m.group(1), re.S):
                attrs, body = cell_m.group(1), cell_m.group(2)
                ctype = (re.search(r't="(\w+)"', attrs) or [None, None])[1]
                texts = _xml_texts(body)
                value = "".join(texts).strip()
                if not value:
                    vm = re.search(r"<v>(.*?)</v>", body, re.S)
                    if vm:
                        value = vm.group(1).strip()
                if ctype == "s" and value.isdigit() and int(value) < len(shared):
                    value = shared[int(value)]
                cells.append(value)
            if any(cells):
                rows.append(" | ".join(cells))
        if rows:
            out.append(f"[{name}]")
            out.extend(rows[:400])
    return _clean("\n".join(out))


def _extract_archive_listing(zf: zipfile.ZipFile) -> str:
    entries = []
    for info in zf.infolist()[:MAX_ARCHIVE_ENTRIES]:
        entries.append(f"{info.filename} ({info.file_size} bytes)")
    if len(zf.infolist()) > MAX_ARCHIVE_ENTRIES:
        entries.append(f"… {len(zf.infolist()) - MAX_ARCHIVE_ENTRIES} more entries")
    return "\n".join(entries)


def _extract_zip_family(data: bytes, ext: str) -> tuple[Optional[str], str, Optional[str]]:
    try:
        zf = zipfile.ZipFile(__import__("io").BytesIO(data))
    except Exception as e:
        return None, "archive", f"corrupt or unsupported zip container: {e}"
    if ext == ".docx" or ext == ".odt" or ext == ".epub":
        if ext == ".epub":
            texts = []
            for name in zf.namelist():
                if name.endswith((".xhtml", ".html", ".htm", ".ncx")):
                    xml = zf.read(name).decode("utf-8", errors="ignore")
                    texts.extend(_xml_texts(xml))
            text = _clean("\n".join(texts))
            capped, _truncated = _cap(text)
            return (capped or None), "docx", (None if text else "no text found in epub chapters")
        text = _extract_docx(zf)
        return (text or None), "docx", (None if text else "no text runs found in document.xml")
    if ext in (".xlsx", ".ods"):
        text = _extract_xlsx(zf)
        return (text or None), "xlsx", (None if text else "no cell text found")
    if ext == ".pptx" or ext == ".odp":
        text = _extract_pptx(zf)
        return (text or None), "pptx", (None if text else "no slide text found")
    # plain archive: give the model the entry listing
    listing = _extract_archive_listing(zf)
    return listing, "archive", "archive contents listed by entry; ask to unpack specific entries"


# ------------------------------------------------------------------------ pdf
def _extract_pdf(data: bytes) -> tuple[Optional[str], str, Optional[str]]:
    try:
        from pypdf import PdfReader  # type: ignore

        reader = PdfReader(__import__("io").BytesIO(data))
        pages = []
        for page in reader.pages[:80]:
            try:
                pages.append(page.extract_text() or "")
            except Exception:
                continue
        text = _clean("\n".join(pages))
        if text:
            capped, truncated = _cap(text)
            return capped, "pdf", ("truncated" if truncated else None)
        return None, "pdf", "pypdf extracted no text (scanned/image PDF needs OCR)"
    except ImportError:
        pass
    except Exception as e:
        return None, "pdf", f"pypdf failed: {e}"
    # naive fallback: uncompressed text operators only
    try:
        raw = data.decode("latin-1", errors="ignore")
        chunks = re.findall(r"\((.*?)\)\s*Tj", raw)
        text = _clean("\n".join(c.replace("\\(", "(").replace("\\)", ")") for c in chunks))
        if text:
            capped, truncated = _cap(text)
            return capped, "pdf", (
                "naive extraction (install pypdf for reliable PDF text)"
                + ("; truncated" if truncated else "")
            )
        return None, "pdf", "PDF text not extractable without pypdf (pip install pypdf)"
    except Exception as e:
        return None, "pdf", f"pdf fallback failed: {e}"


# ---------------------------------------------------------------------- image
def _extract_image(data: bytes, filename: str) -> tuple[Optional[str], str, Optional[str]]:
    meta: list[str] = []
    try:
        from PIL import Image  # type: ignore

        with Image.open(__import__("io").BytesIO(data)) as im:
            meta.append(f"format={im.format} size={im.size[0]}x{im.size[1]} mode={im.mode}")
    except Exception:
        meta.append("image (metadata unavailable)")
    note = (
        "image bytes saved to workspace uploads; the model needs a vision/"
        "OCR tool or multimodal model to see the actual pixels"
    )
    return "; ".join(meta), "image", note


def _salvage_text(data: bytes) -> Optional[str]:
    """Printable-run salvage for unknown binary formats (never fabricated)."""
    try:
        text = data.decode("utf-8", errors="ignore")
    except Exception:
        return None
    runs = re.findall(r"[\x20-\x7e\n\r\t]{6,}", text)
    if not runs:
        return None
    return _clean("\n".join(runs[:2000]))


# ---------------------------------------------------------------------- public
def extract_document(
    filename: str,
    data: bytes,
    content_type: str = "",
    *,
    save_binary_to: Optional[Path] = None,
) -> ExtractedDocument:
    """Extract usable text from an uploaded file, honestly.

    ``save_binary_to`` (directory) persists the raw bytes for formats we cannot
    inline (images, videos, unknown binaries) so the agent's tools can still
    open them by path.
    """
    ext = Path(filename or "").suffix.lower()
    doc = ExtractedDocument(
        filename=filename or "upload",
        content_type=content_type or "",
        size_bytes=len(data),
    )

    # 1) plain text / code
    if ext in TEXT_UPLOAD_EXTS or (not ext and not _looks_binary(data[:2048])):
        try:
            text = data.decode("utf-8", errors="replace")
            capped, truncated = _cap(text)
            doc.text, doc.method, doc.truncated = capped, "plain", truncated
            return doc
        except Exception:
            pass

    # 2) office / archive containers (zip-based)
    if ext in ZIP_TEXT_EXTS:
        text, method, note = _extract_zip_family(data, ext)
        if text:
            capped, truncated = _cap(text)
            doc.text, doc.truncated = capped, truncated
        doc.method, doc.note = method, note
    elif ext in ARCHIVE_EXTS:
        text, method, note = _extract_zip_family(data, ext)
        if text:
            capped, truncated = _cap(text)
            doc.text, doc.truncated = capped, truncated
        doc.method, doc.note = method, note
        # archives always get their bytes persisted too
    elif ext == ".pdf" or "pdf" in (content_type or ""):
        text, method, note = _extract_pdf(data)
        if text:
            capped, truncated = _cap(text)
            doc.text, doc.truncated = capped, truncated
        doc.method, doc.note = method, note
    elif ext in IMAGE_EXTS or (content_type or "").startswith("image/"):
        text, method, note = _extract_image(data, filename)
        doc.text, doc.method, doc.note = text or None, method, note
    elif ext in (".mp3", ".wav", ".ogg", ".m4a", ".mp4", ".mov", ".avi", ".webm"):
        doc.method = "media"
        doc.note = "media bytes saved to workspace uploads; use transcribe_audio / vision tools"
    else:
        # unknown binary: try utf-8 salvage of printable runs
        salvaged = _salvage_text(data)
        if salvaged and len(salvaged) >= 40:
            capped, truncated = _cap(salvaged)
            doc.text, doc.method, doc.truncated = capped, truncated, truncated
            doc.note = "salvaged printable strings from an unknown binary format"
        else:
            doc.method = "binary"
            doc.note = "binary format with no text extraction path; bytes saved for tools"

    # 3) persist binary bytes so the agent's tools (vision/OCR/unzip/…) can
    #    actually open the file even when we could not inline its contents.
    if save_binary_to is not None and data:
        try:
            save_dir = Path(save_binary_to)
            save_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            safe = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(filename or "upload").name)
            target = save_dir / f"{stamp}_{safe}"
            counter = 1
            while target.exists() and counter < 50:
                target = save_dir / f"{stamp}_{counter}_{safe}"
                counter += 1
            target.write_bytes(data)
            doc.saved_path = str(target)
        except Exception:
            doc.saved_path = None

    return doc


def attachment_prompt_block(doc: ExtractedDocument) -> str:
    """Render one extracted attachment as a prompt block for the agent."""
    header = f"--- Attachment: {doc.filename} ({doc.size_bytes} bytes"
    if doc.content_type:
        header += f", {doc.content_type}"
    if doc.method != "plain":
        header += f", extracted via {doc.method}"
    header += ") ---"
    lines = [header]
    if doc.text:
        lines.append(doc.text)
        if doc.truncated:
            lines.append("…(attachment text truncated)")
    else:
        lines.append("(no text could be extracted)")
    if doc.note:
        lines.append(f"[note: {doc.note}]")
    if doc.saved_path:
        lines.append(f"[raw bytes saved at: {doc.saved_path} — tools can open this path]")
    return "\n".join(lines)
