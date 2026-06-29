#!/usr/bin/env python3
"""ERC 可执行修复建议生成器 — 将 finding 转为结构化 JSON 修复指令。

本模块为 phase2_erc_check.py 的 companion module，不产生 CLI 入口。
通过 generate_actions(findings, netlist, design_spec) 调用。

每条 action 包含:
  - action_type: 10 种操作类型之一
  - target: 目标元件/引脚/网络
  - parameters: 具体参数值 + LCSC 元件推荐
  - confidence: high / medium / low
  - source: 手册依据

自包含设计：仅依赖 stdlib + netlist dict，不 import phase2_erc_check 私有函数。
"""

from __future__ import annotations

import re
from typing import Optional

# ═══════════════════════════════════════════════════════════════════════════════
# LCSC 元件推荐查找表
# ═══════════════════════════════════════════════════════════════════════════════

# 电容推荐表 — key: farad (float), 用 epsilon 容差匹配
LCSC_CAP_LUT: list[tuple[float, list[dict]]] = [
    (1e-7, [
        {"lcsc": "C14663", "package": "0603", "dielectric": "X7R", "voltage": "50V",
         "rank": 1, "usage": "首选: 基础库, 库存充足, 0603 小型封装"},
        {"lcsc": "C1525",  "package": "0805", "dielectric": "X7R", "voltage": "50V",
         "rank": 2, "usage": "备选: 0805 封装, 方便手工焊接"},
    ]),
    (1e-5, [
        {"lcsc": "C15849", "package": "0805", "dielectric": "X5R", "voltage": "25V",
         "rank": 1, "usage": "首选: 10µF 基础库"},
        {"lcsc": "C1714",  "package": "0603", "dielectric": "X5R", "voltage": "10V",
         "rank": 2, "usage": "备选: 0603 小型封装, 耐压仅 10V"},
    ]),
    (1e-6, [
        {"lcsc": "C28322", "package": "0603", "dielectric": "X5R", "voltage": "25V",
         "rank": 1, "usage": "首选: 1µF 0603"},
        {"lcsc": "C15849", "package": "0805", "dielectric": "X5R", "voltage": "25V",
         "rank": 2, "usage": "备选: 0805 封装 (10µF, 非 1µF)"},
    ]),
    (4.7e-6, [
        {"lcsc": "C19666", "package": "0603", "dielectric": "X5R", "voltage": "25V",
         "rank": 1, "usage": "首选: 4.7µF 0603"},
    ]),
    (2.2e-11, [
        {"lcsc": "C1531", "package": "0603", "dielectric": "C0G", "voltage": "50V",
         "rank": 1, "usage": "首选: 22pF, C0G 温度稳定, 晶振负载电容推荐"},
    ]),
    (3.3e-11, [
        {"lcsc": "C1540", "package": "0603", "dielectric": "C0G", "voltage": "50V",
         "rank": 1, "usage": "首选: 33pF, C0G 温度稳定, 晶振负载电容推荐"},
    ]),
    (4.7e-11, [
        {"lcsc": "C1534", "package": "0603", "dielectric": "C0G", "voltage": "50V",
         "rank": 1, "usage": "首选: 47pF, C0G 温度稳定"},
    ]),
    (1.5e-11, [
        {"lcsc": "C1528", "package": "0603", "dielectric": "C0G", "voltage": "50V",
         "rank": 1, "usage": "首选: 15pF, C0G"},
    ]),
    (1.8e-11, [
        {"lcsc": "C1529", "package": "0603", "dielectric": "C0G", "voltage": "50V",
         "rank": 1, "usage": "首选: 18pF, C0G"},
    ]),
    (2.7e-11, [
        {"lcsc": "C1532", "package": "0603", "dielectric": "C0G", "voltage": "50V",
         "rank": 1, "usage": "首选: 27pF, C0G"},
    ]),
]

# 电阻推荐表 — key: ohm (int), 用容差匹配
LCSC_RES_LUT: list[tuple[int, list[dict]]] = [
    (10000, [
        {"lcsc": "C25804", "package": "0603", "power": "0.1W", "tolerance": "±5%",
         "rank": 1, "usage": "首选: 基础库, 0603 小型封装"},
        {"lcsc": "C17414", "package": "0805", "power": "0.125W", "tolerance": "±5%",
         "rank": 2, "usage": "备选: 0805 封装, 方便手工焊接"},
    ]),
    (4700, [
        {"lcsc": "C23162", "package": "0603", "power": "0.1W", "tolerance": "±5%",
         "rank": 1, "usage": "首选: 4.7KΩ 基础库"},
        {"lcsc": "C17291", "package": "0805", "power": "0.125W", "tolerance": "±5%",
         "rank": 2, "usage": "备选: 0805 封装"},
    ]),
    (2200, [
        {"lcsc": "C17560", "package": "0805", "power": "0.125W", "tolerance": "±5%",
         "rank": 1, "usage": "首选: 2.2KΩ"},
    ]),
    (1000, [
        {"lcsc": "C17165", "package": "0805", "power": "0.125W", "tolerance": "±5%",
         "rank": 1, "usage": "首选: 1KΩ"},
    ]),
    (100000, [
        {"lcsc": "C17415", "package": "0805", "power": "0.125W", "tolerance": "±5%",
         "rank": 1, "usage": "首选: 100KΩ"},
    ]),
    (330, [
        {"lcsc": "C17344", "package": "0805", "power": "0.125W", "tolerance": "±5%",
         "rank": 1, "usage": "首选: 330Ω"},
    ]),
    (5100, [
        {"lcsc": "C23186", "package": "0603", "power": "0.1W", "tolerance": "±5%",
         "rank": 1, "usage": "首选: 5.1KΩ"},
    ]),
    (20000, [
        {"lcsc": "C17449", "package": "0805", "power": "0.125W", "tolerance": "±5%",
         "rank": 1, "usage": "首选: 20KΩ"},
    ]),
    (0, [
        {"lcsc": "C17489", "package": "0805", "power": "0.125W", "tolerance": "±5%",
         "rank": 1, "usage": "首选: 0Ω 跳线电阻"},
    ]),
]

_CAP_TOLERANCE = 0.15   # 15% 容差匹配 LCSC 推荐
_RES_TOLERANCE = 0.10   # 10% 容差匹配 LCSC 推荐


def _suggest_cap_lcsc(value_f: float, preferred_footprint: str = "0603") -> list[dict]:
    """按电容值 (farad) 查找 LCSC 推荐。"""
    results = []
    for key_f, parts in LCSC_CAP_LUT:
        if key_f == 0:
            continue
        if abs(value_f - key_f) / key_f <= _CAP_TOLERANCE:
            results.extend(parts)
    # 按 footprint 匹配排序
    results.sort(key=lambda p: (
        0 if p.get("package", "") == preferred_footprint else 1,
        p.get("rank", 99),
    ))
    return results[:3]


def _suggest_resistor_lcsc(value_ohm: float, preferred_footprint: str = "0805") -> list[dict]:
    """按电阻值 (ohm) 查找 LCSC 推荐。"""
    results = []
    for key_ohm, parts in LCSC_RES_LUT:
        if key_ohm == 0:
            continue
        if abs(value_ohm - key_ohm) / key_ohm <= _RES_TOLERANCE:
            results.extend(parts)
    results.sort(key=lambda p: (
        0 if p.get("package", "") == preferred_footprint else 1,
        p.get("rank", 99),
    ))
    return results[:3]


def _suggest_crystal_load_cap_lcsc(
    load_cap_pf: float,
    stray_pf: float = 3.0,
    preferred_footprint: str = "0603",
) -> list[dict]:
    """为晶振推荐负载电容 (C = 2*(CL - Cstray), 取 E6 标准值)。"""
    c_ideal = 2.0 * (load_cap_pf - stray_pf)
    # 取最接近的 E6 值
    e6 = [10, 15, 22, 33, 47, 68]
    best_e6 = None
    best_diff = float("inf")
    decade = 1.0
    if c_ideal >= 100:
        decade = 100.0
    elif c_ideal >= 10:
        decade = 10.0
    for val in e6:
        for mul in [0.1, 1.0, 10.0, 100.0]:
            v = val * mul
            diff = abs(v - c_ideal)
            if diff < best_diff:
                best_diff = diff
                best_e6 = v
    if best_e6 is None:
        return []
    return _suggest_cap_lcsc(best_e6 * 1e-12, preferred_footprint)


# ═══════════════════════════════════════════════════════════════════════════════
# 辅助解析函数（自包含，不依赖 phase2_erc_check）
# ═══════════════════════════════════════════════════════════════════════════════

def _parse_cap_value(value_str: str) -> float | None:
    """解析电容值字符串 → farad。"""
    if not value_str:
        return None
    s = value_str.strip().upper().replace(" ", "")
    # 匹配: 100nF, 0.1uF, 10uF, 22pF, 47pF 等
    m = re.match(r"([\d.]+)\s*(PF|NF|UF|MF|P|N|U|M)?F?", s)
    if not m:
        # 纯数字，默认 uF
        try:
            return float(s) * 1e-6
        except ValueError:
            return None
    val = float(m.group(1))
    unit = (m.group(2) or "UF").upper()
    if unit in ("PF", "P"):
        return val * 1e-12
    elif unit in ("NF", "N"):
        return val * 1e-9
    elif unit in ("UF", "U"):
        return val * 1e-6
    elif unit in ("MF", "M"):
        return val * 1e-3
    return val * 1e-6  # 默认 uF


def _parse_res_value(value_str: str) -> float | None:
    """解析电阻值字符串 → ohm。"""
    if not value_str:
        return None
    s = value_str.strip().upper().replace(" ", "").replace(",", ".")
    # 匹配: 10K, 4.7KΩ, 100R, 330Ω, 1M
    m = re.match(r"([\d.]+)\s*(M|K|R|Ω)?\s*(OHM|Ω)?", s)
    if not m:
        try:
            return float(s)
        except ValueError:
            return None
    val = float(m.group(1))
    suffix = (m.group(2) or "").upper()
    if suffix in ("M",):
        return val * 1e6
    elif suffix in ("K",):
        return val * 1e3
    elif suffix in ("R", "Ω"):
        return val
    return val  # 无后缀默认 ohm


def _find_component(netlist: dict, designator: str) -> dict | None:
    """在 netlist 中查找指定位号的元件。"""
    des_upper = designator.upper()
    for comp in netlist.get("components", []):
        if (comp.get("designator") or "").upper() == des_upper:
            return comp
    return None


def _is_power_net(name: str) -> bool:
    """判断网络名是否为电源网络。"""
    n = name.upper()
    return any(kw in n for kw in [
        "VCC", "VDD", "VIN", "VOUT", "VBAT", "VSYS",
        "VREF", "PWR", "POWER", "+5V", "+3.3V", "+12V",
        "+3V3", "+1V8", "+2V5",
    ])


def _is_ground_net(name: str) -> bool:
    """判断网络名是否为地网络。"""
    n = name.upper()
    return any(kw in n for kw in [
        "GND", "VSS", "AGND", "DGND", "PGND", "SGND",
        "GROUND", "EARTH",
    ])


def _find_caps_on_net(netlist: dict, net_name: str) -> list[dict]:
    """查找同一 net 上的所有电容元件。"""
    caps = []
    net_upper = net_name.upper()
    for comp in netlist.get("components", []):
        des = comp.get("designator", "")
        if not des.upper().startswith("C"):
            continue
        for pin in comp.get("pins", []):
            pn = (pin.get("net_name") or "").upper()
            if pn == net_upper:
                cap_f = _parse_cap_value(comp.get("value", ""))
                caps.append({
                    "designator": des,
                    "value": comp.get("value", ""),
                    "cap_f": cap_f,
                    "footprint": comp.get("footprint", ""),
                })
                break
    return caps


def _find_resistors_on_net(netlist: dict, net_name: str) -> list[dict]:
    """查找同一 net 上的所有电阻元件。"""
    resistors = []
    net_upper = net_name.upper()
    for comp in netlist.get("components", []):
        des = comp.get("designator", "")
        if not des.upper().startswith("R"):
            continue
        for pin in comp.get("pins", []):
            pn = (pin.get("net_name") or "").upper()
            if pn == net_upper:
                r_ohm = _parse_res_value(comp.get("value", ""))
                # 找电阻另一端的网络
                other_nets = []
                for p2 in comp.get("pins", []):
                    p2n = (p2.get("net_name") or "").upper()
                    if p2n and p2n != net_upper:
                        other_nets.append(p2.get("net_name"))
                resistors.append({
                    "designator": des,
                    "value": comp.get("value", ""),
                    "ohm": r_ohm,
                    "footprint": comp.get("footprint", ""),
                    "other_nets": other_nets,
                })
                break
    return resistors


def _extract_pin_from_location(location: str) -> str | None:
    """从 location 字符串提取引脚号。例: "Pin 44 (VCC+5V)" → "44"."""
    m = re.search(r"Pin\s+(\d+)", location, re.IGNORECASE)
    return m.group(1) if m else None


def _extract_net_from_location(location: str) -> str | None:
    """从 location 字符串提取网络名。例: "Pin 44 (VCC+5V)" → "VCC+5V"."""
    m = re.search(r"\(([^)]+)\)", location)
    return m.group(1) if m else None


def _extract_cap_value_from_text(text: str) -> str | None:
    """从文本中提取电容值。例: "...100nF..." → "100nF"."""
    m = re.search(r"(\d+\.?\d*\s*(?:pf|nf|uf|μf|pF|nF|uF|μF))", text)
    return m.group(1) if m else None


def _extract_resistor_value_from_text(text: str) -> str | None:
    """从文本中提取电阻值。例: "...10KΩ..." → "10KΩ"."""
    m = re.search(r"(\d+\.?\d*\s*(?:KΩ?|kΩ?|MΩ?|R|Ω|ohm))", text)
    return m.group(1) if m else None


# ═══════════════════════════════════════════════════════════════════════════════
# 通用 Action 构建函数
# ═══════════════════════════════════════════════════════════════════════════════

def _make_action(
    action_type: str,
    target: dict,
    parameters: dict,
    reason: str,
    confidence: str,
    source_type: str,
    source_ref: str,
    finding: dict,
    finding_index: int,
) -> dict:
    """构建一条结构化 action dict。"""
    return {
        "action_type": action_type,
        "target": target,
        "parameters": parameters,
        "reason": reason,
        "confidence": confidence,
        "source": {
            "type": source_type,
            "reference": source_ref,
        },
        "triggered_by": {
            "check_id": finding.get("check_id", ""),
            "severity": finding.get("severity", ""),
            "message": finding.get("message", ""),
            "finding_index": finding_index,
        },
    }


def _make_empty_target() -> dict:
    return {"component_ref": None, "pin": None, "net": None, "crystal_ref": None}


# ═══════════════════════════════════════════════════════════════════════════════
# 各 Check Type 对应的 Action Generator
# ═══════════════════════════════════════════════════════════════════════════════

# ── decoupling_cap (generic) ──────────────────────────────────────────────

def _gen_generic_decoupling(
    finding: dict, netlist: dict, design_spec: dict | None
) -> dict | None:
    """通用去耦电容缺失 → ADD_DECOUPLING_CAP (medium confidence)。"""
    comp_ref = finding.get("component_ref", "")
    location = finding.get("location", "")
    pin = _extract_pin_from_location(location)
    net = _extract_net_from_location(location)

    if not comp_ref:
        return None

    target = {
        "component_ref": comp_ref,
        "pin": pin,
        "net": net,
        "crystal_ref": None,
    }
    params = {
        "cap_value": "100nF",
        "cap_value_f": 1e-7,
        "from_net": net,
        "to_net": "GND",
        "suggested_parts": _suggest_cap_lcsc(1e-7, "0603"),
        "placement_note": f"靠近 {comp_ref} Pin{pin} (<5mm)" if pin else f"靠近 {comp_ref}",
        "notes": "通用规则建议值。若数据手册有明确要求，请使用 --spec 精确对照。",
    }
    return _make_action(
        "ADD_DECOUPLING_CAP", target, params,
        finding.get("message", ""),
        "medium", "heuristic", "通用 ERC 规则: IC VCC 引脚需 100nF 去耦电容",
        finding, -1,
    )


# ── enable_pin (generic) ──────────────────────────────────────────────────

def _gen_generic_enable_pin(
    finding: dict, netlist: dict, design_spec: dict | None
) -> dict | None:
    """EN/复位引脚浮空或无上下拉 → ADD_PULLUP_RESISTOR (medium/low confidence)。"""
    comp_ref = finding.get("component_ref", "")
    location = finding.get("location", "")
    pin = _extract_pin_from_location(location)
    net = _extract_net_from_location(location)
    detail = finding.get("detail", "")

    if not comp_ref:
        return None

    confidence = "medium"
    # 浮动引脚 → 高严重度，medium confidence
    if "浮空" in finding.get("message", ""):
        confidence = "medium"
    elif "未检测到" in finding.get("message", ""):
        confidence = "low"

    target = {
        "component_ref": comp_ref,
        "pin": pin,
        "net": net,
        "crystal_ref": None,
    }
    params = {
        "resistor_value": "10KΩ",
        "resistor_ohm": 10000,
        "from_net": net,
        "to_net": "VCC",
        "pull_target": "VCC",
        "suggested_parts": _suggest_resistor_lcsc(10000, "0805"),
        "notes": "通用规则建议值。EN/复位引脚通常需 10KΩ 上拉。若为低电平使能需下拉，请确认。",
    }
    return _make_action(
        "ADD_PULLUP_RESISTOR", target, params,
        finding.get("message", ""),
        confidence, "heuristic",
        "通用 ERC 规则: EN/复位引脚需上下拉电阻",
        finding, -1,
    )


# ── i2c_pullup (generic) ──────────────────────────────────────────────────

def _gen_generic_i2c(
    finding: dict, netlist: dict, design_spec: dict | None
) -> dict | None:
    """I2C 上拉缺失 → ADD_PULLUP_RESISTOR (medium confidence)。"""
    location = finding.get("location", "")
    net = _extract_net_from_location(location)
    # 如果没有括号格式，直接取 "Net: XXX" 格式
    if not net:
        net = location.replace("Net:", "").strip()
        if not net:
            return None

    target = {
        "component_ref": None,
        "pin": None,
        "net": net,
        "crystal_ref": None,
    }
    params = {
        "resistor_value": "4.7KΩ",
        "resistor_ohm": 4700,
        "from_net": net,
        "to_net": "VCC",
        "pull_target": "VCC",
        "suggested_parts": _suggest_resistor_lcsc(4700, "0805"),
        "notes": "I2C 标准上拉值 4.7KΩ。高速模式 (400kHz+) 可用 2.2KΩ，低速可用 10KΩ。",
    }
    return _make_action(
        "ADD_PULLUP_RESISTOR", target, params,
        finding.get("message", ""),
        "medium", "heuristic",
        "通用 ERC 规则: I2C SDA/SCL 需上拉电阻",
        finding, -1,
    )


# ── feedback_divider (generic) ────────────────────────────────────────────

def _gen_generic_feedback(
    finding: dict, netlist: dict, design_spec: dict | None
) -> dict | None:
    """通用反馈分压检查 → REVIEW_MANUALLY (low confidence, 信息不足)。"""
    comp_ref = finding.get("component_ref", "")
    if not comp_ref:
        return None

    target = {
        "component_ref": comp_ref,
        "pin": None,
        "net": None,
        "crystal_ref": None,
    }
    params = {
        "notes": "通用规则无法确定目标 Vout。请提供 Design Spec (--spec) 以启用精确反馈检查。",
    }
    return _make_action(
        "REVIEW_MANUALLY", target, params,
        finding.get("message", ""),
        "low", "heuristic",
        "通用 ERC 规则: 反馈分压检查需 Design Spec 提供目标 Vout",
        finding, -1,
    )


# ── crystal_load_cap (generic) ────────────────────────────────────────────

def _gen_generic_crystal(
    finding: dict, netlist: dict, design_spec: dict | None
) -> dict | None:
    """通用晶振检查 → ADD_CRYSTAL_LOAD_CAPS 或 REVIEW_MANUALLY。"""
    comp_ref = finding.get("component_ref", "")
    message = finding.get("message", "")
    detail = finding.get("detail", "")

    if not comp_ref:
        return None

    target = {
        "component_ref": None,
        "pin": None,
        "net": None,
        "crystal_ref": comp_ref,
    }

    if "不完整" in message:
        # 缺电容 → 建议添加
        params = {
            "cap_value_pf": 22.0,
            "suggested_cap_value": "22pF",
            "suggested_parts": _suggest_cap_lcsc(22e-12, "0603"),
            "placement_note": f"在 {comp_ref} 的两个引脚各接一个电容到 GND",
            "notes": "通用规则建议值 22pF (C0G)。C_ideal = 2×(CL - Cstray)。请提供 Design Spec 以精确计算。",
        }
        return _make_action(
            "ADD_CRYSTAL_LOAD_CAPS", target, params,
            message, "medium", "heuristic",
            "通用 ERC 规则: 晶振需对称负载电容",
            finding, -1,
        )
    else:
        # 有电容但需确认
        params = {
            "notes": "请对照晶振数据手册确认 CL 值是否匹配。可使用 --spec 参数启用精确计算。",
        }
        return _make_action(
            "REVIEW_MANUALLY", target, params,
            message, "low", "heuristic",
            "通用 ERC 规则: 请手动确认晶振负载电容",
            finding, -1,
        )


# ── floating_pin (generic) ────────────────────────────────────────────────

def _gen_generic_floating(
    finding: dict, netlist: dict, design_spec: dict | None
) -> dict | None:
    """悬空引脚 → REVIEW_MANUALLY (suggestion 级别不生成 action)。"""
    severity = finding.get("severity", "")
    # suggestion 级别（可能是故意 NC）→ 不生成 action
    if severity == "suggestion":
        return None

    location = finding.get("location", "")
    net = _extract_net_from_location(location)
    if not net:
        net = location.replace("Net:", "").strip()

    comp_ref = finding.get("component_ref", "")
    message = finding.get("message", "")

    target = {
        "component_ref": comp_ref if comp_ref else None,
        "pin": None,
        "net": net,
        "crystal_ref": None,
    }

    if "电源" in message or "地网络" in message:
        params = {
            "notes": "电源/地网络异常悬空。检查走线是否断开。",
        }
        return _make_action(
            "CONNECT_PIN_TO_NET", target, params,
            message, "low", "heuristic",
            "通用 ERC 规则: 电源/地不应悬空",
            finding, -1,
        )

    return None  # 其他悬空引脚：信息不足，需人工判断


# ── power_ground_short (generic) ──────────────────────────────────────────

def _gen_generic_pg_short(
    finding: dict, netlist: dict, design_spec: dict | None
) -> dict | None:
    """电源-地短路 → REVIEW_MANUALLY (low confidence)。"""
    target = {
        "component_ref": finding.get("component_ref") or None,
        "pin": None,
        "net": _extract_net_from_location(finding.get("location", "")),
        "crystal_ref": None,
    }
    params = {
        "notes": "电源-地短路为严重故障，需人工排查原理图中的短路线或 0Ω 电阻。",
    }
    return _make_action(
        "REVIEW_MANUALLY", target, params,
        finding.get("message", ""),
        "low", "heuristic",
        "通用 ERC 规则: 电源-地短路检测",
        finding, -1,
    )


# ── spec_decoupling ───────────────────────────────────────────────────────

def _gen_spec_decoupling(
    finding: dict, netlist: dict, design_spec: dict
) -> dict | None:
    """Spec 驱动的去耦电容检查 → ADD_DECOUPLING_CAP 或 CHANGE_CAPACITANCE (high)。"""
    comp_ref = finding.get("component_ref", "")
    location = finding.get("location", "")
    message = finding.get("message", "")
    spec_id = finding.get("spec_id", "")

    pin = _extract_pin_from_location(location)
    net = _extract_net_from_location(location)

    # 从 Design Spec 获取精确值
    cap_target_f = 1e-7  # default 100nF
    cap_target_str = "100nF"
    if design_spec:
        for req in design_spec.get("requirements", []):
            if req.get("id") == spec_id:
                rule = req.get("rule", {})
                cap_target_f = rule.get("cap_value_f", 1e-7)
                cap_target_str = rule.get("cap_value", "100nF")
                break

    target = {
        "component_ref": comp_ref,
        "pin": pin,
        "net": net,
        "crystal_ref": None,
    }

    if "缺少" in message:
        # 缺少去耦电容
        params = {
            "cap_value": cap_target_str,
            "cap_value_f": cap_target_f,
            "from_net": net,
            "to_net": "GND",
            "suggested_parts": _suggest_cap_lcsc(cap_target_f, "0603"),
            "placement_note": f"靠近 {comp_ref} Pin{pin} (<5mm)" if pin else "",
            "notes": "",
        }
        return _make_action(
            "ADD_DECOUPLING_CAP", target, params,
            message, "high", "design_spec",
            finding.get("source", ""),
            finding, -1,
        )
    elif "不匹配" in message or "偏差" in message:
        # 电容值不匹配 → 更换
        params = {
            "cap_value": cap_target_str,
            "cap_value_f": cap_target_f,
            "suggested_parts": _suggest_cap_lcsc(cap_target_f, "0603"),
            "notes": "",
        }
        return _make_action(
            "CHANGE_CAPACITANCE", target, params,
            message, "high", "design_spec",
            finding.get("source", ""),
            finding, -1,
        )

    return None


# ── spec_pullup / spec_pulldown ───────────────────────────────────────────

def _gen_spec_pull(
    finding: dict, netlist: dict, design_spec: dict
) -> dict | None:
    """Spec 驱动的上下拉检查 → ADD_PULLUP/PULLDOWN_RESISTOR 或 CHANGE_RESISTANCE。"""
    comp_ref = finding.get("component_ref", "")
    location = finding.get("location", "")
    message = finding.get("message", "")
    spec_id = finding.get("spec_id", "")
    check_id = finding.get("check_id", "")

    pin = _extract_pin_from_location(location)
    net = _extract_net_from_location(location)

    # 从 Design Spec 获取精确值
    target_ohm = None
    target_value_str = "?"
    pull_target = "VCC"
    if design_spec:
        for req in design_spec.get("requirements", []):
            if req.get("id") == spec_id:
                rule = req.get("rule", {})
                target_ohm = rule.get("resistor_ohm")
                target_value_str = rule.get("resistor_value", "?")
                pull_target = rule.get("pull_target", "VCC")
                break

    is_pulldown = "pulldown" in check_id or "下拉" in message or "GND" in pull_target.upper()

    target = {
        "component_ref": comp_ref,
        "pin": pin,
        "net": net,
        "crystal_ref": None,
    }

    if "缺少" in message:
        action_type = "ADD_PULLDOWN_RESISTOR" if is_pulldown else "ADD_PULLUP_RESISTOR"
        params = {
            "resistor_value": target_value_str,
            "resistor_ohm": target_ohm or 10000,
            "from_net": net,
            "to_net": pull_target,
            "pull_target": pull_target,
            "suggested_parts": _suggest_resistor_lcsc(target_ohm or 10000, "0805")
            if target_ohm else [],
            "notes": "",
        }
        return _make_action(
            action_type, target, params,
            message, "high", "design_spec",
            finding.get("source", ""),
            finding, -1,
        )
    elif "偏差" in message or "不匹配" in message:
        params = {
            "resistor_target_value": target_value_str,
            "resistor_target_ohm": target_ohm,
            "suggested_parts": _suggest_resistor_lcsc(target_ohm, "0805")
            if target_ohm else [],
            "notes": "",
        }
        return _make_action(
            "CHANGE_RESISTANCE", target, params,
            message, "medium", "design_spec",
            finding.get("source", ""),
            finding, -1,
        )

    return None


# ── spec_crystal ──────────────────────────────────────────────────────────

def _gen_spec_crystal(
    finding: dict, netlist: dict, design_spec: dict
) -> dict | None:
    """Spec 驱动的晶振检查 → ADD_CRYSTAL_LOAD_CAPS / CHANGE_CAPACITANCE / REVIEW_CRYSTAL_SELECTION。"""
    comp_ref = finding.get("component_ref", "")
    location = finding.get("location", "")
    message = finding.get("message", "")
    detail = finding.get("detail", "")
    spec_id = finding.get("spec_id", "")

    # 从 Design Spec 获取参数
    load_cap_pf = 20.0
    stray_pf = 3.0
    suggested_caps_pf = None
    if design_spec:
        for req in design_spec.get("requirements", []):
            if req.get("id") == spec_id:
                rule = req.get("rule", {})
                load_cap_pf = rule.get("load_cap_pf", 20.0)
                stray_pf = rule.get("stray_cap_pf", 3.0)
                suggested_caps_pf = rule.get("suggested_caps_pf")
                break

    target = {
        "component_ref": None,
        "pin": None,
        "net": None,
        "crystal_ref": comp_ref if comp_ref else None,
    }

    if "不完整" in message:
        # 缺负载电容
        suggested = suggested_caps_pf or 22.0
        params = {
            "cap_value_pf": suggested,
            "suggested_cap_value": f"{suggested:.0f}pF",
            "target_cl_pf": load_cap_pf,
            "stray_cap_pf": stray_pf,
            "suggested_parts": _suggest_crystal_load_cap_lcsc(load_cap_pf, stray_pf, "0603"),
            "placement_note": f"在晶振两个引脚与 GND 之间各接一个 {suggested:.0f}pF 电容",
            "notes": f"C_ideal = 2×({load_cap_pf}−{stray_pf}) = {2*(load_cap_pf-stray_pf):.1f}pF → 取 E6={suggested:.0f}pF",
        }
        return _make_action(
            "ADD_CRYSTAL_LOAD_CAPS", target, params,
            message, "high", "design_spec",
            finding.get("source", ""),
            finding, -1,
        )
    elif "严重偏差" in message:
        # CL 偏差 >30%
        # 提取 CL_eff
        cl_eff_match = re.search(r"CL_eff[≈=]\s*([\d.]+)\s*pF", detail)
        deviation_match = re.search(r"偏差\s*([+-]?[\d.]+)\s*%", detail)
        cl_eff_pf = float(cl_eff_match.group(1)) if cl_eff_match else None
        deviation_pct = float(deviation_match.group(1)) if deviation_match else None

        params = {
            "target_cl_pf": load_cap_pf,
            "current_cl_eff_pf": cl_eff_pf,
            "deviation_pct": deviation_pct,
            "suggested_cap_value": f"{suggested_caps_pf:.0f}pF" if suggested_caps_pf else None,
            "suggested_parts": _suggest_crystal_load_cap_lcsc(load_cap_pf, stray_pf, "0603"),
            "notes": "严重偏差 (>{:+.0f}%)，晶振可能无法正常起振。请更换负载电容。".format(
                abs(deviation_pct) if deviation_pct else 30),
        }
        return _make_action(
            "REVIEW_CRYSTAL_SELECTION", target, params,
            message, "medium", "design_spec",
            finding.get("source", ""),
            finding, -1,
        )
    elif "偏差" in message:
        # CL 偏差 10-30%
        params = {
            "cap_value": f"{suggested_caps_pf:.0f}pF" if suggested_caps_pf else None,
            "cap_value_f": (suggested_caps_pf * 1e-12) if suggested_caps_pf else None,
            "target_cl_pf": load_cap_pf,
            "suggested_parts": _suggest_crystal_load_cap_lcsc(load_cap_pf, stray_pf, "0603"),
            "notes": f"建议更换为 {suggested_caps_pf:.0f}pF" if suggested_caps_pf else "建议参考手册调整",
        }
        return _make_action(
            "CHANGE_CAPACITANCE", target, params,
            message, "medium", "design_spec",
            finding.get("source", ""),
            finding, -1,
        )

    return None


# ── spec_feedback ─────────────────────────────────────────────────────────

def _gen_spec_feedback(
    finding: dict, netlist: dict, design_spec: dict
) -> dict | None:
    """Spec 驱动的反馈检查 → CHANGE_RESISTANCE 或跳过 (REVIEW_MANUALLY for fixed LDO)。"""
    message = finding.get("message", "")

    # 固定输出 LDO → 无需操作
    if "固定输出" in message or "跳过" in message:
        return None

    comp_ref = finding.get("component_ref", "")
    detail = finding.get("detail", "")
    spec_id = finding.get("spec_id", "")

    # 从 Design Spec 获取目标值
    target_vout = None
    if design_spec:
        for req in design_spec.get("requirements", []):
            if req.get("id") == spec_id:
                rule = req.get("rule", {})
                target_vout = rule.get("target_vout")
                break

    target = {
        "component_ref": comp_ref,
        "pin": None,
        "net": None,
        "crystal_ref": None,
    }

    if "不匹配" in message:
        params = {
            "target_vout": target_vout,
            "notes": "请调整反馈电阻 R1/R2 使 Vout 接近目标值。可使用 compute_passive.py 计算。",
        }
        return _make_action(
            "CHANGE_RESISTANCE", target, params,
            message, "medium", "design_spec",
            finding.get("source", ""),
            finding, -1,
        )
    elif "未检测到" in message:
        params = {
            "target_vout": target_vout,
            "notes": "反馈电阻网络不完整，需补全 R1 (FB→VOUT) 和 R2 (FB→GND)。",
        }
        return _make_action(
            "REVIEW_MANUALLY", target, params,
            message, "medium", "design_spec",
            finding.get("source", ""),
            finding, -1,
        )

    return None


# ── spec_termination ──────────────────────────────────────────────────────

def _gen_spec_termination(
    finding: dict, netlist: dict, design_spec: dict
) -> dict | None:
    """Spec 驱动的端接检查 → 多种 action type。"""
    comp_ref = finding.get("component_ref", "")
    location = finding.get("location", "")
    message = finding.get("message", "")
    spec_id = finding.get("spec_id", "")

    pin = _extract_pin_from_location(location)
    net = _extract_net_from_location(location)

    # 从 Design Spec 获取端接要求
    termination = None
    resistor_ohm = None
    resistor_value_str = "?"
    if design_spec:
        for req in design_spec.get("requirements", []):
            if req.get("id") == spec_id:
                rule = req.get("rule", {})
                termination = rule.get("termination")
                resistor_ohm = rule.get("resistor_ohm")
                resistor_value_str = rule.get("resistor_value", "?")
                break

    target = {
        "component_ref": comp_ref,
        "pin": pin,
        "net": net,
        "crystal_ref": None,
    }

    if "浮空但已连接" in message or "应浮空" in message:
        params = {
            "from_net": net,
            "notes": "断开该引脚与当前网络的连接，使其保持浮空。",
        }
        return _make_action(
            "DISCONNECT_PIN", target, params,
            message, "high", "design_spec",
            finding.get("source", ""),
            finding, -1,
        )
    elif "缺少上拉" in message or "应上拉" in message:
        params = {
            "resistor_value": resistor_value_str,
            "resistor_ohm": resistor_ohm or 10000,
            "from_net": net,
            "to_net": "VCC",
            "pull_target": "VCC",
            "suggested_parts": _suggest_resistor_lcsc(resistor_ohm or 10000, "0805")
            if resistor_ohm else [],
            "notes": "",
        }
        return _make_action(
            "ADD_PULLUP_RESISTOR", target, params,
            message, "high", "design_spec",
            finding.get("source", ""),
            finding, -1,
        )
    elif "应下拉" in message:
        params = {
            "resistor_value": resistor_value_str,
            "resistor_ohm": resistor_ohm or 10000,
            "from_net": net,
            "to_net": "GND",
            "pull_target": "GND",
            "suggested_parts": [],
            "notes": "",
        }
        return _make_action(
            "ADD_PULLDOWN_RESISTOR", target, params,
            message, "high", "design_spec",
            finding.get("source", ""),
            finding, -1,
        )
    elif "未连接" in message and "应" in message:
        # 应连接但未连接
        params = {
            "notes": "该引脚必须连接到对应网络。请检查手册确认目标网络。",
        }
        return _make_action(
            "CONNECT_PIN_TO_NET", target, params,
            message, "medium", "design_spec",
            finding.get("source", ""),
            finding, -1,
        )
    elif "上拉电阻值偏差" in message or "下拉电阻值偏差" in message:
        params = {
            "resistor_target_value": resistor_value_str,
            "resistor_target_ohm": resistor_ohm,
            "suggested_parts": _suggest_resistor_lcsc(resistor_ohm, "0805")
            if resistor_ohm else [],
            "notes": "确认当前阻值是否可接受。",
        }
        return _make_action(
            "CHANGE_RESISTANCE", target, params,
            message, "medium", "design_spec",
            finding.get("source", ""),
            finding, -1,
        )

    return None


# ═══════════════════════════════════════════════════════════════════════════════
# Dispatcher — 按 check_id 路由
# ═══════════════════════════════════════════════════════════════════════════════

# Generic check_id → generator
_GENERIC_GENERATORS: dict[str, callable] = {
    "decoupling_cap": _gen_generic_decoupling,
    "enable_pin": _gen_generic_enable_pin,
    "i2c_pullup": _gen_generic_i2c,
    "feedback_divider": _gen_generic_feedback,
    "crystal_load_cap": _gen_generic_crystal,
    "floating_pin": _gen_generic_floating,
    "power_ground_short": _gen_generic_pg_short,
}

# Spec check_id → generator
_SPEC_GENERATORS: dict[str, callable] = {
    "spec_decoupling": _gen_spec_decoupling,
    "spec_pullup": _gen_spec_pull,
    "spec_pulldown": _gen_spec_pull,
    "spec_crystal": _gen_spec_crystal,
    "spec_feedback": _gen_spec_feedback,
    "spec_termination": _gen_spec_termination,
}


def _should_skip_finding(finding: dict) -> bool:
    """判断是否跳过某条 finding（不生成 action）。"""
    check_type = finding.get("check_type", "generic")
    severity = finding.get("severity", "")
    message = finding.get("message", "")

    # spec 驱动的 info/passed 类 finding 跳过
    if check_type == "spec":
        # 固定输出 LDO 跳过信息
        if "固定输出" in message or "跳过" in message:
            return True
        # 未找到匹配引脚 (suggestion, 非实质性缺陷)
        if severity == "suggestion" and "未找到匹配" in message:
            return True
        # 芯片未在 netlist 中找到
        if "未在 netlist 中找到" in message:
            return True
        # 引脚不足 (crystal)
        if "不足 2 个" in message:
            return True
        # 无法解析值
        if "无法解析" in message:
            return True

    # 通用检查：suggestion 级别通常不强制 action
    if check_type == "generic":
        # 悬空引脚的 suggestion（可能是故意 NC）
        if finding.get("check_id") == "floating_pin" and severity == "suggestion":
            return True
        # crystal_load_cap 的 suggestion (仅信息性)
        if finding.get("check_id") == "crystal_load_cap" and severity == "suggestion":
            if "CL_eff" in message or "无法解析" in message:
                return True

    return False


# ═══════════════════════════════════════════════════════════════════════════════
# 顶层 API
# ═══════════════════════════════════════════════════════════════════════════════

def generate_single_action(
    finding: dict,
    netlist: dict,
    design_spec: dict | None = None,
) -> dict | None:
    """为单条 finding 生成修复动作。

    Args:
        finding: ERC finding dict
        netlist: 网表 dict (extract_netlist 输出)
        design_spec: Design Spec dict (可选，含 --spec 时传入)

    Returns:
        Action dict，或 None（不可自动修复）
    """
    if _should_skip_finding(finding):
        return None

    check_id = finding.get("check_id", "")
    check_type = finding.get("check_type", "generic")

    if check_type == "spec" and design_spec:
        gen = _SPEC_GENERATORS.get(check_id)
        if gen:
            return gen(finding, netlist, design_spec)
    elif check_type == "generic":
        gen = _GENERIC_GENERATORS.get(check_id)
        if gen:
            return gen(finding, netlist, design_spec)

    return None


def generate_actions(
    findings: list[dict],
    netlist: dict,
    design_spec: dict | None = None,
) -> list[dict]:
    """为所有 ERC finding 生成结构化修复动作。

    Args:
        findings: ERC findings 列表 (合并后的 all_findings)
        netlist: 网表 dict
        design_spec: Design Spec dict (可选)

    Returns:
        Action dict 列表，每条含 action_id 序号
    """
    actions = []
    for i, finding in enumerate(findings):
        try:
            action = generate_single_action(finding, netlist, design_spec)
            if action is not None:
                # 补充序号和 finding_index
                action["triggered_by"]["finding_index"] = i
                actions.append(action)
        except Exception:
            # 单条生成失败不影响其他
            continue

    # 分配 action_id
    for idx, a in enumerate(actions, 1):
        a["action_id"] = f"auto_{idx}"

    return actions
