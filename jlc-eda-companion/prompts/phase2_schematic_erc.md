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

---

## 五、与阶段一的衔接（Design Spec）

Phase 1 生成的 **Design Spec JSON** 包含手册的结构化要求。Phase 2 ERC 可通过 `--spec` 参数消费：

```bash
# Step 3+ (即将实现): 带 Design Spec 的精确 ERC
python tools/phase2_erc_check.py netlist.json --spec design_spec.json
```

### Design Spec 驱动的精确检查（vs 通用规则）

| 通用规则（当前） | Design Spec 驱动（Step 3） |
|-----------------|-------------------------|
| "IC VCC 有 100nF 电容?" | "手册 §3.4.9 要求 U2 Pin44 有 100nF 去耦，实际检测: 无 ❌" |
| "晶振有负载电容?" | "手册 §25.6 要求 CL=20pF，实际 CLeff=26.5pF，偏差+32.5% ⚠️" |
| "EN 脚浮空?" | "NRST: 手册 §5.1.2 要求 10K 上拉 VCC，实际已接 ✅" |

### 手动交叉参考（Design Spec 未接入前）

在 Step 3 完成前，Claude 可手动执行交叉对比：

```
① 读取 Phase 1 生成的 design_spec.json
② 逐条 requirement 对照 ERC 报告中的发现
③ 区分：
   - ✅ 真问题 — 手册要求 X，实际原理图违反 X
   - ❌ 误报 — ERC 发现 Y，但手册明确定义 Y 的例外情况
   - ⚠️ 待确认 — 手册信息不完整，需查晶振/电阻自身规格书
   - 💡 低风险 — 优化建议，不修也能工作
```

### pin_exclusion 优先级最高

如果 Design Spec 包含 `pin_exclusion` 类别，这些排除规则应在所有其他检查之前应用，避免已知误报（如 PSEN# 被误判为 EN 引脚）。

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
