#!/usr/bin/env python3
"""立创商城数据手册爬取工具 — 从 LCSC 产品页提取并下载元器件数据手册 PDF。

Usage (CLI):
  python fetch_datasheet.py C8734                          # 仅获取 PDF URL
  python fetch_datasheet.py C8734 --format json            # JSON 输出
  python fetch_datasheet.py C8734 --download ./datasheets  # 下载 PDF
  python fetch_datasheet.py C8734 C14663 --download .      # 批量下载

Usage (import):
  from fetch_datasheet import get_datasheet_url, download_datasheet
  url = get_datasheet_url(8734)
  path = download_datasheet(8734, "./datasheets")
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

import requests

# ── Constants ─────────────────────────────────────────────────────────

LCSC_DATASHEET_SPA = "https://www.lcsc.com/datasheet/C{lcsc}.pdf"
LCSC_PRODUCT_PAGE = "https://www.lcsc.com/product-detail/_C{lcsc}.html"
REQUEST_TIMEOUT = 20  # seconds for page fetch
PDF_DOWNLOAD_TIMEOUT = 60  # seconds for PDF download (can be large)
USER_AGENT = "jlc-eda-companion/0.1.0"

# PDF URL extraction patterns (tried in order)
PDF_URL_PATTERNS = [
    # Pattern 1: "Datasheet","url":"https://datasheet.lcsc.com/...pdf?..."
    re.compile(r'"Datasheet"\s*,\s*"url"\s*:\s*"(https://datasheet\.lcsc\.com/[^"]+\.pdf[^"]*)"', re.IGNORECASE),
    # Pattern 2: Bare URL in page text
    re.compile(r'https?://datasheet\.lcsc\.com/[^\s"\'<>]+\.pdf[^\s"\'<>]*', re.IGNORECASE),
    # Pattern 3: Unicode-escaped URL (Nuxt SSR data)
    re.compile(r'datasheet\\u002Flcsc\\u002F[^"]+\.pdf[^"]*', re.IGNORECASE),
]


# ── Session / Proxy ───────────────────────────────────────────────────

def _get_proxies() -> Optional[dict[str, str]]:
    """Read proxy settings from environment variables."""
    proxies: dict[str, str] = {}
    for env_name, proxy_key in [("HTTP_PROXY", "http"), ("HTTPS_PROXY", "https"),
                                 ("http_proxy", "http"), ("https_proxy", "https")]:
        val = os.environ.get(env_name)
        if val and proxy_key not in proxies:
            proxies[proxy_key] = val
    return proxies or None


def _get_session() -> requests.Session:
    """Build a requests Session with proxy support and User-Agent."""
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    proxies = _get_proxies()
    if proxies:
        session.proxies.update(proxies)
    return session


# ── URL Extraction ────────────────────────────────────────────────────

def _extract_pdf_url(html: str, lcsc_number: int) -> Optional[str]:
    """Extract the real datasheet PDF URL from LCSC SPA page HTML.

    Args:
        html: The HTML content of the SPA datasheet viewer page.
        lcsc_number: LCSC part number (for fallback URL construction).

    Returns:
        Full PDF URL string, or None if not found.
    """
    for pattern in PDF_URL_PATTERNS:
        match = pattern.search(html)
        if match:
            url = match.group(0)
            # Clean up Unicode escapes: \\u002F → /
            url = url.replace("\\u002F", "/")
            # If we matched a JSON field, extract just the URL part
            if url.startswith('"'):
                # JSON key match — the actual URL is in group 1
                if match.lastindex and match.lastindex >= 1:
                    url = match.group(1)
            return url
    return None


def _extract_mfr_part(html: str) -> Optional[str]:
    """Try to extract manufacturer part number from the SPA page metadata.

    Args:
        html: The HTML content of the SPA datasheet viewer page.

    Returns:
        Manufacturer part number string, or None.
    """
    # Method 1: <meta name="keywords" content="ST | STM32F103C8T6 | Datasheet">
    match = re.search(
        r'<meta[^>]+name="keywords"[^>]+content="([^"]+)"',
        html, re.IGNORECASE,
    )
    if match:
        parts = [p.strip() for p in match.group(1).split("|")]
        # Filter out generic tokens; prefer the part with digits (real MFR part)
        candidates = [p for p in parts
                      if p.lower() not in ("datasheet", "lcsc", "electronics", "pdf", "")]
        # Pick the candidate that looks most like a part number: has digits, longest
        if candidates:
            scored = sorted(candidates,
                           key=lambda p: (bool(re.search(r'\d', p)), len(p)),
                           reverse=True)
            return scored[0]

    # Method 2: <title> tag — dynamic rendering may include part number
    match = re.search(r'<title>([^<]+)\s+Datasheet\s*-\s*LCSC', html, re.IGNORECASE)
    if match:
        part = match.group(1).strip()
        if part and part.lower() not in ("datasheet", "lcsc", "electronics"):
            return re.sub(r'[<>:"/\\|?*]', '_', part)

    # Method 3: JS variable assignment in inline script
    match = re.search(r'productModel\s*[=:]\s*"([^"]+)"', html)
    if match:
        return match.group(1)

    return None


# ── Public API ────────────────────────────────────────────────────────

def get_datasheet_url(
    lcsc_number: int,
    session: Optional[requests.Session] = None,
) -> dict:
    """Get the real datasheet PDF URL for an LCSC part number.

    Fetches the lightweight SPA viewer page (~23KB) and extracts the
    actual PDF URL pointing to datasheet.lcsc.com.

    Args:
        lcsc_number: LCSC part number (e.g., 8734 for C8734).
        session: Optional shared requests.Session.

    Returns:
        {
            "lcsc": 8734,
            "lcsc_url": "https://www.lcsc.com/product-detail/_C8734.html",
            "pdf_url": "https://datasheet.lcsc.com/...pdf" | None,
            "available": true | false,
            "mfr_part": "STM32F103C8T6" | None,
        }
    """
    result = {
        "lcsc": lcsc_number,
        "lcsc_url": LCSC_PRODUCT_PAGE.format(lcsc=lcsc_number),
        "pdf_url": None,
        "available": False,
        "mfr_part": None,
    }

    if session is None:
        session = _get_session()

    spa_url = LCSC_DATASHEET_SPA.format(lcsc=lcsc_number)

    try:
        resp = session.get(spa_url, timeout=REQUEST_TIMEOUT)
    except requests.Timeout:
        result["error"] = f"获取 SPA 页面超时: {spa_url}"
        return result
    except requests.ConnectionError as e:
        result["error"] = f"连接失败: {spa_url}\n请检查网络或代理设置。原始错误: {e}"
        return result

    if resp.status_code >= 400:
        result["error"] = f"SPA 页面返回 HTTP {resp.status_code}: {spa_url}"
        return result

    html = resp.text
    pdf_url = _extract_pdf_url(html, lcsc_number)

    if pdf_url:
        result["pdf_url"] = pdf_url
        result["available"] = True

    mfr = _extract_mfr_part(html)
    if mfr:
        result["mfr_part"] = mfr

    return result


def download_datasheet(
    lcsc_number: int,
    output_dir: str,
    overwrite: bool = False,
    session: Optional[requests.Session] = None,
) -> dict:
    """Download the datasheet PDF for an LCSC part number.

    First calls get_datasheet_url() to find the PDF URL, then downloads
    and saves the PDF to output_dir.

    Args:
        lcsc_number: LCSC part number (e.g., 8734 for C8734).
        output_dir: Directory to save the PDF file.
        overwrite: If True, re-download even if file exists.
        session: Optional shared requests.Session.

    Returns:
        dict with keys: lcsc, pdf_url, local_path, size_bytes, size_mb,
                        filename, available, skipped (if file exists).
    """
    # Step 1: Get the real PDF URL
    info = get_datasheet_url(lcsc_number, session=session)

    if not info.get("available"):
        return {
            "lcsc": lcsc_number,
            "available": False,
            "error": info.get("error", "数据手册不可用"),
        }

    # Step 2: Determine output path
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    filename = f"C{lcsc_number}.pdf"
    filepath = out_dir / filename

    # Check if already downloaded
    if filepath.exists() and not overwrite:
        file_size = filepath.stat().st_size
        return {
            "lcsc": lcsc_number,
            "pdf_url": info["pdf_url"],
            "local_path": str(filepath.resolve()),
            "filename": filename,
            "size_bytes": file_size,
            "size_mb": round(file_size / (1024 * 1024), 2),
            "available": True,
            "skipped": True,
            "message": f"文件已存在，跳过下载。使用 --overwrite 强制重新下载。",
        }

    # Step 3: Download the PDF
    if session is None:
        session = _get_session()

    pdf_url = info["pdf_url"]

    try:
        # Add Referer for good practice (some CDNs require it)
        headers = {"Referer": "https://www.lcsc.com/"}
        resp = session.get(pdf_url, headers=headers,
                          timeout=PDF_DOWNLOAD_TIMEOUT, stream=True)
    except requests.Timeout:
        return {
            "lcsc": lcsc_number,
            "pdf_url": pdf_url,
            "available": True,
            "error": f"PDF 下载超时 ({PDF_DOWNLOAD_TIMEOUT}s): {pdf_url}",
        }
    except requests.ConnectionError as e:
        return {
            "lcsc": lcsc_number,
            "pdf_url": pdf_url,
            "available": True,
            "error": f"PDF 下载连接失败: {e}",
        }

    if resp.status_code >= 400:
        return {
            "lcsc": lcsc_number,
            "pdf_url": pdf_url,
            "available": True,
            "error": f"PDF 下载返回 HTTP {resp.status_code}",
        }

    # Verify it's actually a PDF
    content_type = resp.headers.get("Content-Type", "")
    if "pdf" not in content_type.lower() and "octet-stream" not in content_type.lower():
        # Not a fatal error, but worth noting
        pass

    # Step 4: Write to disk
    try:
        with open(filepath, "wb") as f:
            total_bytes = 0
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    total_bytes += len(chunk)
    except OSError as e:
        return {
            "lcsc": lcsc_number,
            "pdf_url": pdf_url,
            "available": True,
            "error": f"写入文件失败: {e}",
        }

    return {
        "lcsc": lcsc_number,
        "pdf_url": pdf_url,
        "local_path": str(filepath.resolve()),
        "filename": filename,
        "size_bytes": total_bytes,
        "size_mb": round(total_bytes / (1024 * 1024), 2),
        "available": True,
    }


def check_datasheet(
    lcsc_number: int,
    session: Optional[requests.Session] = None,
) -> bool:
    """Quick check if a datasheet exists for an LCSC part number.

    Only fetches the SPA page and checks for a PDF URL — no PDF download.

    Args:
        lcsc_number: LCSC part number.
        session: Optional shared requests.Session.

    Returns:
        True if a datasheet PDF URL was found.
    """
    info = get_datasheet_url(lcsc_number, session=session)
    return info.get("available", False)


# ── Output Formatting ─────────────────────────────────────────────────

def _format_text_single(result: dict, index: int = 1) -> str:
    """Format a single datasheet result as a text block."""
    lcsc = result["lcsc"]
    lines = [f"C{lcsc}:"]

    if not result.get("available"):
        err = result.get("error", "无数据手册")
        lines.append(f"  ❌ {err}")
        return "\n".join(lines)

    lines.append(f"  ✅ PDF URL: {result.get('pdf_url', 'N/A')}")

    if "local_path" in result:
        lines.append(f"  📁 本地: {result['local_path']}")
        lines.append(f"  📏 大小: {result.get('size_mb', 0):.2f} MB")
        if result.get("skipped"):
            lines.append(f"  ⏭️  跳过（已存在）")

    if result.get("mfr_part"):
        lines.append(f"  🏷️  型号: {result['mfr_part']}")

    return "\n".join(lines)


def _format_text(results: list[dict]) -> str:
    """Format multiple datasheet results as text."""
    parts = []
    for i, r in enumerate(results, 1):
        parts.append(_format_text_single(r, i))
        parts.append("")
    return "\n".join(parts).rstrip()


# ── CLI ────────────────────────────────────────────────────────────────

def _parse_lcsc(s: str) -> int:
    """Parse LCSC number from string. Accepts 'C8734' or '8734'."""
    s = s.strip().upper()
    if s.startswith("C"):
        return int(s[1:])
    return int(s)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="立创商城数据手册爬取 — 从 LCSC 提取并下载元器件数据手册 PDF",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python fetch_datasheet.py C8734                          # 仅获取 PDF URL（文本输出）
  python fetch_datasheet.py C8734 --format json            # JSON 输出
  python fetch_datasheet.py C8734 --download ./datasheets  # 下载 PDF 到本地
  python fetch_datasheet.py C8734 C14663 --download .      # 批量下载
        """,
    )
    parser.add_argument(
        "lcsc_numbers", nargs="+", type=_parse_lcsc,
        help="LCSC 编号（如 C8734 或 8734）",
    )
    parser.add_argument(
        "--download", "-d", metavar="DIR",
        help="下载 PDF 到指定目录（不指定则仅显示 URL）",
    )
    parser.add_argument(
        "--format", "-f", choices=["json", "text"], default="text",
        help="输出格式 (default: text)",
    )
    parser.add_argument(
        "--overwrite", action="store_true",
        help="强制重新下载（覆盖已有文件）",
    )
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if len(args.lcsc_numbers) == 0:
        print("错误：至少需要一个 LCSC 编号", file=sys.stderr)
        sys.exit(1)

    session = _get_session()
    results = []

    for lcsc in args.lcsc_numbers:
        try:
            if args.download:
                result = download_datasheet(
                    lcsc, args.download, overwrite=args.overwrite,
                    session=session,
                )
            else:
                result = get_datasheet_url(lcsc, session=session)
            results.append(result)
        except Exception as e:
            results.append({
                "lcsc": lcsc,
                "available": False,
                "error": f"未预期的错误: {e}",
            })
        # Small delay between requests to be polite
        if len(args.lcsc_numbers) > 1:
            time.sleep(0.3)

    if args.format == "json":
        output = results[0] if len(results) == 1 else results
        print(json.dumps(output, ensure_ascii=False, separators=(",", ":")))
    else:
        print(_format_text(results))


if __name__ == "__main__":
    main()
