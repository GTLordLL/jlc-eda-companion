#!/usr/bin/env python3
"""数据手册 PDF 解析工具 — 将 PDF 转为 Markdown，提取 PCB 设计关键章节。

Usage (CLI):
  python parse_datasheet.py C8734.pdf --format json           # 全文解析
  python parse_datasheet.py C8734.pdf --extract --format json # 解析+提取关键章节
  python parse_datasheet.py C8734.pdf C14663.pdf --extract    # 批量解析
  python parse_datasheet.py --lcsc C8734 --extract            # 按 LCSC 编号自动找 PDF

Usage (import):
  from parse_datasheet import parse_datasheet, extract_sections
  result = parse_datasheet("./datasheets/C8734.pdf")
  print(result["sections"]["pinout"])
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


# ── Section Detection Patterns ─────────────────────────────────────────

# Each section has a list of regex patterns.
# Patterns are matched case-insensitively against potential section header text.
# First match in each category wins.
SECTION_PATTERNS: dict[str, list[str]] = {
    "pinout": [
        r"pin\s*(out)?s?\s*(and|&)?\s*(pin\s*)?(description|configuration|assignment|definition|function|connection|table|list|information|summary)",
        r"terminal\s*(description|configuration|assignment|function|connection)",
        r"pin(ning)?\s*(information|summary|description)",
        r"signal\s*description",
        r"i/o\s*(pin|description|configuration)",
        r"ball\s*(out|assignment|description)",
        r"引脚\s*(描述|定义|配置|功能|排列|说明|分布|图|表)",
        r"管脚\s*(描述|定义|配置|功能|说明)",
        r"端子\s*(描述|定义|功能|说明)",
    ],
    "application_circuit": [
        r"typical\s*application",
        r"application\s*(circuit|information|note|schematic|diagram|example)",
        r"reference\s*(design|circuit|schematic|application)",
        r"test\s*(circuit|configuration)",
        r"evaluation\s*(board|circuit|module)",
        r"典型\s*(应用|电路)",
        r"参考\s*(设计|电路|应用)",
        r"应用\s*(电路|示例|实例|图|方案)",
        r"测试\s*(电路|配置)",
    ],
    "electrical_characteristics": [
        r"electrical\s*characteristics",
        r"dc\s*(electrical\s*)?characteristics",
        r"ac\s*(electrical\s*)?characteristics",
        r"operating\s*(conditions|characteristics)",
        r"recommended\s*operating",
        r"supply\s*(current|voltage)\s*characteristics",
        r"power\s*(consumption|characteristics|specifications)",
        r"(input|output)\s*characteristics",
        r"static\s*characteristics",
        r"dynamic\s*characteristics",
        r"电气\s*(特性|参数|指标|规格)",
        r"直流\s*(特性|参数|指标)",
        r"工作\s*(条件|参数|特性|电压|电流)",
        r"电源\s*(特性|参数|指标)",
        r"(输入|输出)\s*(特性|参数)",
    ],
    "absolute_maximum": [
        r"absolute\s*maximum\s*ratings?",
        r"maximum\s*ratings?",
        r"limiting\s*(values|conditions)",
        r"stress\s*ratings?",
        r"极限\s*(参数|值|条件|额定)",
        r"最大\s*(额定|参数|值|条件)",
        r"绝对\s*最大",
    ],
    "layout_guidelines": [
        r"(pcb\s*)?layout\s*(guideline|recommendation|consideration|note|information|guide)",
        r"printed\s*(circuit\s*)?board\s*(layout|design)",
        r"(soldering|solder(ing)?)\s*(guideline|recommendation|information|profile)",
        r"mounting\s*(guideline|recommendation|information)",
        r"thermal\s*(consideration|management|guideline|pad|characteristics)",
        r"placement\s*(guideline|recommendation)",
        r"land\s*pattern",
        r"footprint\s*(information|dimension)",
        r"reflow\s*(profile|soldering)",
        r"PCB\s*布局\s*(指南|指导|建议|说明|要求|注意)",
        r"布线\s*(指南|指导|建议|说明|要求)",
        r"焊接\s*(指南|指导|建议|要求|条件|温度)",
        r"热\s*(设计|管理|考虑|特性)",
        r"安装\s*(指南|指导|说明)",
    ],
    "package_info": [
        r"package\s*(information|dimension|drawing|description|outline|mechanical)",
        r"mechanical\s*(data|information|drawing|dimension|specification)",
        r"physical\s*dimensions?",
        r"outline\s*(drawing|dimension)",
        r"case\s*(outline|dimension)",
        r"dimension(al)?\s*(drawing|information)",
        r"封装\s*(信息|尺寸|图纸|说明|外形|图)",
        r"外形\s*(尺寸|图纸|图|信息|数据|规格)",
        r"机械\s*(尺寸|图纸|图|信息|数据|规格)",
        r"外壳\s*(尺寸|信息|规格)",
    ],
    "ordering_info": [
        r"order(ing)?\s*(information|code|number|guide)",
        r"part\s*(number|numbering|identification)",
        r"device\s*(summary|identification|marking)",
        r"product\s*(identification|marking|code)",
    ],
}


def _normalize_title(text: str) -> str:
    """Remove section numbers, dots-leaders, and trailing page numbers from a title line."""
    # Remove leading section numbers: "5.2.1 " or "5 "
    text = re.sub(r'^[\d.]+\s+', '', text)
    # Remove trailing dots-leader and page number: ".... 42"
    text = re.sub(r'\s*\.{3,}\s*\d+\s*$', '', text)
    text = re.sub(r'\s*\.\s*\.\s*\.\s*\d+\s*$', '', text)
    # Remove trailing whitespace
    return text.strip()


def _is_toc_entry(title: str) -> bool:
    """Check if a title line looks like a Table of Contents entry (not actual content)."""
    # TOC entries have dots between title and page number
    if re.search(r'\.{3,}\s*\d+$', title):
        return True
    # Or the pattern " . . . . . . . . . . . . 42"
    if re.search(r'\s\.\s\.\s\.\s\d+$', title):
        return True
    return False


def _match_section_title(title: str) -> Optional[str]:
    """Try to match a normalized title against known section patterns.

    Returns the section category key (e.g. 'pinout'), or None.
    """
    text = title.lower().strip()
    # Remove trailing dots (common in datasheets: "3 Pinouts and pin description . . . . . .")
    text = re.sub(r'\s*\.+$', '', text)
    # Remove trailing dots-leaders
    text = re.sub(r'\s*\.{2,}\s*\d*$', '', text)
    text = text.strip()

    for category, patterns in SECTION_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, text, re.IGNORECASE):
                return category
    return None


def _find_section_boundaries(markdown: str) -> list[tuple[int, str, str]]:
    """Find section boundaries in linear markdown text.

    Detects lines that look like section headers:
    - "5 Electrical characteristics"
    - "5.2 Absolute maximum ratings"
    - "3 Pinouts and pin description . . . . . . . . . . . . . . . 20"

    Returns list of (line_index, raw_title, normalized_title) sorted by line position.
    """
    lines = markdown.split('\n')
    boundaries: list[tuple[int, str, str]] = []

    # Pattern: starts with digits, followed by a capitalized title
    header_pattern = re.compile(
        r'^(\d+(?:\.\d+)*)\s+([A-Z][A-Za-z\s/&,()\-–—®™±°%µ\d]+)',
        re.IGNORECASE,
    )

    for i, line in enumerate(lines):
        stripped = line.strip()
        match = header_pattern.match(stripped)
        if match:
            raw_title = match.group(0)
            normalized = _normalize_title(raw_title)

            # Skip very short titles (likely noise)
            if len(normalized) < 5:
                continue
            # Skip TOC entries
            if _is_toc_entry(raw_title) or _is_toc_entry(stripped):
                continue
            # Skip if it looks like a table row (has multiple numbers)
            if re.search(r'\b\d+\s+\d+\s+\d+\b', stripped):
                continue

            boundaries.append((i, stripped, normalized))

    return boundaries


def extract_sections(markdown: str, max_chars_per_section: int = 15000) -> dict[str, str]:
    """Extract PCB design-relevant sections from datasheet markdown.

    Works with linear text output from markitdown (which doesn't produce
    markdown headers). Detects section headers by number patterns
    (e.g., "5 Electrical characteristics") and collects content between
    consecutive section boundaries.

    Args:
        markdown: Full markdown/plain text from PDF parsing.
        max_chars_per_section: Truncate each section to this many characters.

    Returns:
        Dict mapping section keys (pinout, application_circuit, ...) to their
        text content. Keys with no content are omitted.
    """
    lines = markdown.split('\n')
    boundaries = _find_section_boundaries(markdown)

    if not boundaries:
        return {}

    # Match each boundary against section patterns
    matched_boundaries: list[tuple[int, str, str]] = []
    for line_idx, raw_title, normalized in boundaries:
        category = _match_section_title(raw_title)
        if category:
            matched_boundaries.append((line_idx, category, normalized))

    if not matched_boundaries:
        return {}

    # Sort by line position
    matched_boundaries.sort(key=lambda x: x[0])

    # Extract content for each matched section
    sections: dict[str, list[str]] = {}

    for idx, (line_idx, category, title) in enumerate(matched_boundaries):
        # Find end of this section: start of next section, or end of document
        if idx + 1 < len(matched_boundaries):
            end_idx = matched_boundaries[idx + 1][0]
        else:
            # Last section: go until we find the next unmatched section boundary or end
            # Find the next boundary after this one (whether matched or not)
            all_after = [b[0] for b in boundaries if b[0] > line_idx]
            if all_after:
                # Look for the next major section (single digit, or next matched)
                # Prefer the next boundary with a single-digit number (top-level section)
                next_major = None
                for b_idx, b_raw, b_norm in sorted(boundaries, key=lambda x: x[0]):
                    if b_idx > line_idx:
                        # Check if it's a top-level section (single digit)
                        if re.match(r'^\d+\s', b_raw) and not re.match(r'^\d+\.\d+', b_raw):
                            next_major = b_idx
                            break
                if next_major:
                    end_idx = next_major
                else:
                    end_idx = all_after[0]
            else:
                end_idx = len(lines)

        # Collect content
        content_lines = lines[line_idx:end_idx]
        content = '\n'.join(content_lines).strip()

        # Store (append for duplicate keys — e.g., multiple "electrical" subsections)
        if category in sections:
            sections[category].append(content)
        else:
            sections[category] = [content]

    # Merge subsections and truncate
    result: dict[str, str] = {}
    for key, content_list in sections.items():
        merged = '\n\n---\n\n'.join(content_list)
        if len(merged) > max_chars_per_section:
            merged = merged[:max_chars_per_section] + (
                f"\n\n... (截断，原文共 {len(merged)} 字符)"
            )
        result[key] = merged

    return result


# ── PDF Parsing Backends ───────────────────────────────────────────────

def _parse_with_markitdown(pdf_path: str) -> str:
    """Parse PDF to Markdown using Microsoft markitdown (fast, recommended)."""
    from markitdown import MarkItDown
    md = MarkItDown()
    result = md.convert(pdf_path)
    return result.text_content


def _parse_with_docling(pdf_path: str) -> str:
    """Parse PDF to Markdown using IBM Docling (slow, high-quality tables)."""
    from docling.document_converter import DocumentConverter
    converter = DocumentConverter()
    result = converter.convert(pdf_path)
    return result.document.export_to_markdown()


def _parse_with_pypdf(pdf_path: str) -> str:
    """Parse PDF to plain text using pypdf (fallback, minimal dependencies)."""
    from pypdf import PdfReader
    reader = PdfReader(pdf_path)
    parts: list[str] = []
    for i, page in enumerate(reader.pages):
        text = page.extract_text()
        if text:
            parts.append(f"## Page {i + 1}\n\n{text.strip()}")
    return '\n\n'.join(parts)


def _get_page_count(pdf_path: str) -> Optional[int]:
    """Get page count using pdfplumber (already installed with markitdown)."""
    try:
        import pdfplumber
        with pdfplumber.open(pdf_path) as pdf:
            return len(pdf.pages)
    except Exception:
        return None


# ── Public API ─────────────────────────────────────────────────────────

def parse_pdf(
    pdf_path: str,
    backend: str = "markitdown",
) -> dict:
    """Parse a PDF datasheet to Markdown.

    Args:
        pdf_path: Path to the PDF file.
        backend: One of 'markitdown' (default), 'docling', 'pypdf'.

    Returns:
        {
            "pdf_path": str,
            "backend": str,
            "backend_available": bool,
            "markdown": str | None,
            "page_count": int | None,
            "parse_time_s": float,
            "error": str | None,
            "cached": bool,
        }
    """
    start = time.time()
    result: dict = {
        "pdf_path": str(Path(pdf_path).resolve()),
        "backend": backend,
        "backend_available": False,
        "markdown": None,
        "page_count": None,
        "parse_time_s": 0.0,
        "error": None,
        "cached": False,
    }

    # Check file exists
    if not os.path.isfile(pdf_path):
        result["error"] = f"文件不存在: {pdf_path}"
        return result

    # Check cache
    cache_path = Path(pdf_path).with_suffix('.md')
    pdf_mtime = os.path.getmtime(pdf_path)
    if cache_path.exists():
        cache_mtime = os.path.getmtime(cache_path)
        if cache_mtime >= pdf_mtime:
            try:
                result["markdown"] = cache_path.read_text(encoding='utf-8')
                result["cached"] = True
                result["backend_available"] = True
                result["page_count"] = _get_page_count(pdf_path)
                result["parse_time_s"] = round(time.time() - start, 3)
                return result
            except Exception:
                pass  # Cache corrupted, re-parse

    # Parse
    backend_funcs = {
        "markitdown": _parse_with_markitdown,
        "docling": _parse_with_docling,
        "pypdf": _parse_with_pypdf,
    }

    if backend not in backend_funcs:
        result["error"] = f"未知后端: {backend}，可选: {', '.join(backend_funcs)}"
        return result

    try:
        markdown = backend_funcs[backend](pdf_path)
        result["markdown"] = markdown
        result["backend_available"] = True
    except ImportError as e:
        result["error"] = f"后端 '{backend}' 未安装: {e}"
        # Try fallback to pypdf if primary backend fails
        if backend != "pypdf":
            try:
                markdown = _parse_with_pypdf(pdf_path)
                result["markdown"] = markdown
                result["backend"] = "pypdf (fallback)"
                result["backend_available"] = True
                result["error"] = None  # clear the import error
            except Exception:
                pass
    except Exception as e:
        result["error"] = f"解析失败 ({backend}): {e}"
        # Try pypdf fallback
        if backend != "pypdf":
            try:
                markdown = _parse_with_pypdf(pdf_path)
                result["markdown"] = markdown
                result["backend"] = "pypdf (fallback)"
                result["backend_available"] = True
                result["error"] = None
            except Exception:
                pass

    # Get page count
    if result["markdown"]:
        result["page_count"] = _get_page_count(pdf_path)

    # Write cache
    if result["markdown"] and not result.get("error"):
        try:
            cache_path.write_text(result["markdown"], encoding='utf-8')
        except OSError:
            pass  # Non-fatal

    result["parse_time_s"] = round(time.time() - start, 3)
    return result


def parse_datasheet(
    pdf_path: str,
    backend: str = "markitdown",
    extract: bool = True,
    no_cache: bool = False,
) -> dict:
    """One-stop datasheet parsing: PDF → Markdown + section extraction.

    Args:
        pdf_path: Path to the PDF file.
        backend: 'markitdown' (default), 'docling', or 'pypdf'.
        extract: If True, extract key sections from the markdown.
        no_cache: If True, force re-parse even if cache exists.

    Returns:
        Structured dict with markdown, sections, metadata.
    """
    pdf_path = str(Path(pdf_path).resolve())
    result: dict = {
        "pdf_path": pdf_path,
        "filename": Path(pdf_path).name,
        "backend": backend,
        "backend_available": False,
        "available": True,
        "page_count": None,
        "file_size_mb": None,
        "parse_time_s": 0.0,
        "cached": False,
        "sections": {},
        "full_markdown": None,
        "error": None,
    }

    # File existence
    if not os.path.isfile(pdf_path):
        result["available"] = False
        result["error"] = f"文件不存在: {pdf_path}"
        return result

    # File size
    file_size = os.path.getsize(pdf_path)
    result["file_size_mb"] = round(file_size / (1024 * 1024), 2)

    # Invalidate cache if requested
    if no_cache:
        cache_path = Path(pdf_path).with_suffix('.md')
        if cache_path.exists():
            cache_path.unlink()

    # Parse
    parse_result = parse_pdf(pdf_path, backend=backend)
    result["backend"] = parse_result["backend"]
    result["backend_available"] = parse_result["backend_available"]
    result["page_count"] = parse_result["page_count"]
    result["parse_time_s"] = parse_result["parse_time_s"]
    result["cached"] = parse_result["cached"]

    if parse_result.get("error"):
        result["error"] = parse_result["error"]
        result["available"] = False
        return result

    markdown = parse_result.get("markdown") or ""

    # Extract sections
    if extract and markdown:
        result["sections"] = extract_sections(markdown)

    # Full markdown (truncate for JSON output)
    max_full = 80000
    result["full_markdown"] = markdown if len(markdown) <= max_full else (
        markdown[:max_full] + f"\n\n... (原文共 {len(markdown)} 字符，已截断至前 {max_full} 字符)"
    )

    # Summary stats
    result["markdown_length"] = len(markdown)
    result["sections_found"] = list(result["sections"].keys())

    return result


# ── Output Formatting ──────────────────────────────────────────────────

def _format_text(result: dict) -> str:
    """Format parse result as human-readable text."""
    lines = [
        f"📄 {result.get('filename', 'unknown')}",
        f"{'─' * 60}",
    ]

    if not result.get("available"):
        lines.append(f"  ❌ 错误: {result.get('error', '未知错误')}")
        return "\n".join(lines)

    lines.append(f"  🔧 后端: {result.get('backend', 'N/A')}")
    lines.append(f"  📏 文件大小: {result.get('file_size_mb', '?')} MB")
    lines.append(f"  📖 页数: {result.get('page_count', '?')}")
    lines.append(f"  ⏱️  解析耗时: {result.get('parse_time_s', '?')}s")
    if result.get("cached"):
        lines.append(f"  💾 缓存命中")
    lines.append(f"  📝 Markdown 长度: {result.get('markdown_length', 0):,} 字符")

    sections = result.get("sections", {})
    if sections:
        lines.append(f"")
        lines.append(f"  📂 提取到的章节 ({len(sections)}):")
        section_labels = {
            "pinout": "引脚定义",
            "application_circuit": "典型应用电路",
            "electrical_characteristics": "电气特性",
            "absolute_maximum": "极限参数",
            "layout_guidelines": "PCB布局指南",
            "package_info": "封装信息",
        }
        for key in ["pinout", "application_circuit", "electrical_characteristics",
                     "absolute_maximum", "layout_guidelines", "package_info"]:
            if key in sections:
                label = section_labels.get(key, key)
                length = len(sections[key])
                lines.append(f"     ✅ {label} ({length:,} 字符)")

    return "\n".join(lines)


def _format_json(result: dict) -> str:
    """Format parse result as JSON."""
    # For single result, return compact JSON
    return json.dumps(result, ensure_ascii=False, separators=(",", ":"))


# ── CLI ────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="数据手册 PDF 解析 — 将 PDF 转为 Markdown 并提取关键章节",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python parse_datasheet.py C8734.pdf --format json           # 全文解析
  python parse_datasheet.py C8734.pdf --extract --format json # 解析+提取关键章节
  python parse_datasheet.py C8734.pdf C14663.pdf --extract    # 批量解析
  python parse_datasheet.py --lcsc C8734 --extract            # 按 LCSC 编号找 PDF
  python parse_datasheet.py C8734.pdf --backend docling       # 使用 Docling 后端
        """,
    )
    parser.add_argument(
        "files", nargs="*",
        help="PDF 文件路径",
    )
    parser.add_argument(
        "--lcsc", type=str,
        help="LCSC 编号（自动在 ./datasheets/ 下找 C{lcsc}.pdf）",
    )
    parser.add_argument(
        "--extract", "-e", action="store_true",
        help="提取关键章节（引脚定义、电气特性、参考电路等）",
    )
    parser.add_argument(
        "--backend", "-b", choices=["markitdown", "docling", "pypdf"],
        default="markitdown",
        help="PDF 解析后端 (default: markitdown)",
    )
    parser.add_argument(
        "--format", "-f", choices=["json", "text"], default="text",
        help="输出格式 (default: text)",
    )
    parser.add_argument(
        "--no-cache", action="store_true",
        help="强制重新解析，忽略缓存",
    )
    parser.add_argument(
        "--datasheet-dir", default="./datasheets",
        help="数据手册下载目录 (default: ./datasheets)，用于 --lcsc 查找",
    )
    return parser


def _resolve_files(
    files: list[str],
    lcsc: Optional[str],
    datasheet_dir: str,
) -> list[str]:
    """Resolve input files and --lcsc to a list of PDF paths."""
    result: list[str] = list(files)

    if lcsc:
        # Clean LCSC number
        lcsc_num = lcsc.strip().upper()
        if lcsc_num.startswith("C"):
            lcsc_num = lcsc_num[1:]
        filename = f"C{lcsc_num}.pdf"
        path = Path(datasheet_dir) / filename
        if path.exists():
            result.append(str(path))
        else:
            print(f"错误：未找到 {path}", file=sys.stderr)
            sys.exit(1)

    if not result:
        print("错误：需要至少一个 PDF 文件路径或 --lcsc 参数", file=sys.stderr)
        sys.exit(1)

    return result


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    files = _resolve_files(args.files, args.lcsc, args.datasheet_dir)
    results = []

    for f in files:
        try:
            r = parse_datasheet(
                f,
                backend=args.backend,
                extract=args.extract,
                no_cache=args.no_cache,
            )
            results.append(r)
        except Exception as e:
            results.append({
                "pdf_path": f,
                "available": False,
                "error": f"未预期的错误: {e}",
            })

    if args.format == "json":
        output = results[0] if len(results) == 1 else results
        print(json.dumps(output, ensure_ascii=False, separators=(",", ":")))
    else:
        for r in results:
            print(_format_text(r))
            if len(results) > 1:
                print()


if __name__ == "__main__":
    main()
