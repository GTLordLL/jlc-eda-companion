#!/usr/bin/env python3
"""嘉立创 EDA 工程文件统一解析器

支持三种格式：
  - 标准版 .eprj2 (SQLite 3 数据库)
  - 标准版备份 .zip (含 .esch NDJSON 文件)
  - 专业版 .epro2 (Zip 压缩包, 含 .epru 管道分隔 NDJSON)

用途：
  python parse_jlc_project.py project.eprj2               # text 摘要
  python parse_jlc_project.py project.eprj2 --format json  # JSON 输出

Python import:
  from parse_jlc_project import parse_project
  result = parse_project("project.eprj2")
"""

from __future__ import annotations

import argparse
import base64
import gzip
import json
import os
import re
import sqlite3
import sys
import zipfile
from pathlib import Path
from typing import Optional

# ═══════════════════════════════════════════════════════════════════════════════
# 常量
# ═══════════════════════════════════════════════════════════════════════════════

SQLITE_MAGIC = b"SQLite format 3\x00"

# .esch NDJSON 条目类型
ENTITY_COMPONENT = "COMPONENT"
ENTITY_ATTR = "ATTR"
ENTITY_WIRE = "WIRE"

# ATTR 中我们关心的 key
INTERESTING_ATTR_KEYS = {
    "Designator", "Device", "Value", "NET", "Global Net Name",
    "Symbol", "Footprint", "Manufacturer Part", "Supplier Part",
    "Pin Name", "Pin Number", "Name",
}

# 器件属性中映射到 component 输出字段的 key
ATTR_TO_COMPONENT_FIELD = {
    "Designator": "designator",
    "Value": "value",
    "Footprint": "footprint",
    "Manufacturer Part": "manufacturer_part",
}


# ═══════════════════════════════════════════════════════════════════════════════
# 格式自动检测
# ═══════════════════════════════════════════════════════════════════════════════

def detect_format(project_path: str) -> str:
    """检测工程文件格式。

    Returns:
        "eprj2" | "epro2" | "eprj_backup"
    """
    path = Path(project_path)
    suffix = path.suffix.lower()

    # .epro2 专业版
    if suffix == ".epro2":
        return "epro2"

    # .eprj2 标准版 (SQLite)
    if suffix == ".eprj2":
        return "eprj2"

    # 尝试读 magic bytes 判断 SQLite
    try:
        with open(project_path, "rb") as f:
            magic = f.read(16)
        if magic == SQLITE_MAGIC:
            return "eprj2"
    except OSError:
        pass

    # .zip 或 .eprj_backup → 检查内部是否有 SHEET/ 目录
    if suffix in (".zip",) or path.name.endswith(".eprj_backup"):
        try:
            with zipfile.ZipFile(project_path, "r") as zf:
                names = zf.namelist()
                has_sheet = any("SHEET/" in n and n.endswith(".esch") for n in names)
                has_epru = any(n.endswith(".epru") for n in names)
                if has_epru:
                    return "epro2"
                if has_sheet:
                    return "eprj_backup"
        except (zipfile.BadZipFile, OSError):
            pass

    # 兜底：按扩展名猜测
    if suffix == ".eprj2":
        return "eprj2"
    if suffix == ".epro2":
        return "epro2"
    return "eprj_backup"


# ═══════════════════════════════════════════════════════════════════════════════
# NDJSON 解析 (共用)
# ═══════════════════════════════════════════════════════════════════════════════

def parse_ndjson_lines(lines: list[str]) -> dict:
    """解析 NDJSON 行，按实体类型分桶。

    Args:
        lines: 每行一个 JSON 数组的字符串列表。

    Returns:
        {"components": [...], "attrs": [...], "wires": [...], "others": [...]}
    """
    buckets: dict[str, list] = {
        "components": [],
        "attrs": [],
        "wires": [],
        "others": [],
    }

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            arr = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(arr, list) or len(arr) < 2:
            continue

        etype = arr[0]
        if etype == ENTITY_COMPONENT:
            buckets["components"].append(arr)
        elif etype == ENTITY_ATTR:
            buckets["attrs"].append(arr)
        elif etype == ENTITY_WIRE:
            buckets["wires"].append(arr)
        else:
            buckets["others"].append(arr)

    return buckets


# ═══════════════════════════════════════════════════════════════════════════════
# ATTR 交叉引用
# ═══════════════════════════════════════════════════════════════════════════════

def _build_attribute_map(
    attrs: list[list],
) -> dict[str, dict[str, str]]:
    """将 ATTR 列表按 parentId 组织为 {parentId: {key: value}} 的映射。

    ATTR 数组格式: ["ATTR", id, parentId, key, value, ...]
    """
    attr_map: dict[str, dict[str, str]] = {}
    for a in attrs:
        if len(a) < 5:
            continue
        parent_id = a[2]
        key = a[3]
        value = a[4]
        if value is None:
            value = ""
        else:
            value = str(value)
        if parent_id not in attr_map:
            attr_map[parent_id] = {}
        attr_map[parent_id][key] = value
    return attr_map


def _resolve_components(
    components_raw: list[list],
    attr_map: dict[str, dict[str, str]],
    lcsc_map: dict[str, dict[str, str]],
) -> list[dict]:
    """将原始 COMPONENT 条目与 ATTR、LCSC map 关联，构建统一 component 结构。

    COMPONENT 格式: ["COMPONENT", id, name, x, y, rotation, mirror, extra, flag]
    """
    results = []
    for c in components_raw:
        cid = c[1]
        name = c[2] if len(c) > 2 else ""
        x = c[3] if len(c) > 3 else 0
        y = c[4] if len(c) > 4 else 0
        rotation = c[5] if len(c) > 5 else 0
        mirror = c[6] if len(c) > 6 else 0

        comp_attrs = attr_map.get(cid, {})
        device_uuid = comp_attrs.get("Device", "")

        # 从 LCSC map 获取物料信息
        lcsc_info = lcsc_map.get(device_uuid, {})

        comp = {
            "id": cid,
            "name": name,
            "x": x,
            "y": y,
            "rotation": rotation,
            "mirror": bool(mirror),
            "designator": comp_attrs.get("Designator", ""),
            "device_uuid": device_uuid,
            "device_name": lcsc_info.get("title", ""),
            "lcsc_part": lcsc_info.get("lcsc_part", ""),
            "manufacturer_part": lcsc_info.get("manufacturer_part", ""),
            "footprint": lcsc_info.get("footprint", ""),
            "value": comp_attrs.get("Value", ""),
            "extra_attrs": {
                k: v
                for k, v in comp_attrs.items()
                if k not in {"Designator", "Device", "Value"}
            },
        }
        results.append(comp)

    return results


def _resolve_wires(
    wires_raw: list[list],
    attr_map: dict[str, dict[str, str]],
) -> list[dict]:
    """将原始 WIRE 条目与 ATTR 关联，构建统一 wire 结构。

    WIRE 格式: ["WIRE", id, segments, style, flag]
    segments: [[x1,y1,x2,y2], ...]
    """
    results = []
    for w in wires_raw:
        wid = w[1]
        segments = w[2] if len(w) > 2 and isinstance(w[2], list) else []
        style = w[3] if len(w) > 3 else ""

        wire_attrs = attr_map.get(wid, {})
        net_name = wire_attrs.get("NET", "")
        net_global_name = wire_attrs.get("Global Net Name", "")

        results.append({
            "id": wid,
            "segments": segments,
            "style": style,
            "net_name": net_name if net_name else None,
            "net_global_name": net_global_name if net_global_name else None,
        })

    return results


# ═══════════════════════════════════════════════════════════════════════════════
# 格式一：标准版 .eprj2 (SQLite)
# ═══════════════════════════════════════════════════════════════════════════════

def _parse_eprj2(project_path: str) -> dict:
    """解析标准版 .eprj2 (SQLite) 工程文件。"""
    db = sqlite3.connect(project_path)
    cur = db.cursor()

    # --- 获取工程名称 ---
    project_name = Path(project_path).stem
    try:
        row = cur.execute(
            "SELECT name FROM projects LIMIT 1"
        ).fetchone()
        if row and row[0]:
            project_name = row[0]
    except sqlite3.OperationalError:
        pass

    # --- 构建 LCSC map: device_uuid → {lcsc_part, manufacturer_part, title, ...} ---
    lcsc_map: dict[str, dict[str, str]] = {}

    # 先获取 devices 表
    devices: dict[str, dict] = {}
    try:
        for row in cur.execute(
            "SELECT uuid, title, display_title FROM devices"
        ).fetchall():
            devices[row[0]] = {
                "title": row[1] or "",
                "display_title": row[2] or "",
            }
    except sqlite3.OperationalError:
        pass

    # 获取 attributes 表 (含 Supplier Part 等)
    try:
        for row in cur.execute(
            "SELECT key, value, device_uuid FROM attributes"
        ).fetchall():
            key, value, dev_uuid = row[0], row[1], row[2]
            if dev_uuid not in lcsc_map:
                lcsc_map[dev_uuid] = {}
            lcsc_map[dev_uuid][key] = str(value) if value else ""

            # 补充 device title
            if dev_uuid in devices:
                lcsc_map[dev_uuid].setdefault("title", devices[dev_uuid]["title"])
                lcsc_map[dev_uuid].setdefault(
                    "display_title", devices[dev_uuid]["display_title"]
                )
    except sqlite3.OperationalError:
        pass

    # 标准化 key 名
    for dev_uuid in lcsc_map:
        info = lcsc_map[dev_uuid]
        if "Supplier Part" in info:
            info["lcsc_part"] = info.pop("Supplier Part")
        if "Manufacturer Part" in info:
            info["manufacturer_part"] = info.pop("Manufacturer Part")
        if "Supplier Footprint" in info:
            info["footprint"] = info.pop("Supplier Footprint")

    # --- 从 documents 表获取原理图 NDJSON ---
    all_components = []
    all_wires = []
    all_attr_map: dict[str, dict[str, str]] = {}

    try:
        doc_rows = cur.execute(
            "SELECT uuid, docType, dataStr FROM documents WHERE docType = 1"
        ).fetchall()

        for doc_uuid, doc_type, data_str in doc_rows:
            if not data_str:
                continue
            ndjson_text = _decode_eprj2_datastr(data_str)
            if ndjson_text is None:
                continue
            lines = ndjson_text.strip().split("\n")
            buckets = parse_ndjson_lines(lines)
            attr_map = _build_attribute_map(buckets["attrs"])
            components = _resolve_components(
                buckets["components"], attr_map, lcsc_map
            )
            wires = _resolve_wires(buckets["wires"], attr_map)

            all_components.extend(components)
            all_wires.extend(wires)
            all_attr_map.update(attr_map)
    except sqlite3.OperationalError:
        pass

    db.close()

    # --- 构建输出 ---
    return {
        "project_name": project_name,
        "format": "eprj2",
        "project_path": str(Path(project_path).resolve()),
        "components": all_components,
        "wires": all_wires,
        "attribute_map": all_attr_map,
        "stats": {
            "total_components": len(all_components),
            "total_wires": len(all_wires),
            "total_attrs": sum(len(v) for v in all_attr_map.values()),
            "lcsc_parts": len([c for c in all_components if c["lcsc_part"]]),
        },
    }


def _decode_eprj2_datastr(data_str: str) -> Optional[str]:
    """解码 .eprj2 documents.dataStr (base64前缀 + base64 + gzip)。

    dataStr 格式: "base64" + base64_encoded_data
    其中 base64_encoded_data → decode → gzip decompress → UTF-8 NDJSON text
    """
    if not data_str.startswith("base64"):
        return None

    b64_data = data_str[6:]  # 去掉 "base64" 前缀
    try:
        raw = base64.b64decode(b64_data)
    except Exception:
        return None

    # gzip 解压
    if raw[:2] == b"\x1f\x8b":
        try:
            raw = gzip.decompress(raw)
        except Exception:
            return None

    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# 格式二：标准版备份 .zip
# ═══════════════════════════════════════════════════════════════════════════════

def _parse_eprj_backup(project_path: str) -> dict:
    """解析标准版备份 .zip 工程文件。"""
    all_components = []
    all_wires = []
    all_attr_map: dict[str, dict[str, str]] = {}
    project_name = Path(project_path).stem

    with zipfile.ZipFile(project_path, "r") as zf:
        namelist = zf.namelist()

        # --- 从 project.json 获取 LCSC map ---
        lcsc_map: dict[str, dict[str, str]] = {}
        if "project.json" in namelist:
            try:
                proj_data = json.loads(zf.read("project.json").decode("utf-8"))
                devices_data = proj_data.get("devices", {})
                for dev_uuid, dev_info in devices_data.items():
                    attrs = dev_info.get("attributes", {})
                    lcsc_map[dev_uuid] = {
                        "title": dev_info.get("title", ""),
                        "lcsc_part": attrs.get("Supplier Part", ""),
                        "manufacturer_part": attrs.get("Manufacturer Part", ""),
                        "footprint": attrs.get("Supplier Footprint", ""),
                    }
                # 也尝试从 schematics → sheets 获取工程名称
                schematics = proj_data.get("schematics", {})
                for sch_uuid, sch_info in schematics.items():
                    name = sch_info.get("name", "")
                    if name:
                        project_name = name
                        break
            except (json.JSONDecodeError, KeyError):
                pass

        # --- 解析所有 .esch 原理图文件 ---
        esch_files = sorted(
            [n for n in namelist if n.endswith(".esch") and "SHEET/" in n]
        )
        for esch_path in esch_files:
            try:
                ndjson_text = zf.read(esch_path).decode("utf-8")
                lines = ndjson_text.strip().split("\n")
                buckets = parse_ndjson_lines(lines)
                attr_map = _build_attribute_map(buckets["attrs"])
                components = _resolve_components(
                    buckets["components"], attr_map, lcsc_map
                )
                wires = _resolve_wires(buckets["wires"], attr_map)

                all_components.extend(components)
                all_wires.extend(wires)
                all_attr_map.update(attr_map)
            except Exception:
                pass

    return {
        "project_name": project_name,
        "format": "eprj_backup",
        "project_path": str(Path(project_path).resolve()),
        "components": all_components,
        "wires": all_wires,
        "attribute_map": all_attr_map,
        "stats": {
            "total_components": len(all_components),
            "total_wires": len(all_wires),
            "total_attrs": sum(len(v) for v in all_attr_map.values()),
            "lcsc_parts": len([c for c in all_components if c["lcsc_part"]]),
        },
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 格式三：专业版 .epro2 (Zip + .epru 管道分隔)
# ═══════════════════════════════════════════════════════════════════════════════

def _parse_epro2(project_path: str) -> dict:
    """解析专业版 .epro2 工程文件。

    .epro2 内部结构：
      - project2.json: 元数据
      - .epru: 管道分隔的 NDJSON，含 META/PART/COMPONENT/WIRE/LINE/ATTR/NET
        - META: 器件定义 (含 LCSC 信息)，key=title
        - PART: 原理图符号实例，key=id (如 "1N4148TR.1")，title 对应 META.title
        - COMPONENT with partId: 原理图元件；无 partId: PCB 元件
        - WIRE + LINE: 连线 (LINE.lineGroup → WIRE.id)
        - ATTR: parentId → COMPONENT/WIRE id
    """
    project_name = Path(project_path).stem

    # META: title → {lcsc_part, manufacturer_part, footprint, ...}
    meta_by_title: dict[str, dict[str, str]] = {}
    # PART: id → title (如 "1N4148TR.1" → "1N4148TR")
    part_to_meta_title: dict[str, str] = {}
    # device_uuid → LCSC info (兼容 _resolve_components 的 lcsc_map 接口)
    lcsc_by_device_uuid: dict[str, dict[str, str]] = {}

    # 原始数据收集
    components_raw = []  # 仅原理图 COMPONENT (NDJSON 格式)
    attrs_raw = []       # ATTR (NDJSON 格式)
    wires_raw = []       # WIRE (NDJSON 格式)
    line_groups: dict[str, list[list[int]]] = {}  # WIRE id → segments

    with zipfile.ZipFile(project_path, "r") as zf:
        namelist = zf.namelist()

        # --- project2.json ---
        if "project2.json" in namelist:
            try:
                proj2 = json.loads(zf.read("project2.json").decode("utf-8"))
                project_name = proj2.get("title", project_name)
            except (json.JSONDecodeError, KeyError):
                pass

        # --- .epru 主数据文件 ---
        epru_files = [n for n in namelist if n.endswith(".epru")]
        for epru_name in epru_files:
            try:
                raw_data = zf.read(epru_name).decode("utf-8")
                _parse_epru_lines_v2(
                    raw_data, meta_by_title, part_to_meta_title,
                    components_raw, attrs_raw, wires_raw, line_groups,
                )
            except Exception:
                pass

    # --- 构建 lcsc_by_device_uuid: 将 META info 以 Device ATTR 值为 key ---
    # Device ATTR 的值是 device_uuid，对应 META.source
    # 但在 .epro2 中，COMPONENT 的 partId → PART → META.title → META info
    # 为了兼容 _resolve_components，需要根据 device_uuid 建立映射
    # device_uuid 存在于 ATTR("Device") 中
    # 我们先收集 PART id → META info，再通过 COMPONENT 的 partId 建立映射

    # 实际上更简单的方法：在 _resolve_components 之后，遍历每个 component，
    # 用 partId 查找 PART，再查找 META

    # --- 用 LINE 坐标重建 WIRE segments ---
    for w in wires_raw:
        wid = w[1]
        if wid in line_groups:
            w[2] = line_groups[wid]

    # --- 交叉引用解析 ---
    attr_map = _build_attribute_map(attrs_raw)

    # 先用现有 lcsc_map (device_uuid → info) 做基础解析
    components = _resolve_components(components_raw, attr_map, lcsc_by_device_uuid)

    # --- 后处理：通过 PART → META 链补充 LCSC 信息 ---
    _enrich_epro2_components(components, attr_map, part_to_meta_title, meta_by_title)

    wires = _resolve_wires(wires_raw, attr_map)

    return {
        "project_name": project_name,
        "format": "epro2",
        "project_path": str(Path(project_path).resolve()),
        "components": components,
        "wires": wires,
        "attribute_map": attr_map,
        "stats": {
            "total_components": len(components),
            "total_wires": len(wires),
            "total_attrs": sum(len(v) for v in attr_map.values()),
            "lcsc_parts": len([c for c in components if c["lcsc_part"]]),
        },
    }


def _parse_epru_lines_v2(
    raw_data: str,
    meta_by_title: dict[str, dict[str, str]],
    part_to_meta_title: dict[str, str],
    components_raw: list,
    attrs_raw: list,
    wires_raw: list,
    line_groups: dict[str, list[list[int]]],
) -> None:
    """解析 .epru 文件行 (v2: 处理 PART→META 链和 PCB 过滤)。"""
    # 两遍扫描：第一遍收集 META 和 PART
    all_lines = raw_data.strip().split("\n")

    for line in all_lines:
        line = line.strip()
        if not line:
            continue
        parts = line.split("||")
        if len(parts) < 2:
            continue
        try:
            header = json.loads(parts[0])
        except json.JSONDecodeError:
            continue
        try:
            body = json.loads(parts[1].rstrip("|"))
        except json.JSONDecodeError:
            continue

        etype = header.get("type", "")

        if etype == "META":
            title = body.get("title", "")
            attrs = body.get("attributes", {})
            meta_by_title[title] = {
                "title": title,
                "lcsc_part": attrs.get("Supplier Part", ""),
                "manufacturer_part": attrs.get("Manufacturer Part", ""),
                "footprint": attrs.get("Supplier Footprint", ""),
            }

        elif etype == "PART":
            pid = header.get("id", "")
            ptitle = body.get("title", "")
            if pid and ptitle:
                part_to_meta_title[pid] = ptitle

    # 第二遍：收集 COMPONENT (仅原理图), WIRE, LINE, ATTR
    eid_is_schematic: set[str] = set()

    for line in all_lines:
        line = line.strip()
        if not line:
            continue
        parts = line.split("||")
        if len(parts) < 2:
            continue
        try:
            header = json.loads(parts[0])
        except json.JSONDecodeError:
            continue
        try:
            body = json.loads(parts[1].rstrip("|"))
        except json.JSONDecodeError:
            continue

        etype = header.get("type", "")
        eid = header.get("id", "")

        if etype == "COMPONENT":
            part_id = body.get("partId", "")
            # 原理图 COMPONENT 有 partId；PCB COMPONENT 无 partId
            if part_id:
                eid_is_schematic.add(eid)
                components_raw.append([
                    "COMPONENT",
                    eid,
                    part_id,  # 存入 name 字段，后续用 partId 查 PART→META
                    body.get("x", 0),
                    body.get("y", 0),
                    body.get("rotation", 0),
                    1 if body.get("isMirror", False) else 0,
                    body.get("attrs", {}),
                    body.get("groupId", 0),
                ])

        elif etype == "ATTR":
            parent_id = body.get("parentId", "")
            # 仅保留原理图 COMPONENT 或 WIRE 的 ATTR
            key = body.get("key", "")
            value = body.get("value", "")
            attrs_raw.append([
                "ATTR", eid, parent_id, key, value,
                None, 0, None, None, 0, "", 0,
            ])

        elif etype == "WIRE":
            wires_raw.append(["WIRE", eid, [], "", 0])

        elif etype == "LINE":
            line_group = body.get("lineGroup", "")
            if line_group:
                seg = [
                    body.get("startX", 0),
                    body.get("startY", 0),
                    body.get("endX", 0),
                    body.get("endY", 0),
                ]
                if line_group not in line_groups:
                    line_groups[line_group] = []
                line_groups[line_group].append(seg)


def _enrich_epro2_components(
    components: list[dict],
    attr_map: dict[str, dict[str, str]],
    part_to_meta_title: dict[str, str],
    meta_by_title: dict[str, dict[str, str]],
) -> None:
    """通过 PART → META 链为 .epro2 元件补充 LCSC/device_name 信息。

    COMPONENT.name 存储的是 partId (如 "1N4148TR.1"),
    PART title 也是 "1N4148TR.1",
    META title 是基础名 (如 "1N4148TR") — 需要 strip ".N" 后缀.
    """

    for comp in components:
        part_id = comp.get("name", "")  # 被 _resolve_components 存在 name 字段
        if not part_id:
            continue

        # 生成候选 META title 列表
        base = re.sub(r'\.\d+$', '', part_id)  # "1N4148TR.1" → "1N4148TR"
        candidates = [base]
        # 有些 META title 带 _N 后缀
        if '_' not in base:
            candidates.append(base + "_1")

        meta = {}
        for c in candidates:
            if c in meta_by_title:
                meta = meta_by_title[c]
                break

        if not meta:
            # 模糊匹配：尝试在 meta_by_title 中找包含 base 的 key
            for key, val in meta_by_title.items():
                if key.startswith(base) or base.startswith(key):
                    if val.get("lcsc_part"):
                        meta = val
                        break

        if meta:
            if not comp.get("device_name") or comp["device_name"] == part_id:
                comp["device_name"] = meta.get("title", "")
            if not comp.get("lcsc_part"):
                comp["lcsc_part"] = meta.get("lcsc_part", "")
            if not comp.get("manufacturer_part"):
                comp["manufacturer_part"] = meta.get("manufacturer_part", "")
            if not comp.get("footprint"):
                comp["footprint"] = meta.get("footprint", "")
        elif base and not comp.get("device_name"):
            # 至少用 base 作为 device_name
            comp["device_name"] = base


# ═══════════════════════════════════════════════════════════════════════════════
# 统一入口
# ═══════════════════════════════════════════════════════════════════════════════

def parse_project(project_path: str) -> dict:
    """解析嘉立创 EDA 工程文件，自动识别格式。

    Args:
        project_path: 工程文件路径 (.eprj2 / .epro2 / .zip)

    Returns:
        统一格式的工程数据 dict:
        {
            "project_name": str,
            "format": "eprj2" | "epro2" | "eprj_backup",
            "components": [...],
            "wires": [...],
            "attribute_map": {...},
            "stats": {...}
        }
        错误时返回 {"error": "..."}
    """
    path = Path(project_path)
    if not path.exists():
        return {"error": f"文件不存在: {project_path}"}

    fmt = detect_format(project_path)

    try:
        if fmt == "eprj2":
            return _parse_eprj2(project_path)
        elif fmt == "epro2":
            return _parse_epro2(project_path)
        else:
            return _parse_eprj_backup(project_path)
    except Exception as e:
        return {"error": f"解析失败 ({fmt}): {e}"}


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="嘉立创 EDA 工程文件统一解析器 — 支持 .eprj2 / .epro2 / .zip",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python parse_jlc_project.py project.eprj2                # text 摘要
  python parse_jlc_project.py project.eprj2 --format json  # JSON 输出
        """,
    )
    parser.add_argument(
        "project_path",
        help="工程文件路径 (.eprj2 / .epro2 / .zip)",
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
        f"📄 格式: {result['format']}",
        f"📁 路径: {result['project_path']}",
        "",
        f"📊 统计:",
        f"   元件总数: {result['stats']['total_components']}",
        f"   连线总数: {result['stats']['total_wires']}",
        f"   LCSC 物料: {result['stats']['lcsc_parts']}",
        "",
    ]

    # 元件列表
    if result["components"]:
        lines.append("🔧 元件列表:")
        lines.append(f"   {'位号':<10} {'名称':<25} {'LCSC':<15} {'封装':<15} {'值':<10}")
        lines.append(f"   {'-'*10} {'-'*25} {'-'*15} {'-'*15} {'-'*10}")
        for c in result["components"]:
            des = c.get("designator", "") or "(无)"
            name = (c.get("device_name", "") or c.get("name", ""))[:25]
            lcsc = (c.get("lcsc_part", "") or "")[:15]
            fp = (c.get("footprint", "") or "")[:15]
            val = (c.get("value", "") or "")[:10]
            lines.append(f"   {des:<10} {name:<25} {lcsc:<15} {fp:<15} {val:<10}")

    # 网络列表
    if result["wires"]:
        lines.append("")
        lines.append("🔗 网络连线:")
        # 按 net_name 分组统计
        from collections import Counter
        net_counts = Counter()
        for w in result["wires"]:
            name = w.get("net_name") or w.get("net_global_name") or "(匿名)"
            net_counts[name] += 1
        for name, count in net_counts.most_common(20):
            lines.append(f"   {name}: {count} 条连线")

    return "\n".join(lines)


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    result = parse_project(args.project_path)

    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    else:
        print(_format_text(result))

    if "error" in result:
        sys.exit(1)


if __name__ == "__main__":
    main()
