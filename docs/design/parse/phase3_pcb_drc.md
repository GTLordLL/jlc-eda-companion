# 阶段三：PCB 诊断

> 原"阶段四：PCB 布局与布线"
> Skill 角色：🟥 诊断辅助

---

## 一、工具

- `phase4_drc_check.py` — PCB 设计规则检查
- `phase4_layout_script.py` — 布局脚本生成

---

## 二、DRC 检查项

1. 电源走线宽度 vs 载流需求
2. 去耦电容到 IC 电源引脚的实际距离（通过坐标计算）
3. 过孔数量与载流能力
4. 差分线等长检查
5. 关键信号线是否跨分割地平面

---

## 三、布局脚本生成

```
用户: "帮我把8个LED均匀排列成半径20mm的圆"
Claude → 生成嘉立创 EDA JS 脚本：

// 嘉立创 EDA 布局脚本
const leds = ["D1","D2","D3","D4","D5","D6","D7","D8"];
const radius = 20; // mm
const centerX = 50, centerY = 50;
leds.forEach((ref, i) => {
    const angle = (2 * Math.PI * i) / leds.length;
    const x = centerX + radius * Math.cos(angle);
    const y = centerY + radius * Math.sin(angle);
    api("component", "setPosition", { designator: ref, x, y });
});
```
