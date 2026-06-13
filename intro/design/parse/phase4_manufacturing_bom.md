# 阶段四：可制造性与 BOM 导出

> 原"阶段五：可制造性设计与 BOM 导出"
> Skill 角色：🟩 全自动闭环

---

## 一、工具

- `phase5_gerber_export.py` — 生成 Gerber + 钻孔 + 坐标文件
- `phase5_bom_verify.py` — BOM 核对 + LCSC 库存查询

---

## 二、BOM 检查流程

```
1. 提取所有 LCSC 编号 → 去重
2. 逐项查询立创商城库存 API
3. 标记状态：
   ✅ 有库存，基础库 (Extended Part) → 可 SMT
   ✅ 有库存，扩展库 → 需另付换料费
   ⚠️ 库存低 (<100) → 提醒
   ❌ 缺货 → 自动搜索 Pin-to-Pin 替代料
4. 输出 BOM 核对报告
```

---

## 三、替代料搜索策略

1. 同封装 (Supplier Footprint)
2. 同参数范围 (Value, 电压, 电流等)
3. 优先基础库 (JLCPCB Part Class = "Extended Part")
4. 排除当前缺货物料
