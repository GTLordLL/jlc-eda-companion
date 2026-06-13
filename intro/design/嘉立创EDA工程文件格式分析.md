# 嘉立创 EDA 工程文件格式分析

基于两个样本工程的逆向分析结果：`51单片机最小系统板`（标准版）和 `示例-波型产生与变换`（专业版）。

---

## 一、工程文件格式总览

嘉立创 EDA 有两代文件格式，分别对应标准版和专业版：

| 版本 | 主文件后缀 | 容器格式 | 备份格式 |
|---|---|---|---|
| **标准版** | `.eprj2` | SQLite 3 数据库 | `.zip` 包（含 NDJSON 文件） |
| **专业版** | `.epro2` | Zip 压缩包 | 同主文件 |

---

## 二、标准版 (.eprj2) — SQLite 数据库

### 核心表结构

| 表名 | 用途 | 关键列 |
|---|---|---|
| `projects` | 工程元数据 | `uuid`, `name`, `content`, `owner_uuid` |
| `schematics` | 原理图列表 | `uuid`, `name`, `sheet_count`, `project_uuid` |
| `devices` | 器件定义（符号+封装+立创编号） | `uuid`, `title`, `display_title`, `source` |
| `components` | 元件图形数据（base64编码） | `uuid`, `title`, `docType`, `dataStr` |
| `attributes` | 所有属性键值对（**含 LCSC 编号**） | `key`, `value`, `device_uuid` |
| `project_structures` | 工程结构树（JSON） | `structure` (JSON: boards/schematics/sheets/pcbs) |
| `documents` | 文档/图纸 | `uuid`, `docType`, `dataStr`, `schematic_uuid` |
| `resources` | 嵌入资源 | `hash`, `dataStr`, `filename` |

### 关键数据流

```
attributes (key="Supplier Part", value="C6186")
    └── device_uuid → devices.uuid
                         └── devices.title = "AMS1117-3.3_C6186"
                         └── devices.source → 立创云端器件 ID
```

### LCSC 编号的提取路径

1. `attributes` 表中 `key = "Supplier Part"` → `value = "C6186"`
2. 再通过 `device_uuid` 关联到 `devices` 表获取完整器件信息
3. `attributes` 中还有 `key = "LCSC Part Name"`, `"Manufacturer"`, `"Manufacturer Part"` 等

### 标准版备份 Zip 结构

```
backup.zip
├── project.json        # 工程元数据 + devices/symbols/footprints/sheets 汇总
├── meta.json           # 版本信息
├── SHEET/{schematic_uuid}/
│   └── {sheet_id}.esch  # 原理图图纸 (NDJSON)
├── PCB/{pcb_uuid}.epcb  # PCB 数据 (NDJSON)
├── SYMBOL/{uuid}.esym   # 原理图符号 (NDJSON)
├── FOOTPRINT/{uuid}.efoo # PCB 封装 (NDJSON)
├── BLOB/                 # 二进制资源
├── FONT/                 # 字体
├── POUR/                 # 敷铜数据
├── PANEL/                # 拼版数据
└── INSTANCE/             # 复用模块实例
```

---

## 三、核心数据文件格式 — NDJSON (Newline Delimited JSON)

`.esch`、`.epcb`、`.esym`、`.efoo` 文件格式相同：**每行一个 JSON 数组**。

### 3.1 原理图文件 (.esch)

```
["DOCTYPE","SCH","1.1"]
["HEAD",{"originX":0,"originY":0,"version":"2","maxId":7810}]
```

#### COMPONENT 条目（原理图元件实例）
```json
["COMPONENT","e270","电阻.1",210,745,0,0,{},0]
//          [0]id   [1]名称  [2]X  [3]Y [4]旋转 [5]镜像 [6]extra [7]flag
```

#### ATTR 条目（属性绑定）
```json
// 位号
["ATTR","e273","e270","Designator","R1",null,1,200,750,null,"st4",0]
// 值
["ATTR","e275","e270","Value","5.1K",null,null,null,null,null,"st4",0]
// 关联Device UUID
["ATTR","e283","e270","Device","c5d04b2475df4b38b2f34ff206944576",0,0,null,null,0,"st5",0]
// 网络名
["ATTR","e1834","e1832","NET","GND",0,0,345,235,90,"st4",0]
```

#### WIRE 条目（连线）— 关键！
```json
["WIRE","e344",[[190,745,190,735],[165,735,190,735]],"st7",0]
//        [0]id  [1]线段数组([[x1,y1,x2,y2],...])    [2]样式  [3]flag
```

### 3.2 PCB 文件 (.epcb)

```
["DOCTYPE","PCB","1.8"]
["HEAD",{"editorVersion":"2.2.43.2","importFlag":0}]
["CANVAS",0,0,"mil",5,5,5,5,1,1,2,0,5]
```

#### COMPONENT 条目（PCB 元件实例）
```json
["COMPONENT","e27",0,1,511.811,-1141.7323,180,{"Unique ID":"gge4","Reuse Block":"","Group ID":"","Channel ID":"$1e530"},1]
//          [0]id  [1]层 [2]面 [3]X     [4]Y        [5]旋转 [6]extra对象                         [7]锁定
// 通过 extra.Channel ID 的 "$1e530" 与原理图中的 COMPONENT "e530" 关联
```

#### VIA 条目（过孔）
```json
["VIA","e726",0,"GND","",1925,-685,12.0078,24.0158,0,null,null,0,[]]
//       [0]id [1]类型 [2]网络 [3]名称 [4]X [5]Y [6]内径 [7]外径 ...
```

### 3.3 器件定义文件 (.esym / .efoo)

符号和封装库文件，格式与原理图类似，包含图形元素（PIN、PAD、LINE、ARC 等）。

---

## 四、专业版 (.epro2) — Zip 压缩包

```
.epro2 (Zip)
├── project2.json     # 元数据 {"title":"...", "editorVersion":"...", "tags":"[]"}
├── IMAGE/            # 工程截图
└── {name}.epru       # 主数据文件 (NDJSON + 特殊分隔符)
```

### .epru 文件格式

每行格式：`{type-header}||{json-body}|`

- 用 `||` 分隔头部和数据体
- 开头是 `{"type":"DOCHEAD",...}` 条目标记文档开始
- 然后是 `{"type":"LAYER",...}`, `{"type":"META",...}`, `{"type":"ATTR",...}` 等

#### META 条目 — 器件定义（含 LCSC 信息！）
```json
{"type":"META","ticket":149,"id":"META"}||{"title":"1N4148TR","tags":[],"source":"...","attributes":{"LCSC Part Name":"100V 200mA 4ns","Supplier Part":"C84410","Manufacturer":"onsemi(安森美)","Manufacturer Part":"1N4148TR","Supplier Footprint":"DO-35","JLCPCB Part Class":"Extended Part","Datasheet":"https://...","Supplier":"LCSC","Add into BOM":"yes","Convert to PCB":"yes","Symbol":"888c79afffcf4073951b3891116f2507","Designator":"D?","Footprint":"a0dc422ae734465abe307a4f9ff0b2c4","3D Model":"...",...}}|
```

#### ATTR 条目 — 网络/属性
```json
{"type":"ATTR","ticket":166,"id":"e4382"}||{"groupId":"","locked":false,"zIndex":48,"parentId":"e4380","key":"NET","value":"GND",...}|
```

---

## 五、两种格式对比总结

| 维度 | 标准版 (.eprj2) | 标准版备份 (.zip) | 专业版 (.epro2) |
|---|---|---|---|
| 容器 | SQLite | Zip | Zip |
| 原理图数据 | components.dataStr (base64) | .esch NDJSON | .epru NDJSON+`\|\|` |
| PCB数据 | components.dataStr (base64) | .epcb NDJSON | .epru 内 |
| LCSC编号位置 | attributes 表 | project.json devices | .epru META.attributes |
| 可程序化读取难度 | ⭐⭐ (需SQLite) | ⭐ (纯文本) | ⭐ (纯文本) |
| EDA版本 | 标准版 | 标准版导出 | 专业版 |

---

## 六、对 Skill 开发的关键启示

### 立创代料检查器的实现路径

1. **读取工程文件**：
   - 标准版：解压备份 Zip → 读 `project.json` → 遍历 `devices` → 提取 `attributes["Supplier Part"]` 和 `attributes["Supplier Footprint"]`
   - 专业版：解压 `.epro2` → 读 `.epru` → grep `"type":"META"` 行 → 解析 `attributes.Supplier Part`

2. **LCSC 编号格式**：`C` + 数字（如 `C6186`, `C2896063`, `C9900017627`），这是立创商城唯一的物料编号

3. **完整 BOM 信息在一条记录里**：
   ```json
   {
     "Supplier Part": "C6186",        // LCSC 编号
     "Manufacturer": "...",            // 制造商
     "Manufacturer Part": "...",      // 制造商型号
     "Supplier Footprint": "...",     // 封装
     "JLCPCB Part Class": "Extended Part", // 基础库/扩展库
     "Datasheet": "https://...",      // 规格书
     "Value": "3.3V",                 // 参数值
     "Designator": "U?"               // 位号前缀
   }
   ```

### 原理图审查 (ERC) 的实现路径

1. 解析 `.esch` 文件中的 `COMPONENT` → `ATTR("Device")` → `ATTR("Designator")` 映射
2. 解析 `WIRE` 条目重建网络连接拓扑
3. 解析 `ATTR("NET")` 获取网络名（GND, VCC, VCC+5V 等）
4. 对照知识库规则（如"ESP32 EN 脚必须有上拉"、"去耦电容必须靠近 VCC"）进行检查

### PCB 诊断的实现路径

1. 解析 `.epcb` 文件中的 `TRACK`（走线）、`VIA`（过孔）、`PAD`（焊盘）
2. 通过 `COMPONENT` 的 `extra.Channel ID` → 原理图 `COMPONENT` 关联
3. 检查走线宽度是否满足载流要求
