# 阶段一：解决方案生成 — 行为指引

> 本文件指导 Claude 在 PCB 设计的「解决方案生成」阶段如何行动。
> 角色：🟩 主动引导 + 辅助

---

## 一、阶段检测

当用户的对话中出现以下关键词时，判定为阶段一：
- 芯片选型、元器件选型、选型
- BOM、物料清单、外围电路
- 数据手册、datasheet、规格书、手册
- 参考设计、典型应用电路
- 原理图开始、画图之前、设计方案

---

## 二、两种工作路径

### 路径A：参考方案搜索（纯 LLM 能力）

用户描述需求但未指定具体芯片 → Claude 引导需求澄清 → 给出系统架构草稿 → 建议关键芯片类型。

此路径不需要工具脚本，纯靠 Claude 推理完成。

### 路径B：芯片选型 + 外围电路设计（本阶段重点）

用户已确定芯片型号 → 按以下流程执行：

```
① 确认核心芯片
  ├── 用户提供芯片型号（如 STM32F103C8T6）
  └── 运行 search_lcsc.py 确认 LCSC 库存和价格
      命令: python tools/search_lcsc.py general <芯片型号> --format json

② 获取数据手册
  ├── 自动爬取：运行 fetch_datasheet.py 从立创商城自动下载 PDF
  │   命令: python tools/fetch_datasheet.py C<编号> --download ./datasheets --format json
  ├── 若爬取失败：LCSC 产品页手动下载（search_lcsc 结果中有 lcsc_url 字段）
  ├── 备用：制造商官网搜索
  └── 或用户提供 PDF 文件路径

③ 解析 PDF → Markdown + 章节拆分
  ├── 自动解析：运行 parse_datasheet.py 将 PDF 按章节拆分为独立 .md 文件
  │   命令: python tools/parse_datasheet.py process ./datasheets/C8734.pdf --format json
  ├── 返回: output_dir 目录路径 + chapter 列表（含 pcd_relevant 标注）
  ├── 若解析失败：LCSC 产品页直接查看（search_lcsc 结果中有 lcsc_url 字段）
  ├── 或用户提供 PDF 文件路径
  └── Claude 下一步：
      ├── 1. Read datasheets/C8734/index.md → 了解手册结构
      ├── 2. 优先读取 🟢 标注的 PCB 相关章节
      └── 3. 按需读取其他章节（引脚定义、参考电路等）

④ 从章节文件中提取结构化信息
  ├── 先读 index.md → 定位需要的关键章节（🟢 标注优先）
  ├── 电源引脚：Read 引脚描述 + 电气特性章节 → VCC/VDD 数量、电压范围、去耦方案
  ├── 关键外围：Read 典型应用电路章节 → 晶振频率+负载电容、复位电路、Boot 配置
  ├── 通讯接口：Read 引脚描述章节 → I2C/SPI/UART 对应的引脚映射
  └── 参考电路标注的外围器件值（R1=10K, C1=100nF ...）

⑤ 对每个外围器件，搜索 LCSC 有库存的具体型号
  命令:
    python tools/search_lcsc.py capacitor --search "100nF" --package 0603 --format json
    python tools/search_lcsc.py resistor --search "10K" --package 0603 --format json
    python tools/search_lcsc.py general "8MHz 3225" --format json

⑥ 对需要计算的参数，调用 compute_passive.py
  命令:
    python tools/compute_passive.py feedback-divider --vref 0.8 --vout 5 --r2 10k --format json
    python tools/compute_passive.py crystal-load --cl 20pF --format json
    python tools/compute_passive.py rc-filter --r 10k --c 100nF --format json

⑦ 生成最终 BOM 报告
```

---

## 三、parse_datasheet.py 使用说明

`parse_datasheet.py` 使用 **docling** 引擎将 PDF 转为 Markdown，按章节拆分为独立文件，并生成 `index.md` 目录索引。

### 依赖安装

```bash
pip install docling
```

首次运行 docling 会从 Hugging Face Hub 下载模型（~770MB），需要网络代理。

### 基本用法

```bash
# 完整流程：PDF → 章节文件 + index.md（推荐）
python tools/parse_datasheet.py process ./datasheets/C8734.pdf --format json

# 按 LCSC 编号自动找 PDF
python tools/parse_datasheet.py process --lcsc C8734 --format json

# 强制重新解析（忽略缓存）
python tools/parse_datasheet.py process C8734.pdf --no-cache --format json

# 仅解析 PDF → 全文 Markdown（保存缓存）
python tools/parse_datasheet.py parse C8734.pdf --format json
```

### 输出结构

```
datasheets/C8734/
├── index.md                         # 📋 完整目录 + 🟢 PCB 推荐阅读标注
├── 01_1_introduction.md
├── 02_2_description.md
├── 03_3_pinouts_and_pin_description.md        # 🟢
├── 05_5_electrical_characteristics.md         # 🟢
├── 06_6_package_information.md                # 🟢
├── 07_7_ordering_information_scheme.md
└── 08_8_revision_history.md
```

**Claude 工作流：先读 `index.md` 了解手册结构 → 定位需要的章节 → 按需 `Read` 对应 .md 文件（几KB~几十KB）。**

### JSON 输出

```json
{
  "pdf_path": ".../datasheets/C8734.pdf",
  "output_dir": ".../datasheets/C8734",
  "index_file": ".../datasheets/C8734/index.md",
  "page_count": 116,
  "parse_time_s": 86.5,
  "cached": false,
  "total_chapters": 8,
  "pcd_relevant_count": 3,
  "chapters": [
    {"number": "03", "title": "Pinouts and pin description",
     "file": "03_3_pinouts_and_pin_description.md",
     "pcd_relevant": true, "relevance_reasons": ["pinout"]}
  ]
}
```

### index.md 中的 🟢 标注

PCB 设计相关章节会自动标注 🟢 并列入推荐阅读表。标注依据章节标题匹配以下关键词：
- **引脚/管脚/端子** — Pinout
- **电气特性/工作条件/极限参数** — Electrical
- **PCB 布局/焊接/热设计** — Layout
- **封装/外形尺寸/机械尺寸** — Package
- **典型应用/参考设计** — Application

### 缓存机制

解析后自动保存 `{pdf_name}.md` 到 PDF 同目录（带 docling 元数据头）。下次解析同一 PDF 时自动读缓存，秒级命中。

---

## 四、设计原则

### 4.1 手册是唯一权威

信息源可靠性排序：
1. 芯片厂商数据手册 (PDF) ← 唯一权威
2. LCSC 产品页 (HTML) ← 辅助参考，可能有误
3. Claude 硬件知识 ← 兜底推理

**必须以手册信息为准。** 如果手册和 LCSC 产品页的参数冲突，以手册为准。

### 4.2 电路图识别：不做

数据手册中的电路图（PNG/JPG 图片）直接跳过，不尝试用 LLM 视觉识别。
手册的文字描述已包含参考电路的全部连接关系和参数值。

### 4.3 外围器件取值原则

1. **手册明确推荐** → 直接用推荐值
2. **手册给公式** → 调用 compute_passive.py 计算
3. **手册没提但通用惯例** → Claude 根据硬件知识推荐（并标注"未在手册找到依据"）

---

## 五、BOM 输出模板

完成选型后，以下格式输出 BOM：

```markdown
## 设计方案：<项目名称>

### 系统架构
[简要描述电源树和信号总线]

### 核心芯片清单
| 位号 | 芯片 | LCSC 编号 | 封装 | 用途 | 库存 | 单价 |
|------|------|-----------|------|------|------|------|
| U1 | STM32F103C8T6 | C8734 | LQFP-48 | 主控 MCU | 214k+ | ¥1.04 |

### 外围电路 BOM（基于手册参考电路）
| 位号 | 器件 | 值 | LCSC 编号 | 封装 | 数量 | 依据 |
|------|------|-----|-----------|------|------|------|
| C1-C4 | 去耦电容 | 100nF | C14663 | 0603 | 4 | 手册 §5.2: 各 VDD 引脚就近接 100nF |
| R1 | NRST 上拉 | 10KΩ | C17414 | 0603 | 1 | 手册 §4.3: NRST 内部弱上拉，外部建议 10K |
| Y1 | 晶振 | 8MHz | C12674 | SMD3225 | 1 | 手册 §4.1: HSE 4-16MHz，推荐 8MHz |
| C5,C6 | 负载电容 | 22pF | C1540 | 0603 | 2 | 手册 §4.1: CL=20pF, 计算得 2×(20-3)=34pF→取E6=33pF |

### 关键计算公式
- VOUT = Vref × (1 + R1/R2) = 0.8 × (1 + 52.5k/10k) ≈ 5.0V ✅
- CL = (C1×C2)/(C1+C2) + Cstray → 对称设计 C1=C2=34pF → E6=33pF

### 注意事项
- C1-C4 必须尽量靠近对应 VDD 引脚放置（5mm 以内）
- NRST 上拉电阻 R1 不可省略（芯片内部为弱上拉，不可靠）
```

### BOM 表列说明

| 列 | 说明 |
|----|------|
| 位号 | PCB 设计中的元件编号（U1, C1, R1...） |
| 器件 | 元器件类型（去耦电容、上拉电阻...） |
| 值 | 元件参数值（100nF, 10KΩ...） |
| LCSC 编号 | 立创商城物料编号（C + 数字），用于采购 |
| 封装 | 贴片封装（0603, 0805, SOT-223...） |
| 数量 | 该型号需要的数量 |
| 依据 | **必填**。数据手册中的出处（章节号）或计算过程 |

---

## 六、与后续阶段的衔接

方案阶段输出的 BOM 和设计参数，将直接用于：

- **阶段二 ERC**：检查原理图是否与手册一致（去耦电容是否缺少、阻值是否匹配）
- **阶段三 PCB**：检查布局是否符合手册的 Layout Guidelines
- **阶段四 BOM**：核对最终 BOM 库存状态，搜索替代料
