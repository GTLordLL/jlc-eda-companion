# 嘉立创 EDA 官方 GitHub 开源仓库索引

> 更新日期：2026-06-14  
> 数据来源：`github.com/easyeda` 组织页 (通过代理直连抓取)  
> 扩展安装市场：`https://ext.lceda.cn`

---

## 一、总览

`easyeda` GitHub 组织下共发现 **28 个公开仓库**，全部为嘉立创/立创 EDA 官方开发。

核心分类：

| 类别 | 仓库数 | 说明 |
|------|--------|------|
| 🧠 **AI / LLM** | 4 | AI 智能助手、建库、文档生成、知识库 |
| 🔌 **开发工具** | 2 | pro-api-sdk (SDK)、api-debug-tool |
| ⚡ **原理图** | 2 | 网表→原理图、SPICE 仿真 |
| 🏗️ **PCB 工具** | 8 | 扇出、丝印、线圈、Gerber 查看、FreeCAD/Blender 集成 |
| 🔗 **外部集成** | 4 | KiRouting、KiPIDA、PLM、通用外部工具框架 |
| 📊 **报告/BOM** | 3 | HTML BOM、设计报告、PCB 价格计算 |
| 🛠️ **其他** | 5 | 批量放置、器件盒、格式转换、二维码、时序图 |

---

## 二、全部仓库详情

### 🧠 AI / LLM 扩展

| 仓库 | ⭐ | 更新 | 语言 | 描述 |
|------|-----|------|------|------|
| **[eext-easyeda-api-agent](https://github.com/easyeda/eext-easyeda-api-agent)** | 16 | 2025-12 | TypeScript | ⭐ 基于大模型(DeepSeek)的扩展API助手，自然语言/语音 → EDA API 调用 |
| **[eext-ai-library-builder](https://github.com/easyeda/eext-ai-library-builder)** | 16 | 2026-03 | TypeScript | AI 智能建库，从数据手册 PDF 自动生成符号+封装 (v2.3.1) |
| **[eext-chat-with-ai-kimi](https://github.com/easyeda/eext-chat-with-ai-kimi)** | 16 | 2025-12 | TypeScript | Kimi AI 助手，支持元件查询和网表解析 |
| **[eext-docs-generator](https://github.com/easyeda/eext-docs-generator)** | 0 | 2026-06 | HTML | 基于 LLM 的项目文档自动生成 |
| **[eext-knowledge-base](https://github.com/easyeda/eext-knowledge-base)** | 16 | 2025-12 | TypeScript | 本地 Embedding 模型的 RAG 知识库 |

### 🔌 开发工具

| 仓库 | ⭐ | 更新 | 语言 | 描述 |
|------|-----|------|------|------|
| **[pro-api-sdk](https://github.com/easyeda/pro-api-sdk)** | 45 | 2026-06 | TypeScript | 🔑 **官方扩展开发 SDK** (v1.3.2, Apache 2.0)，含 esbuild 构建、ESLint 规则、`@jlceda/pro-api-types` 类型包 |
| **[eext-api-debug-tool](https://github.com/easyeda/eext-api-debug-tool)** | 16 | 2025-12 | TypeScript | 扩展 API 调试工具，请求/响应查看、状态监控 |

### ⚡ 原理图相关

| 仓库 | ⭐ | 更新 | 语言 | 描述 |
|------|-----|------|------|------|
| **[eext-generate-schematic-from-netlist](https://github.com/easyeda/eext-generate-schematic-from-netlist)** | 1 | 2026-06 | TypeScript | 📌 **网表 → 原理图**自动重建，支持 .json/.enet 格式 |
| **[eext-simulation-with-ngspice](https://github.com/easyeda/eext-simulation-with-ngspice)** | 16 | 2025-12 | TypeScript | NGspice 原理图仿真 |

### 🏗️ PCB 工具

| 仓库 | ⭐ | 更新 | 语言 | 描述 |
|------|-----|------|------|------|
| **[eext-pad-fanout](https://github.com/easyeda/eext-pad-fanout)** | 0 | 2026-06 | TypeScript | 焊盘扇出过孔 |
| **[eext-pad-expand-helper](https://github.com/easyeda/eext-pad-expand-helper)** | 0 | 2026-06 | TypeScript | 焊盘阻焊外扩 / 禁止铺铜区域 |
| **[eext-coil-creator](https://github.com/easyeda/eext-coil-creator)** | 0 | 2026-06 | HTML | NFC/无线充电 PCB 线圈生成 |
| **[eext-generate-silkscreen](https://github.com/easyeda/eext-generate-silkscreen)** | 2 | 2026-06 | HTML | 按焊盘网络名生成丝印 |
| **[eext-dynamic-fill-region-for-silkscreen](https://github.com/easyeda/eext-dynamic-fill-region-for-silkscreen)** | 0 | 2026-06 | TypeScript | 动态丝印填充 |
| **[eext-gerber-viewer](https://github.com/easyeda/eext-gerber-viewer)** | 0 | 2026-06 | HTML | Gerber 文件查看器，支持拖拽预览 |
| **[eext-pcb-render-with-blender](https://github.com/easyeda/eext-pcb-render-with-blender)** | 6 | 2026-06 | Python | Blender 渲染 PCB |
| **[eext-mcad-integration-with-freecad](https://github.com/easyeda/eext-mcad-integration-with-freecad)** | 0 | 2025-05 | Python | WebSocket → FreeCAD 3D 查看/编辑 |

### 🔗 外部工具集成

| 仓库 | ⭐ | 更新 | 语言 | 描述 |
|------|-----|------|------|------|
| **[eext-external-tool-integration-demo](https://github.com/easyeda/eext-external-tool-integration-demo)** | 0 | 2026-05 | TypeScript | 🔗 **通用外部工具集成框架** — HTTP API 推送文件/网表/3D到第三方工具 |
| **[eext-kirouting-integration](https://github.com/easyeda/eext-kirouting-integration)** | 16 | 2025-12 | TypeScript | KiCad Routing Tools (Rust A* 自动布线引擎) → EasyEDA |
| **[eext-kipida-integration](https://github.com/easyeda/eext-kipida-integration)** | 1 | 2025-05 | Python | KiPIDA DC 电源完整性 (PI) 分析 |
| **[eext-plm-integration-demo](https://github.com/easyeda/eext-plm-integration-demo)** | 1 | 2025-05 | HTML | PLM 对接参考案例 |

### 📊 报告 / BOM

| 仓库 | ⭐ | 更新 | 语言 | 描述 |
|------|-----|------|------|------|
| **[eext-interactive-html-bom](https://github.com/easyeda/eext-interactive-html-bom)** | 16 | 2025-12 | TypeScript | 基于 InteractiveHtmlBom 的交互式 BOM |
| **[eext-export-design-report](https://github.com/easyeda/eext-export-design-report)** | 2 | 2026-05 | TypeScript | PCB 统计报告（网络长度、差分对等） |
| **[eext-pcb-price-calculator](https://github.com/easyeda/eext-pcb-price-calculator)** | 0 | 2026-06 | HTML | PCB 价格计算器 |

### 🛠️ 其他

| 仓库 | ⭐ | 更新 | 语言 | 描述 |
|------|-----|------|------|------|
| **[eext-batch-place-components](https://github.com/easyeda/eext-batch-place-components)** | 4 | 2026-06 | TypeScript | 坐标文件批量放置元件 |
| **[eext-component-box](https://github.com/easyeda/eext-component-box)** | 1 | 2026-06 | JavaScript | 智能器件盒，管理 LCSC 购买的器件 |
| **[eext-format-convert](https://github.com/easyeda/eext-format-convert)** | 0 | 2026-06 | TypeScript | Xpedition 库格式 → EasyEDA |
| **[eext-export-design-archive](https://github.com/easyeda/eext-export-design-archive)** | 1 | 2026-05 | HTML | 批量导出工程/库文件压缩包 |
| **[eext-qrcode-generator](https://github.com/easyeda/eext-qrcode-generator)** | 0 | 2026-06 | HTML | 二维码生成 |
| **[eext-timing-diagram-tool](https://github.com/easyeda/eext-timing-diagram-tool)** | 1 | 2026-06 | JavaScript | WaveDrom 时序图绘制 |
| **[eext-note-tools](https://github.com/easyeda/eext-note-tools)** | 1 | 2025-05 | HTML | Markdown + LaTeX 文档工具 |
| **[easyeda-documents](https://github.com/easyeda/easyeda-documents)** | 100 | 2026-06 | CSS | EasyEDA Std 教程文档 |

---

## 三、官方 Extension API 能力矩阵

### 3.1 NPM 包

```bash
npm install @jlceda/pro-api-types    # API 类型定义 (v0.2.58, Apache 2.0)
```

### 3.2 API 类一览

#### 原理图 API (`ISCH_*`)

| 类 | 用途 |
|----|------|
| `ISCH_PrimitiveComponent` | 原理图元件 |
| `ISCH_PrimitiveComponentPin` | 元件引脚 |
| `ISCH_PrimitiveWire` | 导线 |
| `ISCH_PrimitiveBus` | 总线 |
| `ISCH_PrimitivePolygon` | 多边形 |
| `ISCH_PrimitiveArc` / `Circle` / `Rectangle` | 几何图形 |
| `ISCH_PrimitiveText` | 文本 |
| `ISCH_PrimitivePin` | 独立引脚 |
| `ISCH_PrimitiveAttribute` | 属性 |
| `ISCH_PrimitiveCbbSymbolComponent` | CBB 符号元件 |

#### PCB API (`IPCB_*`)

| 类 | 用途 |
|----|------|
| `IPCB_PrimitiveComponent` | PCB 元件 |
| `IPCB_PrimitivePad` | 焊盘 |
| `IPCB_PrimitiveVia` | 过孔 |
| `IPCB_PrimitiveLine` / `Arc` | 走线/圆弧 |
| `IPCB_PrimitiveFill` / `Region` / `Polygon` | 填充/区域/多边形 |
| `IPCB_PrimitivePour` / `Poured` | 铺铜 |
| `IPCB_PrimitiveString` | 字符串 |
| `IPCB_PrimitiveDimension` | 尺寸标注 |
| `IPCB_PrimitiveImage` | 图片 |

#### 管理 API (`DMT_*`)

| 类 | 用途 |
|----|------|
| `DMT_Schematic` | 原理图文档管理 |
| `DMT_Pcb` | PCB 文档管理 |
| `DMT_Board` | 电路板管理 |
| `DMT_Project` | 工程管理 |
| `DMT_Folder` | 文件夹管理 |
| `DMT_Workspace` | 工作区 |
| `DMT_EditorControl` | 编辑器控制 |
| `DMT_SelectControl` | 选择控制 |
| `DMT_Event` | 事件系统 |
| `DMT_Panel` | 面板管理 |
| `DMT_Team` | 团队管理 |

#### 库 API (`LIB_*`)

| 类 | 用途 |
|----|------|
| `LIB_Device` | 器件库 |
| `LIB_Footprint` | 封装库 |
| `LIB_Cbb` | CBB 库 |
| `LIB_3DModel` | 3D 模型库 |
| `LIB_Classification` | 分类 |
| `LIB_LibrariesList` | 库列表 |
| `LIB_PanelLibrary` | 面板库 |
| `LIB_SelectControl` | 选择控制 |

#### 顶层 API

| 对象 | 用途 |
|------|------|
| `EDA` | 全局命名空间 |
| `eda.sys_Dialog` | 系统对话框 |
| `eda.sys_I18n` | 国际化 |

### 3.3 API 文档地址

- 开发指南：`https://prodocs.lceda.cn/cn/api/guide/`
- API 参考：`https://prodocs.lceda.cn/cn/api/reference/pro-api.html`
- 标准版 API：`https://docs.lceda.cn/cn/API/EasyEDA-API/`

---

## 四、对齐 jlc-eda-companion 项目

### 4.1 可复用的官方能力

| jlc-eda-companion 模块 | 官方对应轮子 | 复用程度 |
|------------------------|------------|---------|
| **Phase 1: LCSC 搜索** | 无（官方未开源搜索 API） | 继续自研 |
| **Phase 1: 数据手册解析** | `eext-ai-library-builder` — PDF → 符号封装 | 可参考其 PDF 解析 + LLM Prompt 策略 |
| **Phase 1: BOM 生成** | `eext-interactive-html-bom` — HTML BOM | 格式参考 |
| **Phase 2: 工程解析** | `pro-api-sdk` — `ISCH_PrimitiveComponent` API 可直接读取元件/引脚/导线 | 🟢 **高** — Extension API 可完全替代我们手动解析 `.eprj2`/`.epro2` 文件 |
| **Phase 2: 网表提取** | `ISCH_PrimitiveWire` + `DMT_Schematic` 可直接遍历连接关系 | 🟢 **高** — 比 Union-Find 拓扑推断更准确 |
| **Phase 2: ERC 检查** | `eext-chat-with-ai-kimi` 已有网表分析功能 | 🟡 **中** — 可参考其检查策略，但 ERC 规则仍需定制 |
| **Phase 3: PCB DRC** | `IPCB_*` API 可直接读取走线/焊盘/过孔 | 🟢 **高** — Extension API 原生支持 |
| **仿真** | `eext-simulation-with-ngspice` | 🟢 直接用 |
| **PI 分析** | `eext-kipida-integration` (DC Power Integrity) | 🟢 直接用 |
| **自动布线** | `eext-kirouting-integration` (KiCad A* Router) | 🟢 可直接调用 |

### 4.2 官方未覆盖的能力（仍需自研）

| 能力 | 原因 |
|------|------|
| **离线工程文件解析** | 官方 API 需要 EDA 运行中，离线场景仍需自己的 parser |
| **LCSC 搜索** | 官方未开源商城 API |
| **ERC 规则引擎** | 官方扩展只有网表分析 demo，没有系统化的 ERC 规则库 |
| **数据手册→BOM** | `eext-ai-library-builder` 是建库用的，不做 BOM 生成 |

---

## 五、战略决策：两条路径并存

### 路径 A：离线文件解析（当前实现）

```
.eprj2/.epro2/.zip → parse_jlc_project.py → extract_netlist.py → phase2_erc_check.py → Markdown 报告
```

**优势**：不需要 EDA 客户端，适合 CI/CD、批量审查、非 Windows 环境  
**劣势**：解析精度受限（引脚名推断、多页不支持）

### 路径 B：在线 Extension API（可新增）

```
EasyEDA Pro 内 → Extension 调用 ISCH_* API → 数据 → 你的 ERC 引擎 → 结果在 EDA 面板内展示
```

**优势**：100% 数据精度、可在 EDA 内直接跳转到问题位置、交互式修复  
**劣势**：必须打开 EDA Pro、需要 TypeScript 开发

### 建议策略

两条路径**并行维护**，而非二选一：

1. **保留** `parse_jlc_project.py` + `extract_netlist.py`（离线 parser，Phase 2 已完成）
2. **新增** EasyEDA Pro 扩展版 ERC（基于 `pro-api-sdk`），在 EDA 内实时检查
3. 共享 `phase2_erc_check.py` 的 **ERC 规则逻辑**（检查算法与平台无关）

---

## 六、下一步行动建议

1. 🔴 **高优先级**：Clone 并研究 `eext-chat-with-ai-kimi` 的网表分析实现
2. 🔴 **高优先级**：基于 `pro-api-sdk` 搭建 ERC 扩展原型
3. 🟡 **中优先级**：研究 `eext-ai-library-builder` 的 PDF 解析 + LLM Prompt 工程
4. 🟡 **中优先级**：研究 `eext-external-tool-integration-demo` — 看能否把 Python ERC 引擎作为外部工具注册
5. 🟢 **低优先级**：参考 `eext-generate-schematic-from-netlist` 的网表格式定义
