# jlc-eda-companion MVP 计划

> 目标：跑通"调研 → 生成解决方案 → 行动"的完整闭环
> 原则：不一次做好，每一步都打好基础

---

## 现状盘点

### 已就绪（7 个工具 + 2 个 Prompt）

```
Phase 1: 调研 + 方案生成
  search_lcsc.py         ✅ 搜芯片
  fetch_datasheet.py     ✅ 下载 PDF
  parse_datasheet.py     ✅ PDF → 章节化 Markdown
  compute_passive.py     ✅ 阻容计算器
  phase1 prompt          ✅ 详细行为指引

Phase 2: 原理图审查
  parse_jlc_project.py   ✅ 解析工程文件 (3 种格式)
  extract_netlist.py     ✅ 网表提取
  phase2_erc_check.py    ✅ 7 项 ERC 规则
  phase2 prompt          ✅ ERC 审查指引
```

### 核心缺口

Phase 1 产出的"数据手册知识"和 Phase 2 的"ERC 检查"之间是断的：

```
调研（Phase 1）              方案（Phase 1）           行动（Phase 2）
─────────────────           ────────────────          ───────────────
手册 PDF → parse → 章节.md → Claude 读 → 生成 BOM → ERC 检查原理图
                                        │                 │
                                        │   ❌ 断了       │
                                        │                 │
                                    人肉记忆 BOM 值 ──→ 手动对比 ERC 结果
```

目前 Phase 1 产出的 BOM 是 Markdown 文本，Phase 2 的 `phase2_erc_check.py` 做的是通用规则检查（去耦电容有/无），但不知道手册要求的具体值（100nF 还是 10µF？上拉是 10K 还是 4.7K？）。

需要引入一个 **结构化设计规约（Design Spec）** 作为 Phase 1 和 Phase 2 之间的"合同"。

---

## MVP 目标

```
给出需求 → 查手册 → 知道"应该怎么接" → 检查原理图"实际怎么接" → 对比 → 指出哪里不对
```

具体操作流：
1. 拿到芯片型号 → 下载手册 → 解析 → 提取参考电路要求
2. 把参考电路要求结构化 → **Design Spec JSON**（目前缺的）
3. 解析用户的原理图工程文件 → 网表
4. 逐项对比"应该" vs "实际"
5. 输出可执行的修复建议

---

## 四步计划

### Step 1：跑通样本工程全链路（夯实基础）

**目标：** 在 51 单片机样本工程上把 Phase 1 + Phase 2 完整跑一遍，验证所有工具可用。

**具体动作：**

```
Phase 1 管线：
  ① search_lcsc.py general "STC89C52RC" → 确认 LCSC 编号 C8707
  ② fetch_datasheet.py C8707 --download ./datasheets → 下载 PDF
  ③ parse_datasheet.py process ./datasheets/C8707.pdf → 拆分为章节 .md
  ④ Claude Read index.md + 🟢 章节 → 提取参考电路要求
  ⑤ compute_passive.py → 计算晶振负载电容、复位上拉等参数
  ⑥ 生成 BOM 表（Markdown）

Phase 2 管线：
  ⑦ parse_jlc_project.py 51单片机最小系统板.eprj2 --format json
  ⑧ extract_netlist.py --stdin --format json
  ⑨ phase2_erc_check.py --stdin
  ⑩ Claude 解读 ERC 报告 + 对照 Phase 1 手册提取值 → 确认/排除问题
```

**产出：**
- 一份完整的 Phase 1 设计 BOM（基于 STC89C52RC 手册）
- 一份 Phase 2 ERC 审查报告（51 单片机样本工程）
- Claude 手动交叉对比的结果记录

**验证标准：** 管线无报错运行，ERC 报告包含 7 项检查的完整结果。

**预计时间：** 1-2h

---

### Step 2：引入 Design Spec 中间格式（核心桥梁）

**目标：** 定义结构化 JSON 格式，让 Phase 1 的数据手册知识能被 Phase 2 的 ERC 引擎消费。

**Design Spec 示例：**

```json
{
  "project": "51单片机最小系统板",
  "core_chip": {
    "designator": "U1",
    "name": "STC89C52RC",
    "lcsc": "C8707"
  },
  "requirements": [
    {
      "id": "decoupling_vcc",
      "category": "decoupling",
      "severity": "error",
      "description": "每个 VCC 引脚需要 100nF 去耦电容，5mm 以内",
      "rule": {
        "pin_pattern": "VCC|VDD",
        "cap_value": "100nF",
        "distance_mm": 5
      },
      "source": "手册 §5.2 Power Supply"
    },
    {
      "id": "nrst_pullup",
      "category": "pullup",
      "severity": "error",
      "description": "NRST 引脚需要 10KΩ 上拉到 VCC",
      "rule": {
        "pin_pattern": "NRST|RST",
        "resistor": "10KΩ",
        "target": "VCC"
      },
      "source": "手册 §4.3 Reset Circuit"
    },
    {
      "id": "crystal_load",
      "category": "crystal",
      "severity": "warning",
      "description": "晶振 11.0592MHz，负载电容 30pF 对称接 GND",
      "rule": {
        "frequency": "11.0592MHz",
        "load_cap": "30pF",
        "pin_pattern": "XTAL"
      },
      "source": "手册 §4.1 Clock"
    }
  ]
}
```

**具体动作：**

```
① 新建 tools/design_spec.py
   ├── DesignSpec 数据类（dataclass）
   ├── Requirement 数据类（category / severity / rule / source）
   ├── validate(spec) → bool 校验函数
   └── load(path) / save(spec, path) 读写函数

② 新建 prompts/design_spec_template.md
   └── Claude 从数据手册 Markdown 提取 Design Spec 的行为指引
       - 哪些章节找什么信息
       - 如何写 rule 字段（可被 ERC 引擎执行）
       - source 字段格式要求（必须精确到章节号）

③ 增强 skill.json
   └── 注册 design_spec 工具
   └── 新增 Phase 1→2 衔接触发词
```

**产出：**
- `tools/design_spec.py`（数据模型 + 校验）
- `prompts/design_spec_template.md`（Claude 行为指引）
- 51 单片机样本工程的 `design_spec.json`

**验证标准：** Claude 能从 STC89C52RC 手册自动生成合规的 Design Spec JSON，`design_spec.py` 校验通过。

**预计时间：** 2-3h

---

### Step 3：增强 ERC — Design Spec 驱动精确检查（核心能力）

**目标：** 让 `phase2_erc_check.py` 支持 `--spec design_spec.json`，从通用规则升级为手册对照。

**具体动作：**

```
① 修改 phase2_erc_check.py
   ├── 新增 --spec 参数，接收 Design Spec JSON 路径
   ├── 新增 spec_driven_checks(spec, netlist) 函数
   │   ├── check_spec_decoupling(spec, netlist)    — 精确到引脚+电容值
   │   ├── check_spec_pullup(spec, netlist)        — 精确到电阻值
   │   ├── check_spec_crystal(spec, netlist)       — 精确到负载电容值
   │   └── check_spec_power(spec, netlist)         — 电源引脚逐一核对
   └── 合并通用规则 + Spec 规则的结果，去重

② Design Spec 检查示例（vs 现有通用规则）：
   ┌──────────────────┬──────────────────────────────────────┐
   │ 现有（通用）      │ 增强后（+ Design Spec）                │
   ├──────────────────┼──────────────────────────────────────┤
   │ "IC VCC 有 100nF?"│ "手册要求每个 VCC 100nF+10µF，       │
   │                   │  U1.VCC(40) 有 ✅，U1.VCC(20) 缺 ❌"│
   ├──────────────────┼──────────────────────────────────────┤
   │ "晶振有负载电容?" │ "手册要求 CL=30pF，实际 47pF→       │
   │                   │  CLeff=26.5pF，偏差-11.7% ⚠️"       │
   ├──────────────────┼──────────────────────────────────────┤
   │ "EN脚浮空?"      │ "NRST: 手册要求 10K 上拉 VCC，       │
   │                   │  实际未连接 ❌"                       │
   └──────────────────┴──────────────────────────────────────┘

③ 输出增强
   └── 报告中每个发现标注来源: "依据: 手册 §X.Y" vs "依据: 通用规则"
```

**产出：**
- `phase2_erc_check.py` 新增 `--spec` 模式
- Design Spec 驱动检查的报告样例（51 单片机样本工程）

**验证标准：** 用 Step 2 生成的 `design_spec.json` 对 51 样本工程运行 ERC，报告中出现手册章节引用。

**预计时间：** 2-3h

---

### Step 4：可执行修复建议（行动闭环）

**目标：** ERC 报告不只指出问题，还输出可操作的修复指令，让用户知道具体怎么改。

**具体动作：**

```
① 修改 phase2_erc_check.py
   └── 每个 finding 新增 action 字段（对 machine-readable）
   └── 新增 --actions 参数，输出 JSON 修复指令集

② 修复指令格式：
   {
     "finding_id": "decoupling_vcc_missing_u1_pin20",
     "severity": "error",
     "action": "add_component",
     "component": {
       "designator": "C10",
       "type": "capacitor",
       "value": "100nF",
       "lcsc": "C14663",
       "package": "0603"
     },
     "connection": {
       "pin1": "VCC+5V",
       "pin2": "GND"
     },
     "placement": "靠近 U1 的 Pin20，5mm 以内",
     "source": "手册 §5.2"
   }

③ Markdown 报告中增加"修复建议"章节
   └── 按优先级排列（先修 error，再修 warning）
   └── 每条建议含：位号、值、封装、LCSC 编号、连接关系
   └── 用户可以直接按报告去 LCSC 下单 + 改原理图

④ 更新 prompts/phase2_schematic_erc.md
   └── 新增"如何向用户传达修复建议"的行为指引
```

**产出：**
- `phase2_erc_check.py` 新增 `--actions` 模式
- 可执行的 JSON 修复指令 + Markdown 修复建议

**验证标准：** 对 51 样本工程生成修复指令，手动验证每条指令是否可执行。

**预计时间：** 1-2h

---

## 路线图总览

```
Step 1          Step 2            Step 3            Step 4
跑通样本全链路   Design Spec格式   Spec驱动精确ERC   可执行修复建议
    │               │                 │                 │
    ▼               ▼                 ▼                 ▼
 现有工具验证    数据模型+Prompt    ERC增强+对照     action输出
 1-2h            2-3h              2-3h             1-2h

 └── 夯实基础 ──┘ └───── MVP 核心 ─────┘ └── 收尾 ──┘
```

### 依赖关系

```
Step 1 ──→ Step 2 ──→ Step 3 ──→ Step 4
(无依赖)   (需Step1   (需Step2   (需Step3
           确认工具   的Spec     ERC报告
           可用)      格式)      格式)
```

### 完成后效果

给定一个芯片型号（如 STC89C52RC），完整闭环：

```
search_lcsc → fetch_datasheet → parse_datasheet
      ↓
Claude 读手册章节 → 生成 Design Spec JSON
      ↓
Claude 生成 BOM 表（Markdown）
      ↓
用户画好原理图 → 导出 .eprj2
      ↓
parse_jlc_project → extract_netlist → ERC --spec design_spec.json
      ↓
ERC 报告：❌错误 / ⚠️警告 / 💡建议 + 可执行修复指令
      ↓
用户按修复指令改原理图 → 重新检查 → 通过 ✅
```

---

## 不在 MVP 范围

- EasyEDA Pro 在线扩展（TypeScript / pro-api-sdk）— Step 1-4 完成后再启动
- Phase 3（PCB DRC）、Phase 4（制造 BOM）、Phase 5（调试）
- 多页原理图支持
- 专业版 `.epro2` 深度解析
- 自动修改原理图（只建议，不自动改）
