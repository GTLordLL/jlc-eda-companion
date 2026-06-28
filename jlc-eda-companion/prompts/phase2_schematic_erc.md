# 阶段二：原理图 ERC 审查 — 行为指引

> 本文件指导 Claude 在 PCB 设计的「原理图审查」阶段如何行动。
> 角色：🟨 审查/Linter

---

## 一、阶段检测

当用户的对话中出现以下关键词时，判定为阶段二：
- ERC、电气规则检查、原理图审查、检查原理图、原理图检查
- 去耦电容、上拉电阻、I2C上拉、反馈电阻、晶振电容
- 悬空引脚、电源短路、浮空
- `.eprj2`、`.epro2`、`.esch`、工程文件

也可以通过用户的否定式判断（如"已经画好原理图了"、"帮我检查一下"）。

---

## 二、工作流程

### 路径A：用户提供工程文件（工具辅助）

```
① 解析工程文件 → parse_jlc_project.py
  命令: python tools/parse_jlc_project.py <工程文件> --format json

② 提取网表 → extract_netlist.py
  命令 (管线):
    python tools/parse_jlc_project.py <工程文件> --format json | \
    python tools/extract_netlist.py --stdin --format json

③ 运行 ERC 检查 → phase2_erc_check.py
  命令 (管线):
    python tools/parse_jlc_project.py <工程文件> --format json | \
    python tools/extract_netlist.py --stdin --format json | \
    python tools/phase2_erc_check.py --stdin

  或指定特定检查项:
    python tools/phase2_erc_check.py netlist.json --check decoupling,enable_pin

④ 解读报告 + 数据手册交叉参考
  ├── 读取报告中的 ❌错误 → 定位原理图中的具体元件
  ├── 若 Phase 1 有数据手册 → 对照手册验证 ERC 发现
  │   例: "U2 NRST浮空" → 查 datasheets/C8704/index.md → 引脚描述章节 → 确认NRST需要上拉
  ├── 区分真正问题 vs 误报 (见第四节)
  └── 向用户提供分优先级的修复建议
```

### 路径B：用户描述原理图（纯推理）

用户没有工程文件，只口头描述原理图 → Claude 凭硬件知识进行推理检查。此时不调用工具，直接在对话中进行。

---

## 三、工具使用说明

### 3.1 parse_jlc_project.py — 工程文件解析

支持三种格式：标准版 `.eprj2`、标准版备份 `.zip`、专业版 `.epro2`。

```bash
# JSON 输出（供管线）
python tools/parse_jlc_project.py project.eprj2 --format json

# 文本摘要（人类阅读）
python tools/parse_jlc_project.py project.eprj2
```

**输出结构：**
```json
{
  "project_name": "51单片机最小系统板",
  "format": "eprj2",
  "components": [
    {
      "id": "e270",
      "designator": "R1",
      "device_name": "Res_0805",
      "lcsc_part": "C1234",
      "value": "10K",
      "footprint": "0805",
      "x": 210, "y": 745,
      "rotation": 0, "mirror": false
    }
  ],
  "wires": [
    {
      "id": "e344",
      "segments": [[190,745,190,735],[165,735,190,735]],
      "net_name": "GND",
      "net_global_name": null
    }
  ],
  "stats": {"total_components": 52, "total_wires": 105, "lcsc_parts": 10}
}
```

### 3.2 extract_netlist.py — 网表提取

从 WIRE 线段拓扑重建网络连接，映射组件引脚到网络。

```bash
# 从文件读取
python tools/extract_netlist.py parsed_project.json --format json

# 从管线读取
python tools/parse_jlc_project.py project.eprj2 --format json | \
  python tools/extract_netlist.py --stdin --format json
```

**输出结构 (关键字段)：**
```json
{
  "nets": [
    {
      "id": "net_1",
      "name": "VCC+5V",
      "type": "power",
      "pin_count": 12,
      "is_floating": false,
      "wire_ids": ["e344", "e345"]
    }
  ],
  "components": [
    {
      "designator": "U2",
      "pins": [
        {"pin_number": "9", "net_name": "RST"},
        {"pin_number": "40", "net_name": "VCC+5V"}
      ]
    }
  ],
  "stats": {
    "total_components": 52, "total_nets": 49,
    "power_nets": 2, "signal_nets": 47, "floating_nets": 3
  }
}
```

### 3.3 phase2_erc_check.py — ERC 检查引擎

运行 7 项电气规则检查，输出 Markdown 报告。

```bash
# 全线管
python tools/parse_jlc_project.py project.eprj2 --format json | \
  python tools/extract_netlist.py --stdin --format json | \
  python tools/phase2_erc_check.py --stdin

# 指定检查项
python tools/phase2_erc_check.py netlist.json --check decoupling_cap,floating_pin

# JSON 输出
python tools/phase2_erc_check.py netlist.json --format json

# 带可执行修复建议（含 LCSC 推荐）
python tools/phase2_erc_check.py netlist.json --spec design_spec.json --actions
```

**7 项检查：**

| ID | 检查项 | 严重度 |
|----|--------|--------|
| `decoupling_cap` | IC VCC 引脚是否就近有 100nF 去耦电容 | ❌ Error |
| `enable_pin` | EN/复位引脚是否有上拉/下拉（不能浮空） | ❌ Error / ⚠️ Warning |
| `i2c_pullup` | I2C 总线是否有上拉电阻 (SDA/SCL→VCC) | ⚠️ Warning |
| `feedback_divider` | 电源芯片反馈电阻分压是否与目标输出电压匹配 | ⚠️ Warning |
| `crystal_load_cap` | 晶振是否有匹配的负载电容 | 💡 Suggestion |
| `floating_pin` | 未连接的引脚（悬空 Net）警告 | 💡 Suggestion |
| `power_ground_short` | 电源网络是否与地短路 | ❌ Error |

### 3.4 --actions: 可执行修复建议

当传入 `--actions` 标志时，ERC 引擎在生成 findings 之后，会为每条可修复的 finding 生成**结构化 JSON 修复指令**，包含操作类型、目标元件/网络、参数值、LCSC 元件推荐、手册依据。

```bash
# JSON 输出含 actions 数组
python tools/phase2_erc_check.py netlist.json --format json --actions

# 带 Design Spec 的精确修复建议
python tools/phase2_erc_check.py netlist.json --spec design_spec.json --actions

# 全管线
python tools/parse_jlc_project.py project.eprj2 --format json | \
  python tools/extract_netlist.py --stdin --format json | \
  python tools/phase2_erc_check.py --stdin --spec design_spec.json --actions
```

**10 种操作类型：**

| Action Type | 触发条件 | 说明 |
|-------------|---------|------|
| `ADD_DECOUPLING_CAP` | VCC 引脚无去耦电容 | 添加去耦电容到 GND，含 LCSC 推荐和放置建议 |
| `ADD_PULLUP_RESISTOR` | EN/RST/I2C 引脚缺上拉 | 添加上拉电阻到 VCC |
| `ADD_PULLDOWN_RESISTOR` | 引脚缺下拉 | 添加下拉电阻到 GND |
| `ADD_CRYSTAL_LOAD_CAPS` | 晶振缺负载电容 | 对称添加一对 C0G 负载电容 |
| `CHANGE_CAPACITANCE` | 电容值与手册不符 | 更换为正确容值的电容 |
| `CHANGE_RESISTANCE` | 电阻值与手册不符 | 更换为正确阻值的电阻 |
| `CONNECT_PIN_TO_NET` | 应连接但浮空的引脚 | 连接到指定网络 |
| `DISCONNECT_PIN` | 应浮空但已连接的引脚 | 断开当前连接 |
| `REVIEW_CRYSTAL_SELECTION` | 晶振 CL 严重不匹配 | 建议替代晶振型号或更换电容 |
| `REVIEW_MANUALLY` | 无法自动判断 | 需人工确认（如电源-地短路） |

**置信度说明：**
- 🟢 **high**: 手册明确要求，直接可执行（spec 驱动）
- 🟡 **medium**: 合理推断，可能有替代方案
- 🔴 **low**: 启发式猜测，需人工验证

**Markdown 报告**中带 `--actions` 时会增加 "🔧 可执行修复建议" 表格。**JSON 输出**中会新增 `actions` 数组和 `action_stats` 统计字段。

---

## 四、报告解读指引

### 4.1 区分真正问题 vs 误报

ERC 检查基于启发式规则，可能产生误报：

| 发现类型 | 常见原因 | 判断方法 |
|---------|---------|---------|
| PSEN# 被标记为 EN 引脚 | `PSEN` 含 `EN` 子串 | 查手册：PSEN 是 Program Store Enable，非使能/复位引脚 → 误报 |
| 去耦电容缺失 | VCC 网络上有 10uF/22uF 大电容，无 100nF | 大电容 ≠ 去耦电容（ESR高，高频响应差）→ 真实问题 |
| I2C 上拉缺失 | 网络名不含 SDA/SCL 但实际是 I2C | 需查手册确认引脚功能 |
| 悬空引脚 | MCU 的 NC 引脚或未使用的 GPIO | 对照手册确认是 NC 还是需要处理 |
| 反馈分压 | 固定输出 LDO 也有 FB 引脚？ | 固定输出 LDO 无 FB 引脚 → 跳过 |

### 4.2 向用户解释时的要点

1. **❌ 错误**：极可能是真实问题，建议立即修复，但可让用户手动确认
2. **⚠️ 警告**：可能是问题，建议对照数据手册确认
3. **💡 建议**：优化建议，不修也可以工作

### 4.3 与 Phase 1 数据手册交叉参考

当 Phase 1 已下载并解析了数据手册时：

```
① 从 ERC 发现中提取关键芯片的 LCSC 编号
  例：U2(STC89C52RC) → LCSC C8707

② 若 datasheets/C8707/ 已存在：
  ├── Read datasheets/C8707/index.md → 了解手册结构
  ├── 找 🟢 PCB 相关章节 (引脚描述、电气特性)
  └── 对照手册验证 ERC 发现
      例："NRST 引脚浮空" → 查 §4.3 → "NRST 需外部 10K 上拉" → ❌ 确认

③ 若数据手册不存在：
  └── 建议用户运行 Phase 1 fetch_datasheet.py + parse_datasheet.py 获取手册
```

### 4.4 修复建议解读 (--actions)

启用 `--actions` 后，报告底部会增加 "🔧 可执行修复建议" 表格，每行对应一条可执行的修复操作。

**JSON 输出的 `actions` 数组**包含结构化修复指令，可直接用于：
- 生成 BOM 补料清单（列出缺的元件及 LCSC 编号）
- 指导原理图修改（添加/更换/断开元件）
- 向用户展示"缺什么元件、换什么值"

**向用户解释时的要点：**
1. 🟢 高置信度 + spec 驱动 → 手册明确要求，强烈建议执行
2. 🟡 中置信度 → 合理建议，但可能有替代方案
3. 🔴 低置信度 → 仅作参考，需对照手册人工确认
4. 带 LCSC 推荐的动作 → 可直接采购，减少选型时间

---

## 五、与阶段一的衔接（Design Spec）

Phase 1 生成的 **Design Spec JSON** 包含手册的结构化要求。Phase 2 ERC 通过 `--spec` 参数消费，从"通用规则猜测"升级为"手册精确对照"。

### 命令

```bash
# 带 Design Spec 的精确 ERC
python tools/phase2_erc_check.py netlist.json --spec design_spec.json

# 全管线
python tools/parse_jlc_project.py project.eprj2 --format json | \
  python tools/extract_netlist.py --stdin --format json | \
  python tools/phase2_erc_check.py --stdin --spec design_spec.json
```

### Spec 启用后的增强

| 特性 | 说明 |
|------|------|
| 通用 7 项检查 | 仍然运行（向后兼容） |
| `pin_exclusion` 过滤 | 自动排除已知误报（如 PSEN# 不再被报告为 EN 引脚） |
| 手册对照段落 | 报告新增 "📋 手册对照检查" 部分，逐条对照 Design Spec 的 requirement |
| 来源标注 | 每条 spec finding 标注 `依据: 手册 §X.Y` |
| 精确值检查 | 去耦电容检查具体值（100nF±20%）而非通用 80-120nF |
| CL 偏差计算 | 晶振检查输出实际 CL_eff、目标 CL、偏差百分比 |
| 固定 LDO 跳过 | 自动识别固定输出 LDO（`rule.notes` 含 `fixed_output`）并跳过反馈检查 |
| 严重度自适应 | `confidence=low` 自动降级，条件性规则（"如用作XX"）降级为 suggestion |

### Design Spec 驱动的精确检查示例

| 通用规则（无 spec） | Design Spec 驱动（有 spec） |
|-----------------|-------------------------|
| "IC VCC 有 100nF 电容?" | "手册 §3.4.9 要求 U2 Pin44 有 100nF 去耦，VCC+5V 网络检测到 C1=10µF C2=?，无 100nF ❌" |
| "晶振有负载电容?" | "手册 §25.6 要求 CL=20pF，实际 C6=C7=47pF → CLeff=26.5pF，偏差+32.5% ❌" |
| "EN 脚浮空?" | "PSEN#: pin_exclusion 规则排除 ✅ | RST: 手册 §5.1.2 新版内置复位，外部 RC 非必须 💡" |

### pin_exclusion 优先级最高

Design Spec 的 `pin_exclusion` 规则在通用检查之后、报告生成之前应用，过滤已知误报。排除逻辑基于 `pin_pattern` + `check_id` 匹配，不要求 `component_ref` 精确匹配（因为通用检查可能将 finding 归属到同一网络上的其他元件，如排针）。

### 报告解读

- **通用规则检查** 🔍：7 项启发式规则，按 error/warning/suggestion 分级
- **手册对照检查** 📋：逐条 Design Spec requirement 对照，标注手册出处
- 底部统计显示手册要求满足率（passed/total）

### 无 spec 时的行为

不传 `--spec` 时，行为与 Step 2 完全一致：7 项通用检查，无 spec 段落，无 pin_exclusion 过滤。

## 六、与阶段一的衔接（BOM）

1. **对比推荐值 vs 实际值**：
   - Phase 1 BOM: "C1-C4: 100nF 去耦电容"
   - ERC 发现: "U2 VCC 引脚缺少 100nF 去耦电容"
   - → 确认一致，建议按 BOM 添加

2. **验证晶振负载电容**：
   - Phase 1 从手册提取: "CL=20pF, Cstray=3pF → C1=C2=34pF → E6=33pF"
   - ERC 发现: "C6=47pF, C7=47pF, CL_eff=26.5pF"
   - → 47pF 偏离推荐值 33pF，可能是设计者针对不同晶振的调整 → 建议确认晶振型号

3. **反馈分压验证**：
   - Phase 1 从手册提取: "Vref=1.25V, Vout=3.3V → R1/R2=(3.3/1.25-1)=1.64"
   - ERC 计算实际分压比 → 对比差异

---

## 七、设计限制与已知局限

1. **引脚数量推断**：基于元件位号/名称启发式推断（非从 symbol 文件精确读取）
2. **引脚名称推断**：网络名称代表引脚功能（如 `RXD` 网络 → MCU 的 RXD 引脚），非 symbol 文件中的精确引脚名
3. **多页原理图**：当前管线假设单页原理图
4. **专业版 `.epro2`**：支持基本解析，但 LCSC 映射可能不完整
5. **关键字匹配误报**：EN 引脚检测用网络名关键词，可能对 `PSEN#` 等误报
