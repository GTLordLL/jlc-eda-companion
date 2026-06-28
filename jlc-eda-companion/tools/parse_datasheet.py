#!/usr/bin/env python3
"""数据手册 PDF 解析工具 — docling 解析 + 按章节拆分 + index.md 目录索引。

Usage (CLI):
  python parse_datasheet.py process C8734.pdf                    # 完整流程：PDF → 章节 + index
  python parse_datasheet.py process C8734.pdf --format json       # JSON 输出
  python parse_datasheet.py process C8734.pdf --output-dir ./out  # 指定输出目录
  python parse_datasheet.py parse C8734.pdf                       # 仅解析：PDF → Markdown + 缓存
  python parse_datasheet.py split C8734.md --output-dir ./out     # 仅拆分：Markdown → 章节
  python parse_datasheet.py --lcsc C8734                          # 按 LCSC 编号自动找 PDF

Usage (import):
  from parse_datasheet import process_datasheet, parse_pdf, split_chapters
  result = process_datasheet("./datasheets/C8734.pdf")
  print(result["index_file"])
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Optional

# ── Constants ─────────────────────────────────────────────────────────

CACHE_HEADER_RE = re.compile(
    r'^<!-- jlc-datasheet-cache backend=(\S+) pdf=(\S+) timestamp=(.+) -->$'
)
ATX_HEADING_RE = re.compile(r'^(#{1,6})\s+(.*)$')
LEGACY_HEADING_RE = re.compile(
    r'^(\d+(?:\.\d+)*)\s+([A-Z][A-Za-z\s/&,()\-–—®™±°%µ\d]{5,})'
)
# Extract section number from ATX heading text, e.g.:
#   "1 Introduction" → "1", "2.3.1 Arm..." → "2.3.1", "Features" → None
SECTION_NUMBER_RE = re.compile(r'^(\d+(?:\.\d+)*)\s')
TOC_DOTS_RE = re.compile(r'\.{3,}\s*\d+$')
TABLE_ROW_RE = re.compile(r'\b\d+\s+\d+\s+\d+\b')

# Headings that should be ignored (table/figure captions, notes, etc.)
SKIP_HEADING_PATTERNS = [
    re.compile(r'^Table\s+\d+', re.IGNORECASE),
    re.compile(r'^Figure\s+\d+', re.IGNORECASE),
    re.compile(r'^(Note|Caution|Warning|Important)\b', re.IGNORECASE),
    re.compile(r'^Example\s+\d+\s*:', re.IGNORECASE),
    re.compile(r'^Equation\s+\d+', re.IGNORECASE),
    re.compile(r'^where:?\s*$', re.IGNORECASE),
]

# ── Batching Constants ──────────────────────────────────────────────────

DEFAULT_BATCH_SIZE = 100
AUTO_BATCH_THRESHOLD = 200  # PDF 超过此页数自动分批处理


# ── PCB Relevance Keywords ────────────────────────────────────────────

PCB_KEYWORD_GROUPS: dict[str, list[str]] = {
    "pinout": [
        "pin", "pinout", "terminal", "ball out", "ball assignment",
        "signal description", "i/o pin", "i/o description",
        "引脚", "管脚", "端子",
    ],
    "electrical": [
        "electrical characteristic", "dc characteristic", "ac characteristic",
        "operating condition", "recommended operating",
        "supply current", "supply voltage", "power consumption",
        "input characteristic", "output characteristic",
        "static characteristic", "dynamic characteristic",
        "电气特性", "直流特性", "工作条件", "电源特性",
    ],
    "absolute_maximum": [
        "absolute maximum", "maximum rating", "limiting value",
        "stress rating", "极限参数", "最大额定", "绝对最大",
    ],
    "layout": [
        "layout", "pcb layout", "pcb design",
        "soldering", "solder", "mounting",
        "thermal", "placement", "land pattern", "footprint",
        "reflow", "PCB 布局", "布线", "焊接", "热设计", "安装",
    ],
    "package": [
        "package", "mechanical", "physical dimension",
        "outline drawing", "case outline", "dimension",
        "封装", "外形尺寸", "机械尺寸",
    ],
    "application": [
        "typical application", "application circuit", "application note",
        "reference design", "reference circuit", "test circuit",
        "evaluation board", "典型应用", "参考设计", "应用电路",
    ],
}


# ── Utilities ──────────────────────────────────────────────────────────

def slugify(title: str, max_length: int = 60) -> str:
    """Convert a chapter title to a filesystem-safe slug.

    Examples:
        "Introduction" -> "introduction"
        "Pinouts and pin description" -> "pinouts_and_pin_description"
        "Electrical characteristics" -> "electrical_characteristics"
    """
    # Lowercase
    slug = title.lower().strip()
    # Replace any sequence of non-alphanumeric chars with single underscore
    slug = re.sub(r'[^a-z0-9]+', '_', slug)
    # Strip leading / trailing underscores
    slug = slug.strip('_')
    # Collapse multiple underscores
    slug = re.sub(r'_+', '_', slug)
    # Truncate
    if len(slug) > max_length:
        slug = slug[:max_length].rstrip('_')
    return slug or "chapter"


# ── PDF Parsing (docling) ──────────────────────────────────────────────

def _parse_with_docling(
    pdf_path: str,
    page_range: Optional[tuple[int, int]] = None,
    max_pages: Optional[int] = None,
    low_memory: bool = False,
) -> str:
    """Convert a PDF to Markdown using docling.

    Args:
        pdf_path: Absolute or relative path to the PDF file.
        page_range: Optional (start, end) 1-indexed inclusive page range.
        max_pages: Optional maximum number of pages to process.
        low_memory: If True, disable OCR and table detection to save memory.

    Returns:
        Markdown text.

    Raises:
        ImportError: If docling is not installed.
        Exception: On docling parse failure.
    """
    from docling.document_converter import DocumentConverter, InputFormat, PdfFormatOption
    from docling.datamodel.pipeline_options import PdfPipelineOptions

    # Configure pipeline options
    pipeline_opts = PdfPipelineOptions()

    if low_memory:
        pipeline_opts.do_ocr = False
        pipeline_opts.do_table_structure = False
        pipeline_opts.ocr_batch_size = 1
        pipeline_opts.layout_batch_size = 1
        pipeline_opts.table_batch_size = 1
        pipeline_opts.images_scale = 0.5  # Lower resolution → less memory

    converter = DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_opts)
        }
    )

    # Compute effective page_range
    kwargs: dict = {}
    if page_range is not None:
        kwargs["page_range"] = page_range
    elif max_pages is not None:
        kwargs["page_range"] = (1, max_pages)

    result = converter.convert(pdf_path, **kwargs)
    return result.document.export_to_markdown()


def _parse_with_docling_batched(
    pdf_path: str,
    total_pages: int,
    batch_size: int = DEFAULT_BATCH_SIZE,
    low_memory: bool = False,
) -> str:
    """Process a large PDF in batches to avoid unbounded image cache OOM.

    Each batch calls :func:`_parse_with_docling` with a limited ``page_range``.
    The docling image cache is freed between batches by ``gc.collect()``.

    Args:
        pdf_path: Path to the PDF file.
        total_pages: Total page count (from pdfplumber).
        batch_size: Pages per batch (default 100).
        low_memory: Passed through to ``_parse_with_docling``.

    Returns:
        Combined Markdown text from all batches.
    """
    import gc

    num_batches = (total_pages + batch_size - 1) // batch_size
    parts: list[str] = []

    for batch_idx in range(num_batches):
        batch_start = batch_idx * batch_size + 1
        batch_end = min(batch_start + batch_size - 1, total_pages)

        print(
            f"   📦 批次 {batch_idx + 1}/{num_batches}: "
            f"第 {batch_start}–{batch_end} 页 (共 {total_pages} 页) ...",
            file=sys.stderr,
        )

        try:
            md = _parse_with_docling(
                pdf_path,
                page_range=(batch_start, batch_end),
                low_memory=low_memory,
            )
            parts.append(md)
        except Exception as e:
            print(
                f"   ⚠️  批次 {batch_idx + 1} 失败 ({e})，跳过",
                file=sys.stderr,
            )
            # Continue with remaining batches — don't lose everything
            # because one batch failed

        gc.collect()

    if not parts:
        raise RuntimeError(
            f"所有 {num_batches} 个批次均解析失败，无法生成任何内容"
        )

    print(
        f"   ✅ 分批解析完成：{len(parts)}/{num_batches} 批次成功",
        file=sys.stderr,
    )

    return "\n\n".join(parts)


def _parse_with_pdfplumber(
    pdf_path: str,
    page_range: Optional[tuple[int, int]] = None,
    max_pages: Optional[int] = None,
) -> str:
    """Lightweight text extraction using pdfplumber (no ML models, no bitmaps).

    Best for text-heavy PDFs where OCR / table detection are unnecessary.
    Processes hundreds of pages per second with minimal memory.

    Args:
        pdf_path: Path to the PDF file.
        page_range: Optional (start, end) 1-indexed inclusive range.
        max_pages: Optional maximum pages.

    Returns:
        Plain-text Markdown with ``## Page N`` delimiters.

    Raises:
        ImportError: If pdfplumber is not installed.
    """
    import pdfplumber

    # Determine page range
    start = 1
    with pdfplumber.open(pdf_path) as pdf:
        total = len(pdf.pages)
        end = total

        if page_range:
            start, user_end = page_range
            end = min(user_end, total)
        elif max_pages:
            end = min(max_pages, total)

        start = max(1, start)
        end = max(start, end)

        lines_out: list[str] = []
        for pg_num in range(start, end + 1):
            page = pdf.pages[pg_num - 1]
            text = page.extract_text()
            lines_out.append(f"## Page {pg_num}\n")
            if text:
                lines_out.append(text)
            else:
                lines_out.append("*(此页无可提取文本)*")

        return "\n\n".join(lines_out)


def _get_page_count(pdf_path: str) -> Optional[int]:
    """Get page count using pdfplumber. Returns None if unavailable."""
    try:
        import pdfplumber
        with pdfplumber.open(pdf_path) as pdf:
            return len(pdf.pages)
    except Exception:
        return None


def parse_pdf(
    pdf_path: str,
    no_cache: bool = False,
    page_range: Optional[tuple[int, int]] = None,
    max_pages: Optional[int] = None,
    low_memory: bool = False,
    text_only: bool = False,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> dict:
    """Parse a PDF to full Markdown using docling, with caching.

    Cache is saved as ``{pdf_name}.md`` alongside the PDF.
    A header line identifies the cache as docling-generated; old markitdown
    caches (without the header) are automatically invalidated.

    For PDFs over ``AUTO_BATCH_THRESHOLD`` pages (default 200), processing is
    automatically split into batches to avoid memory overflow.

    Args:
        pdf_path: Path to the PDF file.
        no_cache: If True, delete and recreate the cache.
        page_range: Optional (start, end) 1-indexed inclusive page range.
        max_pages: Optional maximum number of pages to process.
        low_memory: If True, disable OCR and table detection.
        text_only: If True, use pdfplumber for lightweight text extraction.
        batch_size: Pages per batch when auto-batching (default 100).

    Returns:
        {
            "pdf_path": str,
            "markdown": str | None,
            "page_count": int | None,
            "parse_time_s": float,
            "cached": bool,
            "cache_path": str | None,
            "error": str | None,
        }
    """
    pdf_path = str(Path(pdf_path).resolve())
    result: dict = {
        "pdf_path": pdf_path,
        "markdown": None,
        "page_count": None,
        "parse_time_s": 0.0,
        "cached": False,
        "cache_path": None,
        "error": None,
    }

    # Check file exists
    if not os.path.isfile(pdf_path):
        result["error"] = f"文件不存在: {pdf_path}"
        return result

    cache_path = str(Path(pdf_path).with_suffix(".md"))
    result["cache_path"] = cache_path

    # Invalidate cache if requested (but only when no range filter is active —
    # a range-filtered parse creates a partial markdown that shouldn't
    # invalidate a full-parse cache).
    if no_cache and os.path.exists(cache_path) and not page_range and not max_pages:
        try:
            os.remove(cache_path)
        except OSError:
            pass

    # Try cache hit (only when not using page_range — range-filtered parses
    # should not read the full-parse cache)
    if not page_range and not max_pages:
        pdf_mtime = os.path.getmtime(pdf_path)
        if os.path.exists(cache_path) and os.path.getmtime(cache_path) >= pdf_mtime:
            try:
                cached_text = Path(cache_path).read_text(encoding="utf-8")
                # Check for docling cache header
                first_line = cached_text.split("\n", 1)[0].strip()
                m = CACHE_HEADER_RE.match(first_line)
                if m and m.group(1) == "docling":
                    result["markdown"] = cached_text
                    result["cached"] = True
                    result["parse_time_s"] = 0.0
                    # Still get page count if possible
                    result["page_count"] = _get_page_count(pdf_path)
                    return result
                # Old cache (markitdown or no header) — ignore, re-parse
            except Exception:
                pass  # Corrupt cache — re-parse

    # ── Determine parsing strategy ──────────────────────────────────────

    total_pages = _get_page_count(pdf_path)  # None if pdfplumber unavailable

    # Strategy 1: text_only — use pdfplumber, no ML
    if text_only:
        t0 = time.time()
        try:
            markdown = _parse_with_pdfplumber(
                pdf_path, page_range=page_range, max_pages=max_pages
            )
        except ImportError as e:
            result["error"] = f"pdfplumber 未安装。请运行: pip install pdfplumber\n原始错误: {e}"
            return result
        except Exception as e:
            result["error"] = f"pdfplumber 文本提取失败: {e}"
            return result
        elapsed = time.time() - t0
        result["parse_time_s"] = round(elapsed, 3)
        result["page_count"] = total_pages
        result["markdown"] = markdown
        # Don't cache text-only results (they're cheap to regenerate)
        return result

    # Strategy 2: docling with (optional) auto-batching
    t0 = time.time()
    use_batching = False

    if page_range:
        # User-specified range — single call, no batching
        effective_range = page_range
    elif max_pages:
        effective_range = (1, max_pages)
    elif (
        total_pages is not None
        and total_pages > AUTO_BATCH_THRESHOLD
        and batch_size > 0
    ):
        # Auto-batch for large PDFs
        use_batching = True
    else:
        # Small PDF — single call
        effective_range = None

    try:
        if use_batching:
            print(
                f"   🔄 PDF 共 {total_pages} 页，"
                f"自动分批处理（每批 {batch_size} 页）...",
                file=sys.stderr,
            )
            markdown = _parse_with_docling_batched(
                pdf_path,
                total_pages=total_pages,
                batch_size=batch_size,
                low_memory=low_memory,
            )
        else:
            markdown = _parse_with_docling(
                pdf_path,
                page_range=effective_range if (
                    page_range is not None or max_pages is not None
                ) else None,
                max_pages=None,  # already folded into effective_range
                low_memory=low_memory,
            )
    except ImportError as e:
        result["error"] = f"docling 未安装。请运行: pip install docling\n原始错误: {e}"
        return result
    except Exception as e:
        result["error"] = f"docling 解析失败: {e}"
        return result

    elapsed = time.time() - t0
    result["parse_time_s"] = round(elapsed, 3)

    # Prepend cache header (only cache full parses, not range-filtered)
    if not page_range and not max_pages:
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
        pdf_basename = os.path.basename(pdf_path)
        header = (
            f"<!-- jlc-datasheet-cache backend=docling "
            f"pdf={pdf_basename} timestamp={timestamp} -->\n"
        )
        markdown = header + markdown

        # Write cache
        try:
            Path(cache_path).write_text(markdown, encoding="utf-8")
        except OSError:
            pass  # Non-fatal

    result["markdown"] = markdown
    result["cached"] = False
    result["page_count"] = total_pages

    return result


# ── Heading Detection ──────────────────────────────────────────────────

def _is_toc_entry(line: str) -> bool:
    """Check if a line looks like a Table of Contents entry (dots + page number)."""
    return bool(TOC_DOTS_RE.search(line))


def _is_table_row(line: str) -> bool:
    """Check if a line looks like a table data row (multiple adjacent numbers)."""
    return bool(TABLE_ROW_RE.search(line))


def _is_skip_heading(title: str) -> bool:
    """Check if a heading looks like a table/figure caption or note.

    These are not real document chapters and should be filtered out.
    """
    stripped = title.strip()
    for pattern in SKIP_HEADING_PATTERNS:
        if pattern.match(stripped):
            return True
    return False


def _extract_section_number(title: str) -> Optional[str]:
    """Extract section number from heading text.

    Examples:
        "1 Introduction" → "1"
        "2.3.1 Arm Cortex-M3 core..." → "2.3.1"
        "Features" → None
        "Table 5. Pin definitions" → None (filtered by _is_skip_heading)
    """
    m = SECTION_NUMBER_RE.match(title.strip())
    if m:
        return m.group(1)
    return None


def detect_headings(markdown: str) -> list[dict]:
    """Detect heading lines in markdown using ATX headings with number-based hierarchy.

    Docling typically outputs all headings as ``##`` (level 2).  The real
    hierarchy is recovered from section-number depth:

    - ``1 Introduction``          → depth 0 (chapter)
    - ``2.1 Device overview``     → depth 1 (section)
    - ``2.3.1 Arm Cortex-M3...``  → depth 2 (sub-section)

    Headings without a section number (e.g. "Advanced-control timer") are
    attached to the nearest preceding numbered heading at the next-higher
    inferred level.

    Args:
        markdown: Full markdown text (may include cache header line).

    Returns:
        List of heading dicts, each with: level, number, title, line, raw.
        ``level`` is the inferred hierarchical level (1=chapter, 2=section,
        3=subsection, ...).
    """
    lines = markdown.split("\n")
    headings: list[dict] = []

    # Strategy A: ATX markdown headings with section-number hierarchy
    for i, line in enumerate(lines):
        if i == 0 and line.startswith("<!-- jlc-datasheet-cache"):
            continue
        m = ATX_HEADING_RE.match(line.strip())
        if not m:
            continue

        raw_level = len(m.group(1))  # original # count
        title = m.group(2).strip()

        # Filter short titles and pure numbers
        if len(title) < 3:
            continue
        if re.match(r'^\d+$', title):
            continue
        # Filter table/figure/note captions
        if _is_skip_heading(title):
            continue

        # Extract section number to infer real hierarchy
        sec_num = _extract_section_number(title)
        if sec_num is not None:
            # Level = number of dots + 1 (i.e. depth):
            #   "5" → 1, "5.3" → 2, "5.3.1" → 3
            inferred_level = sec_num.count(".") + 1
        else:
            # No section number — infer from raw markdown level
            # Usually these are children of the previous numbered heading
            inferred_level = raw_level

        headings.append({
            "level": inferred_level,
            "number": sec_num,
            "title": title,
            "line": i,
            "raw": line.strip(),
            "raw_md_level": raw_level,
        })

    # If few ATX headings found, try legacy strategy
    if len(headings) < 3:
        return _detect_headings_legacy(markdown)

    return headings


def _detect_headings_legacy(markdown: str) -> list[dict]:
    """Fallback: detect headings from number-prefixed section lines.

    Used when ATX-heading detection produces fewer than 3 results
    (e.g., linear markdown without markdown heading markup).
    """
    lines = markdown.split("\n")
    headings: list[dict] = []

    for i, line in enumerate(lines):
        if i == 0 and line.startswith("<!-- jlc-datasheet-cache"):
            continue
        stripped = line.strip()
        if not stripped:
            continue
        m = LEGACY_HEADING_RE.match(stripped)
        if not m:
            continue
        raw_number = m.group(1)
        title = m.group(2).strip()

        if _is_toc_entry(stripped):
            continue
        if _is_table_row(stripped):
            continue
        if len(title) < 5:
            continue

        # Infer level from number depth
        depth = raw_number.count(".")  # "5"=0, "5.3"=1, "5.3.1"=2
        level = depth + 1

        headings.append({
            "level": level,
            "number": raw_number,
            "title": title,
            "line": i,
            "raw": stripped,
            "raw_md_level": 2,
        })

    return headings


# ── Chapter Tree Building ──────────────────────────────────────────────

def build_chapter_tree(headings: list[dict]) -> list[dict]:
    """Build a hierarchical chapter tree from a flat heading list.

    Chapter level is determined as the minimum heading level that has at
    least 2 entries.  Headings at or above this level become top-level
    chapters; headings below it are grouped as children of the nearest
    ancestor chapter.

    Args:
        headings: Flat list from :func:`detect_headings`.

    Returns:
        List of chapter-level tree nodes, each with ``children`` sub-trees
        and ``number`` (sequential "01", "02", ...).
    """
    if not headings:
        return []

    # Count occurrences per (inferred) level
    from collections import Counter
    level_counts = Counter(h["level"] for h in headings)

    # Determine chapter level: pick the shallowest level with >= 2 entries
    chapter_level = None
    for lvl in sorted(level_counts.keys()):
        if level_counts[lvl] >= 2:
            chapter_level = lvl
            break

    if chapter_level is None:
        chapter_level = min(level_counts.keys()) if level_counts else 1

    # Group: nodes at chapter_level (or above) start new chapters;
    # deeper nodes become children of the nearest chapter ancestor.
    chapters: list[dict] = []
    current_chapter: Optional[dict] = None

    for h in headings:
        if h["level"] <= chapter_level:
            # Start a new chapter
            node = {
                "number": None,           # Assigned later
                "pdf_number": h.get("number"),
                "title": h["title"],
                "slug": slugify(h["title"]),
                "level": h["level"],
                "line": h["line"],
                "children": [],
                "pcd_relevant": False,
                "relevance_reasons": [],
            }
            chapters.append(node)
            current_chapter = node
        elif current_chapter is not None:
            # Child of current chapter
            child = {
                "number": h.get("number"),
                "title": h["title"],
                "slug": slugify(h["title"]),
                "level": h["level"],
                "line": h["line"],
                "children": [],
                "pcd_relevant": False,
                "relevance_reasons": [],
            }
            current_chapter["children"].append(child)

    # If no chapters were created, create a single chapter from the first heading
    if not chapters and headings:
        first = headings[0]
        chapters = [{
            "number": None,
            "pdf_number": first.get("number"),
            "title": first["title"],
            "slug": slugify(first["title"]),
            "level": first["level"],
            "line": first["line"],
            "children": [],
            "pcd_relevant": False,
            "relevance_reasons": [],
        }]

    # Assign sequential numbers
    for idx, ch in enumerate(chapters, 1):
        ch["number"] = f"{idx:02d}"

    return chapters


# ── PCB Relevance Annotation ───────────────────────────────────────────

def _is_pcb_relevant(title: str) -> tuple[bool, list[str]]:
    """Check if a section title indicates PCB-design relevance.

    Uses word-boundary-aware matching: a keyword matches only when it appears
    as a whole word/phrase (preceded/followed by a non-alphanumeric character
    or string boundary).  This prevents substring false positives like
    "mapping" matching "pin".

    Args:
        title: The section title to check.

    Returns:
        (is_relevant, list_of_matching_groups)
    """
    text = title.lower().strip()
    matched: list[str] = []
    for group, keywords in PCB_KEYWORD_GROUPS.items():
        for kw in keywords:
            # Build a regex that matches the keyword as a whole phrase
            # (word-boundary on both sides)
            pattern = re.compile(r'(?<![a-z])' + re.escape(kw) + r'(?![a-z])')
            if pattern.search(text):
                matched.append(group)
                break  # One match per group
    return (len(matched) > 0, matched)


def _annotate_pcb_relevance(chapter: dict) -> None:
    """Annotate a chapter node and its children with PCB relevance markers.

    A chapter is PCB-relevant if its own title OR any descendant's title matches.
    Modifies the tree in-place.
    """
    # Check children first (bottom-up)
    child_relevant = False
    for child in chapter.get("children", []):
        _annotate_pcb_relevance(child)
        if child.get("pcd_relevant"):
            child_relevant = True

    own_relevant, own_reasons = _is_pcb_relevant(chapter["title"])
    chapter["pcd_relevant"] = own_relevant or child_relevant
    # Merge child reasons into parent
    all_reasons = set(own_reasons)
    if child_relevant:
        for child in chapter.get("children", []):
            all_reasons.update(child.get("relevance_reasons", []))
    chapter["relevance_reasons"] = sorted(all_reasons)


# ── Chapter Splitting ──────────────────────────────────────────────────

def _resolve_collision(slug: str, used: set[str]) -> str:
    """Resolve filename collisions by appending _2, _3, etc."""
    if slug not in used:
        return slug
    counter = 2
    while f"{slug}_{counter}" in used:
        counter += 1
    return f"{slug}_{counter}"


def split_chapters(
    markdown: str,
    output_dir: str,
    chapter_tree: Optional[list[dict]] = None,
) -> dict:
    """Split full markdown into per-chapter .md files.

    Args:
        markdown: Full markdown text.
        output_dir: Directory to write chapter files and index.md.
        chapter_tree: Pre-built chapter tree. If None, computed from markdown.

    Returns:
        {
            "output_dir": str,
            "chapters": list[dict],  # tree with "file" and "path" added
            "chapter_count": int,
            "error": str | None,
        }
    """
    if chapter_tree is None:
        headings = detect_headings(markdown)
        chapter_tree = build_chapter_tree(headings)

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    lines = markdown.split("\n")
    total_lines = len(lines)

    used_slugs: set[str] = set()

    for idx, ch in enumerate(chapter_tree):
        # Determine slug and filename
        slug = _resolve_collision(ch["slug"], used_slugs)
        used_slugs.add(slug)
        filename = f"{ch['number']}_{slug}.md"
        ch["file"] = filename
        ch["path"] = str(out / filename)

        # Determine content boundaries
        start_line = ch["line"]
        # Find end: next chapter's heading line, or end of document
        if idx + 1 < len(chapter_tree):
            end_line = chapter_tree[idx + 1]["line"]
        else:
            end_line = total_lines

        # Extract content
        content_lines = lines[start_line:end_line]
        # Skip leading blank lines
        while content_lines and not content_lines[0].strip():
            content_lines.pop(0)
        # Skip trailing blank lines
        while content_lines and not content_lines[-1].strip():
            content_lines.pop()

        content = "\n".join(content_lines)

        # Write chapter file
        (out / filename).write_text(content, encoding="utf-8")

    return {
        "output_dir": str(out.resolve()),
        "chapters": chapter_tree,
        "chapter_count": len(chapter_tree),
        "error": None,
    }


# ── Index Generation ───────────────────────────────────────────────────

def write_index(
    chapter_tree: list[dict],
    output_dir: str,
    metadata: dict,
) -> str:
    """Write index.md to the output directory.

    Args:
        chapter_tree: Chapter tree with PCB annotations.
        output_dir: Directory to write index.md.
        metadata: {"pdf_path", "page_count", "file_size_mb", "generated"}.

    Returns:
        Path to the generated index.md.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    pdf_name = Path(metadata.get("pdf_path", "unknown")).name
    page_count = metadata.get("page_count")
    file_size = metadata.get("file_size_mb")
    generated = metadata.get("generated", time.strftime("%Y-%m-%d %H:%M:%S"))

    lines: list[str] = []

    # Header
    lines.append(f"# {pdf_name} — Datasheet Index")
    lines.append("")
    lines.append(f"> **Source:** `{pdf_name}`")
    if page_count:
        lines.append(f"> **Pages:** {page_count}")
    if file_size:
        lines.append(f"> **Size:** {file_size} MB")
    lines.append(f"> **Parsed with:** {metadata.get('backend', 'docling')}")
    lines.append(f"> **Generated:** {generated}")
    lines.append("")

    # TOC section
    lines.append("## Table of Contents")
    lines.append("")

    def _render_tree(
        nodes: list[dict], depth: int = 0,
        parent_file: str = "",
    ) -> None:
        indent = "  " * depth
        for node in nodes:
            prefix = "🟢 " if node.get("pcd_relevant") else ""
            # Chapter nodes have their own file; children link to parent file
            file_ref = node.get("file", "") or parent_file
            title = node.get("title", "")
            lines.append(f"{indent}- {prefix}[{title}]({file_ref})")
            # Pass this node's file (or parent's) down to children
            child_parent = node.get("file", "") or parent_file
            _render_tree(node.get("children", []), depth + 1, child_parent)

    _render_tree(chapter_tree)
    lines.append("")

    # Recommended Reading section
    pcb_chapters = [ch for ch in chapter_tree if ch.get("pcd_relevant")]

    if pcb_chapters:
        lines.append("## 🟢 PCB Design Relevant Chapters")
        lines.append("")
        lines.append("These chapters are most relevant for PCB design work:")
        lines.append("")
        lines.append("| # | Chapter | File | Key Content |")
        lines.append("|---|---------|------|-------------|")
        for ch in pcb_chapters:
            reasons = ", ".join(ch.get("relevance_reasons", []))
            lines.append(
                f"| {ch['number']} | {ch['title']} "
                f"| `{ch.get('file', '')}` "
                f"| {reasons} |"
            )
        lines.append("")

    # File listing
    lines.append("## Chapter File Listing")
    lines.append("")
    lines.append("| File | Chapter |")
    lines.append("|------|---------|")
    for ch in chapter_tree:
        lines.append(f"| `{ch.get('file', '')}` | {ch['number']} {ch['title']} |")
    lines.append("")

    content = "\n".join(lines)
    index_path = out / "index.md"
    index_path.write_text(content, encoding="utf-8")

    return str(index_path.resolve())


# ── Full Pipeline ──────────────────────────────────────────────────────

def process_datasheet(
    pdf_path: str,
    output_dir: Optional[str] = None,
    no_cache: bool = False,
    page_range: Optional[tuple[int, int]] = None,
    max_pages: Optional[int] = None,
    low_memory: bool = False,
    text_only: bool = False,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> dict:
    """Run the full pipeline: parse PDF → detect chapters → split → write index.

    For PDFs over ``AUTO_BATCH_THRESHOLD`` pages (default 200), docling parsing
    is automatically split into batches to avoid memory overflow.

    Args:
        pdf_path: Path to the PDF file.
        output_dir: Directory for chapter files + index.md.
                    Defaults to ``datasheets/{pdf_stem}/`` alongside the PDF.
        no_cache: If True, force re-parse.
        page_range: Optional (start, end) 1-indexed inclusive page range.
        max_pages: Optional maximum number of pages to process.
        low_memory: If True, disable OCR and table detection.
        text_only: If True, use pdfplumber for lightweight text extraction.
        batch_size: Pages per batch when auto-batching (default 100).

    Returns:
        {
            "pdf_path": str,
            "output_dir": str,
            "index_file": str,
            "page_count": int | None,
            "file_size_mb": float | None,
            "parse_time_s": float,
            "split_time_s": float,
            "cached": bool,
            "total_chapters": int,
            "pcd_relevant_count": int,
            "chapters": [...],
            "error": str | None,
        }
    """
    # Default output dir: alongside PDF in datasheets/
    pdf_path = str(Path(pdf_path).resolve())
    pdf_stem = Path(pdf_path).stem
    if output_dir is None:
        output_dir = str(Path(pdf_path).parent / pdf_stem)

    # File size
    try:
        file_size_mb = round(os.path.getsize(pdf_path) / (1024 * 1024), 2)
    except OSError:
        file_size_mb = None

    # Step 1: Parse PDF
    parse_result = parse_pdf(
        pdf_path,
        no_cache=no_cache,
        page_range=page_range,
        max_pages=max_pages,
        low_memory=low_memory,
        text_only=text_only,
        batch_size=batch_size,
    )
    if parse_result["error"]:
        return {
            "pdf_path": pdf_path,
            "output_dir": output_dir,
            "index_file": "",
            "page_count": None,
            "file_size_mb": file_size_mb,
            "parse_time_s": parse_result["parse_time_s"],
            "split_time_s": 0.0,
            "cached": False,
            "total_chapters": 0,
            "pcd_relevant_count": 0,
            "chapters": [],
            "error": parse_result["error"],
        }

    markdown = parse_result["markdown"]
    if not markdown:
        return {
            "pdf_path": pdf_path,
            "output_dir": output_dir,
            "index_file": "",
            "page_count": parse_result["page_count"],
            "file_size_mb": file_size_mb,
            "parse_time_s": parse_result["parse_time_s"],
            "split_time_s": 0.0,
            "cached": parse_result["cached"],
            "total_chapters": 0,
            "pcd_relevant_count": 0,
            "chapters": [],
            "error": "docling 未返回任何文本内容",
        }

    # Step 2: Detect headings & build tree
    headings = detect_headings(markdown)
    chapter_tree = build_chapter_tree(headings)

    # Step 3: Annotate PCB relevance
    for ch in chapter_tree:
        _annotate_pcb_relevance(ch)

    # Step 4: Split + write index
    t0 = time.time()
    split_result = split_chapters(markdown, output_dir, chapter_tree)
    split_time = round(time.time() - t0, 4)

    metadata = {
        "pdf_path": pdf_path,
        "page_count": parse_result["page_count"],
        "file_size_mb": file_size_mb,
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "backend": "pdfplumber" if text_only else "docling",
    }
    index_file = write_index(split_result["chapters"], output_dir, metadata)

    pcb_count = sum(1 for ch in split_result["chapters"] if ch.get("pcd_relevant"))

    return {
        "pdf_path": pdf_path,
        "output_dir": str(Path(output_dir).resolve()),
        "index_file": index_file,
        "page_count": parse_result["page_count"],
        "file_size_mb": file_size_mb,
        "parse_time_s": parse_result["parse_time_s"],
        "split_time_s": split_time,
        "cached": parse_result["cached"],
        "total_chapters": split_result["chapter_count"],
        "pcd_relevant_count": pcb_count,
        "chapters": [
            {
                "number": ch["number"],
                "pdf_number": ch.get("pdf_number"),
                "title": ch["title"],
                "slug": ch["slug"],
                "file": ch.get("file", ""),
                "pcd_relevant": ch.get("pcd_relevant", False),
                "relevance_reasons": ch.get("relevance_reasons", []),
                "children": [
                    {
                        "number": c.get("number"),
                        "title": c["title"],
                        "pcd_relevant": c.get("pcd_relevant", False),
                    }
                    for c in ch.get("children", [])
                ],
            }
            for ch in split_result["chapters"]
        ],
        "error": None,
    }


# ── Output Formatting ──────────────────────────────────────────────────

SECTION_LABELS: dict[str, str] = {
    "pinout": "引脚定义",
    "electrical": "电气特性",
    "absolute_maximum": "极限参数",
    "layout": "PCB 布局",
    "package": "封装信息",
    "application": "参考电路",
}


def _format_process_text(result: dict) -> str:
    """Format process result as human-readable text."""
    if result.get("error"):
        return f"❌ 错误: {result['error']}"

    lines = [
        f"📄 {Path(result['pdf_path']).name}",
        f"   📁 输出目录: {result['output_dir']}",
        f"   📋 索引文件: {result['index_file']}",
    ]
    if result.get("page_count"):
        lines.append(f"   📏 页数: {result['page_count']}")
    if result.get("file_size_mb"):
        lines.append(f"   💾 文件大小: {result['file_size_mb']} MB")
    lines.append(f"   ⏱️  解析耗时: {result['parse_time_s']}s"
                 f"{' (缓存)' if result.get('cached') else ''}")
    lines.append(f"   📑 章节数: {result['total_chapters']}")
    lines.append(f"   🟢 PCB 相关: {result['pcd_relevant_count']}")
    lines.append("")
    lines.append("   章节列表:")

    for ch in result.get("chapters", []):
        marker = "🟢" if ch.get("pcd_relevant") else "  "
        reasons = ", ".join(
            SECTION_LABELS.get(r, r) for r in ch.get("relevance_reasons", [])
        )
        reason_str = f"  [{reasons}]" if reasons else ""
        lines.append(f"   {marker} {ch['number']}. {ch['title']}  →  {ch['file']}{reason_str}")

    return "\n".join(lines)


def _format_parse_text(result: dict) -> str:
    """Format parse result as human-readable text."""
    if result.get("error"):
        return f"❌ 错误: {result['error']}"

    lines = [
        f"📄 {Path(result['pdf_path']).name}",
        f"   📝 缓存文件: {result.get('cache_path', 'N/A')}",
        f"   📏 页数: {result.get('page_count', 'N/A')}",
        f"   📊 Markdown 长度: {result.get('markdown_length', 0):,} 字符",
        f"   ⏱️  解析耗时: {result['parse_time_s']}s"
        f"{' (缓存)' if result.get('cached') else ''}",
    ]
    return "\n".join(lines)


def _format_split_text(result: dict) -> str:
    """Format split result as human-readable text."""
    if result.get("error"):
        return f"❌ 错误: {result['error']}"

    lines = [
        f"📝 {Path(result.get('markdown_path', '')).name}",
        f"   📁 输出目录: {result['output_dir']}",
        f"   📑 章节数: {result.get('total_chapters', result.get('chapter_count', 0))}",
    ]
    for ch in result.get("chapters", []):
        lines.append(f"   - {ch.get('file', '')}")
    return "\n".join(lines)


# ── File Resolution ────────────────────────────────────────────────────

def _resolve_files(
    files: list[str],
    lcsc: Optional[str],
    datasheet_dir: str,
) -> list[str]:
    """Resolve file arguments and --lcsc into a list of absolute PDF paths.

    Exits with code 1 if no files are provided or --lcsc file not found.
    """
    resolved: list[str] = []

    if lcsc:
        lcsc_num = lcsc.strip().upper()
        if lcsc_num.startswith("C"):
            lcsc_num = lcsc_num[1:]
        candidate = str(Path(datasheet_dir).resolve() / f"C{lcsc_num}.pdf")
        if os.path.isfile(candidate):
            resolved.append(candidate)
        else:
            print(
                json.dumps(
                    {"error": f"未找到 C{lcsc_num}.pdf 在 {datasheet_dir}/"},
                    ensure_ascii=False,
                ),
                file=sys.stderr,
            )
            sys.exit(1)

    for f in files:
        path = str(Path(f).resolve())
        if os.path.isfile(path):
            resolved.append(path)
        else:
            print(
                json.dumps({"error": f"文件不存在: {f}"}, ensure_ascii=False),
                file=sys.stderr,
            )
            sys.exit(1)

    if not resolved:
        print(
            json.dumps(
                {"error": "请提供 PDF 文件路径或 --lcsc 编号"},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        sys.exit(1)

    return resolved


# ── CLI ────────────────────────────────────────────────────────────────

def _parse_page_range_arg(raw: str) -> tuple[int, int]:
    """Parse a ``--pages`` argument like ``1-100`` or ``1,100``.

    Returns (start, end) 1-indexed inclusive.  Exit with code 2 on bad format.
    """
    sep = "-" if "-" in raw else ","
    parts = raw.split(sep)
    if len(parts) != 2:
        print(
            json.dumps(
                {"error": f"无效的页码范围格式: {raw!r}（期望: START-END 或 START,END）"},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        sys.exit(2)
    try:
        start, end = int(parts[0].strip()), int(parts[1].strip())
    except ValueError:
        print(
            json.dumps(
                {"error": f"页码范围必须为数字: {raw!r}"},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        sys.exit(2)
    if start < 1 or end < start:
        print(
            json.dumps(
                {"error": f"无效范围: start={start}, end={end}（start >= 1, end >= start）"},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        sys.exit(2)
    return (start, end)


def _add_common_args(p: argparse.ArgumentParser) -> None:
    """Add --format argument shared across all subcommands."""
    p.add_argument(
        "--format", "-f", choices=["json", "text"], default="text",
        help="输出格式 (default: text)",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="数据手册 PDF 解析 — docling 解析 + 按章节拆分 + index.md",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python parse_datasheet.py process C8734.pdf                     # 完整流程
  python parse_datasheet.py process C8734.pdf --format json       # JSON 输出
  python parse_datasheet.py process C8734.pdf --output-dir ./out  # 指定输出目录
  python parse_datasheet.py process C8734.pdf --pages 1-100       # 只处理前100页
  python parse_datasheet.py process C8734.pdf --max-pages 50      # 最多处理50页（快速预览）
  python parse_datasheet.py process C8734.pdf --low-memory        # 低内存模式（关闭OCR）
  python parse_datasheet.py process C8734.pdf --text-only         # 纯文本模式（最快）
  python parse_datasheet.py process C8734.pdf --batch-size 50     # 自定义分批大小
  python parse_datasheet.py parse C8734.pdf                       # 仅解析 PDF
  python parse_datasheet.py split C8734.md --output-dir ./out     # 仅拆分 Markdown
  python parse_datasheet.py --lcsc C8734                          # 按 LCSC 编号查找
        """,
    )
    # Top-level --lcsc (works without subcommand for backward compat)
    parser.add_argument(
        "--lcsc", metavar="LCSC",
        help="LCSC 编号（如 C8734），自动在 datasheets/ 目录查找 PDF",
    )
    parser.add_argument(
        "--datasheet-dir", default="./datasheets",
        help="--lcsc 查找 PDF 的目录 (default: ./datasheets)",
    )
    # --format at parent level for backward-compat (no subcommand mode).
    # Subparsers also define --format independently.
    parser.add_argument(
        "--format", "-f", choices=["json", "text"], default="text",
        help="输出格式 (default: text)",
    )

    subparsers = parser.add_subparsers(dest="mode", help="子命令")

    # process
    p_process = subparsers.add_parser(
        "process", help="完整流程：PDF → Markdown → 章节拆分 + index.md",
    )
    _add_common_args(p_process)
    p_process.add_argument(
        "pdf_paths", nargs="*",
        help="PDF 文件路径（一个或多个）",
    )
    p_process.add_argument(
        "--lcsc", metavar="LCSC",
        help="LCSC 编号（如 C8734），自动在 datasheets/ 目录查找 PDF",
    )
    p_process.add_argument(
        "--datasheet-dir", default="./datasheets",
        help="--lcsc 查找 PDF 的目录 (default: ./datasheets)",
    )
    p_process.add_argument(
        "--output-dir", "-o",
        help="输出目录 (default: datasheets/{pdf_name}/)",
    )
    p_process.add_argument(
        "--no-cache", action="store_true",
        help="强制重新解析（忽略缓存）",
    )
    p_process.add_argument(
        "--pages", metavar="START-END",
        help="只处理指定页码范围，如 --pages 1-100（1-indexed，包含边界）",
    )
    p_process.add_argument(
        "--max-pages", metavar="N", type=int,
        help="最多处理 N 页（快速预览前 N 页）",
    )
    p_process.add_argument(
        "--low-memory", action="store_true",
        help="低内存模式：关闭 OCR 和表格检测，降低内存消耗",
    )
    p_process.add_argument(
        "--text-only", action="store_true",
        help="纯文本模式：使用 pdfplumber 轻量提取（零 ML 模型，最快速度）",
    )
    p_process.add_argument(
        "--batch-size", metavar="N", type=int, default=DEFAULT_BATCH_SIZE,
        help=f"分批大小（默认 {DEFAULT_BATCH_SIZE} 页/批）",
    )

    # parse
    p_parse = subparsers.add_parser(
        "parse", help="仅解析：PDF → Markdown + 缓存",
    )
    _add_common_args(p_parse)
    p_parse.add_argument(
        "pdf_path",
        help="PDF 文件路径",
    )
    p_parse.add_argument(
        "--no-cache", action="store_true",
        help="强制重新解析",
    )
    p_parse.add_argument(
        "--pages", metavar="START-END",
        help="只处理指定页码范围，如 --pages 1-100",
    )
    p_parse.add_argument(
        "--max-pages", metavar="N", type=int,
        help="最多处理 N 页",
    )
    p_parse.add_argument(
        "--low-memory", action="store_true",
        help="低内存模式：关闭 OCR 和表格检测",
    )
    p_parse.add_argument(
        "--text-only", action="store_true",
        help="纯文本模式：使用 pdfplumber 轻量提取",
    )

    # split
    p_split = subparsers.add_parser(
        "split", help="仅拆分：已有 Markdown → 章节文件 + index.md",
    )
    _add_common_args(p_split)
    p_split.add_argument(
        "markdown_path",
        help="Markdown 文件路径（.md）",
    )
    p_split.add_argument(
        "--output-dir", "-o", required=True,
        help="输出目录（必填）",
    )

    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    try:
        if args.mode == "process" or args.mode is None:
            # Resolve file paths: use subparser lcsc if available, else parent
            lcsc = getattr(args, 'lcsc', None)
            datasheet_dir = getattr(args, 'datasheet_dir', './datasheets')
            pdf_paths = _resolve_files(
                getattr(args, 'pdf_paths', []) or [],
                lcsc,
                datasheet_dir,
            )

            # Parse --pages range if provided
            pages_raw = getattr(args, 'pages', None)
            page_range = _parse_page_range_arg(pages_raw) if pages_raw else None

            results = []
            for pdf_path in pdf_paths:
                output_dir = getattr(args, 'output_dir', None) or None
                result = process_datasheet(
                    pdf_path,
                    output_dir=output_dir,
                    no_cache=getattr(args, 'no_cache', False),
                    page_range=page_range,
                    max_pages=getattr(args, 'max_pages', None),
                    low_memory=getattr(args, 'low_memory', False),
                    text_only=getattr(args, 'text_only', False),
                    batch_size=getattr(args, 'batch_size', DEFAULT_BATCH_SIZE),
                )
                results.append(result)

            if args.format == "json":
                output = results[0] if len(results) == 1 else results
                print(json.dumps(output, ensure_ascii=False, separators=(",", ":")))
            else:
                for r in results:
                    print(_format_process_text(r))
                    if len(results) > 1:
                        print("")

        elif args.mode == "parse":
            pages_raw = getattr(args, 'pages', None)
            page_range = _parse_page_range_arg(pages_raw) if pages_raw else None
            result = parse_pdf(
                args.pdf_path,
                no_cache=args.no_cache,
                page_range=page_range,
                max_pages=getattr(args, 'max_pages', None),
                low_memory=getattr(args, 'low_memory', False),
                text_only=getattr(args, 'text_only', False),
            )
            # Add markdown_length for display (don't embed full markdown in output)
            if result.get("markdown"):
                result["markdown_length"] = len(result["markdown"])
                result.pop("markdown", None)

            if args.format == "json":
                print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
            else:
                print(_format_parse_text(result))

        elif args.mode == "split":
            md_path = args.markdown_path
            if not os.path.isfile(md_path):
                print(
                    json.dumps({"error": f"文件不存在: {md_path}"}, ensure_ascii=False),
                    file=sys.stderr,
                )
                sys.exit(1)

            markdown = Path(md_path).read_text(encoding="utf-8")
            headings = detect_headings(markdown)
            chapter_tree = build_chapter_tree(headings)
            for ch in chapter_tree:
                _annotate_pcb_relevance(ch)

            split_result = split_chapters(markdown, args.output_dir, chapter_tree)

            metadata = {
                "pdf_path": md_path,
                "page_count": None,
                "file_size_mb": None,
                "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
                "backend": "docling",
            }
            index_file = write_index(split_result["chapters"], args.output_dir, metadata)

            output = {
                "markdown_path": str(Path(md_path).resolve()),
                "output_dir": split_result["output_dir"],
                "index_file": index_file,
                "total_chapters": split_result["chapter_count"],
                "pcd_relevant_count": sum(
                    1 for ch in split_result["chapters"] if ch.get("pcd_relevant")
                ),
                "chapters": split_result["chapters"],
                "error": None,
            }

            if args.format == "json":
                print(json.dumps(output, ensure_ascii=False, separators=(",", ":")))
            else:
                print(_format_split_text(output))

    except Exception as e:
        print(
            json.dumps({"error": f"未预期的错误: {e}"}, ensure_ascii=False),
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
