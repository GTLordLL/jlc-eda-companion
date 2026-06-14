# Design Spec 生成指引 — 从数据手册到结构化规约

> 本文件指导 Claude 将数据手册知识转换为 **Design Spec JSON**（Phase 1→2 的桥梁格式）。
> 角色：手册分析师 — 从手册提取可被 ERC 引擎执行的规则。

---

## 一、什么是 Design Spec

Design Spec 是把数据手册中的"参考电路要求"结构化为 JSON，让 Phase 2 的 ERC 引擎能自动逐项对照检查。

```
数据手册 PDF → parse_datasheet.py → 章节 .md → Claude 阅读 → Design Spec JSON
                                                                    │
                                                    ┌───────────────┘
                                                    ▼
                                          phase2_erc_check.py --spec spec.json
                                                    │
                                                    ▼
                                          "手册 §3.4.9 要求 U2 Pin44 有 100nF 去耦"
                                          "实际原理图中 U2 Pin44 (VCC+5V) 未检测到 100nF ❌"
```

**没有 Design Spec 时**：ERC 只知道"IC 的 VCC 引脚应该有个电容"，不知道是 100nF 还是 10µF。
**有了 Design Spec 后**：ERC 知道"手册 §3.4.9 要求 100nF 去耦电容"，精确检查。

---

## 二、何时生成 Design Spec

**触发条件**：Phase 1 已完成数据手册下载和解析（parse_datasheet.py 成功），且用户意图进入方案设计。

**典型对话流**：
1. 用户运行 parse_datasheet → 获得 chapters
2. Claude 读取 index.md + 🟢 标注章节
3. Claude 逐类别提取需求 → 生成 Design Spec JSON
4. 运行 `design_spec.py validate` 自检
5. 将合法的 spec 保存为 `design_spec.json`

**重要**：如果 parse_datasheet.py 超时（如大型中文 PDF），可使用 pdftotext 文本 + WebSearch 作为备用手册来源。Design Spec 的核心是内容准确，不要求来源格式统一。

---

## 三、逐类别提取指南

### 3.1 `decoupling` — 去耦电容

**去哪里找**：电气特性（Electrical Characteristics）、典型应用（Typical Application）、电源（Power Supply）章节。

**提取什么**：
- 每个 VCC/VDD 引脚需要多大的去耦电容（通常是 100nF）
- 是否需要额外的大电容组合（如 10µF + 100nF）
- 放置要求（如"尽量靠近 VCC 引脚，5mm 以内"）

**rule 字段说明**：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `target_chip` | str | ✅ | 目标芯片位号（如 U2） |
| `pin_pattern` | str | ✅ | 引脚名匹配模式（正则，如 `VCC\|VDD`） |
| `cap_value` | str | ✅ | 电容值字符串（如 `"100nF"`） |
| `cap_value_f` | float | ✅ | 电容值（法拉），如 `1e-7` |
| `tolerance` | float | | 允许偏差，默认 0.2（±20%） |
| `placement` | str | | 放置要求：`"near_pin"` |
| `distance_mm` | float | | 放置距离（mm） |

**示例**：
```json
{
  "id": "decoupling_u2_vcc_pin44",
  "category": "decoupling",
  "severity": "error",
  "description": "STC89C52RC Pin44 (MCU-VCC) 需 100nF 去耦电容，就近放置",
  "rule": {
    "target_chip": "U2",
    "pin_pattern": "VCC|VDD",
    "cap_value": "100nF",
    "cap_value_f": 1e-7,
    "tolerance": 0.2,
    "placement": "near_pin",
    "distance_mm": 5
  },
  "source": "STC89/90系列技术手册 §3.4.9 LQFP44最小系统"
}
```

**注意事项**：
- 如果一个芯片有多个 VCC 引脚，每个引脚都应有去耦电容
- 若手册推荐大+小电容组合（10µF+100nF），拆为两条 requirement

---

### 3.2 `pullup` — 上拉电阻

**去哪里找**：复位电路（Reset）、引脚描述（Pin Description）、典型应用（Typical Application）。

**提取什么**：
- 哪些引脚需要上拉（RST, EN, I2C, BOOT...）
- 上拉电阻值（如 10KΩ）
- 上拉到哪个电源轨（通常是 VCC）

**rule 字段说明**：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `target_chip` | str | ✅ | 目标芯片位号 |
| `pin_pattern` | str | ✅ | 引脚名匹配模式（正则，如 `RST\|NRST`） |
| `resistor_value` | str | ✅ | 电阻值字符串（如 `"10KΩ"`） |
| `resistor_ohm` | float | ✅ | 电阻值（欧姆），如 `10000` |
| `pull_target` | str | ✅ | 上拉目标：`"VCC"` 或特定网络名 |

**示例**：
```json
{
  "id": "pullup_rst",
  "category": "pullup",
  "severity": "error",
  "description": "RST 引脚需 10KΩ 上拉到 VCC",
  "rule": {
    "target_chip": "U2",
    "pin_pattern": "RST",
    "resistor_value": "10KΩ",
    "resistor_ohm": 10000,
    "pull_target": "VCC"
  },
  "source": "§5.1.2 复位电路参考设计"
}
```

---

### 3.3 `pulldown` — 下拉电阻

**去哪里找**：Boot 配置、引脚描述、模式选择。

**提取什么**：
- 哪些引脚需要下拉到 GND（如 BOOT0）
- 下拉电阻值

**rule 字段**：与 pullup 相同，`pull_target` 为 `"GND"`。

**示例**：
```json
{
  "id": "pulldown_boot0",
  "category": "pulldown",
  "severity": "warning",
  "description": "BOOT0 引脚需 10KΩ 下拉到 GND（从 Flash 启动）",
  "rule": {
    "target_chip": "U1",
    "pin_pattern": "BOOT0",
    "resistor_value": "10KΩ",
    "resistor_ohm": 10000,
    "pull_target": "GND"
  },
  "source": "§3.5 Boot Configuration"
}
```

---

### 3.4 `crystal` — 晶振负载电容

**去哪里找**：时钟（Clock）、振荡器（Oscillator）章节。

**提取什么**：
- 晶振频率（如 12MHz）
- 负载电容 CL（如 20pF）
- 杂散电容估算值（通常 3-5pF）
- 手册推荐的负载电容值

**rule 字段说明**：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `target_chip` | str | ✅ | 目标芯片位号 |
| `pin_pattern` | str | ✅ | 晶振引脚匹配（如 `XTAL\|OSC`） |
| `load_cap_pf` | float | ✅ | 晶振负载电容 CL（pF） |
| `frequency` | str | | 晶振频率（如 `"12MHz"`） |
| `stray_cap_pf` | float | | 杂散电容估算值，默认 3.0pF |
| `suggested_caps_pf` | float | | 建议的 C1/C2 电容值（pF） |

**计算关系**：
```
CL = (C1 × C2) / (C1 + C2) + Cstray
对称设计 C1=C2=C:
CL = C/2 + Cstray  →  C = 2 × (CL − Cstray)
```

**示例**：
```json
{
  "id": "crystal_load_12mhz",
  "category": "crystal",
  "severity": "warning",
  "description": "12MHz 晶振，CL=20pF，Cstray=3pF，建议 C1=C2=34pF → 取标准值 33pF",
  "rule": {
    "target_chip": "U2",
    "pin_pattern": "XTAL",
    "frequency": "12MHz",
    "load_cap_pf": 20.0,
    "stray_cap_pf": 3.0,
    "suggested_caps_pf": 33.0
  },
  "source": "§25.6 外部晶振电路；计算：C=2×(20-3)=34pF→E6=33pF"
}
```

**注意事项**：
- 如果手册未给出精确 CL 值，用典型值 20pF 并标注 `"confidence": "low"`
- 不同晶振型号 CL 不同，需对照晶振自身的数据手册

---

### 3.5 `power_feedback` — 电源反馈分压

**去哪里找**：电源（Power Supply）、电压调节器（Voltage Regulator）章节。

**提取什么**：
- Vref（反馈参考电压，如 1.25V for AMS1117）
- 目标 Vout（如 3.3V）
- 手册推荐的反馈电阻值（如 R1=52.3K, R2=10K）

**rule 字段说明**：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `target_chip` | str | ✅ | 电源芯片位号（如 U1） |
| `vref` | float | ✅ | 反馈参考电压（V） |
| `target_vout` | float | ✅ | 目标输出电压（V） |
| `r1_ohm` | float | | 手册推荐 R1 值（欧姆），如有 |
| `r2_ohm` | float | | 手册推荐 R2 值（欧姆），如有 |

**计算关系**：
```
Vout = Vref × (1 + R1/R2)
R1 = R2 × (Vout/Vref − 1)
```

**示例**：
```json
{
  "id": "power_feedback_3v3",
  "category": "power_feedback",
  "severity": "warning",
  "description": "AMS1117-3.3 固定输出，无需外部反馈分压电阻",
  "rule": {
    "target_chip": "U1",
    "vref": 1.25,
    "target_vout": 3.3
  },
  "source": "AMS1117 数据手册：固定输出版本，内部集成反馈电阻"
}
```

**注意事项**：
- **固定输出 LDO**（如 AMS1117-3.3）无 FB 引脚 → 标记 `"notes": "fixed_output"`
- **可调输出 LDO**（如 AMS1117-ADJ）有 ADJ 引脚 → 必须提取反馈电阻要求

---

### 3.6 `pin_termination` — 引脚端接要求

**去哪里找**：引脚描述（Pin Description）、引脚功能表（Pin Function Table）。

**提取什么**：
- 哪些引脚有特殊的端接要求
- EA/VPP 是否必须接 VCC
- NC（No Connect）引脚是否必须浮空
- 测试引脚是否应接地

**rule 字段说明**：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `target_chip` | str | ✅ | 目标芯片位号 |
| `pin_pattern` | str | ✅ | 引脚名匹配模式 |
| `termination` | str | ✅ | 端接类型（见下） |
| `resistor_value` | str | | 如需电阻，电阻值字符串 |
| `resistor_ohm` | float | | 如需电阻，电阻值（欧姆） |
| `notes` | str | | 补充说明 |

**termination 有效值**：
- `"pullup_to_vcc"` — 需上拉到 VCC
- `"pulldown_to_gnd"` — 需下拉到 GND
- `"must_connect"` — 必须连接（不能浮空）
- `"must_float"` — 必须浮空（NC 引脚）
- `"connect_to_net"` — 必须连接到特定网络

**示例**：
```json
{
  "id": "termination_ea_vpp_high",
  "category": "pin_termination",
  "severity": "error",
  "description": "EA/VPP 必须接高电平（VCC）才能从内部 Flash 运行",
  "rule": {
    "target_chip": "U2",
    "pin_pattern": "EA",
    "termination": "pullup_to_vcc",
    "notes": "若 EA=GND，MCU 将从外部 ROM 启动（8051 兼容模式）"
  },
  "source": "§3.6 管脚说明 — EA/P4.5：内部程序存储器选择"
}
```

---

### 3.7 `pin_exclusion` — 引脚排除

**去哪里找**：引脚描述表 — 找到名字含关键词但功能完全不同的引脚。

**提取什么**：
- 哪些引脚名含关键词但功能不同（如 PSEN 含 "EN" 但不是使能引脚）
- 应从哪些 ERC 检查中排除

**rule 字段说明**：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `target_chip` | str | ✅ | 目标芯片位号 |
| `pin_pattern` | str | ✅ | 排除的引脚名模式 |
| `exclude_from_checks` | list[str] | ✅ | 从哪些检查排除（如 `["enable_pin"]`) |
| `reason` | str | ✅ | 排除原因 |

**示例**：
```json
{
  "id": "exclude_psen_from_enable_pin",
  "category": "pin_exclusion",
  "severity": "suggestion",
  "description": "PSEN# 是外部程序存储使能（Program Store Enable），非芯片Enable/复位引脚，需从 enable_pin 检查中排除",
  "rule": {
    "target_chip": "U2",
    "pin_pattern": "PSEN",
    "exclude_from_checks": ["enable_pin"],
    "reason": "PSEN 是 8051 外部程序存储器读选通信号，非 Enable/复位引脚。无外部 ROM 时可浮空"
  },
  "source": "§3.6 管脚说明 — PSEN/P4.4：外部 ROM 选通"
}
```

---

## 四、生成规范

### 4.1 id 命名

格式：`{category}_{chip}_{detail}`（kebab-case）

```
decoupling_u2_vcc        ✅
pullup_u2_rst            ✅
crystal_load_12mhz       ✅
exclude_psen_enable      ✅
decap_u2                 ❌ 太模糊
VCC_cap                  ❌ 无类别前缀
```

### 4.2 source 字段

**必填且必须精确到手册章节号。** 格式优先级：
1. 手册章节号：`§5.1.2 复位电路`
2. 手册章节+页：`§3.4.9 (p.47) LQFP44 最小系统`
3. 手册表格号：`Table 6-3: Pin Definitions`
4. 计算推导：`§CL=20pF, C=2×(20-3)=34pF→E6=33pF`

**反例**：
```
"数据手册"                     ❌ 不精确
"参考电路要求"                  ❌ 无章节号
"STC89C52RC datasheet"        ❌ 无具体章节
```

### 4.3 rule 中的数值

**字符串 + 浮点双写**（方便人类阅读 + 机器计算）：

```json
// ✅ 正确
"resistor_value": "10KΩ",
"resistor_ohm": 10000

// ✅ 正确
"cap_value": "100nF",
"cap_value_f": 1e-7

// ❌ 错误 — 只有字符串
"cap_value": "100nF"

// ❌ 错误 — 只有浮点（无人能读）
"cap_value_f": 1e-7
```

### 4.4 不确定值的处理

如果手册给出的信息不完整，标注置信度：

```json
{
  "id": "crystal_load_12mhz_uncertain",
  "category": "crystal",
  "severity": "warning",
  "description": "【置信度: LOW】手册未明确指定 12MHz 晶振的 CL 值，假定典型值 20pF",
  "rule": {
    "target_chip": "U2",
    "pin_pattern": "XTAL",
    "load_cap_pf": 20.0,
    "stray_cap_pf": 3.0,
    "suggested_caps_pf": 33.0,
    "confidence": "low"
  },
  "source": "§25.6 外部晶振电路：仅给出典型值 22-33pF，未针对 12MHz 指定"
}
```

---

## 五、输出流程

### 步骤 1：通读手册结构

```
Read datasheets/C8707/index.md → 了解手册有哪些章节
```

### 步骤 2：精读 PCB 相关章节

优先读取 🟢 标注的章节：
- 引脚描述 → `pin_termination`, `pin_exclusion`
- 电气特性 → `decoupling`, `power_feedback`
- 典型应用 / 参考电路 → 所有类别

### 步骤 3：按类别生成 requirements

每读完一个相关章节，立即提取对应类别的 requirement，不要等全部读完再写。

### 步骤 4：初始化和保存

```bash
# 生成模板
python tools/design_spec.py init "项目名" --core-chip <芯片名> --lcsc <编号> --package <封装> > design_spec.json

# 编辑 design_spec.json 填充真实内容

# 校验
python tools/design_spec.py validate design_spec.json --format json
```

### 步骤 5：根据校验错误修正

如果 validate 报错，根据错误提示逐一修正，直到通过。

---

## 六、完整示例

以下是一个覆盖 6 个类别的完整 Design Spec（51 单片机最小系统板）：

```json
{
  "meta": {
    "version": "1.0",
    "project_name": "51单片机最小系统板",
    "created": "2026-06-14",
    "source_datasheets": ["C8707"],
    "source_chapters": ["§3.4.9", "§3.6", "§5.1.2", "§25.6"]
  },
  "core_chips": [
    {
      "designator": "U2",
      "name": "STC89C52RC-40I",
      "lcsc": "C8707",
      "package": "LQFP-44",
      "pins": {
        "power": [{"number": 44, "name": "VCC"}],
        "ground": [{"number": 22, "name": "GND"}],
        "reset": [{"number": 9, "name": "RST"}],
        "crystal": [
          {"number": 18, "name": "XTAL2"},
          {"number": 19, "name": "XTAL1"}
        ]
      },
      "excluded_pins": [
        {
          "number": 25,
          "name": "PSEN#",
          "reason": "外部程序存储使能，非芯片Enable/复位引脚。无外部ROM时可浮空"
        }
      ]
    }
  ],
  "requirements": [
    {
      "id": "decoupling_u2_vcc",
      "category": "decoupling",
      "severity": "error",
      "description": "STC89C52RC Pin44 (MCU-VCC) 需 100nF 去耦电容，就近放置 (<5mm)",
      "rule": {
        "target_chip": "U2",
        "pin_pattern": "VCC|VDD",
        "cap_value": "100nF",
        "cap_value_f": 1e-7,
        "tolerance": 0.2,
        "placement": "near_pin",
        "distance_mm": 5
      },
      "source": "STC89/90系列技术手册 §3.4.9 LQFP44最小系统"
    },
    {
      "id": "pullup_rst_vcc",
      "category": "pullup",
      "severity": "error",
      "description": "RST 复位引脚需 10KΩ 上拉到 VCC",
      "rule": {
        "target_chip": "U2",
        "pin_pattern": "RST",
        "resistor_value": "10KΩ",
        "resistor_ohm": 10000,
        "pull_target": "VCC"
      },
      "source": "§5.1.2 复位电路参考设计：传统8051高电平上电复位 R=10KΩ"
    },
    {
      "id": "crystal_load_12mhz_x1",
      "category": "crystal",
      "severity": "warning",
      "description": "12MHz 晶振 X1，CL≈20pF → C1=C2=34pF → 标准值33pF",
      "rule": {
        "target_chip": "U2",
        "pin_pattern": "XTAL",
        "frequency": "12MHz",
        "load_cap_pf": 20.0,
        "stray_cap_pf": 3.0,
        "suggested_caps_pf": 33.0
      },
      "source": "§25.6 外部晶振电路；典型负载电容22-33pF；计算C=2×(20-3)=34pF→E6=33pF"
    },
    {
      "id": "termination_ea_high",
      "category": "pin_termination",
      "severity": "error",
      "description": "EA/VPP 引脚必须接高电平（VCC）从内部 Flash 执行程序",
      "rule": {
        "target_chip": "U2",
        "pin_pattern": "EA",
        "termination": "pullup_to_vcc",
        "notes": "若 EA=GND，MCU 从外部 ROM 启动，51 最小系统一般无外部 ROM"
      },
      "source": "§3.6 管脚说明 — EA/P4.5：内部/外部程序存储器选择"
    },
    {
      "id": "termination_p0_pullup",
      "category": "pin_termination",
      "severity": "warning",
      "description": "P0 口（P0.0-P0.7）作为 I/O 时需外接 10KΩ-4.7KΩ 上拉电阻",
      "rule": {
        "target_chip": "U2",
        "pin_pattern": "P0\\.",
        "termination": "pullup_to_vcc",
        "resistor_value": "10KΩ",
        "resistor_ohm": 10000,
        "notes": "P0 口上电复位后处于开漏模式，内部无上拉。若作为地址/数据总线使用则可不上拉"
      },
      "source": "§3.6 管脚说明 — P0 口特性"
    },
    {
      "id": "exclude_psen_enable",
      "category": "pin_exclusion",
      "severity": "suggestion",
      "description": "PSEN# 是外部 ROM 选通信号，非芯片 Enable/复位引脚，从 enable_pin 检查排除",
      "rule": {
        "target_chip": "U2",
        "pin_pattern": "PSEN",
        "exclude_from_checks": ["enable_pin"],
        "reason": "PSEN 是 8051 外部程序存储器读选通信号，非芯片 Enable。无外部 ROM 时可浮空"
      },
      "source": "§3.6 管脚说明 — PSEN/P4.4"
    }
  ]
}
```

---

## 七、与各阶段的协作

```
Phase 1 Prompt (phase1_solution_generation.md)
  └── ⑥ 生成 BOM ──→ ⑦ 生成 Design Spec JSON (本 prompt)

Phase 2 Prompt (phase2_schematic_erc.md)
  └── ERC --spec design_spec.json (Step 3 实现)
```

当 Phase 1 完成 BOM 生成后，Claude 应自动触发 Design Spec 生成流程。
