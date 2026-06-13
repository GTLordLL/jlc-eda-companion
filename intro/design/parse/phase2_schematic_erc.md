# 阶段二：原理图审查 (ERC)

> 原"阶段三：原理图设计与仿真"
> Skill 角色：🟨 审查/Linter

---

## 一、设计理念

ERC（电气规则检查）是 Skill 最核心的技术能力。理念已从"凭 Claude 硬件知识审查"转变为"对照芯片手册审查"：

```
之前：Claude 凭知识 → "EN 脚应该有上拉"           ← 可能不准确
现在：读手册 → "手册 Reference Design 里 EN 脚接了 10K 上拉到 VCC，你的原理图缺了这个电阻"  ← 可靠
```

---

## 二、工具

- `parse_jlc_project.py` — 解析工程文件，提取所有 COMPONENT、ATTR、WIRE
- `phase3_erc_check.py` — 规则引擎

### 数据流

```
.esch NDJSON
  ↓ parse_jlc_project.py
  ├── components: [{id, designator, device_uuid, lcsc_part, value, position}]
  ├── nets: [{id, name, wires: [[x1,y1,x2,y2],...]}]
  └── connections: [{component_id, pin, net_id}]  ← 需要从 WIRE 拓扑重建
```

---

## 三、ERC 检查项（首批）

1. ✅ 每个 IC 的 VCC 引脚是否就近有 100nF 去耦电容
2. ✅ EN/复位引脚是否有上拉/下拉（不能浮空）
3. ✅ I2C 总线是否有上拉电阻（SDA/SCL 到 VCC）
4. ✅ 电源芯片反馈电阻分压比例是否与目标输出电压匹配
5. ✅ 晶振是否有匹配的负载电容
6. ✅ 未连接的引脚（悬空 Net）警告
7. ✅ 电源网络是否与地短路

### 输出格式

```markdown
## ERC 审查报告：51单片机最小系统板

### ❌ 错误 (必须修复) — 0 项

### ⚠️ 警告 — 2 项
| # | 位置 | 问题 | 建议 |
|---|---|---|---|
| 1 | U1(STC89C52RC) Pin9 | NRST 引脚未接上拉电阻 | 添加 10KΩ 上拉到 VCC |
| 2 | C5(100nF) | 去耦电容距离 U1 VCC 引脚较远 (~15mm) | 移动到距离 Pin40 5mm 以内 |

### 💡 建议 — 3 项
...
```
