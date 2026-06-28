#!/usr/bin/env python3
"""嘉立创 EDA ERC 检查引擎 — 7 项通用检查 + Design Spec 驱动精确检查

读取 extract_netlist.py 的网表 JSON 输出，运行 ERC 检查并生成 Markdown 报告。

通用检查项（7 项）：
  1. IC VCC 引脚去耦电容检测
  2. EN/复位引脚上下拉检测
  3. I2C 总线上拉电阻检测
  4. 电源芯片反馈电阻分压检测
  5. 晶振负载电容检测
  6. 悬空引脚警告
  7. 电源-地短路检测

Design Spec 驱动检查（5 类）：
  - 去耦电容精确值对照（含 tolerance）
  - 上拉/下拉电阻精确值对照
  - 晶振负载电容 CL 偏差计算
  - 电源反馈 Vout 验证 / 固定输出跳过
  - 引脚端接状态检查（pullup/pulldown/must_float/must_connect）

用法：
  python phase2_erc_check.py netlist.json
  python phase2_erc_check.py netlist.json --format json
  python phase2_erc_check.py netlist.json --check decoupling,i2c
  python phase2_erc_check.py netlist.json --spec design_spec.json

管线：
  python parse_jlc_project.py project.eprj2 --format json | \\
    python extract_netlist.py --stdin --format json | \\
    python phase2_erc_check.py --stdin --spec design_spec.json

Python import:
  from phase2_erc_check import run_erc
  report = run_erc(netlist, design_spec=spec)
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    from phase2_action_gen import generate_actions as _generate_actions
    _ACTIONS_AVAILABLE = True
except ImportError:
    _ACTIONS_AVAILABLE = False

# ═══════════════════════════════════════════════════════════════════════════════
# 常量
# ═══════════════════════════════════════════════════════════════════════════════

# 去耦电容期望值范围
DECOUPLING_CAP_MIN_F = 80e-9   # 80nF
DECOUPLING_CAP_MAX_F = 120e-9  # 120nF
DECOUPLING_CAP_UF_MIN = 0.08   # 0.08uF
DECOUPLING_CAP_UF_MAX = 0.12   # 0.12uF

# I2C 上拉电阻典型范围 (ohm)
I2C_PULLUP_MIN = 1000    # 1K
I2C_PULLUP_MAX = 10000   # 10K

# 常见电源芯片 Vref (V)
KNOWN_VREF: dict[str, float] = {
    "ams1117": 1.25,
    "ams1117-3.3": 1.25,
    "ams1117-5.0": 1.25,
    "lm1117": 1.25,
    "mp1584": 0.8,
    "tps5430": 1.221,
    "lm2596": 1.23,
    "xl4005": 0.8,
    "xl4015": 1.25,
    "ld1117": 1.25,
    "ap2112": 0.8,
    "spx3819": 1.235,
    "me6211": 0.8,
    "rt9193": 0.8,
    "lm317": 1.25,
    "lm7805": None,  # 固定输出，无需反馈电阻
    "lm7803": None,
}

# 常见晶振杂散电容 (pF)
CSTRAY_DEFAULT = 3.0  # pF


# ═══════════════════════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════════════════════

def _find_component_by_ref(netlist: dict, designator: str) -> Optional[dict]:
    """按位号查找组件。"""
    for c in netlist.get("components", []):
        if (c.get("designator") or "").upper() == designator.upper():
            return c
    return None


def _find_net_by_name(netlist: dict, name: str) -> Optional[dict]:
    """按名称查找网络。"""
    for n in netlist.get("nets", []):
        if n.get("name", "").upper() == name.upper():
            return n
    return None


def _get_net_of_pin(component: dict, pin_number: str) -> Optional[str]:
    """获取组件某个引脚的 net_name。"""
    for pin in component.get("pins", []):
        if str(pin.get("pin_number")) == str(pin_number):
            return pin.get("net_name")
    return None


def _get_component_pin_net_names(component: dict) -> dict[str, Optional[str]]:
    """获取组件所有引脚的 {pin_number: net_name} 映射。"""
    return {
        str(p.get("pin_number")): p.get("net_name")
        for p in component.get("pins", [])
    }


def _parse_resistance_value(value_str: str) -> Optional[float]:
    """解析电阻值字符串，返回欧姆值。

    支持: "10K", "10KΩ", "5.1K", "100R", "1M", "10", "100nF" (不匹配)
    """
    if not value_str:
        return None
    import re
    s = str(value_str).strip().upper().replace("Ω", "").replace("OHM", "")
    # 匹配: 数字 + 可选单位(K, M, R)
    m = re.match(r'^([\d.]+)\s*(K|M|R|)?$', s)
    if not m:
        return None
    val = float(m.group(1))
    unit = m.group(2) or ""
    if unit == "K":
        val *= 1000
    elif unit == "M":
        val *= 1_000_000
    # R = just ohms, no multiplier
    return val


def _parse_capacitance_value(value_str: str) -> Optional[float]:
    """解析电容值字符串，返回法拉值。

    支持: "100nF", "0.1uF", "10uF", "47pF", "22pF"
    """
    if not value_str:
        return None
    import re
    s = str(value_str).strip().upper().replace(" ", "")
    m = re.match(r'^([\d.]+)\s*(PF|NF|UF|MF|P|N|U|F)?$', s)
    if not m:
        return None
    val = float(m.group(1))
    unit = (m.group(2) or "F").rstrip("F")
    multipliers = {"P": 1e-12, "N": 1e-9, "U": 1e-6, "M": 1e-3, "": 1.0}
    return val * multipliers.get(unit, 1.0)


def _is_power_net(name: Optional[str]) -> bool:
    """判断是否为电源网络。"""
    if not name:
        return False
    upper = name.upper()
    power_kw = ["VCC", "VDD", "VIN", "+5V", "+3.3V", "+12V", "VBUS", "VBAT"]
    return any(kw in upper for kw in power_kw)


def _is_ground_net(name: Optional[str]) -> bool:
    """判断是否为地网络。"""
    if not name:
        return False
    upper = name.upper()
    return "GND" in upper or "VSS" in upper or "GROUND" in upper


def _is_ic_component(comp: dict) -> bool:
    """判断是否为 IC 类元件。"""
    des = (comp.get("designator") or "").upper()
    if not des:
        return False  # 无位号，可能是电源/地符号
    if des.startswith("U"):
        return True
    pins = comp.get("pins", [])
    connected = sum(1 for p in pins if p.get("net_name"))
    if connected >= 6:
        return True
    return False


# ═══════════════════════════════════════════════════════════════════════════════
# 检查 1: IC VCC 去耦电容
# ═══════════════════════════════════════════════════════════════════════════════

def check_decoupling_caps(
    netlist: dict, ctx: dict | None = None
) -> list[dict]:
    """检查 IC 的 VCC/VDD 引脚是否就近有 100nF 去耦电容。

    策略:
      1. 找所有 IC 元件 (U* 或 pin>=6)
      2. 找 IC 连接到电源网络的引脚
      3. 检查同一电源网络上是否有电容 (C*) 其值在 80nF-120nF 范围
      4. 电容与 IC 的距离应合理 (< ~200mil)
    """
    findings = []
    components = netlist.get("components", [])
    nets = netlist.get("nets", [])

    # 建立 net_id → net 索引
    net_by_name: dict[str, dict] = {}
    for n in nets:
        name = n.get("name", "")
        if name:
            net_by_name[name.upper()] = n

    for comp in components:
        if not _is_ic_component(comp):
            continue

        des = (comp.get("designator") or comp.get("id") or "")
        # 找该 IC 连接到电源网络的引脚
        power_pins = []
        for pin in comp.get("pins", []):
            net_name = pin.get("net_name")
            if net_name and _is_power_net(net_name):
                power_pins.append(pin)

        if not power_pins:
            continue

        # 检查每个电源引脚
        for ppin in power_pins:
            ppin_net = ppin.get("net_name", "")
            pin_num = ppin.get("pin_number", "?")

            # 在该电源网络上找去耦电容
            has_decoupling = False
            for c2 in components:
                c2_des = c2.get("designator", "")
                if not c2_des.upper().startswith("C"):
                    continue

                # 检查电容是否在同一电源网络
                c2_on_same_net = any(
                    p.get("net_name") == ppin_net
                    for p in c2.get("pins", [])
                )
                if not c2_on_same_net:
                    continue

                # 检查电容值是否在去耦范围内
                cap_value = c2.get("value", "")
                cap_f = _parse_capacitance_value(cap_value)
                if cap_f is not None:
                    if DECOUPLING_CAP_MIN_F <= cap_f <= DECOUPLING_CAP_MAX_F:
                        has_decoupling = True
                        break

            if not has_decoupling:
                findings.append({
                    "check_id": "decoupling_cap",
                    "severity": "error",
                    "component_ref": des,
                    "location": f"Pin {pin_num} ({ppin_net})",
                    "message": f"IC 电源引脚缺少 100nF 去耦电容",
                    "detail": (
                        f"{des} Pin{pin_num} 连接到 {ppin_net}，"
                        f"但该网络上未检测到 100nF (80nF-120nF) 去耦电容。"
                        f"多数 IC 数据手册要求每个 VCC/VDD 引脚就近放置 100nF 电容。"
                    ),
                    "suggestion": (
                        f"在 {des} Pin{pin_num} 附近 (<5mm) 添加 100nF 去耦电容，"
                        f"推荐 LCSC C14663 (0603/X7R/100nF)。"
                    ),
                })

    return findings


# ═══════════════════════════════════════════════════════════════════════════════
# 检查 2: EN/复位引脚上下拉
# ═══════════════════════════════════════════════════════════════════════════════

_EN_PIN_NAMES = [
    "EN", "RST", "NRST", "RESET", "~RST", "RSTN", "ENABLE",
    "MR", "RSTIN", "POR", "ENA", "SHDN", "SHDN#",
]

def check_enable_pins(
    netlist: dict, ctx: dict | None = None
) -> list[dict]:
    """检查 EN/复位引脚是否有上拉/下拉电阻（不能浮空）。

    策略:
      1. 找 IC 元件
      2. 找连接到名称含 EN/RST/RESET... 的网络的引脚
      3. 检查该 net 上是否有电阻连接到 VCC (上拉) 或 GND (下拉)
      4. 若 net 只有 1 个 pin → ERROR (浮空)
      5. 若 net 有多个 pin 但无电阻 → WARNING
    """
    findings = []
    components = netlist.get("components", [])
    nets = netlist.get("nets", [])

    for comp in components:
        if not _is_ic_component(comp):
            continue
        des = (comp.get("designator") or comp.get("id") or "")

        for pin in comp.get("pins", []):
            net_name = (pin.get("net_name") or "").upper()
            if not net_name:
                continue

            # 检查网络名是否含 EN/RST 相关关键词
            is_en_pin = any(
                kw.upper() in (net_name or "")
                for kw in _EN_PIN_NAMES
            )
            if not is_en_pin:
                continue

            pin_num = pin.get("pin_number", "?")

            # 查该 net 信息
            net_info = None
            pin_count = 0
            is_floating = False
            for n in nets:
                if (n.get("name", "") or "").upper() == net_name:
                    net_info = n
                    pin_count = n.get("pin_count", 0)
                    is_floating = n.get("is_floating", False)
                    break

            # 查该 net 上是否有电阻
            has_pull_resistor = False
            resistor_info = ""
            for c2 in components:
                c2_des = (c2.get("designator") or "")
                if not c2_des.upper().startswith("R"):
                    continue
                c2_on_net = any(
                    (p.get("net_name") or "").upper() == net_name
                    for p in c2.get("pins", [])
                )
                if not c2_on_net:
                    continue
                # 检查电阻另一端是否接 VCC 或 GND
                for p2 in c2.get("pins", []):
                    p2_net = (p2.get("net_name") or "").upper()
                    if p2_net == net_name:
                        continue
                    if _is_power_net(p2_net):
                        has_pull_resistor = True
                        resistor_info = f"{c2_des}({c2.get('value','?')}) 上拉到 {p2_net}"
                        break
                    elif _is_ground_net(p2_net):
                        has_pull_resistor = True
                        resistor_info = f"{c2_des}({c2.get('value','?')}) 下拉到 {p2_net}"
                        break

            if is_floating:
                findings.append({
                    "check_id": "enable_pin",
                    "severity": "error",
                    "component_ref": des,
                    "location": f"Pin {pin_num} ({net_name})",
                    "message": f"EN/复位引脚浮空",
                    "detail": (
                        f"{des} Pin{pin_num} 连接到 {net_name}，"
                        f"但该网络只有 1 个引脚连接（浮空）。"
                        f"EN/复位引脚通常需要上拉或下拉电阻。"
                    ),
                    "suggestion": (
                        f"在 {des} Pin{pin_num} ({net_name}) 与 VCC 之间"
                        f"添加 10KΩ 上拉电阻，或与 GND 之间添加 10KΩ 下拉电阻。"
                    ),
                })
            elif not has_pull_resistor:
                findings.append({
                    "check_id": "enable_pin",
                    "severity": "warning",
                    "component_ref": des,
                    "location": f"Pin {pin_num} ({net_name})",
                    "message": "EN/复位引脚未检测到上下拉电阻",
                    "detail": (
                        f"{des} Pin{pin_num} 连接到 {net_name} (pin_count={pin_count})，"
                        f"但网络上未检测到连接到 VCC/GND 的电阻。"
                    ),
                    "suggestion": (
                        f"确认 {net_name} 是否需要上下拉。"
                        f"若是 EN/复位引脚，建议添加 10KΩ 上拉电阻到 VCC。"
                    ),
                })

    return findings


# ═══════════════════════════════════════════════════════════════════════════════
# 检查 3: I2C 上拉电阻
# ═══════════════════════════════════════════════════════════════════════════════

def check_i2c_pullups(
    netlist: dict, ctx: dict | None = None
) -> list[dict]:
    """检查 I2C 总线 (SDA/SCL) 是否有上拉电阻。

    策略:
      1. 找名称含 SDA/SCL 的网络
      2. 检查该网络上是否有电阻 (R*) 连接到 VCC
      3. 电阻值应在 1K-10K 范围
    """
    findings = []
    nets = netlist.get("nets", [])
    components = netlist.get("components", [])

    i2c_nets = []
    for n in nets:
        name = (n.get("name", "") or "").upper()
        if "SDA" in name or "SCL" in name or "I2C" in name:
            i2c_nets.append(n)

    if not i2c_nets:
        return findings

    for net in i2c_nets:
        net_name = net.get("name", "")
        has_pullup = False
        resistor_value = ""

        for comp in components:
            des = (comp.get("designator", "") or "").upper()
            if not des.startswith("R"):
                continue

            # 查电阻各引脚连接的网络
            pin_nets = {}
            for pin in comp.get("pins", []):
                pn = pin.get("net_name", "") or ""
                if pn:
                    pin_nets[pn.upper()] = pn

            net_upper = net_name.upper()
            if net_upper not in pin_nets:
                continue

            # 电阻一端在 I2C net，另一端在 VCC?
            for pn, orig_name in pin_nets.items():
                if pn == net_upper:
                    continue
                if _is_power_net(pn):
                    has_pullup = True
                    resistor_value = comp.get("value", "?")
                    break

        if not has_pullup:
            findings.append({
                "check_id": "i2c_pullup",
                "severity": "warning",
                "component_ref": "",
                "location": f"Net: {net_name}",
                "message": f"I2C 总线 {net_name} 缺少上拉电阻",
                "detail": (
                    f"I2C 总线 {net_name} 需要上拉电阻到 VCC。"
                    f"标准值: 2.2KΩ-10KΩ (高速用低值, 低速用高值)。"
                ),
                "suggestion": (
                    f"在 {net_name} 与 VCC 之间添加 4.7KΩ 上拉电阻。"
                    f"推荐 LCSC 0805/4.7KΩ 电阻。"
                ),
            })

    return findings


# ═══════════════════════════════════════════════════════════════════════════════
# 检查 4: 电源芯片反馈电阻分压
# ═══════════════════════════════════════════════════════════════════════════════

_POWER_CHIP_KEYWORDS = [
    "ams1117", "lm1117", "mp1584", "tps5430", "lm2596", "xl4005",
    "xl4015", "ld1117", "ap2112", "spx3819", "me6211", "rt9193",
    "lm317", "ldo", "regulator", "稳压", "dc-dc", "buck", "boost",
]

_FB_PIN_NAMES = ["FB", "ADJ", "VOUT_SENSE", "VFEEDBACK", "FEEDBACK",
                 "VSENSE", "SENSE", "SET", "VADJ"]


def check_feedback_divider(
    netlist: dict, ctx: dict | None = None
) -> list[dict]:
    """检查电源芯片反馈电阻分压是否与目标输出电压匹配。

    策略:
      1. 找电源芯片 (AMS1117等，含 ADJ/FB 引脚)
      2. 从 LDO 的 ADJ/FB 引脚出发
      3. 找两个电阻: R1(FB→VOUT), R2(FB→GND)
      4. 计算 Vout = Vref * (1 + R1/R2)
      5. 与预期输出电压对比

    注意: 固定输出 LDO (如 AMS1117-3.3) 不需要反馈电阻。
    """
    findings = []
    components = netlist.get("components", [])

    for comp in components:
        des = comp.get("designator", "") or ""
        if not des.upper().startswith("U"):
            continue

        dev_name = (comp.get("device_name", "") or "").lower()
        is_power_chip = any(kw in dev_name for kw in _POWER_CHIP_KEYWORDS)
        if not is_power_chip:
            continue

        # 找 FB/ADJ 引脚
        fb_pins = []
        for pin in comp.get("pins", []):
            net_name = (pin.get("net_name") or "").upper()
            if any(fb.upper() in net_name for fb in _FB_PIN_NAMES):
                fb_pins.append(pin)

        if not fb_pins:
            continue  # 固定输出 LDO，不需要检查

        for fpin in fb_pins:
            fb_net = (fpin.get("net_name") or "").upper()
            pin_num = fpin.get("pin_number", "?")

            # 找 FB net 上的电阻
            r_to_vout = None  # (designator, value_in_ohm)
            r_to_gnd = None

            for c2 in components:
                c2_des = c2.get("designator", "")
                if not c2_des.upper().startswith("R"):
                    continue

                c2_pin_nets = {}
                for p2 in c2.get("pins", []):
                    pn = (p2.get("net_name") or "").upper()
                    if pn:
                        c2_pin_nets[pn] = pn

                if fb_net not in c2_pin_nets:
                    continue

                r_val = _parse_resistance_value(c2.get("value", ""))
                if r_val is None:
                    continue

                # 另一端接 VOUT 还是 GND?
                for pn in c2_pin_nets:
                    if pn == fb_net:
                        continue
                    if _is_power_net(pn) and not _is_ground_net(pn):
                        r_to_vout = (c2_des, r_val)
                    elif _is_ground_net(pn):
                        r_to_gnd = (c2_des, r_val)

            if not r_to_vout or not r_to_gnd:
                findings.append({
                    "check_id": "feedback_divider",
                    "severity": "warning",
                    "component_ref": des,
                    "location": f"Pin {pin_num} (FB/ADJ)",
                    "message": "未检测到完整的反馈电阻网络",
                    "detail": (
                        f"电源芯片 {des} 的 FB/ADJ 引脚需接两个电阻: "
                        f"一个到 VOUT, 一个到 GND。"
                        f"当前: R_to_VOUT={r_to_vout[0] if r_to_vout else '无'}, "
                        f"R_to_GND={r_to_gnd[0] if r_to_gnd else '无'}。"
                    ),
                    "suggestion": "确认反馈电阻网络是否完整。可调用 compute_passive.py 计算所需阻值。",
                })
                continue

            # 计算 Vout
            r1_val = r_to_vout[1]
            r2_val = r_to_gnd[1]

            # 查 Vref
            vref = None
            for kw, v in KNOWN_VREF.items():
                if kw in dev_name:
                    vref = v
                    break
            if vref is None:
                vref = 1.25  # 默认值 (最常见的 LDO Vref)

            vout_calc = vref * (1 + r1_val / r2_val)

            findings.append({
                "check_id": "feedback_divider",
                "severity": "warning",
                "component_ref": des,
                "location": (
                    f"Pin {pin_num} (FB): R1={r_to_vout[0]}({r1_val:.0f}Ω→VOUT), "
                    f"R2={r_to_gnd[0]}({r2_val:.0f}Ω→GND)"
                ),
                "message": f"反馈分压计算: Vout ≈ {vout_calc:.2f}V (Vref={vref}V)",
                "detail": (
                    f"Vout = Vref × (1 + R1/R2) = {vref} × (1 + {r1_val:.0f}/{r2_val:.0f})"
                    f" = {vout_calc:.2f}V。"
                    f"请与数据手册对比确认目标输出电压。"
                ),
                "suggestion": (
                    f"若目标 Vout ≠ {vout_calc:.2f}V，使用 "
                    f"`compute_passive.py feedback-divider --vref {vref} --vout <目标> --r2 {r2_val:.0f}`"
                    f" 计算正确的 R1 值。"
                ),
            })

    return findings


# ═══════════════════════════════════════════════════════════════════════════════
# 检查 5: 晶振负载电容
# ═══════════════════════════════════════════════════════════════════════════════

def check_crystal_load_caps(
    netlist: dict, ctx: dict | None = None
) -> list[dict]:
    """检查晶振是否有匹配的负载电容。

    策略:
      1. 找晶振元件 (Y*, X* 或 名称含 crystal/晶振)
      2. 每个晶振引脚应对称接电容到 GND
      3. CL = (C1*C2)/(C1+C2) + Cstray
      4. 若 datasheet ctx 给出 CL，验证电容值；否则仅检查是否存在电容
    """
    findings = []
    components = netlist.get("components", [])

    for comp in components:
        des = comp.get("designator", "") or ""
        dev_name = (comp.get("device_name", "") or comp.get("name", "")).lower()

        is_crystal = (
            des.upper().startswith("Y") or
            des.upper().startswith("X") or
            any(kw in dev_name for kw in ("crystal", "晶振", "xtal", "xihcelnanf"))
        )
        if not is_crystal:
            continue

        pins = comp.get("pins", [])
        if len(pins) < 2:
            continue

        # 找每个引脚上的电容到 GND
        pin_caps = []
        for pin in pins:
            pin_net = (pin.get("net_name") or "").upper()
            if not pin_net:
                continue

            for c2 in components:
                c2_des = c2.get("designator", "")
                if not c2_des.upper().startswith("C"):
                    continue

                # 检查电容是否一端在 pin_net，另一端在 GND
                c2_on_pin_net = False
                c2_on_gnd = False
                for p2 in c2.get("pins", []):
                    p2_net = (p2.get("net_name") or "").upper()
                    if p2_net == pin_net:
                        c2_on_pin_net = True
                    if _is_ground_net(p2_net):
                        c2_on_gnd = True

                if c2_on_pin_net and c2_on_gnd:
                    cap_f = _parse_capacitance_value(c2.get("value", ""))
                    pin_caps.append({
                        "pin": pin.get("pin_number"),
                        "net": pin.get("net_name"),
                        "cap_ref": c2_des,
                        "cap_value": c2.get("value", ""),
                        "cap_f": cap_f,
                    })
                    break  # 每引脚只找一个电容

        if len(pin_caps) < 2:
            findings.append({
                "check_id": "crystal_load_cap",
                "severity": "warning",
                "component_ref": des,
                "location": "",
                "message": "晶振负载电容不完整",
                "detail": (
                    f"晶振 {des} 有 {len(pins)} 个引脚，但只检测到 "
                    f"{len(pin_caps)} 个负载电容。每个晶振引脚应对称接电容到 GND。"
                ),
                "suggestion": (
                    "在每个晶振引脚与 GND 之间各添加一个相同的负载电容。"
                    "CL = (C1×C2)/(C1+C2) + Cstray(~3pF)。常用值: 22pF。"
                ),
            })
            continue

        # 检查两个电容值是否对称
        c1_val = pin_caps[0].get("cap_f")
        c2_val = pin_caps[1].get("cap_f")
        if c1_val and c2_val:
            cl_eff = (c1_val * c2_val) / (c1_val + c2_val) + CSTRAY_DEFAULT * 1e-12
            findings.append({
                "check_id": "crystal_load_cap",
                "severity": "suggestion",
                "component_ref": des,
                "location": (
                    f"{pin_caps[0]['cap_ref']}({pin_caps[0]['cap_value']}), "
                    f"{pin_caps[1]['cap_ref']}({pin_caps[1]['cap_value']})"
                ),
                "message": f"晶振负载电容: CL_eff ≈ {cl_eff * 1e12:.1f}pF",
                "detail": (
                    f"CL_eff = ({c1_val*1e12:.1f}×{c2_val*1e12:.1f})/"
                    f"({c1_val*1e12:.1f}+{c2_val*1e12:.1f}) + {CSTRAY_DEFAULT}pF"
                    f" = {cl_eff*1e12:.1f}pF。"
                    f"请对照晶振数据手册中的 CL 要求验证。"
                ),
                "suggestion": (
                    "若 CL 不匹配，使用 `compute_passive.py crystal-load --cl <目标> --format json`"
                    " 计算所需负载电容值。"
                ),
            })
        else:
            findings.append({
                "check_id": "crystal_load_cap",
                "severity": "suggestion",
                "component_ref": des,
                "location": "",
                "message": "晶振负载电容值无法解析，请手动确认",
                "detail": (
                    f"晶振 {des} 的负载电容值无法解析。"
                    f"C1={pin_caps[0].get('cap_value','?')}, "
                    f"C2={pin_caps[1].get('cap_value','?')}。"
                ),
                "suggestion": "确认电容值是否满足晶振的 CL 要求。",
            })

    return findings


# ═══════════════════════════════════════════════════════════════════════════════
# 检查 6: 悬空引脚
# ═══════════════════════════════════════════════════════════════════════════════

def check_floating_pins(
    netlist: dict, ctx: dict | None = None
) -> list[dict]:
    """检查悬空网络 (pin_count <= 1 的 net)。

    策略:
      1. 找 pin_count <= 1 的网络
      2. 若网络名是 VCC/GND → ERROR
      3. 若网络名是信号名 → WARNING
      4. 排除故意 NC (No Connect) 的引脚
    """
    findings = []
    nets = netlist.get("nets", [])
    components = netlist.get("components", [])

    for net in nets:
        if not net.get("is_floating", False):
            continue

        net_name = net.get("name", "")
        pin_count = net.get("pin_count", 0)

        # 找该 net 上的组件
        connected_comps = []
        for comp in components:
            for pin in comp.get("pins", []):
                if pin.get("net_name", "") == net_name:
                    connected_comps.append({
                        "ref": comp.get("designator", "") or comp.get("id", ""),
                        "pin": pin.get("pin_number", "?"),
                    })

        comp_strs = [f"{c['ref']} Pin{c['pin']}" for c in connected_comps]

        if _is_power_net(net_name):
            findings.append({
                "check_id": "floating_pin",
                "severity": "error",
                "component_ref": comp_strs[0] if comp_strs else "",
                "location": f"Net: {net_name}",
                "message": f"电源网络 {net_name} 悬空 (pin_count={pin_count})",
                "detail": f"电源网络仅连接了 {pin_count} 个引脚。",
                "suggestion": "检查电源网络连接是否完整。",
            })
        elif _is_ground_net(net_name):
            findings.append({
                "check_id": "floating_pin",
                "severity": "error",
                "component_ref": comp_strs[0] if comp_strs else "",
                "location": f"Net: {net_name}",
                "message": f"地网络 {net_name} 悬空 (pin_count={pin_count})",
                "detail": f"地网络仅连接了 {pin_count} 个引脚。",
                "suggestion": "检查地网络连接是否完整。",
            })
        else:
            findings.append({
                "check_id": "floating_pin",
                "severity": "suggestion",
                "component_ref": comp_strs[0] if comp_strs else "",
                "location": f"Net: {net_name}",
                "message": f"网络 {net_name} 悬空 (pin_count={pin_count})",
                "detail": (
                    f"网络 {net_name} 仅连接了 {pin_count} 个引脚，"
                    f"可能是故意 NC (No Connect) 的引脚。"
                ),
                "suggestion": "确认该引脚是否确实不需要连接。若是 IO 引脚，可能需要外部上下拉。",
            })

    return findings


# ═══════════════════════════════════════════════════════════════════════════════
# 检查 7: 电源-地短路
# ═══════════════════════════════════════════════════════════════════════════════

def check_power_ground_short(
    netlist: dict, ctx: dict | None = None
) -> list[dict]:
    """检查电源网络是否与地短路。

    策略:
      1. 找所有 power net 和 ground net
      2. 检查是否有元件同时连接 power 和 ground（如去耦电容是正常的）
      3. 若同一 net 既是 power 又是 ground → ERROR
    """
    findings = []
    nets = netlist.get("nets", [])

    power_nets = []
    ground_nets = []
    for n in nets:
        name = n.get("name", "")
        if _is_ground_net(name):
            ground_nets.append(n)
        elif _is_power_net(name):
            power_nets.append(n)

    # 检查 power 和 ground 是否在同一个 net
    # (这不太可能发生，但以防万一)
    for n in nets:
        name = n.get("name", "")
        is_power = _is_power_net(name)
        is_ground = _is_ground_net(name)
        if is_power and is_ground:
            findings.append({
                "check_id": "power_ground_short",
                "severity": "error",
                "component_ref": "",
                "location": f"Net: {name}",
                "message": "电源网络与地网络同名",
                "detail": f"网络 {name} 名称同时含电源和地关键词。",
                "suggestion": "检查网络命名。",
            })

    # 检查是否有 wire 或 0-ohm 电阻直接连接 power 和 ground
    components = netlist.get("components", [])
    for comp in components:
        des = comp.get("designator", "")
        pin_nets = {}
        for pin in comp.get("pins", []):
            pn = pin.get("net_name")
            if pn:
                pin_nets[pn.upper()] = pn

        has_power = any(_is_power_net(pn) for pn in pin_nets)
        has_ground = any(_is_ground_net(pn) for pn in pin_nets)

        if has_power and has_ground:
            res_val = comp.get("value", "")
            r_ohm = _parse_resistance_value(res_val)
            if r_ohm is not None and r_ohm < 1.0:
                findings.append({
                    "check_id": "power_ground_short",
                    "severity": "error",
                    "component_ref": des,
                    "location": f"{des}({res_val})",
                    "message": "电源与地之间检测到 ~0Ω 电阻 (短路!)",
                    "detail": (
                        f"{des}({res_val}) 同时连接电源和地。"
                        f"如果是跳线/0Ω电阻，确认是否故意为之。"
                    ),
                    "suggestion": "检查是否无意中将电源与地短路。",
                })

    return findings


# ═══════════════════════════════════════════════════════════════════════════════
# Design Spec 驱动检查（手册精确对照）
# ═══════════════════════════════════════════════════════════════════════════════

def _find_chip_in_netlist(netlist: dict, designator: str) -> Optional[dict]:
    """在 netlist 中查找指定位号的芯片。"""
    des_upper = designator.upper()
    for comp in netlist.get("components", []):
        if (comp.get("designator") or "").upper() == des_upper:
            return comp
    return None


def _match_pins(component: dict, pin_pattern: str) -> list[dict]:
    """匹配 component 中 net_name 满足 pin_pattern (regex) 的引脚。"""
    import re
    try:
        pat = re.compile(pin_pattern, re.IGNORECASE)
    except re.error:
        pat = re.compile(re.escape(pin_pattern), re.IGNORECASE)
    matched = []
    for pin in component.get("pins", []):
        net_name = pin.get("net_name") or ""
        if pat.search(net_name):
            matched.append(pin)
    return matched


def _find_caps_on_net(netlist: dict, net_name: str) -> list[dict]:
    """查找同一 net 上的所有电容元件 (C*)。返回 [{designator, value, cap_f, ...}]。"""
    caps = []
    net_upper = net_name.upper()
    for comp in netlist.get("components", []):
        des = (comp.get("designator") or "").upper()
        if not des.startswith("C"):
            continue
        on_net = any(
            (p.get("net_name") or "").upper() == net_upper
            for p in comp.get("pins", [])
        )
        if on_net:
            cap_f = _parse_capacitance_value(comp.get("value", ""))
            caps.append({
                "designator": comp.get("designator", ""),
                "value": comp.get("value", ""),
                "cap_f": cap_f,
            })
    return caps


def _find_resistors_on_net(netlist: dict, net_name: str) -> list[dict]:
    """查找同一 net 上的所有电阻元件 (R*)。返回 [{designator, value, ohm, pins}]。"""
    resistors = []
    net_upper = net_name.upper()
    for comp in netlist.get("components", []):
        des = (comp.get("designator") or "").upper()
        if not des.startswith("R"):
            continue
        on_net = any(
            (p.get("net_name") or "").upper() == net_upper
            for p in comp.get("pins", [])
        )
        if on_net:
            ohm = _parse_resistance_value(comp.get("value", ""))
            resistors.append({
                "designator": comp.get("designator", ""),
                "value": comp.get("value", ""),
                "ohm": ohm,
                "pins": comp.get("pins", []),
            })
    return resistors


def _make_finding(
    check_id: str,
    severity: str,
    component_ref: str,
    location: str,
    message: str,
    detail: str,
    suggestion: str,
    source: str | None = None,
    spec_id: str | None = None,
    check_type: str = "spec",
) -> dict:
    """构建一条 ERC finding（统一工厂函数）。"""
    f = {
        "check_id": check_id,
        "severity": severity,
        "component_ref": component_ref,
        "location": location,
        "message": message,
        "detail": detail,
        "suggestion": suggestion,
        "check_type": check_type,
    }
    if source:
        f["source"] = source
    if spec_id:
        f["spec_id"] = spec_id
    return f


def _downgrade_severity(severity: str, steps: int = 1) -> str:
    """降低严重度等级: error → warning → suggestion → suggestion."""
    levels = {"error": 0, "warning": 1, "suggestion": 2}
    current = levels.get(severity, 2)
    new_level = min(current + steps, 2)
    return ["error", "warning", "suggestion"][new_level]


# ── Spec Check: decoupling ──────────────────────────────────────────────

def _spec_check_decoupling(netlist: dict, req: dict) -> list[dict]:
    """根据 Design Spec 的 decoupling 规则检查去耦电容。

    rule 必填字段: target_chip, pin_pattern, cap_value, cap_value_f
    rule 可选字段: tolerance (默认 0.2), placement, distance_mm
    """
    findings = []
    rule = req.get("rule", {})
    target_chip = rule.get("target_chip", "")
    pin_pattern = rule.get("pin_pattern", r"VCC|VDD")
    cap_target_f = rule.get("cap_value_f")
    cap_target_str = rule.get("cap_value", "?")
    tolerance = rule.get("tolerance", 0.2)
    source = req.get("source", "")
    spec_id = req.get("id", "")
    req_severity = req.get("severity", "error")
    notes = rule.get("notes", "")

    chip = _find_chip_in_netlist(netlist, target_chip)
    if chip is None:
        findings.append(_make_finding(
            "spec_decoupling", "warning", target_chip, "",
            f"Design Spec 目标芯片 {target_chip} 未在 netlist 中找到",
            f"spec 要求检查 {target_chip} 的去耦电容，但该位号不在当前原理图中。",
            "确认位号是否匹配，或芯片是否已放置。",
            source=source, spec_id=spec_id,
        ))
        return findings

    matched_pins = _match_pins(chip, pin_pattern)
    if not matched_pins:
        findings.append(_make_finding(
            "spec_decoupling", "suggestion", target_chip, "",
            f"{target_chip} 未找到匹配 '{pin_pattern}' 的电源引脚",
            f"spec 要求检查匹配 {pin_pattern} 的引脚去耦，但未找到匹配引脚。",
            "确认 pin_pattern 是否与实际原理图网络名一致。",
            source=source, spec_id=spec_id,
        ))
        return findings

    for pin in matched_pins:
        pin_num = pin.get("pin_number", "?")
        net_name = pin.get("net_name") or "(无网络)"
        caps = _find_caps_on_net(netlist, net_name)

        # 过滤出电容值在 tolerance 范围内的
        matched_caps = []
        for c in caps:
            if c["cap_f"] is not None and cap_target_f is not None:
                if abs(c["cap_f"] - cap_target_f) / cap_target_f <= tolerance:
                    matched_caps.append(c)

        if not matched_caps:
            if caps:
                # 有电容但值不匹配
                cap_list = ", ".join(
                    f"{c['designator']}({c['value']})" for c in caps
                )
                findings.append(_make_finding(
                    "spec_decoupling",
                    _downgrade_severity(req_severity),
                    target_chip,
                    f"Pin {pin_num} ({net_name})",
                    f"去耦电容值不匹配: 期望 {cap_target_str}±{tolerance*100:.0f}%",
                    f"手册要求 {cap_target_str}，实际: {cap_list}。"
                    f"来源: {source}",
                    f"将电容更换为 {cap_target_str}，或确认该值是否可接受。",
                    source=source, spec_id=spec_id,
                ))
            else:
                findings.append(_make_finding(
                    "spec_decoupling",
                    req_severity,
                    target_chip,
                    f"Pin {pin_num} ({net_name})",
                    f"缺少去耦电容: 手册要求 {cap_target_str}",
                    f"{target_chip} Pin{pin_num} ({net_name}) 网络上未检测到任何电容。"
                    f"手册 {source} 要求 {cap_target_str} 去耦电容。",
                    f"在 {target_chip} Pin{pin_num} 附近添加 {cap_target_str} 去耦电容到 GND。"
                    f"{' ' + notes if notes else ''}",
                    source=source, spec_id=spec_id,
                ))

    return findings


# ── Spec Check: pullup / pulldown ───────────────────────────────────────

def _spec_check_pull(netlist: dict, req: dict) -> list[dict]:
    """根据 Design Spec 的 pullup/pulldown 规则检查上下拉电阻。

    rule 必填字段: target_chip, pin_pattern, resistor_value, resistor_ohm, pull_target
    """
    findings = []
    rule = req.get("rule", {})
    target_chip = rule.get("target_chip", "")
    pin_pattern = rule.get("pin_pattern", "")
    target_ohm = rule.get("resistor_ohm")
    target_value_str = rule.get("resistor_value", "?")
    pull_target = rule.get("pull_target", "VCC")  # VCC or GND
    source = req.get("source", "")
    spec_id = req.get("id", "")
    req_severity = req.get("severity", "error")
    category = req.get("category", "pullup")
    notes = rule.get("notes", "")

    # 若 notes 注明"可选"/"非必须"，降级 severity
    optional_keywords = ["可选", "非必须", "内部已集成", "内置", "optional", "not required"]
    is_optional = any(kw in notes for kw in optional_keywords)
    effective_severity = (
        _downgrade_severity(req_severity, 2) if is_optional
        else req_severity
    )

    chip = _find_chip_in_netlist(netlist, target_chip)
    if chip is None:
        findings.append(_make_finding(
            f"spec_{category}", "warning", target_chip, "",
            f"Design Spec 目标芯片 {target_chip} 未在 netlist 中找到",
            f"spec 要求检查 {target_chip} 的上下拉，但该位号不在当前原理图中。",
            "确认位号是否匹配。",
            source=source, spec_id=spec_id,
        ))
        return findings

    matched_pins = _match_pins(chip, pin_pattern)
    if not matched_pins:
        findings.append(_make_finding(
            f"spec_{category}", "suggestion", target_chip, "",
            f"{target_chip} 未找到匹配 '{pin_pattern}' 的引脚",
            f"spec 要求检查 {pin_pattern} 的上下拉，但未找到匹配引脚。",
            "确认 pin_pattern 是否与实际原理图网络名一致。",
            source=source, spec_id=spec_id,
        ))
        return findings

    for pin in matched_pins:
        pin_num = pin.get("pin_number", "?")
        net_name = pin.get("net_name") or ""
        if not net_name:
            findings.append(_make_finding(
                f"spec_{category}", effective_severity, target_chip,
                f"Pin {pin_num} (无网络)",
                f"引脚未连接任何网络，无法检查上下拉",
                f"{target_chip} Pin{pin_num} 无网络连接。",
                "确认该引脚是否已连接到正确的网络。",
                source=source, spec_id=spec_id,
            ))
            continue

        resistors = _find_resistors_on_net(netlist, net_name)
        has_pull = False
        pull_info = ""
        for r in resistors:
            # 检查电阻另一端是否连接到 pull_target
            for rp in r["pins"]:
                rp_net = (rp.get("net_name") or "").upper()
                if rp_net == net_name.upper():
                    continue
                if pull_target.upper() in rp_net or (
                    pull_target.upper() == "VCC" and _is_power_net(rp_net)
                ) or (
                    pull_target.upper() == "GND" and _is_ground_net(rp_net)
                ):
                    has_pull = True
                    pull_info = f"{r['designator']}({r['value']}) → {rp_net}"
                    # 检查阻值
                    if target_ohm is not None and r["ohm"] is not None:
                        if abs(r["ohm"] - target_ohm) / target_ohm > 0.3:
                            findings.append(_make_finding(
                                f"spec_{category}", "warning", target_chip,
                                f"Pin {pin_num} ({net_name})",
                                f"上下拉电阻值偏差: 期望 {target_value_str}, 实际 {r['value']}",
                                f"手册 {source} 建议 {target_value_str}，"
                                f"实际 {r['designator']}={r['value']}（偏差 {(abs(r['ohm']-target_ohm)/target_ohm)*100:.0f}%）。",
                                f"确认 {r['designator']} 阻值是否可接受，或更换为 {target_value_str}。",
                                source=source, spec_id=spec_id,
                            ))
                    break
            if has_pull:
                break

        if not has_pull:
            pull_dir = "上拉到 VCC" if "VCC" in pull_target.upper() else "下拉到 GND"
            findings.append(_make_finding(
                f"spec_{category}", effective_severity, target_chip,
                f"Pin {pin_num} ({net_name})",
                f"缺少{pull_dir}电阻: 手册要求 {target_value_str}",
                f"{target_chip} Pin{pin_num} ({net_name}) 网络上未检测到{pull_dir}的电阻。"
                f"手册 {source} 要求 {target_value_str} {pull_dir}。",
                f"在 {target_chip} Pin{pin_num} 与 {pull_target} 之间添加 {target_value_str} 电阻。"
                f"{' (' + notes + ')' if notes else ''}",
                source=source, spec_id=spec_id,
            ))

    return findings


# ── Spec Check: crystal ─────────────────────────────────────────────────

def _spec_check_crystal(netlist: dict, req: dict) -> list[dict]:
    """根据 Design Spec 的 crystal 规则检查晶振负载电容。

    rule 必填字段: target_chip, pin_pattern, load_cap_pf
    rule 可选字段: frequency, stray_cap_pf (默认 3.0), suggested_caps_pf, confidence
    """
    findings = []
    rule = req.get("rule", {})
    target_chip = rule.get("target_chip", "")
    pin_pattern = rule.get("pin_pattern", r"XTAL")
    load_cap_pf = rule.get("load_cap_pf")
    stray_cap_pf = rule.get("stray_cap_pf", CSTRAY_DEFAULT)
    suggested_caps_pf = rule.get("suggested_caps_pf")
    frequency = rule.get("frequency", "")
    confidence = rule.get("confidence", "high")
    source = req.get("source", "")
    spec_id = req.get("id", "")
    req_severity = req.get("severity", "warning")
    notes = rule.get("notes", "")

    # confidence=low 时降级
    if confidence == "low":
        effective_severity = _downgrade_severity(req_severity, 1)
    else:
        effective_severity = req_severity

    chip = _find_chip_in_netlist(netlist, target_chip)
    if chip is None:
        findings.append(_make_finding(
            "spec_crystal", "warning", target_chip, "",
            f"Design Spec 目标芯片 {target_chip} 未在 netlist 中找到",
            f"spec 要求检查 {target_chip} 的晶振电路，但该位号不在当前原理图中。",
            "确认位号是否匹配。",
            source=source, spec_id=spec_id,
        ))
        return findings

    matched_pins = _match_pins(chip, pin_pattern)
    if len(matched_pins) < 2:
        findings.append(_make_finding(
            "spec_crystal", "suggestion", target_chip, "",
            f"{target_chip} 匹配 '{pin_pattern}' 的引脚不足 2 个 (找到 {len(matched_pins)})",
            f"spec 要求检查晶振电路，但匹配的 XTAL 引脚少于 2 个。",
            "确认 pin_pattern 是否正确，或晶振是否已放置。",
            source=source, spec_id=spec_id,
        ))
        return findings

    # 找到晶振元件 (通过 XTAL 引脚 net 追踪)
    xtal_nets = set()
    for pin in matched_pins:
        net_name = pin.get("net_name")
        if net_name:
            xtal_nets.add(net_name.upper())

    # 找在每个 XTAL net 上的晶振 (Y*/X*)
    crystal_comp = None
    for comp in netlist.get("components", []):
        des = (comp.get("designator") or "").upper()
        dev_name = (comp.get("device_name", "") or comp.get("name", "")).lower()
        is_xtal = (
            des.startswith("Y") or des.startswith("X") or
            any(kw in dev_name for kw in ("crystal", "晶振", "xtal"))
        )
        if not is_xtal:
            continue
        comp_nets = set(
            (p.get("net_name") or "").upper()
            for p in comp.get("pins", [])
            if p.get("net_name")
        )
        if comp_nets & xtal_nets:
            crystal_comp = comp
            break

    if crystal_comp is None:
        # 直接在 XTAL 引脚 net 上找电容
        pin_caps = []
        for pin in matched_pins:
            pin_net = pin.get("net_name") or ""
            if not pin_net:
                continue
            for c2 in netlist.get("components", []):
                c2_des = (c2.get("designator") or "").upper()
                if not c2_des.startswith("C"):
                    continue
                c2_on_pin_net = False
                c2_on_gnd = False
                for p2 in c2.get("pins", []):
                    p2_net = (p2.get("net_name") or "").upper()
                    if p2_net == pin_net.upper():
                        c2_on_pin_net = True
                    if _is_ground_net(p2_net):
                        c2_on_gnd = True
                if c2_on_pin_net and c2_on_gnd:
                    cap_f = _parse_capacitance_value(c2.get("value", ""))
                    pin_caps.append({
                        "pin": pin.get("pin_number"),
                        "cap_ref": c2.get("designator", ""),
                        "cap_value": c2.get("value", ""),
                        "cap_f": cap_f,
                    })
                    break

        if len(pin_caps) < 2:
            findings.append(_make_finding(
                "spec_crystal", effective_severity, target_chip, "",
                f"晶振负载电容不完整: 仅检测到 {len(pin_caps)} 个",
                f"手册 {source} 要求 CL≈{load_cap_pf}pF，"
                f"但 {target_chip} XTAL 引脚仅 {len(pin_caps)} 个电容到 GND。",
                f"每个 XTAL 引脚应对称接电容到 GND。"
                f"{'建议值: ' + str(suggested_caps_pf) + 'pF' if suggested_caps_pf else ''}",
                source=source, spec_id=spec_id,
            ))
            return findings

        c1_f = pin_caps[0].get("cap_f")
        c2_f = pin_caps[1].get("cap_f")
    else:
        # 通过晶振元件找电容
        pin_caps = []
        for cpin in crystal_comp.get("pins", []):
            cpin_net = (cpin.get("net_name") or "").upper()
            if not cpin_net:
                continue
            for c2 in netlist.get("components", []):
                c2_des = (c2.get("designator") or "").upper()
                if not c2_des.startswith("C"):
                    continue
                c2_on_pin_net = False
                c2_on_gnd = False
                for p2 in c2.get("pins", []):
                    p2_net = (p2.get("net_name") or "").upper()
                    if p2_net == cpin_net:
                        c2_on_pin_net = True
                    if _is_ground_net(p2_net):
                        c2_on_gnd = True
                if c2_on_pin_net and c2_on_gnd:
                    cap_f = _parse_capacitance_value(c2.get("value", ""))
                    pin_caps.append({
                        "pin": cpin.get("pin_number"),
                        "cap_ref": c2.get("designator", ""),
                        "cap_value": c2.get("value", ""),
                        "cap_f": cap_f,
                    })
                    break

        if len(pin_caps) < 2:
            findings.append(_make_finding(
                "spec_crystal", effective_severity,
                crystal_comp.get("designator", target_chip), "",
                f"晶振负载电容不完整: 仅检测到 {len(pin_caps)} 个",
                f"手册 {source} 要求 CL≈{load_cap_pf}pF，"
                f"但晶振 {crystal_comp.get('designator','?')} 仅 {len(pin_caps)} 个电容到 GND。",
                f"每个晶振引脚应对称接电容到 GND。"
                f"{'建议值: ' + str(suggested_caps_pf) + 'pF' if suggested_caps_pf else ''}",
                source=source, spec_id=spec_id,
            ))
            return findings

        c1_f = pin_caps[0].get("cap_f")
        c2_f = pin_caps[1].get("cap_f")

    if c1_f is None or c2_f is None:
        findings.append(_make_finding(
            "spec_crystal", "suggestion",
            crystal_comp.get("designator", target_chip) if crystal_comp else target_chip,
            f"{pin_caps[0].get('cap_ref','?')}({pin_caps[0].get('cap_value','?')}), "
            f"{pin_caps[1].get('cap_ref','?')}({pin_caps[1].get('cap_value','?')})",
            "晶振负载电容值无法解析，请手动确认",
            f"手册 {source} 要求 CL≈{load_cap_pf}pF，但电容值无法自动解析。"
            f"C1={pin_caps[0].get('cap_value','?')}, C2={pin_caps[1].get('cap_value','?')}。",
            f"确认电容值是否满足晶振 CL={load_cap_pf}pF 的要求。",
            source=source, spec_id=spec_id,
        ))
        return findings

    # 计算 CL_eff
    cl_eff = (c1_f * c2_f) / (c1_f + c2_f) + stray_cap_pf * 1e-12
    cl_eff_pf = cl_eff * 1e12
    deviation = (cl_eff_pf - load_cap_pf) / load_cap_pf

    freq_str = f" {frequency}" if frequency else ""

    if abs(deviation) < 0.10:
        # 偏差 <10%，不生成 finding (通过)
        pass
    elif abs(deviation) < 0.30:
        findings.append(_make_finding(
            "spec_crystal", _downgrade_severity(effective_severity),
            crystal_comp.get("designator", target_chip) if crystal_comp else target_chip,
            f"{pin_caps[0]['cap_ref']}({pin_caps[0]['cap_value']}), "
            f"{pin_caps[1]['cap_ref']}({pin_caps[1]['cap_value']})",
            f"晶振负载电容偏差: CL目标{load_cap_pf}pF, 实际CL_eff≈{cl_eff_pf:.1f}pF (偏差{deviation*100:+.1f}%)",
            f"手册 {source} 要求{freq_str} CL≈{load_cap_pf}pF (Cstray={stray_cap_pf}pF)。"
            f"实际 CL_eff = ({pin_caps[0].get('cap_value','?')}×{pin_caps[1].get('cap_value','?')})/"
            f"({pin_caps[0].get('cap_value','?')}+{pin_caps[1].get('cap_value','?')}) + {stray_cap_pf}pF"
            f" = {cl_eff_pf:.1f}pF，偏差 {deviation*100:+.1f}%。",
            f"建议更换为 {suggested_caps_pf}pF 电容。"
            if suggested_caps_pf else
            f"建议调整电容使 CL_eff 接近 {load_cap_pf}pF。"
            f" C_ideal = 2×({load_cap_pf}−{stray_cap_pf}) = {2*(load_cap_pf-stray_cap_pf):.1f}pF。"
            f"{' 置信度偏低，以晶振实际规格书为准。' if confidence == 'low' else ''}",
            source=source, spec_id=spec_id,
        ))
    else:
        findings.append(_make_finding(
            "spec_crystal", effective_severity,
            crystal_comp.get("designator", target_chip) if crystal_comp else target_chip,
            f"{pin_caps[0]['cap_ref']}({pin_caps[0]['cap_value']}), "
            f"{pin_caps[1]['cap_ref']}({pin_caps[1]['cap_value']})",
            f"晶振负载电容严重偏差: CL目标{load_cap_pf}pF, 实际CL_eff≈{cl_eff_pf:.1f}pF (偏差{deviation*100:+.1f}%)",
            f"手册 {source} 要求{freq_str} CL≈{load_cap_pf}pF。"
            f"实际 CL_eff = {cl_eff_pf:.1f}pF，偏差 {deviation*100:+.1f}%，超过 30%。"
            f"晶振可能无法正常起振或频率偏差过大。",
            f"必须更换负载电容。建议值: {suggested_caps_pf}pF。"
            if suggested_caps_pf else
            f"必须更换负载电容。C_ideal = 2×({load_cap_pf}−{stray_cap_pf}) = {2*(load_cap_pf-stray_cap_pf):.1f}pF。",
            source=source, spec_id=spec_id,
        ))

    return findings


# ── Spec Check: power_feedback ──────────────────────────────────────────

def _spec_check_feedback(netlist: dict, req: dict) -> list[dict]:
    """根据 Design Spec 的 power_feedback 规则检查电源反馈。

    rule 必填字段: target_chip, vref, target_vout
    rule 可选字段: r1_ohm, r2_ohm, notes
    """
    findings = []
    rule = req.get("rule", {})
    target_chip = rule.get("target_chip", "")
    vref = rule.get("vref", 1.25)
    target_vout = rule.get("target_vout")
    source = req.get("source", "")
    spec_id = req.get("id", "")
    notes = rule.get("notes", "")

    # 固定输出 LDO → 跳过
    if "fixed_output" in notes or "固定输出" in notes:
        findings.append(_make_finding(
            "spec_feedback", "suggestion", target_chip, "",
            f"{target_chip} 为固定输出 LDO，跳过反馈分压检查",
            f"手册 {source}: {target_chip} 内部集成反馈电阻，固定输出 {target_vout}V，无需外部反馈网络。",
            "无需操作。若实际输出电压异常请检查芯片是否损坏。",
            source=source, spec_id=spec_id,
        ))
        return findings

    chip = _find_chip_in_netlist(netlist, target_chip)
    if chip is None:
        findings.append(_make_finding(
            "spec_feedback", "warning", target_chip, "",
            f"Design Spec 目标芯片 {target_chip} 未在 netlist 中找到",
            f"spec 要求检查 {target_chip} 的反馈分压，但该位号不在当前原理图中。",
            "确认位号是否匹配。",
            source=source, spec_id=spec_id,
        ))
        return findings

    # 找 FB/ADJ 引脚
    fb_pins = []
    for pin in chip.get("pins", []):
        net_name = (pin.get("net_name") or "").upper()
        if any(fb_kw.upper() in net_name for fb_kw in _FB_PIN_NAMES):
            fb_pins.append(pin)

    if not fb_pins:
        findings.append(_make_finding(
            "spec_feedback", "warning", target_chip, "",
            f"{target_chip} 未找到 FB/ADJ 引脚",
            f"spec 要求检查反馈分压，但 {target_chip} 未检测到 FB/ADJ 引脚网络。"
            f"若为固定输出 LDO，应在 spec rule.notes 中标注 'fixed_output'。",
            "确认芯片类型: 固定输出还是可调输出？",
            source=source, spec_id=spec_id,
        ))
        return findings

    for fpin in fb_pins:
        fb_net = (fpin.get("net_name") or "").upper()
        pin_num = fpin.get("pin_number", "?")

        r_to_vout = None
        r_to_gnd = None

        for c2 in netlist.get("components", []):
            c2_des = c2.get("designator", "")
            if not c2_des.upper().startswith("R"):
                continue
            c2_pin_nets = {}
            for p2 in c2.get("pins", []):
                pn = (p2.get("net_name") or "").upper()
                if pn:
                    c2_pin_nets[pn] = pn

            if fb_net not in c2_pin_nets:
                continue

            r_val = _parse_resistance_value(c2.get("value", ""))
            if r_val is None:
                continue

            for pn in c2_pin_nets:
                if pn == fb_net:
                    continue
                if _is_power_net(pn) and not _is_ground_net(pn):
                    r_to_vout = (c2_des, r_val)
                elif _is_ground_net(pn):
                    r_to_gnd = (c2_des, r_val)

        if not r_to_vout or not r_to_gnd:
            findings.append(_make_finding(
                "spec_feedback", "error", target_chip,
                f"Pin {pin_num} (FB/ADJ)",
                "未检测到完整的反馈电阻网络",
                f"手册 {source}: {target_chip} Vref={vref}V, 目标 Vout={target_vout}V。"
                f"需要 R1(FB→VOUT) 和 R2(FB→GND)，但当前: "
                f"R_to_VOUT={r_to_vout[0] if r_to_vout else '无'}, "
                f"R_to_GND={r_to_gnd[0] if r_to_gnd else '无'}。",
                "确认反馈电阻网络是否完整。可使用 compute_passive.py 计算所需阻值。",
                source=source, spec_id=spec_id,
            ))
            continue

        r1_val = r_to_vout[1]
        r2_val = r_to_gnd[1]
        vout_calc = vref * (1 + r1_val / r2_val)

        deviation = abs(vout_calc - target_vout) / target_vout
        if deviation < 0.05:
            # 偏差 <5%，通过
            pass
        else:
            findings.append(_make_finding(
                "spec_feedback",
                "error" if deviation > 0.10 else "warning",
                target_chip,
                f"Pin {pin_num} (FB): R1={r_to_vout[0]}({r1_val:.0f}Ω→VOUT), "
                f"R2={r_to_gnd[0]}({r2_val:.0f}Ω→GND)",
                f"反馈分压不匹配: 计算 Vout={vout_calc:.2f}V, 目标 {target_vout}V (偏差 {deviation*100:.1f}%)",
                f"手册 {source}: Vref={vref}V, 目标 Vout={target_vout}V。"
                f"实际 Vout = {vref} × (1 + {r1_val:.0f}/{r2_val:.0f}) = {vout_calc:.2f}V。",
                f"调整 R1={r_to_vout[0]} 或 R2={r_to_gnd[0]} 使 Vout 接近 {target_vout}V。"
                f"理想比值 R1/R2 = ({target_vout}/{vref} - 1) = {target_vout/vref - 1:.2f}。",
                source=source, spec_id=spec_id,
            ))

    return findings


# ── Spec Check: pin_termination ─────────────────────────────────────────

def _spec_check_termination(netlist: dict, req: dict) -> list[dict]:
    """根据 Design Spec 的 pin_termination 规则检查引脚端接状态。

    rule 必填字段: target_chip, pin_pattern, termination
    rule 可选字段: resistor_value, resistor_ohm, notes
    termination 取值: pullup_to_vcc | pulldown_to_gnd | must_connect | must_float | connect_to_net
    """
    findings = []
    rule = req.get("rule", {})
    target_chip = rule.get("target_chip", "")
    pin_pattern = rule.get("pin_pattern", "")
    termination = rule.get("termination", "")
    resistor_ohm = rule.get("resistor_ohm")
    resistor_value_str = rule.get("resistor_value", "?")
    source = req.get("source", "")
    spec_id = req.get("id", "")
    req_severity = req.get("severity", "error")
    notes = rule.get("notes", "")

    # 若 notes 含条件性描述，降级
    conditional_keywords = ["如用作", "若用作", "如果", "如不需", "可浮空", "可不接"]
    is_conditional = any(kw in notes for kw in conditional_keywords)
    effective_severity = (
        _downgrade_severity(req_severity, 1) if is_conditional
        else req_severity
    )

    chip = _find_chip_in_netlist(netlist, target_chip)
    if chip is None:
        findings.append(_make_finding(
            "spec_termination", "warning", target_chip, "",
            f"Design Spec 目标芯片 {target_chip} 未在 netlist 中找到",
            f"spec 要求检查 {target_chip} 的引脚端接，但该位号不在当前原理图中。",
            "确认位号是否匹配。",
            source=source, spec_id=spec_id,
        ))
        return findings

    matched_pins = _match_pins(chip, pin_pattern)
    if not matched_pins:
        findings.append(_make_finding(
            "spec_termination", "suggestion", target_chip, "",
            f"{target_chip} 未找到匹配 '{pin_pattern}' 的引脚",
            f"spec 要求检查 {pin_pattern} 引脚的端接状态，但未找到匹配引脚。",
            "确认 pin_pattern 是否与实际原理图网络名一致。",
            source=source, spec_id=spec_id,
        ))
        return findings

    for pin in matched_pins:
        pin_num = pin.get("pin_number", "?")
        net_name = pin.get("net_name") or ""
        net_id = pin.get("net_id")

        if termination == "must_float":
            if net_name and net_id is not None:
                findings.append(_make_finding(
                    "spec_termination",
                    "warning",
                    target_chip,
                    f"Pin {pin_num} ({net_name})",
                    f"引脚应浮空但已连接: {net_name}",
                    f"手册 {source}: {target_chip} Pin{pin_num} 应保持浮空。"
                    f"当前连接到 {net_name}。{notes}",
                    f"断开 {target_chip} Pin{pin_num} 与 {net_name} 的连接。",
                    source=source, spec_id=spec_id,
                ))

        elif termination == "pullup_to_vcc":
            if not net_name:
                findings.append(_make_finding(
                    "spec_termination", effective_severity, target_chip,
                    f"Pin {pin_num} (无网络)",
                    f"引脚应上拉到 VCC，但未连接任何网络",
                    f"手册 {source}: {target_chip} Pin{pin_num} 必须上拉到 VCC。{notes}",
                    f"将 {target_chip} Pin{pin_num} 通过{resistor_value_str + ' ' if resistor_value_str != '?' else ''}上拉电阻连接到 VCC。",
                    source=source, spec_id=spec_id,
                ))
                continue

            # 检查是否已上拉
            resistors = _find_resistors_on_net(netlist, net_name)
            has_pullup = False
            for r in resistors:
                for rp in r["pins"]:
                    rp_net = (rp.get("net_name") or "").upper()
                    if rp_net == net_name.upper():
                        continue
                    if _is_power_net(rp_net):
                        has_pullup = True
                        if resistor_ohm and r["ohm"]:
                            if abs(r["ohm"] - resistor_ohm) / resistor_ohm > 0.3:
                                findings.append(_make_finding(
                                    "spec_termination", "warning", target_chip,
                                    f"Pin {pin_num} ({net_name})",
                                    f"上拉电阻值偏差: 期望 {resistor_value_str}, 实际 {r['value']}",
                                    f"手册 {source} 建议 {resistor_value_str}。"
                                    f"实际 {r['designator']}={r['value']}。",
                                    f"确认 {r['designator']} 阻值是否可接受。",
                                    source=source, spec_id=spec_id,
                                ))
                        break
                if has_pullup:
                    break

            if not has_pullup:
                # 检查 net 是否直接是 VCC
                if _is_power_net(net_name):
                    pass  # 直接接 VCC，OK
                else:
                    findings.append(_make_finding(
                        "spec_termination", effective_severity, target_chip,
                        f"Pin {pin_num} ({net_name})",
                        f"引脚缺少上拉到 VCC",
                        f"手册 {source}: {target_chip} Pin{pin_num} 必须上拉到 VCC。"
                        f"当前网络 {net_name} 未检测到上拉电阻或 VCC 连接。{notes}",
                        f"在 {target_chip} Pin{pin_num} 与 VCC 之间添加"
                        f"{resistor_value_str + ' ' if resistor_value_str != '?' else ''}上拉电阻。",
                        source=source, spec_id=spec_id,
                    ))

        elif termination == "pulldown_to_gnd":
            if not net_name:
                findings.append(_make_finding(
                    "spec_termination", effective_severity, target_chip,
                    f"Pin {pin_num} (无网络)",
                    f"引脚应下拉到 GND，但未连接任何网络",
                    f"手册 {source}: {target_chip} Pin{pin_num} 必须下拉到 GND。{notes}",
                    f"将 {target_chip} Pin{pin_num} 通过{resistor_value_str + ' ' if resistor_value_str != '?' else ''}下拉电阻连接到 GND。",
                    source=source, spec_id=spec_id,
                ))
                continue

            resistors = _find_resistors_on_net(netlist, net_name)
            has_pulldown = any(
                any(
                    _is_ground_net((rp.get("net_name") or "").upper())
                    for rp in r["pins"]
                    if (rp.get("net_name") or "").upper() != net_name.upper()
                )
                for r in resistors
            )

            if not has_pulldown and not _is_ground_net(net_name):
                findings.append(_make_finding(
                    "spec_termination", effective_severity, target_chip,
                    f"Pin {pin_num} ({net_name})",
                    f"引脚缺少下拉到 GND",
                    f"手册 {source}: {target_chip} Pin{pin_num} 必须下拉到 GND。"
                    f"当前网络 {net_name} 未检测到下拉电阻或 GND 连接。{notes}",
                    f"在 {target_chip} Pin{pin_num} 与 GND 之间添加"
                    f"{resistor_value_str + ' ' if resistor_value_str != '?' else ''}下拉电阻。",
                    source=source, spec_id=spec_id,
                ))

        elif termination == "must_connect":
            # 仅检查引脚是否有网络连接
            if not net_name:
                findings.append(_make_finding(
                    "spec_termination", effective_severity, target_chip,
                    f"Pin {pin_num}",
                    "引脚未连接（应连接）",
                    f"手册 {source}: {target_chip} Pin{pin_num} 必须连接。"
                    f"当前浮空。{notes}",
                    f"根据手册要求连接 {target_chip} Pin{pin_num}。",
                    source=source, spec_id=spec_id,
                ))

    return findings


# ── Spec Check 调度器 ───────────────────────────────────────────────────

# Design Spec category → spec check function 映射
SPEC_CATEGORY_CHECKERS: dict[str, callable | None] = {
    "decoupling": _spec_check_decoupling,
    "pullup": _spec_check_pull,
    "pulldown": _spec_check_pull,
    "crystal": _spec_check_crystal,
    "power_feedback": _spec_check_feedback,
    "pin_termination": _spec_check_termination,
    "pin_exclusion": None,  # 不生成 finding，仅用于过滤
}

SPEC_CATEGORY_NAMES = {
    "decoupling": "去耦电容",
    "pullup": "上拉电阻",
    "pulldown": "下拉电阻",
    "crystal": "晶振负载电容",
    "power_feedback": "电源反馈分压",
    "pin_termination": "引脚端接",
    "pin_exclusion": "引脚排除",
}


def spec_driven_checks(netlist: dict, spec: dict) -> list[dict]:
    """根据 Design Spec 的每条 requirement 执行精确对照检查。

    Args:
        netlist: extract_netlist 输出
        spec: Design Spec JSON (dict)

    Returns:
        list[dict] — 每条 finding 额外包含 source, spec_id, check_type="spec"
    """
    findings = []
    requirements = spec.get("requirements", [])
    if not requirements:
        return findings

    for req in requirements:
        category = req.get("category", "")
        checker = SPEC_CATEGORY_CHECKERS.get(category)
        if checker is None:
            continue  # pin_exclusion 不生成 finding

        try:
            result = checker(netlist, req)
            if result:
                findings.extend(result)
        except Exception as e:
            findings.append(_make_finding(
                f"spec_{category}", "error", "", "",
                f"Spec 检查执行失败 (id={req.get('id','?')}): {e}",
                str(e),
                "检查输入数据格式和 spec 规则定义。",
                source=req.get("source", ""),
                spec_id=req.get("id", ""),
            ))

    return findings


def apply_pin_exclusions(
    findings: list[dict],
    spec: dict,
) -> tuple[list[dict], int]:
    """应用 pin_exclusion 规则过滤通用检查的误报。

    Args:
        findings: 通用检查的 finding 列表
        spec: Design Spec JSON (dict)

    Returns:
        (filtered_findings, exclusion_count)
    """
    import re

    # 收集所有 pin_exclusion 规则
    exclusions: list[dict] = []
    for req in spec.get("requirements", []):
        if req.get("category") != "pin_exclusion":
            continue
        rule = req.get("rule", {})
        exclusions.append({
            "target_chip": (rule.get("target_chip") or "").upper(),
            "pin_pattern": rule.get("pin_pattern", ""),
            "exclude_from": [c.strip() for c in rule.get("exclude_from_checks", [])],
            "reason": rule.get("reason", ""),
        })

    if not exclusions:
        return findings, 0

    filtered = []
    exclusion_count = 0

    for f in findings:
        excluded = False
        f_chip = (f.get("component_ref") or "").upper()
        f_check_id = f.get("check_id", "")
        f_location = f.get("location", "")

        for exc in exclusions:
            # 检查 check_id 在 exclude_from 中
            if f_check_id not in exc["exclude_from"]:
                continue
            # 检查 location 匹配 pin_pattern（这是主要判断依据，因为
            # 通用检查可能将 finding 归属到同 net 上的其他元件上，而非 target_chip）
            pat_matched = False
            try:
                if re.search(exc["pin_pattern"], f_location, re.IGNORECASE):
                    pat_matched = True
            except re.error:
                if exc["pin_pattern"].upper() in f_location.upper():
                    pat_matched = True
            if not pat_matched:
                continue
            # target_chip 约束：非空时必须匹配 component_ref（可选约束）
            if exc["target_chip"] and exc["target_chip"] != f_chip:
                # 即使 component_ref 不匹配，若 location 中已确认包含 pin_pattern，
                # 说明该 finding 是关于被排除引脚的，仍应排除
                # (如 PSEN# 被 H6 报告而非 U2)
                pass  # 不跳过，继续排除
            excluded = True
            break

        if excluded:
            exclusion_count += 1
        else:
            filtered.append(f)

    return filtered, exclusion_count


# ═══════════════════════════════════════════════════════════════════════════
# 检查注册表
# ═══════════════════════════════════════════════════════════════════════════

ALL_CHECKS: dict[str, callable] = {
    "decoupling_cap": check_decoupling_caps,
    "enable_pin": check_enable_pins,
    "i2c_pullup": check_i2c_pullups,
    "feedback_divider": check_feedback_divider,
    "crystal_load_cap": check_crystal_load_caps,
    "floating_pin": check_floating_pins,
    "power_ground_short": check_power_ground_short,
}

CHECK_NAMES = {
    "decoupling_cap": "IC VCC 去耦电容",
    "enable_pin": "EN/复位引脚上下拉",
    "i2c_pullup": "I2C 上拉电阻",
    "feedback_divider": "电源反馈分压",
    "crystal_load_cap": "晶振负载电容",
    "floating_pin": "悬空引脚",
    "power_ground_short": "电源-地短路",
}


# ═══════════════════════════════════════════════════════════════════════════════
# 统一入口
# ═══════════════════════════════════════════════════════════════════════════════

def run_erc(
    netlist: dict,
    datasheet_context: dict | None = None,
    design_spec: dict | None = None,
    checks: list[str] | None = None,
    generate_actions: bool = False,
) -> dict:
    """运行 ERC 检查。

    Args:
        netlist: extract_netlist.extract_netlist() 的输出。
        datasheet_context: Phase 1 数据手册上下文 (可选)。
        design_spec: Design Spec JSON (Phase 1→2 桥梁，可选)。
        checks: 要运行的检查项 ID 列表。None = 全部。
        generate_actions: 是否生成结构化修复建议 (含 LCSC 推荐)。

    Returns:
        {
            "project_name": str,
            "timestamp": str,
            "checks_run": [...],
            "findings": {"errors": [...], "warnings": [...], "suggestions": [...]},
            "spec_applied": bool,
            "spec_stats": {...} | None,
            "stats": {...},
            "markdown_report": str,
            "actions": [...] | None,
            "action_stats": {...} | None,
        }
    """
    if "error" in netlist:
        return {"error": netlist["error"]}

    if checks is None:
        checks = list(ALL_CHECKS.keys())
    else:
        # 验证 check IDs
        invalid = set(checks) - set(ALL_CHECKS.keys())
        if invalid:
            return {"error": f"无效的检查项: {invalid}。可用: {list(ALL_CHECKS.keys())}"}

    # ── 阶段 1: 通用检查 ──
    all_findings = []
    for check_id in checks:
        try:
            result = ALL_CHECKS[check_id](netlist, datasheet_context)
            all_findings.extend(result)
        except Exception as e:
            all_findings.append({
                "check_id": check_id,
                "severity": "error",
                "component_ref": "",
                "location": "",
                "message": f"检查项 {check_id} 执行失败: {e}",
                "detail": str(e),
                "suggestion": "检查输入数据格式。",
                "check_type": "generic",
            })

    # ── 阶段 2: Design Spec 驱动检查（如提供）──
    spec_findings: list[dict] = []
    exclusion_count = 0
    spec_stats = None

    if design_spec:
        # 2a. 应用 pin_exclusion 过滤通用检查
        all_findings, exclusion_count = apply_pin_exclusions(
            all_findings, design_spec
        )

        # 2b. 运行 spec 驱动检查
        spec_findings = spec_driven_checks(netlist, design_spec)

        # 2c. 统计
        requirements = design_spec.get("requirements", [])
        spec_errors = sum(1 for f in spec_findings if f["severity"] == "error")
        spec_warnings = sum(1 for f in spec_findings if f["severity"] == "warning")
        spec_suggestions = sum(1 for f in spec_findings if f["severity"] == "suggestion")
        spec_checked = sum(
            1 for r in requirements
            if r.get("category") != "pin_exclusion"
        )
        spec_skipped = sum(
            1 for r in requirements
            if r.get("category") == "pin_exclusion"
        )
        spec_passed = spec_checked - len(spec_findings)

        spec_stats = {
            "requirements_total": len(requirements),
            "requirements_checked": spec_checked,
            "requirements_skipped": spec_skipped,
            "requirements_passed": max(0, spec_passed),
            "exclusions_applied": exclusion_count,
            "spec_errors": spec_errors,
            "spec_warnings": spec_warnings,
            "spec_suggestions": spec_suggestions,
        }

    # ── 合并 & 分组 ──
    all_findings.extend(spec_findings)

    # ── 阶段 3: 生成可执行修复建议（如启用）──
    actions = None
    action_stats = None
    if generate_actions:
        if not _ACTIONS_AVAILABLE:
            actions = [{
                "action_id": "error",
                "action_type": "REVIEW_MANUALLY",
                "target": {},
                "parameters": {"notes": "phase2_action_gen 模块不可用，无法生成修复建议。"},
                "reason": "Action generation module not found",
                "confidence": "low",
                "source": {"type": "heuristic", "reference": "internal error"},
                "triggered_by": {"check_id": "", "severity": "", "message": "", "finding_index": -1},
            }]
        else:
            try:
                actions = _generate_actions(all_findings, netlist, design_spec)
            except Exception as e:
                actions = [{
                    "action_id": "error",
                    "action_type": "REVIEW_MANUALLY",
                    "target": {},
                    "parameters": {"notes": f"动作生成失败: {e}"},
                    "reason": f"Action generation failed: {e}",
                    "confidence": "low",
                    "source": {"type": "heuristic", "reference": "internal error"},
                    "triggered_by": {"check_id": "", "severity": "", "message": "", "finding_index": -1},
                }]
        if actions:
            # 统计
            by_type: dict[str, int] = {}
            by_confidence: dict[str, int] = {}
            with_lcsc = 0
            for a in actions:
                at = a.get("action_type", "?")
                by_type[at] = by_type.get(at, 0) + 1
                ac = a.get("confidence", "low")
                by_confidence[ac] = by_confidence.get(ac, 0) + 1
                if a.get("parameters", {}).get("suggested_parts"):
                    with_lcsc += 1
            action_stats = {
                "total": len(actions),
                "by_type": by_type,
                "by_confidence": by_confidence,
                "with_lcsc_suggestions": with_lcsc,
            }

    errors = [f for f in all_findings if f["severity"] == "error"]
    warnings = [f for f in all_findings if f["severity"] == "warning"]
    suggestions = [f for f in all_findings if f["severity"] == "suggestion"]

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    project_name = netlist.get("project_name", "")
    stats = netlist.get("stats", {})

    # ── 生成 Markdown 报告 ──
    report = _format_markdown_report(
        project_name, timestamp, checks,
        errors, warnings, suggestions, stats,
        spec_applied=design_spec is not None,
        spec_stats=spec_stats,
        generic_errors=[f for f in errors if f.get("check_type") != "spec"],
        generic_warnings=[f for f in warnings if f.get("check_type") != "spec"],
        generic_suggestions=[f for f in suggestions if f.get("check_type") != "spec"],
        spec_errors=[f for f in errors if f.get("check_type") == "spec"],
        spec_warnings=[f for f in warnings if f.get("check_type") == "spec"],
        spec_suggestions=[f for f in suggestions if f.get("check_type") == "spec"],
        actions=actions,
    )

    result = {
        "project_name": project_name,
        "timestamp": timestamp,
        "checks_run": checks,
        "findings": {
            "errors": errors,
            "warnings": warnings,
            "suggestions": suggestions,
        },
        "totals": {
            "errors": len(errors),
            "warnings": len(warnings),
            "suggestions": len(suggestions),
        },
        "stats": stats,
        "spec_applied": design_spec is not None,
        "markdown_report": report,
    }

    if spec_stats:
        result["spec_stats"] = spec_stats

    if actions is not None:
        result["actions"] = actions
    if action_stats is not None:
        result["action_stats"] = action_stats

    return result


def _format_markdown_report(
    project_name: str,
    timestamp: str,
    checks: list[str],
    errors: list[dict],
    warnings: list[dict],
    suggestions: list[dict],
    stats: dict,
    spec_applied: bool = False,
    spec_stats: dict | None = None,
    generic_errors: list[dict] | None = None,
    generic_warnings: list[dict] | None = None,
    generic_suggestions: list[dict] | None = None,
    spec_errors: list[dict] | None = None,
    spec_warnings: list[dict] | None = None,
    spec_suggestions: list[dict] | None = None,
    actions: list[dict] | None = None,
) -> str:
    """生成 Markdown 格式的 ERC 审查报告。"""
    total = len(errors) + len(warnings) + len(suggestions)

    spec_extra = ""
    if spec_applied and spec_stats:
        spec_extra = (
            f" | Design Spec: ✅ 已应用"
            f" | 手册要求: {spec_stats.get('requirements_passed',0)}/{spec_stats.get('requirements_checked',0)} 满足"
            f" | 排除误报: {spec_stats.get('exclusions_applied',0)}"
        )

    lines = [
        f"## ERC 审查报告：{project_name}",
        "",
        f"> 审查时间：{timestamp}",
        f"> 检查项：{len(checks)} | ❌ 错误：{len(errors)} | ⚠️ 警告：{len(warnings)} | 💡 建议：{len(suggestions)}{spec_extra}",
        "",
        "---",
        "",
    ]

    # ── 通用检查结果 ──
    generic_err = generic_errors if generic_errors is not None else errors
    generic_warn = generic_warnings if generic_warnings is not None else warnings
    generic_sug = generic_suggestions if generic_suggestions is not None else suggestions

    # 若 spec 被应用，只显示非 spec 的通用 finding
    if spec_applied:
        generic_err = [f for f in errors if f.get("check_type") != "spec"]
        generic_warn = [f for f in warnings if f.get("check_type") != "spec"]
        generic_sug = [f for f in suggestions if f.get("check_type") != "spec"]

    lines.append(f"### 🔍 通用规则检查")
    lines.append("")

    # ❌ 错误
    lines.append(f"#### ❌ 错误 (必须修复) — {len(generic_err)} 项")
    lines.append("")
    if generic_err:
        lines.append("| # | 位置 | 问题 | 详情 | 建议 |")
        lines.append("|---|------|------|------|------|")
        for i, f in enumerate(generic_err, 1):
            ref = f.get("component_ref", "")
            loc = f.get("location", "")
            msg = f.get("message", "")
            detail = (f.get("detail", "") or "")[:80]
            sug = (f.get("suggestion", "") or "")[:80]
            lines.append(f"| {i} | {ref} {loc} | {msg} | {detail} | {sug} |")
    else:
        lines.append("✅ 无错误。")
    lines.append("")

    # ⚠️ 警告
    lines.append(f"#### ⚠️ 警告 — {len(generic_warn)} 项")
    lines.append("")
    if generic_warn:
        lines.append("| # | 位置 | 问题 | 详情 | 建议 |")
        lines.append("|---|------|------|------|------|")
        for i, f in enumerate(generic_warn, 1):
            ref = f.get("component_ref", "")
            loc = f.get("location", "")
            msg = f.get("message", "")
            detail = (f.get("detail", "") or "")[:80]
            sug = (f.get("suggestion", "") or "")[:80]
            lines.append(f"| {i} | {ref} {loc} | {msg} | {detail} | {sug} |")
    else:
        lines.append("✅ 无警告。")
    lines.append("")

    # 💡 建议
    lines.append(f"#### 💡 建议 — {len(generic_sug)} 项")
    lines.append("")
    if generic_sug:
        lines.append("| # | 位置 | 问题 | 详情 | 建议 |")
        lines.append("|---|------|------|------|------|")
        for i, f in enumerate(generic_sug, 1):
            ref = f.get("component_ref", "")
            loc = f.get("location", "")
            msg = f.get("message", "")
            detail = (f.get("detail", "") or "")[:80]
            sug = (f.get("suggestion", "") or "")[:80]
            lines.append(f"| {i} | {ref} {loc} | {msg} | {detail} | {sug} |")
    else:
        lines.append("✅ 无建议。")
    lines.append("")

    # ── 手册对照检查结果（仅当 spec 被应用时）──
    if spec_applied:
        spec_err = spec_errors or []
        spec_warn = spec_warnings or []
        spec_sug = spec_suggestions or []
        spec_total = len(spec_err) + len(spec_warn) + len(spec_sug)

        lines.append("---")
        lines.append("")
        lines.append(f"### 📋 手册对照检查（Design Spec 驱动）— {spec_total} 项")
        lines.append("")

        if spec_total == 0:
            lines.append("✅ 所有手册要求均已满足。")
            lines.append("")
        else:
            lines.append("| # | 类别 | 位置 | 判定 | 详情 | 依据 |")
            lines.append("|---|------|------|------|------|------|")
            all_spec = spec_err + spec_warn + spec_sug
            for i, f in enumerate(all_spec, 1):
                cat = SPEC_CATEGORY_NAMES.get(
                    f.get("check_id", "").replace("spec_", ""), ""
                )
                ref = f.get("component_ref", "")
                loc = f.get("location", "")
                sev = f.get("severity", "")
                sev_icon = {"error": "❌", "warning": "⚠️", "suggestion": "💡"}.get(sev, "")
                msg = f.get("message", "")[:60]
                source = f.get("source", "")[:50]
                lines.append(
                    f"| {i} | {cat} | {ref} {loc} | {sev_icon} {msg} | {source} |"
                )

            if spec_stats:
                lines.append("")
                passed = spec_stats.get("requirements_passed", 0)
                checked = spec_stats.get("requirements_checked", 0)
                lines.append(
                    f"> 📊 手册要求满足率: **{passed}/{checked}**"
                    f" ({passed/checked*100:.0f}%)"
                    if checked > 0 else
                    f"> 📊 手册要求满足率: **{passed}/{checked}**"
                )

        lines.append("")

    # ── 可执行修复建议 ──
    if actions:
        lines.append("---")
        lines.append("")
        lines.append(f"### 🔧 可执行修复建议 — {len(actions)} 项")
        lines.append("")
        lines.append("| # | 操作 | 目标 | 参数 | 建议元件 | 置信度 | 依据 |")
        lines.append("|---|------|------|------|---------|--------|------|")
        for i, a in enumerate(actions, 1):
            at = a.get("action_type", "?")
            icon = _action_type_icon(at)
            target_str = _format_action_target(a.get("target", {}))
            params_str = _format_action_params(a.get("parameters", {}))
            parts_str = _format_suggested_parts_short(a.get("parameters", {}))
            conf = _confidence_label(a.get("confidence", ""))
            ref = (a.get("source", {}).get("reference", ""))[:40]
            lines.append(
                f"| {i} | {icon} {at} | {target_str} | {params_str} | "
                f"{parts_str} | {conf} | {ref} |"
            )
        lines.append("")

    # ── 统计摘要 ──
    lines.append("---")
    lines.append("")
    lines.append("### 📊 统计摘要")
    lines.append("")
    lines.append("| 指标 | 值 |")
    lines.append("|------|-----|")
    lines.append(f"| 总元件数 | {stats.get('total_components', '?')} |")
    lines.append(f"| 总网络数 | {stats.get('total_nets', '?')} |")
    lines.append(f"| 电源网络 | {stats.get('power_nets', '?')} |")
    lines.append(f"| 信号网络 | {stats.get('signal_nets', '?')} |")
    lines.append(f"| 悬空网络 | {stats.get('floating_nets', '?')} |")
    lines.append("")
    lines.append("| 检查项 | 结果 |")
    lines.append("|--------|------|")
    for cid in checks:
        name = CHECK_NAMES.get(cid, cid)
        err_count = sum(1 for f in errors if f["check_id"] == cid)
        warn_count = sum(1 for f in warnings if f["check_id"] == cid)
        sug_count = sum(1 for f in suggestions if f["check_id"] == cid)
        parts = []
        if err_count:
            parts.append(f"❌ {err_count}")
        if warn_count:
            parts.append(f"⚠️ {warn_count}")
        if sug_count:
            parts.append(f"💡 {sug_count}")
        result_str = " ".join(parts) if parts else "✅ 通过"
        lines.append(f"| {name} | {result_str} |")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# Action 格式化辅助函数（Markdown 报告用）
# ═══════════════════════════════════════════════════════════════════════════════

_ACTION_ICONS: dict[str, str] = {
    "ADD_DECOUPLING_CAP": "🧩",
    "ADD_PULLUP_RESISTOR": "⬆️",
    "ADD_PULLDOWN_RESISTOR": "⬇️",
    "ADD_CRYSTAL_LOAD_CAPS": "💎",
    "CHANGE_CAPACITANCE": "🔄",
    "CHANGE_RESISTANCE": "🔄",
    "CONNECT_PIN_TO_NET": "🔗",
    "DISCONNECT_PIN": "✂️",
    "REVIEW_CRYSTAL_SELECTION": "🔍",
    "REVIEW_MANUALLY": "👁️",
}


def _action_type_icon(action_type: str) -> str:
    """返回 action type 对应的 emoji 图标。"""
    return _ACTION_ICONS.get(action_type, "❓")


def _confidence_label(confidence: str) -> str:
    """返回置信度标签。"""
    return {"high": "🟢 高", "medium": "🟡 中", "low": "🔴 低"}.get(confidence, confidence)


def _format_action_target(target: dict) -> str:
    """格式化 action target 为简短字符串。"""
    parts = []
    if target.get("component_ref"):
        parts.append(target["component_ref"])
    if target.get("pin"):
        parts.append(f"Pin{target['pin']}")
    if target.get("net"):
        parts.append(f"({target['net']})")
    if target.get("crystal_ref"):
        parts.append(target["crystal_ref"])
    return " ".join(parts) if parts else "-"


def _format_action_params(params: dict) -> str:
    """格式化 action parameters 为简短字符串。"""
    parts = []
    if params.get("cap_value"):
        parts.append(params["cap_value"])
    if params.get("resistor_value"):
        parts.append(params["resistor_value"])
    if params.get("resistor_target_value"):
        parts.append(f"→{params['resistor_target_value']}")
    if params.get("suggested_cap_value"):
        parts.append(params["suggested_cap_value"])
    if params.get("cap_value_pf"):
        parts.append(f"{params['cap_value_pf']:.0f}pF")
    if params.get("from_net") and params.get("to_net"):
        parts.append(f"{params['from_net']}→{params['to_net']}")
    return " ".join(parts) if parts else "-"


def _format_suggested_parts_short(params: dict) -> str:
    """格式化 LCSC 推荐为简短的 Markdown 内字符串。"""
    parts_list = params.get("suggested_parts", [])
    if not parts_list:
        return "-"
    # 显示排名最高的 1 个
    best = parts_list[0]
    pkg = best.get("package", "")
    lcsc = best.get("lcsc", "")
    return f"{lcsc} ({pkg})"


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="嘉立创 EDA ERC 检查引擎 — 7 项通用检查 + Design Spec 驱动精确对照",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python phase2_erc_check.py netlist.json
  python phase2_erc_check.py netlist.json --format json
  python phase2_erc_check.py netlist.json --check decoupling,i2c
  python phase2_erc_check.py netlist.json --spec design_spec.json
        """,
    )
    parser.add_argument(
        "input_file",
        nargs="?",
        help="extract_netlist.py 输出的 JSON 文件（或使用 --stdin）",
    )
    parser.add_argument(
        "--stdin",
        action="store_true",
        help="从标准输入读取 JSON",
    )
    parser.add_argument(
        "--format",
        choices=["json", "text"],
        default="text",
        help="输出格式 (default: text, 即 Markdown 报告)",
    )
    parser.add_argument(
        "--check",
        help="指定检查项 (逗号分隔)。可选: decoupling_cap,enable_pin,i2c_pullup,feedback_divider,crystal_load_cap,floating_pin,power_ground_short",
    )
    parser.add_argument(
        "--datasheet",
        help="数据手册上下文 JSON 文件 (可选, Phase 1 输出)",
    )
    parser.add_argument(
        "--spec",
        type=str,
        default=None,
        help="Design Spec JSON 文件路径（Phase 1 产出），启用手册精确对照检查",
    )
    parser.add_argument(
        "--actions",
        action="store_true",
        help="生成结构化可执行修复建议（含 LCSC 元件推荐），输出到 JSON actions 字段和 Markdown 报告",
    )
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    # 读取输入
    if args.stdin:
        raw = sys.stdin.read()
    elif args.input_file:
        raw = Path(args.input_file).read_text(encoding="utf-8")
    else:
        print(json.dumps({"error": "需要 input_file 或 --stdin"}, ensure_ascii=False),
              file=sys.stderr)
        sys.exit(1)

    try:
        netlist = json.loads(raw)
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"JSON 解析失败: {e}"}, ensure_ascii=False),
              file=sys.stderr)
        sys.exit(1)

    # 读取数据手册上下文 (可选)
    datasheet_ctx = None
    if args.datasheet:
        try:
            datasheet_ctx = json.loads(Path(args.datasheet).read_text(encoding="utf-8"))
        except Exception as e:
            print(json.dumps({"error": f"读取数据手册上下文失败: {e}"}, ensure_ascii=False),
                  file=sys.stderr)
            sys.exit(1)

    # 读取 Design Spec (可选)
    design_spec = None
    if args.spec:
        try:
            design_spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
        except Exception as e:
            print(json.dumps({"error": f"读取 Design Spec 失败: {e}"}, ensure_ascii=False),
                  file=sys.stderr)
            sys.exit(1)

    # 解析检查项
    checks = None
    if args.check:
        checks = [c.strip() for c in args.check.split(",") if c.strip()]

    result = run_erc(
        netlist,
        datasheet_context=datasheet_ctx,
        design_spec=design_spec,
        checks=checks,
        generate_actions=args.actions,
    )

    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    else:
        print(result.get("markdown_report", json.dumps(result, ensure_ascii=False)))

    if "error" in result:
        sys.exit(1)


if __name__ == "__main__":
    main()
