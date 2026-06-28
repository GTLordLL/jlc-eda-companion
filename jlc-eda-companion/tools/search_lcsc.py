#!/usr/bin/env python3
"""LCSC 元器件搜索工具 — 查询立创商城库存、价格、封装、JLCPCB贴片状态。

API: jlcsearch.tscircuit.com (社区免费 API)

Usage (CLI):
  python search_lcsc.py general STM32F103C8T6
  python search_lcsc.py general C8734 --format json
  python search_lcsc.py resistor --search 10K --package 0805
  python search_lcsc.py capacitor --search 100nF --package 0603
  python search_lcsc.py component AMS1117-3.3 --format table

Usage (import):
  from search_lcsc import search_general, search_resistors, search_capacitors
  result = search_general("STM32F103C8T6")
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Optional, Literal

import requests

# ── Constants ─────────────────────────────────────────────────────────

BASE_URL = "https://jlcsearch.tscircuit.com"
REQUEST_TIMEOUT = 15  # seconds
RATE_LIMIT_DELAY = 0.5  # seconds between requests
LCSC_PRODUCT_URL = "https://www.lcsc.com/product-detail/_C{lcsc}.html"

USER_AGENT = "jlc-eda-companion/0.1.0"


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


# ── Rate-limited request ──────────────────────────────────────────────

_last_request_time: float = 0.0


def _make_request(endpoint: str, params: dict,
                  session: Optional[requests.Session] = None) -> dict:
    """Make a rate-limited GET request to the jlcsearch API.

    Args:
        endpoint: API path, e.g. "/api/search".
        params: Query parameters dict.
        session: Optional shared requests.Session.

    Returns:
        Parsed JSON response dict.

    Raises:
        requests.Timeout: On timeout.
        requests.ConnectionError: On connection failure.
        RuntimeError: On non-200 HTTP status.
    """
    global _last_request_time

    # Rate limiting
    elapsed = time.monotonic() - _last_request_time
    if elapsed < RATE_LIMIT_DELAY:
        time.sleep(RATE_LIMIT_DELAY - elapsed)

    if session is None:
        session = _get_session()

    url = f"{BASE_URL}{endpoint}"
    try:
        resp = session.get(url, params=params, timeout=REQUEST_TIMEOUT)
        _last_request_time = time.monotonic()
    except requests.Timeout:
        raise requests.Timeout(
            f"请求超时 ({REQUEST_TIMEOUT}s): {url}\n"
            "请检查网络连接或代理设置"
        )
    except requests.ConnectionError as e:
        raise requests.ConnectionError(
            f"连接失败: {url}\n"
            f"请检查网络连接。如果使用代理，请确认代理端口 7897 是否运行。\n"
            f"原始错误: {e}"
        )

    if resp.status_code >= 400:
        raise RuntimeError(
            f"API 返回 HTTP {resp.status_code}: {url}\n"
            f"响应: {resp.text[:500]}"
        )

    return resp.json()


# ── Result normalization ──────────────────────────────────────────────

def _lcsc_url(lcsc_number: int) -> str:
    """Construct LCSC product page URL from part number."""
    return LCSC_PRODUCT_URL.format(lcsc=lcsc_number)


def _parse_price(price_raw) -> tuple[float, Optional[list[dict]]]:
    """Parse price field from API response.

    The API returns either:
    - A number (float/int): unit price
    - A list of dicts: tiered pricing [{qFrom, qTo, price}, ...]
    - A JSON string of the above

    Returns (unit_price, price_tiers_or_None).
    """
    if isinstance(price_raw, (int, float)):
        return float(price_raw), None
    if isinstance(price_raw, list):
        tiers = [{"qty_from": t.get("qFrom", 0),
                  "qty_to": t.get("qTo"),
                  "price": t.get("price", 0)}
                 for t in price_raw]
        unit_price = tiers[0]["price"] if tiers else 0.0
        return unit_price, tiers
    if isinstance(price_raw, str):
        try:
            parsed = json.loads(price_raw)
            return _parse_price(parsed)
        except (json.JSONDecodeError, TypeError):
            return 0.0, None
    return 0.0, None


def _normalize_general(comp: dict) -> dict:
    """Normalize a component from general/api search endpoint."""
    lcsc = comp.get("lcsc", 0)
    unit_price, price_tiers = _parse_price(comp.get("price", 0))
    result = {
        "lcsc": lcsc,
        "lcsc_url": _lcsc_url(lcsc),
        "mfr": comp.get("mfr", ""),
        "package": comp.get("package", ""),
        "stock": comp.get("stock", 0),
        "price": unit_price,
        "is_basic": comp.get("is_basic", False),
        "is_preferred": comp.get("is_preferred", False),
    }
    if price_tiers:
        result["price_tiers"] = price_tiers
    # Include extra fields if present
    for key in ("category", "subcategory", "description"):
        if comp.get(key):
            result[key] = comp[key]
    return result


def _normalize_resistor(comp: dict) -> dict:
    """Normalize a resistor from /resistors/list.json."""
    lcsc = comp.get("lcsc", 0)
    # Parse attributes JSON string
    attrs = {}
    attr_raw = comp.get("attributes", "")
    if isinstance(attr_raw, str) and attr_raw:
        try:
            attrs = json.loads(attr_raw)
        except json.JSONDecodeError:
            pass

    # API returns power_watts in milliwatts (e.g., 100 = 100mW = 0.1W)
    power_raw = comp.get("power_watts", 0)
    if power_raw is not None:
        power_watts = float(power_raw) / 1000.0
    else:
        power_watts = 0.0

    result = {
        "lcsc": lcsc,
        "lcsc_url": _lcsc_url(lcsc),
        "mfr": comp.get("mfr", ""),
        "package": comp.get("package", ""),
        "resistance": float(comp.get("resistance", 0)),
        "tolerance_pct": float(comp.get("tolerance_fraction", 0)) * 100,
        "power_watts": power_watts,
        "stock": comp.get("stock", 0),
        "price": float(comp.get("price1", 0)),
        "is_basic": comp.get("is_basic", False),
        "is_preferred": comp.get("is_preferred", False),
    }
    if attrs:
        result["attributes"] = attrs
    return result


def _parse_cap_farads(value_str: str) -> Optional[float]:
    """Parse a capacitance string from attributes to farads. E.g., '100nF' -> 1e-7."""
    if not value_str:
        return None
    s = value_str.strip().upper().replace("F", "").replace("Μ", "U")
    multipliers = {"M": 1e-3, "U": 1e-6, "N": 1e-9, "P": 1e-12}
    for suffix, mult in multipliers.items():
        if s.endswith(suffix):
            try:
                return float(s[:-1]) * mult
            except ValueError:
                return None
    try:
        return float(s)
    except ValueError:
        return None


def _normalize_capacitor(comp: dict) -> dict:
    """Normalize a capacitor from /capacitors/list.json."""
    lcsc = comp.get("lcsc", 0)
    attrs = {}
    attr_raw = comp.get("attributes", "")
    if isinstance(attr_raw, str) and attr_raw:
        try:
            attrs = json.loads(attr_raw)
        except json.JSONDecodeError:
            pass

    # API top-level capacitance field may be None; fall back to attributes
    cap_raw = comp.get("capacitance")
    cap_farads = 0.0
    if cap_raw is not None:
        cap_farads = float(cap_raw)
    elif attrs:
        cap_str = attrs.get("Capacitance", "")
        parsed = _parse_cap_farads(cap_str)
        if parsed is not None:
            cap_farads = parsed

    result = {
        "lcsc": lcsc,
        "lcsc_url": _lcsc_url(lcsc),
        "mfr": comp.get("mfr", ""),
        "package": comp.get("package", ""),
        "capacitance_farads": cap_farads,
        "tolerance_pct": float(comp.get("tolerance_fraction", 0) or 0) * 100,
        "voltage_rating": float(comp.get("voltage_rating", 0) or 0),
        "stock": comp.get("stock", 0),
        "price": float(comp.get("price1", 0) or 0),
        "is_basic": comp.get("is_basic", False),
        "is_preferred": comp.get("is_preferred", False),
    }
    if attrs:
        result["attributes"] = attrs
    return result


# ── Public API ────────────────────────────────────────────────────────

def search_general(
    query: str,
    limit: int = 20,
    session: Optional[requests.Session] = None,
) -> dict:
    """Search LCSC by keyword (chip name, MPN, or LCSC number like 'C8734').

    Endpoint: GET /api/search?q=<query>&limit=<limit>

    Returns:
        {"query": str, "count": int, "results": [component, ...]}
    """
    resp = _make_request("/api/search", {"q": query, "limit": limit},
                         session=session)
    components = resp.get("components", [])
    results = [_normalize_general(c) for c in components[:limit]]
    return {
        "query": query,
        "count": len(results),
        "results": results,
    }


def search_resistors(
    search: Optional[str] = None,
    resistance: Optional[float] = None,
    package: Optional[str] = None,
    limit: int = 20,
    session: Optional[requests.Session] = None,
) -> dict:
    """Search resistors by parameters.

    Endpoint: GET /resistors/list.json

    Returns:
        {"query": dict, "count": int, "results": [resistor, ...]}
    """
    params: dict[str, object] = {}
    if search:
        params["search"] = search
    if resistance is not None:
        params["resistance"] = resistance
    if package:
        params["package"] = package
    params["full"] = "true"

    resp = _make_request("/resistors/list.json", params, session=session)
    resistors = resp.get("resistors", [])
    results = [_normalize_resistor(r) for r in resistors[:limit]]

    query_info: dict[str, object] = {}
    if search:
        query_info["search"] = search
    if resistance is not None:
        query_info["resistance_ohm"] = resistance
    if package:
        query_info["package"] = package

    return {
        "query": query_info,
        "count": len(results),
        "results": results,
    }


def search_capacitors(
    search: Optional[str] = None,
    capacitance: Optional[float] = None,
    voltage_rating: Optional[float] = None,
    package: Optional[str] = None,
    limit: int = 20,
    session: Optional[requests.Session] = None,
) -> dict:
    """Search capacitors by parameters.

    Endpoint: GET /capacitors/list.json

    Returns:
        {"query": dict, "count": int, "results": [capacitor, ...]}
    """
    params: dict[str, object] = {}
    if search:
        params["search"] = search
    if capacitance is not None:
        params["capacitance"] = capacitance
    if package:
        params["package"] = package
    params["full"] = "true"

    resp = _make_request("/capacitors/list.json", params, session=session)
    capacitors = resp.get("capacitors", [])
    results = [_normalize_capacitor(c) for c in capacitors[:limit]]

    query_info: dict[str, object] = {}
    if search:
        query_info["search"] = search
    if capacitance is not None:
        query_info["capacitance_farads"] = capacitance
    if voltage_rating is not None:
        query_info["voltage_rating"] = voltage_rating
    if package:
        query_info["package"] = package

    return {
        "query": query_info,
        "count": len(results),
        "results": results,
    }


def search_component(
    search: str,
    package: Optional[str] = None,
    limit: int = 20,
    session: Optional[requests.Session] = None,
) -> dict:
    """Full-mode component search with category/subcategory and tiered pricing.

    Endpoint: GET /components/list.json?search=<term>&full=true

    Returns:
        {"query": dict, "count": int, "results": [component, ...]}
    """
    params: dict[str, object] = {"search": search, "full": "true"}
    if package:
        params["package"] = package

    resp = _make_request("/components/list.json", params, session=session)
    components = resp.get("components", [])
    results = [_normalize_general(c) for c in components[:limit]]

    query_info: dict[str, object] = {"search": search}
    if package:
        query_info["package"] = package

    return {
        "query": query_info,
        "count": len(results),
        "results": results,
    }


# ── Output Formatting ─────────────────────────────────────────────────

def _jlcpcb_status(comp: dict) -> str:
    """Format JLCPCB assembly status."""
    if comp.get("is_basic"):
        return "✅ 基础库"
    elif comp.get("is_preferred"):
        return "🟡 优选扩展"
    else:
        return "⭕ 扩展库"


def _format_stock(stock: int) -> str:
    """Format stock quantity for display."""
    if stock >= 10_000:
        return f"{stock // 1000}k+"
    elif stock >= 1_000:
        return f"{stock:,}"
    elif stock > 0:
        return f"⚠️ {stock}"
    else:
        return "❌ 缺货"


def _format_price(price: float) -> str:
    """Format price for display."""
    if price < 0.01:
        return f"¥{price:.4f}"
    elif price < 1:
        return f"¥{price:.3f}"
    else:
        return f"¥{price:.2f}"


def _format_resistance(ohms: float) -> str:
    """Format resistance value for display."""
    if ohms >= 1_000_000:
        return f"{ohms / 1_000_000:g}MΩ"
    elif ohms >= 1_000:
        return f"{ohms / 1_000:g}kΩ"
    else:
        return f"{ohms:g}Ω"


def _format_capacitance_short(farads: float) -> str:
    """Format capacitance value for display."""
    if farads >= 1e-3:
        return f"{farads * 1e3:g}mF"
    elif farads >= 1e-6:
        return f"{farads * 1e6:g}µF"
    elif farads >= 1e-9:
        return f"{farads * 1e9:g}nF"
    else:
        return f"{farads * 1e12:g}pF"


def _format_table(results: dict, result_type: str = "general") -> str:
    """Format search results as a Markdown table for user display."""
    query_str = results.get("query", "")
    if isinstance(query_str, dict):
        query_str = ", ".join(f"{k}={v}" for k, v in query_str.items())

    count = results.get("count", 0)
    comps = results.get("results", [])

    lines = [
        f"## LCSC 搜索结果",
        f"",
        f"查询：`{query_str}`  |  匹配：{count} 条",
        f"",
    ]

    if count == 0:
        lines.append("未找到匹配结果。")
        return "\n".join(lines)

    if result_type == "resistor":
        lines.append("| # | LCSC编号 | 型号 | 阻值 | 精度 | 功率 | 封装 | 库存 | 单价 | JLCPCB |")
        lines.append("|---|----------|------|------|------|------|------|------|------|--------|")
        for i, c in enumerate(comps, 1):
            lines.append(
                f"| {i} | [{c['lcsc']}]({c['lcsc_url']}) | {c['mfr']} "
                f"| {_format_resistance(c.get('resistance', 0))} "
                f"| ±{c.get('tolerance_pct', 0):.0f}% "
                f"| {c.get('power_watts', 0) * 1000:.0f}mW "
                f"| {c['package']} "
                f"| {_format_stock(c['stock'])} "
                f"| {_format_price(c['price'])} "
                f"| {_jlcpcb_status(c)} |"
            )
    elif result_type == "capacitor":
        lines.append("| # | LCSC编号 | 型号 | 容值 | 精度 | 耐压 | 封装 | 库存 | 单价 | JLCPCB |")
        lines.append("|---|----------|------|------|------|------|------|------|------|--------|")
        for i, c in enumerate(comps, 1):
            lines.append(
                f"| {i} | [{c['lcsc']}]({c['lcsc_url']}) | {c['mfr']} "
                f"| {_format_capacitance_short(c.get('capacitance_farads', 0))} "
                f"| ±{c.get('tolerance_pct', 0):.0f}% "
                f"| {c.get('voltage_rating', 0):.0f}V "
                f"| {c['package']} "
                f"| {_format_stock(c['stock'])} "
                f"| {_format_price(c['price'])} "
                f"| {_jlcpcb_status(c)} |"
            )
    else:
        lines.append("| # | LCSC编号 | 型号 | 封装 | 库存 | 单价 | JLCPCB |")
        lines.append("|---|----------|------|------|------|------|--------|")
        for i, c in enumerate(comps, 1):
            extra = ""
            if c.get("category"):
                extra = f" | {c['category']}"
            lines.append(
                f"| {i} | [{c['lcsc']}]({c['lcsc_url']}) | {c['mfr']} "
                f"| {c['package']} "
                f"| {_format_stock(c['stock'])} "
                f"| {_format_price(c['price'])} "
                f"| {_jlcpcb_status(c)}{extra} |"
            )

    # Summary line
    basic_count = sum(1 for c in comps if c.get("is_basic"))
    instock_count = sum(1 for c in comps if c.get("stock", 0) > 0)
    lines.append("")
    lines.append(f"共 {count} 条：{instock_count} 有库存，{basic_count} 基础库")

    return "\n".join(lines)


# ── Value Parsing (for CLI) ───────────────────────────────────────────

def parse_resistance(s: str) -> float:
    """Parse human-readable resistance to ohms. '10k' -> 10000, '4.7K' -> 4700."""
    s = s.strip().upper().replace("Ω", "").replace("OHM", "")
    multipliers = {"M": 1_000_000, "K": 1_000, "R": 1}
    for suffix, mult in multipliers.items():
        if s.endswith(suffix):
            return float(s[:-1]) * mult
    return float(s)


def parse_capacitance(s: str) -> float:
    """Parse human-readable capacitance to farads. '100nF' -> 1e-7."""
    s = s.strip().upper().replace("F", "").replace("Μ", "U")
    multipliers = {"M": 1e-3, "U": 1e-6, "N": 1e-9, "P": 1e-12}
    for suffix, mult in multipliers.items():
        if s.endswith(suffix):
            return float(s[:-1]) * mult
    return float(s)


# ── CLI ────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="LCSC 元器件搜索工具 — 查询立创商城库存、价格、封装、JLCPCB贴片状态",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python search_lcsc.py general STM32F103C8T6
  python search_lcsc.py general C8734 --format json
  python search_lcsc.py resistor --search 10K --package 0805
  python search_lcsc.py capacitor --search 100nF --package 0603
  python search_lcsc.py component AMS1117-3.3
        """,
    )

    def _add_common(p):
        p.add_argument("--format", choices=["json", "table"], default="table",
                       help="输出格式 (default: table)")
        p.add_argument("--limit", type=int, default=20,
                       help="返回结果数量上限 (default: 20)")

    sub = parser.add_subparsers(dest="subcommand", required=True)

    # general
    p = sub.add_parser("general", help="通用搜索（芯片名称、MPN、LCSC编号）")
    _add_common(p)
    p.add_argument("query", help="搜索关键词 (如 STM32F103C8T6, C8734)")
    p.add_argument("--package", "-p", help="封装筛选 (如 SOT-223, LQFP-48)")

    # resistor
    p = sub.add_parser("resistor", help="电阻搜索（阻值、封装）")
    _add_common(p)
    p.add_argument("--search", "-s", help="搜索关键词 (如 10K, 0805W8F1002T5E)")
    p.add_argument("--resistance", "-r", type=parse_resistance, help="阻值筛选 (如 10K, 4.7k)")
    p.add_argument("--package", "-p", help="封装 (如 0805, 0603)")

    # capacitor
    p = sub.add_parser("capacitor", help="电容搜索（容值、封装）")
    _add_common(p)
    p.add_argument("--search", "-s", help="搜索关键词 (如 100nF)")
    p.add_argument("--capacitance", "-c", type=parse_capacitance, help="容值筛选 (如 100nF, 10uF)")
    p.add_argument("--voltage", "-v", type=float, help="耐压筛选 (V) (如 50)")
    p.add_argument("--package", "-p", help="封装 (如 0603, 0805)")

    # component
    p = sub.add_parser("component", help="详细搜索（含分类、阶梯价格）")
    _add_common(p)
    p.add_argument("search", help="搜索关键词 (如 AMS1117-3.3)")
    p.add_argument("--package", "-p", help="封装筛选")

    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    session = _get_session()

    try:
        if args.subcommand == "general":
            results = search_general(args.query, limit=args.limit,
                                     session=session)
            if args.package:
                results["results"] = [c for c in results["results"]
                                      if args.package.lower() in c.get("package", "").lower()]
                results["count"] = len(results["results"])
            rtype = "general"

        elif args.subcommand == "resistor":
            results = search_resistors(
                search=args.search,
                resistance=args.resistance,
                package=args.package,
                limit=args.limit,
                session=session,
            )
            rtype = "resistor"

        elif args.subcommand == "capacitor":
            results = search_capacitors(
                search=args.search,
                capacitance=args.capacitance,
                voltage_rating=args.voltage,
                package=args.package,
                limit=args.limit,
                session=session,
            )
            rtype = "capacitor"

        elif args.subcommand == "component":
            results = search_component(
                search=args.search,
                package=args.package,
                limit=args.limit,
                session=session,
            )
            rtype = "component"

        else:
            print(json.dumps({"error": f"未知子命令: {args.subcommand}"},
                             ensure_ascii=False), file=sys.stderr)
            sys.exit(1)

    except (requests.Timeout, requests.ConnectionError, RuntimeError) as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False),
              file=sys.stderr)
        sys.exit(2)

    if args.format == "json":
        # Compact JSON for Claude consumption
        print(json.dumps(results, ensure_ascii=False, separators=(",", ":")))
    else:
        print(_format_table(results, rtype))


if __name__ == "__main__":
    main()
