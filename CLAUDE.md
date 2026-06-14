# CLAUDE.md

请使用中文回答，代码/命令/提示词等高效的地方使用英文。

## 项目目标

开发一个基于嘉立创 EDA 的 **Claude Code Agent Skill**（`jlc-eda-companion`），专注 PCB 设计前两个阶段：

| 阶段 | 角色 | 能力 |
|---|---|---|
| **一：解决方案生成** | 🟩 主动引导 + 辅助 | 需求澄清 → 芯片选型(LCSC搜索) → 数据手册下载与解析 → 外围电路BOM生成 |
| **二：原理图审查** | 🟨 审查/Linter | 解析嘉立创 EDA 原理图 → 提取网表 → 对照芯片手册做电气规则检查（ERC） |

> 阶段三～五（PCB诊断、可制造性、调试）为远期规划，详见 `intro/design/Skill设计文档.md`。

## 仓库结构

```
jialichuang_eda_skill/
├── CLAUDE.md                              # 本文件
├── intro/                                 # 项目文档与分析
│   └── design/
│       ├── PCB设计流程(初级).md             # PCB 设计流程参考
│       ├── 嘉立创EDA工程文件格式分析.md      # 工程文件格式逆向分析
│       ├── Skill设计文档.md                # Skill 架构设计（五阶段总览）
│       └── parse/                         # 各阶段详细设计
│           ├── phase1_solution_generation.md  # 阶段一设计
│           ├── phase2_schematic_erc.md        # 阶段二设计
│           ├── phase3_pcb_drc.md              # [远期] 阶段三
│           ├── phase4_manufacturing_bom.md    # [远期] 阶段四
│           └── phase5_debug.md               # [远期] 阶段五
├── jlc_project/                           # 样本工程（测试用）
├── datasheets/                            # 下载的数据手册 PDF 缓存
└── jlc-eda-companion/                     # ⭐ Skill 源代码
    ├── skill.json                         # Skill 总控（描述、触发词、工具注册）
    ├── prompts/                           # Claude 行为指引
    │   └── phase1_solution_generation.md    # 阶段一：解决方案生成
    └── tools/                             # Python 工具脚本
        ├── search_lcsc.py                 # ✅ LCSC 元器件搜索
        ├── fetch_datasheet.py             # ✅ 数据手册 PDF 下载
        ├── parse_datasheet.py             # ✅ PDF → Markdown 解析 + 章节提取
        ├── compute_passive.py             # ✅ 阻容值计算器
        ├── parse_jlc_project.py           # 📋 统一工程解析器（3 种格式）
        ├── extract_netlist.py             # 📋 网表提取（WIRE 拓扑 → 网络连接）
        └── phase2_erc_check.py            # 📋 ERC 检查引擎
```

> ✅ = 已实现　　📋 = 阶段二待开发

## 当前开发重点：阶段二

阶段一工具链已完成（搜索→下载→解析→计算）。当前聚焦阶段二：

```
parse_jlc_project.py   ← 第一优先级，统一解析 .eprj2 / .epro2 / .zip
    ↓
extract_netlist.py     ← 从 WIRE 拓扑重建网络连接
    ↓
phase2_erc_check.py    ← 对照手册 + ERC 规则库检查原理图
    ↓
prompts/phase2_erc_rules.md  ← Claude 审查行为指引
```

### 阶段二 ERC 检查项（首批 7 项）

1. IC 的 VCC 引脚是否就近有 100nF 去耦电容
2. EN/复位引脚是否有上拉/下拉（不能浮空）
3. I2C 总线是否有上拉电阻（SDA/SCL → VCC）
4. 电源芯片反馈电阻分压比例是否与目标输出电压匹配
5. 晶振是否有匹配的负载电容
6. 未连接的引脚（悬空 Net）警告
7. 电源网络是否与地短路

## 关键技术与接口

- **嘉立创 EDA 工程格式**：详见 `intro/design/嘉立创EDA工程文件格式分析.md`
- **Skill 架构设计**：详见 `intro/design/Skill设计文档.md`
- **立创商城 API**：LCSC 元器件搜索与库存查询
- **数据手册解析**：markitdown 引擎（自包含，无需外部 MCP）

## 样本工程速查

| 工程 | 版本 | 主文件 | 元件数 |
|---|---|---|---|
| 51单片机最小系统板 | 标准版 | `.eprj2` (SQLite) | ~30 个 |
| 示例-波型产生与变换 | 专业版 | `.epro2` (Zip) | ~20 个 |

## 运行须知

1. 运行 Python 前先 `source .venv/bin/activate`
2. 访问国外网站使用代理 `7897`
