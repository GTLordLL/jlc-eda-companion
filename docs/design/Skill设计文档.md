# jlc-eda-companion Skill 设计文档

基于 `PCB设计流程(初级).md` 的设计方法论，对 `jlc-eda-companion` Skill 进行架构设计。

各阶段的详细设计已拆分到 `parse/` 目录下的独立文件。

---

## 一、核心设计理念：Phase-aware 流程伴侣

Skill 按 PCB 设计的五阶段组织，Claude 根据上下文推断当前阶段，主动提供该阶段能力。

---

## 二、五阶段能力矩阵

| 阶段 | 用户活动 | Skill 角色 | 详细设计 |
|---|---|---|---|
| **一：解决方案生成** | 需求分析、芯片选型、手册解读 | 🟩 主动引导 + 辅助 | [phase1_solution_generation.md](parse/phase1_solution_generation.md) |
| **二：原理图审查** | 画原理图、ERC 检查 | 🟨 审查/Linter | [phase2_schematic_erc.md](parse/phase2_schematic_erc.md) |
| **三：PCB 诊断** | 布局、布线 | 🟥 诊断辅助 | [phase3_pcb_drc.md](parse/phase3_pcb_drc.md) |
| **四：可制造性与 BOM** | 导出 Gerber、BOM 核对 | 🟩 全自动闭环 | [phase4_manufacturing_bom.md](parse/phase4_manufacturing_bom.md) |
| **五：打样调试** | 焊接、上电、联调 | 🟨 Bug 诊断 | [phase5_debug.md](parse/phase5_debug.md) |

---

## 三、Skill 架构：三层工具

```
Layer 1: 数据层 (Data Layer) —— 统一入口，屏蔽格式差异
  ├── parse_jlc_project.py     # 统一解析器（自动识别 .eprj2 / .epro2 / .zip）
  ├── extract_bom.py           # 提取 BOM 清单
  └── extract_netlist.py       # 提取网表

Layer 2: 分析层 (Analysis Layer) —— 每个阶段专用的分析工具
  ├── search_lcsc.py           # 阶段一：LCSC API 搜索
  ├── compute_passive.py       # 阶段一：阻容值计算
  ├── phase2_erc_check.py      # 阶段二：ERC 检查引擎
  ├── phase3_drc_check.py      # 阶段三：DRC 检查引擎
  ├── phase3_layout_script.py  # 阶段三：布局脚本生成
  ├── phase4_gerber_export.py  # 阶段四：Gerber 打包
  └── phase4_bom_verify.py     # 阶段四：BOM 核对 + 替代料

Layer 3: 报告层 (Report Layer) —— 统一输出格式
  └── generate_report.py       # Markdown 诊断报告生成
```

**外部依赖**：Docling MCP Server（IBM 开源），用于解析 Datasheet PDF → Markdown。

---

## 四、目录结构

```
jialichuang_eda_skill/
├── CLAUDE.md
├── intro/design/
│   ├── PCB设计流程(初级).md
│   ├── 讨论.md
│   ├── 嘉立创EDA工程文件格式分析.md
│   ├── Skill设计文档.md                    # 本文件（总览）
│   └── parse/                              # 各阶段详细设计
│       ├── phase1_solution_generation.md    # 阶段一：解决方案生成
│       ├── phase2_schematic_erc.md          # 阶段二：原理图审查
│       ├── phase3_pcb_drc.md               # 阶段三：PCB 诊断
│       ├── phase4_manufacturing_bom.md      # 阶段四：可制造性
│       └── phase5_debug.md                 # 阶段五：调试辅助
├── jlc_project/                            # 样本工程
└── jlc-eda-companion/                      # Skill 源代码
    ├── skill.json
    ├── prompts/
    └── tools/
```

---

## 五、开发路线图

```
Phase 1 ─  阶段一：解决方案生成 ← 当前
          ├── search_lcsc.py（LCSC API 搜索 + 库存查询）
          ├── compute_passive.py（阻容值计算：分压/滤波/欧姆定律）
          ├── Docling MCP 集成（数据手册 PDF → Markdown）
          └── 验证：给定芯片型号 + 手册 PDF，生成外围电路 BOM

Phase 2 ─  阶段二：原理图审查 (ERC)
          ├── parse_jlc_project.py（统一解析器，支持 3 种格式）
          ├── extract_netlist.py（WIRE 拓扑 → 网络连接）
          ├── phase2_erc_check.py（对照手册检查原理图）
          └── 验证：对样本工程运行 ERC 检查

Phase 3 ─  阶段三：PCB 诊断
          ├── phase3_drc_check.py + phase3_layout_script.py
          └── 验证：PCB 走线诊断

Phase 4 ─  阶段四：可制造性与 BOM 导出
          ├── phase4_bom_verify.py + phase4_gerber_export.py
          └── 验证：生成 BOM 核对报告

Phase 5 ─  阶段五：调试辅助
          └── 验证：生成上电 Debug 手册
```

---

## 六、关键设计决策记录

| 决策 | 选择 | 理由 |
|---|---|---|
| 工具语言 | Python 3 | 标准库即够用 |
| 阶段入口 | Phase-aware 上下文检测 | Claude 推断阶段，无需用户显式指定 |
| 解析器策略 | 统一入口 + 自动格式识别 | 一个 `parse_jlc_project.py` 处理三种格式 |
| PDF 解析 | Docling MCP | IBM 开源，原生 MCP Server，不自己造轮子 |
| 电路图识别 | 不做 | LLM 视觉不可靠，手册文字描述已足够 |
| 信息权威源 | 芯片数据手册 | LCSC 页面可能出错，手册是唯一权威 |
| 报告格式 | Markdown | 终端友好 |
| 开发顺序 | 阶段一→二→三→四→五 | 按 PCB 设计流程自然递进 |
