# CLAUDE.md

本文件为 Claude Code (claude.ai/code) 在此仓库中工作提供指导。请使用中文回答，代码/命令/提示词等高效的地方使用英文。

## 项目目标

开发一个基于嘉立创 EDA 的 **Claude Code Agent Skill**（名称暂定 `jlc-eda-companion`），用于辅助电子设计的全流程。该 Skill 覆盖 PCB 设计的五个阶段，在不同阶段扮演不同角色：

| 阶段 | 角色 | 能力 |
|---|---|---|
| 一：解决方案生成 | 🟩 主动引导 + 辅助 | 需求澄清→芯片选型(LCSC搜索)→数据手册解读(Docling MCP)→外围电路BOM生成 |
| 二：原理图审查 | 🟨 审查/Linter | 读取嘉立创 EDA 原理图数据，对照芯片手册做电气规则检查（ERC） |
| 三：PCB 诊断 | 🟥 诊断辅助 | 生成布局脚本（JS/Python），走线热点诊断（线宽、载流能力） |
| 四：可制造性与 BOM | 🟩 全自动闭环 | BOM 缺货替代检查，一键生成 Gerber 和坐标文件 |
| 五：打样调试 | 🟨 Bug 诊断 | 个性化上电 Debug 手册，软硬件联调故障定位 |

## 仓库结构

```
jialichuang_eda_skill/
├── CLAUDE.md                              # 本文件
├── intro/                                 # 项目文档与分析
│   └── design/
│       ├── PCB设计流程(初级).md             # PCB 设计流程参考文档
│       ├── 讨论.md                         # 项目需求讨论与架构构思
│       ├── 嘉立创EDA工程文件格式分析.md      # 两种工程文件格式的逆向分析
│       ├── Skill设计文档.md                # Skill 架构设计（总览）
│       └── parse/                         # 各阶段详细设计
│           ├── phase1_solution_generation.md  # 阶段一：解决方案生成
│           ├── phase2_schematic_erc.md        # 阶段二：原理图审查
│           ├── phase3_pcb_drc.md              # 阶段三：PCB 诊断
│           ├── phase4_manufacturing_bom.md    # 阶段四：可制造性
│           └── phase5_debug.md               # 阶段五：调试辅助
├── jlc_project/                           # 样本工程文件
│   ├── 51单片机最小系统板/                  # 标准版 .eprj2 + 备份 .zip
│   └── 示例-波型产生与变换/                 # 专业版 .epro2
└── jlc-eda-companion/                     # ⭐ Skill 源代码
    ├── skill.json                         # [开发中] Skill 总控（描述、触发词、权限）
    ├── prompts/                           # [开发中] 硬件知识库与审查规则
    │   ├── phase1_solution_generation.md    # 阶段一：解决方案生成
    │   ├── phase2_erc_rules.md             # 阶段二：电气规则检查知识库
    │   ├── phase3_drc_rules.md             # 阶段三：PCB 设计规则
    │   ├── phase4_dfm_rules.md             # 阶段四：可制造性规则
    │   └── phase5_debug_guide.md           # 阶段五：调试知识库
    └── tools/                             # [开发中] 底层执行脚本（Python）
        ├── parse_jlc_project.py           # 统一工程解析器（支持 3 种格式）
        ├── extract_bom.py                 # BOM 提取
        ├── extract_netlist.py             # 网表提取（WIRE 拓扑 → 网络连接）
        ├── search_lcsc.py                 # LCSC API 搜索 + 库存查询
        ├── compute_passive.py             # 阻容值计算（欧姆定律/分压/滤波）
        ├── phase2_erc_check.py            # ERC：对照手册检查原理图
        ├── phase3_drc_check.py            # DRC：PCB 设计规则检查
        ├── phase3_layout_script.py        # 布局脚本生成
        ├── phase4_gerber_export.py        # Gerber 打包
        ├── phase4_bom_verify.py           # BOM 核对 + 替代料
        └── generate_report.py             # 统一报告生成
```

## 开发路径（按阶段递进）

详细设计见 `intro/design/Skill设计文档.md`，各阶段细节见 `intro/design/parse/`。

## 关键技术与接口

- **嘉立创 EDA 工程格式**：详见 `intro/design/嘉立创EDA工程文件格式分析.md`
- **Skill 架构设计**：详见 `intro/design/Skill设计文档.md`
- **立创商城 API**：用于查询元器件库存、价格、LCSC 编号
- **MCP 协议 / Claude Code Skill 扩展机制**：Skill 挂载到 Claude Code 的标准方式
- **嘉立创 EDA 脚本**：支持 JavaScript/Python 控制元件布局

## 样本工程速查

两个样本工程在 `jlc_project/` 下，用于测试解析器：

| 工程 | 版本 | 主文件 | 元件数 |
|---|---|---|---|
| 51单片机最小系统板 | 标准版 | `.eprj2` (SQLite) | ~30个器件，含 STC89C52RC、AMS1117-3.3、TYPE-C 等 |
| 示例-波型产生与变换 | 专业版 | `.epro2` (Zip) | ~20个器件，含 NE555DR、TL072CP、电阻电容等 |


### 运行须知
1. 运行python时，先source进入虚拟环境
2. 访问国外网站需要使用代理7897

