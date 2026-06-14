#!/usr/bin/env python3
"""嘉立创 EDA ERC 检查引擎 — 7 项电气规则检查

读取 extract_netlist.py 的网表 JSON 输出，运行 ERC 检查并生成 Markdown 报告。

检查项：
  1. IC VCC 引脚去耦电容检测
  2. EN/复位引脚上下拉检测
  3. I2C 总线上拉电阻检测
  4. 电源芯片反馈电阻分压检测
  5. 晶振负载电容检测
  6. 悬空引脚警告
  7. 电源-地短路检测

用法：
  python phase2_erc_check.py netlist.json
  python phase2_erc_check.py netlist.json --format json
  python phase2_erc_check.py netlist.json --check decoupling,i2c

管线：
  python parse_jlc_project.py project.eprj2 --format json | \\
    python extract_netlist.py --stdin --format json | \\
    python phase2_erc_check.py --stdin

Python import:
  from phase2_erc_check import run_erc
  report = run_erc(netlist)
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

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
# 检查注册表
# ═══════════════════════════════════════════════════════════════════════════════

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
    checks: list[str] | None = None,
) -> dict:
    """运行 ERC 检查。

    Args:
        netlist: extract_netlist.extract_netlist() 的输出。
        datasheet_context: Phase 1 数据手册上下文 (可选)。
        checks: 要运行的检查项 ID 列表。None = 全部。

    Returns:
        {
            "project_name": str,
            "timestamp": str,
            "findings": {"errors": [...], "warnings": [...], "suggestions": [...]},
            "stats": {...},
            "markdown_report": str,
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
            })

    # 按严重度分组
    errors = [f for f in all_findings if f["severity"] == "error"]
    warnings = [f for f in all_findings if f["severity"] == "warning"]
    suggestions = [f for f in all_findings if f["severity"] == "suggestion"]

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    project_name = netlist.get("project_name", "")
    stats = netlist.get("stats", {})

    # 生成 Markdown 报告
    report = _format_markdown_report(
        project_name, timestamp, checks,
        errors, warnings, suggestions, stats,
    )

    return {
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
        "markdown_report": report,
    }


def _format_markdown_report(
    project_name: str,
    timestamp: str,
    checks: list[str],
    errors: list[dict],
    warnings: list[dict],
    suggestions: list[dict],
    stats: dict,
) -> str:
    """生成 Markdown 格式的 ERC 审查报告。"""
    total = len(errors) + len(warnings) + len(suggestions)

    lines = [
        f"## ERC 审查报告：{project_name}",
        "",
        f"> 审查时间：{timestamp}",
        f"> 检查项：{len(checks)} | ❌ 错误：{len(errors)} | ⚠️ 警告：{len(warnings)} | 💡 建议：{len(suggestions)}",
        "",
        "---",
        "",
    ]

    # ❌ 错误
    lines.append(f"### ❌ 错误 (必须修复) — {len(errors)} 项")
    lines.append("")
    if errors:
        lines.append("| # | 位置 | 问题 | 详情 | 建议 |")
        lines.append("|---|------|------|------|------|")
        for i, f in enumerate(errors, 1):
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
    lines.append(f"### ⚠️ 警告 — {len(warnings)} 项")
    lines.append("")
    if warnings:
        lines.append("| # | 位置 | 问题 | 详情 | 建议 |")
        lines.append("|---|------|------|------|------|")
        for i, f in enumerate(warnings, 1):
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
    lines.append(f"### 💡 建议 — {len(suggestions)} 项")
    lines.append("")
    if suggestions:
        lines.append("| # | 位置 | 问题 | 详情 | 建议 |")
        lines.append("|---|------|------|------|------|")
        for i, f in enumerate(suggestions, 1):
            ref = f.get("component_ref", "")
            loc = f.get("location", "")
            msg = f.get("message", "")
            detail = (f.get("detail", "") or "")[:80]
            sug = (f.get("suggestion", "") or "")[:80]
            lines.append(f"| {i} | {ref} {loc} | {msg} | {detail} | {sug} |")
    else:
        lines.append("✅ 无建议。")
    lines.append("")

    # 统计摘要
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
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="嘉立创 EDA ERC 检查引擎 — 7 项电气规则检查",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python phase2_erc_check.py netlist.json
  python phase2_erc_check.py netlist.json --format json
  python phase2_erc_check.py netlist.json --check decoupling,i2c
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

    # 解析检查项
    checks = None
    if args.check:
        checks = [c.strip() for c in args.check.split(",") if c.strip()]

    result = run_erc(netlist, datasheet_ctx, checks)

    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    else:
        print(result.get("markdown_report", json.dumps(result, ensure_ascii=False)))

    if "error" in result:
        sys.exit(1)


if __name__ == "__main__":
    main()
