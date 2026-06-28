#!/usr/bin/env python3
"""嘉立创 EDA 网表提取器 — 从 WIRE 拓扑重建网络连接

读取 parse_jlc_project.py 的 JSON 输出，通过 Union-Find 连通图算法
将 WIRE 线段重建为网络 (net)，并将组件引脚映射到对应的网络。

算法：
  1. 收集所有 WIRE 线段端点，按坐标容差合并 → 连通分量 (Union-Find)
  2. 从 ATTR("NET") / ATTR("Global Net Name") 命名网络
  3. WIRE 端点坐标 → 最近 COMPONENT → 引脚归属

用法：
  python extract_netlist.py parsed_project.json --format json
  python parse_jlc_project.py project.eprj2 --format json | python extract_netlist.py --stdin --format json

Python import:
  from extract_netlist import extract_netlist
  netlist = extract_netlist(project_data)
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Optional

# ═══════════════════════════════════════════════════════════════════════════════
# 常量
# ═══════════════════════════════════════════════════════════════════════════════

# 坐标容差 (mil)，两个端点在此距离内视为连接
PROXIMITY_TOLERANCE = 2

# WIRE 端点距离 COMPONENT 中心的最大距离 (mil)，超过此距离不视为连接
# 小元件(R/C/L): ~30mil; 中型(SOIC): ~100mil; 大型(QFP): ~300mil
PIN_SNAP_DISTANCE = 200

# 按元件类型估算的引脚区域半径
def _get_pin_radius(component: dict) -> float:
    """根据组件类型返回引脚搜索半径。"""
    name = (component.get("device_name", "") or component.get("name", "")).lower()
    des = (component.get("designator", "") or "").upper()

    # 大型 IC
    if any(kw in name for kw in ("lqfp", "qfp", "qfn", "bga", "tqfp", "plcc",
                                   "stc89", "stm32", "atmega", "单片机")):
        return 300
    # 中型 IC / 连接器
    if any(kw in name for kw in ("soic", "sop", "ssop", "tssop", "dip", "sot-",
                                   "header", "connector", "排针", "排母")):
        return 150
    if des.startswith("U") or des.startswith("J"):
        return 150
    # 小型无源元件
    if any(kw in name for kw in ("res", "cap", "ind", "led", "diode", "电阻",
                                   "电容", "电感", "晶振", "crystal", "xtal")):
        return 40
    if des.startswith("R") or des.startswith("C") or des.startswith("L") or \
       des.startswith("D") or des.startswith("Y") or des.startswith("X"):
        return 40
    # 开关/按键
    if des.startswith("SW") or des.startswith("K"):
        return 60
    return 60  # 默认

class UnionFind:
    """并查集，用于合并连通分量。"""

    def __init__(self):
        self._parent: dict[str, str] = {}
        self._size: dict[str, int] = {}

    def add(self, x: str) -> None:
        if x not in self._parent:
            self._parent[x] = x
            self._size[x] = 1

    def find(self, x: str) -> str:
        """查找根节点（带路径压缩）。"""
        if x not in self._parent:
            self.add(x)
            return x
        if self._parent[x] != x:
            self._parent[x] = self.find(self._parent[x])
        return self._parent[x]

    def union(self, x: str, y: str) -> None:
        """合并两个集合（按大小合并）。"""
        rx, ry = self.find(x), self.find(y)
        if rx == ry:
            return
        if self._size.get(rx, 0) < self._size.get(ry, 0):
            rx, ry = ry, rx
        self._parent[ry] = rx
        self._size[rx] = self._size.get(rx, 0) + self._size.get(ry, 0)


# ═══════════════════════════════════════════════════════════════════════════════
# 连通图构建
# ═══════════════════════════════════════════════════════════════════════════════

def _snap_coord(x: float, y: float, tolerance: float = PROXIMITY_TOLERANCE) -> str:
    """将坐标四舍五入到容差网格，生成字符串 key。"""
    grid_x = round(x / tolerance) * tolerance
    grid_y = round(y / tolerance) * tolerance
    return f"{grid_x},{grid_y}"


def _build_connectivity_graph(
    wires: list[dict],
    attr_map: dict[str, dict[str, str]],
) -> dict:
    """构建 WIRE 连通图。

    Args:
        wires: parse_jlc_project 输出的 wires 列表
        attr_map: parentId → {ATTR key: ATTR value}

    Returns:
        {
            "net_of_wire": {wire_id: net_index},
            "net_names": {net_index: name},
            "wire_ids_by_net": {net_index: [wire_id, ...]},
        }
    """
    uf = UnionFind()

    # --- Pass 1: 同一 WIRE 内的线段共享端点 → 合并 ---
    for w in wires:
        wid = w["id"]
        uf.add(wid)
        # 同一 WIRE 的所有线段都在同一个 net
        # (这是由 WIRE 数据结构保证的）

    # --- Pass 2: 不同 WIRE 的端点坐标重合 → 合并 ---
    # coord_snap → set of wire_ids
    coord_to_wires: dict[str, set[str]] = {}

    for w in wires:
        wid = w["id"]
        for seg in w.get("segments", []):
            if len(seg) < 4:
                continue
            # 线段两个端点
            for i in (0, 1):
                x, y = seg[i * 2], seg[i * 2 + 1]
                key = _snap_coord(x, y)
                if key not in coord_to_wires:
                    coord_to_wires[key] = set()
                coord_to_wires[key].add(wid)

    # 合并共享坐标的所有 WIRE
    for key, wire_set in coord_to_wires.items():
        wire_list = list(wire_set)
        for i in range(1, len(wire_list)):
            uf.union(wire_list[0], wire_list[i])

    # --- Pass 3: 共享同一 NET 名称的 WIRE → 强制合并 ---
    net_name_groups: dict[str, list[str]] = {}
    for w in wires:
        wid = w["id"]
        name = w.get("net_name") or w.get("net_global_name")
        if name:
            if name not in net_name_groups:
                net_name_groups[name] = []
            net_name_groups[name].append(wid)

    for name, wire_list in net_name_groups.items():
        for i in range(1, len(wire_list)):
            uf.union(wire_list[0], wire_list[i])

    # --- 收集结果 ---
    net_index: dict[str, int] = {}  # root → sequential net index
    wire_ids_by_net: dict[int, list[str]] = {}
    net_of_wire: dict[str, int] = {}

    for w in wires:
        wid = w["id"]
        root = uf.find(wid)
        if root not in net_index:
            idx = len(net_index)
            net_index[root] = idx
            wire_ids_by_net[idx] = []
        net_of_wire[wid] = net_index[root]
        wire_ids_by_net[net_index[root]].append(wid)

    return {
        "net_of_wire": net_of_wire,
        "net_index": net_index,
        "wire_ids_by_net": wire_ids_by_net,
        "num_nets": len(net_index),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 网络命名
# ═══════════════════════════════════════════════════════════════════════════════

def _name_nets(
    graph: dict,
    wires: list[dict],
) -> dict[int, str]:
    """为每个 net 分配名称。

    优先级:
      1. Global Net Name (如 "VCC+5V", "GND")
      2. NET name
      3. 匿名 → "Net_1", "Net_2", ...

    Returns:
        {net_index: net_name}
    """
    # 收集每个 net 的所有名称候选
    net_global_names: dict[int, set[str]] = {}
    net_names: dict[int, set[str]] = {}

    net_of_wire = graph["net_of_wire"]

    for w in wires:
        wid = w["id"]
        net_idx = net_of_wire.get(wid)
        if net_idx is None:
            continue

        gname = w.get("net_global_name")
        if gname:
            if net_idx not in net_global_names:
                net_global_names[net_idx] = set()
            net_global_names[net_idx].add(gname)

        nname = w.get("net_name")
        if nname:
            if net_idx not in net_names:
                net_names[net_idx] = set()
            net_names[net_idx].add(nname)

    # 分配最终名称
    result: dict[int, str] = {}
    for net_idx in range(graph["num_nets"]):
        # 优先 Global Net Name
        global_names = net_global_names.get(net_idx, set())
        if global_names:
            # 如果只有一个，直接使用；多个则选最短的（最具体）
            result[net_idx] = min(global_names, key=len)
            continue

        # 其次 NET name
        local_names = net_names.get(net_idx, set())
        if local_names:
            # 过滤空字符串
            non_empty = {n for n in local_names if n}
            if non_empty:
                result[net_idx] = min(non_empty, key=len)
                continue

        # 匿名
        result[net_idx] = f"Net_{net_idx + 1}"

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# 组件引脚 → 网络映射
# ═══════════════════════════════════════════════════════════════════════════════

def _infer_pin_count(component: dict) -> int:
    """从组件名称和位号推断引脚数量（兜底值）。"""
    name = (component.get("device_name", "") or component.get("name", "")).lower()
    des = (component.get("designator", "") or "").upper()

    # 大型 IC (LQFP, QFP 等)
    if any(kw in name for kw in ("lqfp", "qfp", "qfn", "bga", "tqfp", "plcc",
                                   "stc89", "stm32", "atmega", "单片机")):
        return 44
    # 中型 IC (SOIC, SOP 等)
    if any(kw in name for kw in ("soic", "sop", "ssop", "tssop", "dip", "sot-223",
                                   "to-220", "to-252")):
        return 8
    if des.startswith("U"):
        return 8
    # 连接器
    if any(kw in name for kw in ("header", "connector", "排针", "排母", "type-c",
                                   "usb", "dc")):
        return 6
    if des.startswith("J") or des.startswith("P"):
        return 4
    # 电源符号
    if any(kw in name for kw in ("ground", "gnd", "vcc", "power", "电源")):
        return 1
    # 晶振
    if any(kw in name for kw in ("crystal", "晶振", "xtal", "xihcelnanf")):
        return 2
    if des.startswith("Y") or des.startswith("X"):
        return 2
    # 无源元件
    if any(kw in name for kw in ("res", "电阻", "cap", "电容", "ind", "电感",
                                   "led", "diode", "二极管", "bead", "磁珠")):
        return 2
    if des.startswith("R") or des.startswith("C") or des.startswith("L") or \
       des.startswith("D"):
        return 2
    # 开关
    if des.startswith("SW") or des.startswith("K"):
        return 4

    return 2


def _map_pins_to_nets(
    components: list[dict],
    wires: list[dict],
    graph: dict,
) -> tuple[list[dict], dict[int, int]]:
    """将组件引脚映射到网络。

    策略：对于每个 WIRE 端点，找最近的 COMPONENT。
    若距离 < PIN_SNAP_DISTANCE，该端点属于该组件的某个引脚。

    Returns:
        (enriched_components, net_pin_counts)
        enriched_components: 添加了 .pins 字段的组件列表
        net_pin_counts: {net_index: pin_count}
    """
    net_of_wire = graph["net_of_wire"]

    # 为每个组件收集连接的 net
    # comp_id → {net_index: count_of_connections}
    comp_nets: dict[str, dict[int, int]] = {}

    for w in wires:
        wid = w["id"]
        net_idx = net_of_wire.get(wid)
        if net_idx is None:
            continue

        # 收集该 WIRE 的所有端点坐标
        endpoints: list[tuple[float, float]] = []
        for seg in w.get("segments", []):
            if len(seg) < 4:
                continue
            endpoints.append((seg[0], seg[1]))
            endpoints.append((seg[2], seg[3]))

        # 对每个端点，找最近的组件
        for ex, ey in endpoints:
            nearest_comp = None
            nearest_dist = float("inf")

            for c in components:
                cx, cy = c.get("x", 0), c.get("y", 0)
                dist = math.hypot(ex - cx, ey - cy)
                radius = _get_pin_radius(c)
                if dist < nearest_dist and dist < radius:
                    nearest_dist = dist
                    nearest_comp = c

            if nearest_comp:
                cid = nearest_comp["id"]
                if cid not in comp_nets:
                    comp_nets[cid] = {}
                comp_nets[cid][net_idx] = comp_nets[cid].get(net_idx, 0) + 1

    # 构建带 pin 信息的组件列表
    enriched = []
    net_pin_counts: dict[int, int] = {}

    for c in components:
        cid = c["id"]
        nets = comp_nets.get(cid, {})

        # 去重：多个端点可能连到同一个 net
        unique_nets = list(nets.keys())
        pin_count = max(len(unique_nets), _infer_pin_count(c))
        # 确保 pin_count 至少等于实际连接数
        pin_count = max(pin_count, len(unique_nets))

        pins = []
        for i in range(pin_count):
            if i < len(unique_nets):
                net_idx = unique_nets[i]
                pins.append({
                    "pin_number": str(i + 1),
                    "net_id": f"net_{net_idx}",
                    "net_name": "",  # 后续由 _name_nets 结果填充
                    "net_index": net_idx,
                })
            else:
                pins.append({
                    "pin_number": str(i + 1),
                    "net_id": None,
                    "net_name": None,
                    "net_index": None,
                })

        # 统计 net pin count
        for net_idx in unique_nets:
            net_pin_counts[net_idx] = net_pin_counts.get(net_idx, 0) + 1

        enriched.append({
            **c,
            "pins": pins,
        })

    return enriched, net_pin_counts


# ═══════════════════════════════════════════════════════════════════════════════
# 网络类型推断
# ═══════════════════════════════════════════════════════════════════════════════

def _infer_net_type(name: str) -> str:
    """根据网络名称推断类型：power / signal。

    - 含 GND, VSS, AGND, DGND → power (ground)
    - 含 VCC, VDD, VIN, +, 3.3V, 5V → power (supply)
    - 其他 → signal
    """
    if not name:
        return "signal"

    upper = name.upper()

    # Ground nets
    ground_patterns = ["GND", "VSS", "AGND", "DGND", "PGND", "SGND", "GROUND",
                       "地", "电源地", "信号地"]
    for p in ground_patterns:
        if p in upper:
            return "power"

    # Power supply nets
    power_patterns = ["VCC", "VDD", "VIN", "VEE", "VREF", "+5V", "+3.3V",
                      "+12V", "+", "V+", "VCC+", "POWER", "VBUS", "VBAT",
                      "VCC_INT", "电源"]
    for p in power_patterns:
        if p in upper:
            return "power"

    return "signal"


# ═══════════════════════════════════════════════════════════════════════════════
# 统一入口
# ═══════════════════════════════════════════════════════════════════════════════

def extract_netlist(project_data: dict) -> dict:
    """从工程数据提取网表。

    Args:
        project_data: parse_jlc_project.parse_project() 的输出。

    Returns:
        {
            "project_name": str,
            "components": [...],   # 含 .pins[] 字段
            "nets": [...],         # 网络列表
            "stats": {...}
        }
        错误时返回 {"error": "..."}
    """
    if "error" in project_data:
        return project_data

    components = project_data.get("components", [])
    wires = project_data.get("wires", [])
    attr_map = project_data.get("attribute_map", {})

    if not wires:
        return {
            **project_data,
            "nets": [],
            "stats": {
                "total_components": len(components),
                "total_nets": 0,
                "power_nets": 0,
                "signal_nets": 0,
                "floating_nets": 0,
            },
        }

    # Step 1: 构建连通图
    graph = _build_connectivity_graph(wires, attr_map)

    # Step 2: 命名网络
    net_names = _name_nets(graph, wires)

    # Step 3: 映射引脚
    enriched_components, net_pin_counts = _map_pins_to_nets(
        components, wires, graph
    )

    # Step 4: 构建 nets 输出
    nets = []
    for net_idx in range(graph["num_nets"]):
        name = net_names.get(net_idx, f"Net_{net_idx + 1}")
        net_type = _infer_net_type(name)
        pin_count = net_pin_counts.get(net_idx, 0)
        is_floating = pin_count <= 1

        nets.append({
            "id": f"net_{net_idx}",
            "name": name,
            "type": net_type,
            "wire_ids": graph["wire_ids_by_net"].get(net_idx, []),
            "pin_count": pin_count,
            "is_floating": is_floating,
        })

    # Step 5: 用 net_name 回填组件 pins
    net_id_to_name = {f"net_{i}": name for i, name in net_names.items()}
    for comp in enriched_components:
        for pin in comp.get("pins", []):
            if pin.get("net_id") and pin["net_id"] in net_id_to_name:
                pin["net_name"] = net_id_to_name[pin["net_id"]]

    # 统计
    power_nets = sum(1 for n in nets if n["type"] == "power")
    signal_nets = sum(1 for n in nets if n["type"] == "signal")
    floating_nets = sum(1 for n in nets if n["is_floating"])

    return {
        "project_name": project_data.get("project_name", ""),
        "format": project_data.get("format", ""),
        "project_path": project_data.get("project_path", ""),
        "components": enriched_components,
        "nets": nets,
        "stats": {
            "total_components": len(enriched_components),
            "total_nets": len(nets),
            "power_nets": power_nets,
            "signal_nets": signal_nets,
            "floating_nets": floating_nets,
        },
    }


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="嘉立创 EDA 网表提取器 — 从 WIRE 拓扑重建网络连接",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python extract_netlist.py parsed.json --format json
  python parse_jlc_project.py project.eprj2 --format json | \\
    python extract_netlist.py --stdin --format json
        """,
    )
    parser.add_argument(
        "input_file",
        nargs="?",
        help="parse_jlc_project.py 输出的 JSON 文件（或使用 --stdin）",
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
        help="输出格式 (default: text)",
    )
    return parser


def _format_text(result: dict) -> str:
    """格式化为人类可读的文本摘要。"""
    if "error" in result:
        return f"❌ 错误: {result['error']}"

    lines = [
        f"📦 工程名称: {result['project_name']}",
        f"📊 统计:",
        f"   元件总数: {result['stats']['total_components']}",
        f"   网络总数: {result['stats']['total_nets']}",
        f"   电源网络: {result['stats']['power_nets']}",
        f"   信号网络: {result['stats']['signal_nets']}",
        f"   悬空网络: {result['stats']['floating_nets']}",
        "",
        "🔗 网络列表:",
    ]

    # 网络列表
    power_nets = [n for n in result["nets"] if n["type"] == "power"]
    signal_nets = [n for n in result["nets"] if n["type"] == "signal"]

    if power_nets:
        lines.append("  ⚡ 电源网络:")
        for n in power_nets:
            flag = " ⚠️悬空" if n["is_floating"] else ""
            lines.append(
                f"    {n['name']:<15} pin_count={n['pin_count']:<3} wires={len(n['wire_ids'])}{flag}"
            )

    if signal_nets:
        lines.append("  🔵 信号网络:")
        for n in signal_nets[:30]:
            flag = " ⚠️悬空" if n["is_floating"] else ""
            lines.append(
                f"    {n['name']:<15} pin_count={n['pin_count']:<3} wires={len(n['wire_ids'])}{flag}"
            )
        if len(signal_nets) > 30:
            lines.append(f"    ... 共 {len(signal_nets)} 个信号网络")

    # 元件连接摘要
    lines.append("")
    lines.append("🔧 元件连接:")
    lines.append(f"   {'位号':<10} {'名称':<25} {'引脚数':<8} {'连接'}")
    lines.append(f"   {'-'*10} {'-'*25} {'-'*8} {'-'*20}")
    for c in result["components"]:
        des = c.get("designator", "") or "(无)"
        name = (c.get("device_name", "") or c.get("name", ""))[:25]
        pins = c.get("pins", [])
        connected = sum(1 for p in pins if p.get("net_name"))
        pin_names = ", ".join(
            f"Pin{p['pin_number']}→{p['net_name']}"
            for p in pins
            if p.get("net_name")
        )[:40]
        lines.append(f"   {des:<10} {name:<25} {len(pins):<8} {pin_names}")

    return "\n".join(lines)


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
        project_data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"JSON 解析失败: {e}"}, ensure_ascii=False),
              file=sys.stderr)
        sys.exit(1)

    result = extract_netlist(project_data)

    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    else:
        print(_format_text(result))

    if "error" in result:
        sys.exit(1)


if __name__ == "__main__":
    main()
