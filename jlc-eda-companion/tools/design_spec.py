#!/usr/bin/env python3
"""Design Spec 管理工具 — Phase 1→2 结构化桥梁

定义 Design Spec JSON 格式：将数据手册知识结构化，供 ERC 引擎消费。

用法：
  # 校验已有 Design Spec
  python design_spec.py validate spec.json --format json

  # 从模板初始化
  python design_spec.py init "51单片机最小系统板" --core-chip STC89C52RC --lcsc C8707 --package LQFP-44

  # 列出所有需求类别
  python design_spec.py categories

Python import:
  from design_spec import DesignSpec, Requirement, validate, load, save
  spec = load("design_spec.json")
  errors = validate(spec)
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field, asdict
from datetime import date
from pathlib import Path
from typing import Optional, Any


# ═══════════════════════════════════════════════════════════════════════════════
# 需求类别定义
# ═══════════════════════════════════════════════════════════════════════════════

CATEGORIES: dict[str, dict] = {
    "decoupling": {
        "display": "去耦电容",
        "description": "VCC/VDD 引脚就近去耦电容要求",
        "required_rule_fields": ["target_chip", "pin_pattern", "cap_value", "cap_value_f"],
        "optional_rule_fields": ["tolerance", "placement", "distance_mm"],
    },
    "pullup": {
        "display": "上拉电阻",
        "description": "引脚需上拉电阻到 VCC",
        "required_rule_fields": ["target_chip", "pin_pattern", "resistor_value", "resistor_ohm", "pull_target"],
        "optional_rule_fields": [],
    },
    "pulldown": {
        "display": "下拉电阻",
        "description": "引脚需下拉电阻到 GND",
        "required_rule_fields": ["target_chip", "pin_pattern", "resistor_value", "resistor_ohm", "pull_target"],
        "optional_rule_fields": [],
    },
    "crystal": {
        "display": "晶振负载电容",
        "description": "晶振负载电容 CL 要求",
        "required_rule_fields": ["target_chip", "pin_pattern", "load_cap_pf"],
        "optional_rule_fields": ["frequency", "stray_cap_pf", "suggested_caps_pf"],
    },
    "power_feedback": {
        "display": "电源反馈分压",
        "description": "电源芯片反馈分压目标输出电压",
        "required_rule_fields": ["target_chip", "vref", "target_vout"],
        "optional_rule_fields": ["r1_ohm", "r2_ohm"],
    },
    "pin_termination": {
        "display": "引脚端接",
        "description": "特殊引脚端接要求（必须接/必须浮空/…）",
        "required_rule_fields": ["target_chip", "pin_pattern", "termination"],
        "optional_rule_fields": ["resistor_value", "resistor_ohm", "notes"],
    },
    "pin_exclusion": {
        "display": "引脚排除",
        "description": "明确排除的引脚（不参与某些 ERC 检查）",
        "required_rule_fields": ["target_chip", "pin_pattern", "exclude_from_checks", "reason"],
        "optional_rule_fields": [],
    },
}

SEVERITIES = ["error", "warning", "suggestion"]

VALID_TERMINATIONS = [
    "pullup_to_vcc",
    "pulldown_to_gnd",
    "must_connect",
    "must_float",
    "connect_to_net",
]

SPEC_VERSION = "1.0"


# ═══════════════════════════════════════════════════════════════════════════════
# 数据模型
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class PinInfo:
    """芯片引脚信息。"""
    number: int | str
    name: str = ""
    net: str | None = None

    def to_dict(self) -> dict:
        d = {"number": self.number}
        if self.name:
            d["name"] = self.name
        if self.net:
            d["net"] = self.net
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "PinInfo":
        return cls(
            number=d.get("number", ""),
            name=d.get("name", ""),
            net=d.get("net"),
        )


@dataclass
class CoreChip:
    """核心芯片定义。"""
    designator: str
    name: str
    lcsc: str
    package: str = ""
    pins: dict[str, list[PinInfo]] = field(default_factory=dict)
    excluded_pins: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = {
            "designator": self.designator,
            "name": self.name,
            "lcsc": self.lcsc,
        }
        if self.package:
            d["package"] = self.package
        if self.pins:
            d["pins"] = {k: [p.to_dict() for p in v] for k, v in self.pins.items()}
        if self.excluded_pins:
            d["excluded_pins"] = self.excluded_pins
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "CoreChip":
        pins_raw = d.get("pins", {})
        pins = {}
        for group, pin_list in pins_raw.items():
            pins[group] = [PinInfo.from_dict(p) for p in pin_list]
        return cls(
            designator=d.get("designator", ""),
            name=d.get("name", ""),
            lcsc=d.get("lcsc", ""),
            package=d.get("package", ""),
            pins=pins,
            excluded_pins=d.get("excluded_pins", []),
        )


@dataclass
class Requirement:
    """单条设计需求。"""
    id: str
    category: str
    severity: str
    description: str
    rule: dict
    source: str

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "category": self.category,
            "severity": self.severity,
            "description": self.description,
            "rule": self.rule,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Requirement":
        return cls(
            id=d.get("id", ""),
            category=d.get("category", ""),
            severity=d.get("severity", ""),
            description=d.get("description", ""),
            rule=d.get("rule", {}),
            source=d.get("source", ""),
        )


@dataclass
class DesignSpec:
    """完整设计规约。"""
    meta: dict
    core_chips: list[CoreChip]
    requirements: list[Requirement]

    def to_dict(self) -> dict:
        return {
            "meta": self.meta,
            "core_chips": [c.to_dict() for c in self.core_chips],
            "requirements": [r.to_dict() for r in self.requirements],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "DesignSpec":
        return cls(
            meta=d.get("meta", {}),
            core_chips=[CoreChip.from_dict(c) for c in d.get("core_chips", [])],
            requirements=[Requirement.from_dict(r) for r in d.get("requirements", [])],
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 校验
# ═══════════════════════════════════════════════════════════════════════════════

def validate(data: dict | DesignSpec) -> list[str]:
    """校验 Design Spec，返回错误列表。空列表 = 合法。

    检查项：
      1. meta 顶层结构
      2. core_chips 字段完整性
      3. requirements 逐条检查
      4. rule 字段按 category 检查
    """
    errors: list[str] = []

    if isinstance(data, DesignSpec):
        data = data.to_dict()

    if not isinstance(data, dict):
        return ["Design Spec 必须是 JSON 对象"]

    # --- meta ---
    meta = data.get("meta", {})
    if not isinstance(meta, dict):
        errors.append("meta: 必须是对象")
    else:
        if not meta.get("project_name", "").strip():
            errors.append("meta.project_name: 不能为空")
        if not meta.get("version", ""):
            errors.append("meta.version: 不能为空")

    # --- core_chips ---
    chips = data.get("core_chips", [])
    if not isinstance(chips, list):
        errors.append("core_chips: 必须是数组")
    elif not chips:
        errors.append("core_chips: 至少需要一个核心芯片")
    else:
        for i, chip in enumerate(chips):
            if not isinstance(chip, dict):
                errors.append(f"core_chips[{i}]: 必须是对象")
                continue
            if not chip.get("designator", "").strip():
                errors.append(f"core_chips[{i}].designator: 不能为空")
            if not chip.get("lcsc", "").strip():
                errors.append(f"core_chips[{i}].lcsc: 不能为空 (chip={chip.get('designator', '?')})")

    # --- requirements ---
    reqs = data.get("requirements", [])
    if not isinstance(reqs, list):
        errors.append("requirements: 必须是数组")
        return errors

    if not reqs:
        errors.append("requirements: 至少需要一条需求")

    seen_ids: set[str] = set()
    for i, req in enumerate(reqs):
        if not isinstance(req, dict):
            errors.append(f"requirements[{i}]: 必须是对象")
            continue

        rid = req.get("id", "")
        prefix = f"requirements[{i}]" + (f" ({rid})" if rid else "")

        # id
        if not isinstance(rid, str) or not rid.strip():
            errors.append(f"{prefix}: id 不能为空")
        elif rid in seen_ids:
            errors.append(f"{prefix}: id={rid} 重复")
        else:
            seen_ids.add(rid)

        # category
        cat = req.get("category", "")
        if not cat:
            errors.append(f"{prefix}: category 不能为空")
        elif cat not in CATEGORIES:
            errors.append(
                f"{prefix}: category={cat} 无效，可用: {', '.join(CATEGORIES)}"
            )

        # severity
        sev = req.get("severity", "")
        if not sev:
            errors.append(f"{prefix}: severity 不能为空")
        elif sev not in SEVERITIES:
            errors.append(f"{prefix}: severity={sev} 无效，可用: {', '.join(SEVERITIES)}")

        # source
        src = req.get("source", "")
        if not isinstance(src, str) or not src.strip():
            errors.append(f"{prefix}: source 不能为空（必须精确到手册章节号）")

        # rule
        rule = req.get("rule", {})
        if not isinstance(rule, dict):
            errors.append(f"{prefix}: rule 必须是对象")
        elif cat in CATEGORIES:
            cat_def = CATEGORIES[cat]
            for required_field in cat_def["required_rule_fields"]:
                if required_field not in rule or rule[required_field] is None:
                    errors.append(
                        f"{prefix}: rule.{required_field} 缺失 (category={cat} 必填)"
                    )

    return errors


# ═══════════════════════════════════════════════════════════════════════════════
# 读写
# ═══════════════════════════════════════════════════════════════════════════════

def load(path: str | Path) -> DesignSpec:
    """从 JSON 文件加载 Design Spec。"""
    raw = Path(path).read_text(encoding="utf-8")
    data = json.loads(raw)
    return DesignSpec.from_dict(data)


def save(spec: DesignSpec, path: str | Path, indent: int = 2) -> None:
    """保存 Design Spec 到 JSON 文件。"""
    Path(path).write_text(
        json.dumps(spec.to_dict(), ensure_ascii=False, indent=indent),
        encoding="utf-8",
    )


def loads(json_str: str) -> DesignSpec:
    """从 JSON 字符串加载 Design Spec。"""
    data = json.loads(json_str)
    return DesignSpec.from_dict(data)


def dumps(spec: DesignSpec, indent: int = 2) -> str:
    """将 Design Spec 序列化为 JSON 字符串。"""
    return json.dumps(spec.to_dict(), ensure_ascii=False, indent=indent)


# ═══════════════════════════════════════════════════════════════════════════════
# 模板生成
# ═══════════════════════════════════════════════════════════════════════════════

def init_template(
    project_name: str,
    core_chip_name: str = "",
    core_chip_lcsc: str = "",
    core_chip_package: str = "",
    core_chip_designator: str = "U1",
) -> DesignSpec:
    """生成 Design Spec 模板。"""
    today = date.today().isoformat()

    meta = {
        "version": SPEC_VERSION,
        "project_name": project_name,
        "created": today,
        "source_datasheets": [],
        "source_chapters": [],
        "notes": "请根据数据手册内容填充 core_chips 和 requirements",
    }

    chips: list[CoreChip] = []
    if core_chip_name or core_chip_lcsc:
        chips.append(CoreChip(
            designator=core_chip_designator,
            name=core_chip_name,
            lcsc=core_chip_lcsc,
            package=core_chip_package,
        ))

    # 提供示例 Requirement
    example_req = Requirement(
        id="example_decoupling",
        category="decoupling",
        severity="error",
        description="[请替换] 主芯片 VCC 引脚需 100nF 去耦电容",
        rule={
            "target_chip": core_chip_designator,
            "pin_pattern": "VCC|VDD",
            "cap_value": "100nF",
            "cap_value_f": 1e-7,
            "tolerance": 0.2,
            "placement": "near_pin",
            "distance_mm": 5,
        },
        source="[请替换为手册章节号，如 §5.2 Power Supply]",
    )

    return DesignSpec(
        meta=meta,
        core_chips=chips,
        requirements=[example_req],
    )


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Design Spec 管理工具 — Phase 1→2 结构化桥梁",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python design_spec.py validate spec.json
  python design_spec.py validate spec.json --format json
  python design_spec.py init "51单片机" --core-chip STC89C52RC --lcsc C8707 --package LQFP-44
  python design_spec.py categories
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="子命令")

    # --- validate ---
    val_parser = subparsers.add_parser("validate", help="校验 Design Spec JSON")
    val_parser.add_argument("spec_file", help="Design Spec JSON 文件路径")
    val_parser.add_argument("--format", choices=["json", "text"], default="text",
                            help="输出格式 (default: text)")

    # --- init ---
    init_parser = subparsers.add_parser("init", help="初始化 Design Spec 模板")
    init_parser.add_argument("project_name", help="项目名称")
    init_parser.add_argument("--core-chip", default="", help="核心芯片名称")
    init_parser.add_argument("--lcsc", default="", help="核心芯片 LCSC 编号")
    init_parser.add_argument("--package", default="", help="核心芯片封装")
    init_parser.add_argument("--designator", default="U1", help="核心芯片位号 (default: U1)")
    init_parser.add_argument("--output", "-o", default=None, help="输出文件路径（默认输出到 stdout）")
    init_parser.add_argument("--format", choices=["json", "text"], default="json",
                            help="输出格式 (default: json)")

    # --- categories ---
    subparsers.add_parser("categories", help="列出所有需求类别")

    return parser


def _cmd_validate(args: argparse.Namespace) -> int:
    spec_path = Path(args.spec_file)
    if not spec_path.exists():
        result = {"error": f"文件不存在: {args.spec_file}"}
        if args.format == "json":
            print(json.dumps(result, ensure_ascii=False))
        else:
            print(f"❌ 文件不存在: {args.spec_file}")
        return 1

    try:
        data = json.loads(spec_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        result = {"error": f"JSON 解析失败: {e}"}
        if args.format == "json":
            print(json.dumps(result, ensure_ascii=False))
        else:
            print(f"❌ JSON 解析失败: {e}")
        return 1

    errors = validate(data)

    if args.format == "json":
        result = {
            "valid": len(errors) == 0,
            "error_count": len(errors),
            "errors": errors,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        project = data.get("meta", {}).get("project_name", spec_path.name)
        if not errors:
            print(f"✅ {project} — Design Spec 校验通过")
            req_count = len(data.get("requirements", []))
            chip_count = len(data.get("core_chips", []))
            print(f"   核心芯片: {chip_count} | 需求条目: {req_count}")
        else:
            print(f"❌ {project} — {len(errors)} 个校验错误:")
            for err in errors:
                print(f"   • {err}")

    return 0 if not errors else 1


def _cmd_init(args: argparse.Namespace) -> int:
    spec = init_template(
        project_name=args.project_name,
        core_chip_name=getattr(args, "core_chip", ""),
        core_chip_lcsc=getattr(args, "lcsc", ""),
        core_chip_package=getattr(args, "package", ""),
        core_chip_designator=getattr(args, "designator", "U1"),
    )

    output = dumps(spec)

    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
        if args.format == "json":
            print(json.dumps({"status": "ok", "output": args.output}, ensure_ascii=False))
        else:
            print(f"✅ Design Spec 模板已保存到: {args.output}")
    else:
        print(output)

    return 0


def _cmd_categories(args: argparse.Namespace) -> int:
    print("## Design Spec 需求类别\n")
    print("| 类别 | 说明 | rule 必填字段 |")
    print("|------|------|---------------|")
    for cat_id, cat_def in CATEGORIES.items():
        display = cat_def["display"]
        desc = cat_def["description"]
        required = ", ".join(f"`{f}`" for f in cat_def["required_rule_fields"])
        print(f"| `{cat_id}` | {display} — {desc} | {required} |")
    return 0


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    handlers = {
        "validate": _cmd_validate,
        "init": _cmd_init,
        "categories": _cmd_categories,
    }

    handler = handlers.get(args.command)
    if handler:
        sys.exit(handler(args))


if __name__ == "__main__":
    main()
