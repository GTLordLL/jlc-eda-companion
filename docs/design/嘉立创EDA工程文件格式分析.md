# 嘉立创 EDA 工程文件格式分析

基于两个样本工程的逆向分析：`51单片机最小系统板`（标准版）和 `示例-波型产生与变换`（专业版）。

---

## 一、格式总览

嘉立创 EDA 有两代文件格式：

| 版本 | 主文件后缀 | 容器格式 | 备份/导出格式 |
|---|---|---|---|
| **标准版** | `.eprj2` | SQLite 3 | `.zip`（含 NDJSON 文件） |
| **专业版** | `.epro2` | Zip | 同主文件 |

---

## 二、标准版 (.eprj2) — SQLite 数据库

### 2.1 核心表结构

| 表名 | 用途 | 关键列 |
|---|---|---|
| `projects` | 工程元数据 | `uuid`, `name`, `content` |
| `schematics` | 原理图列表 | `uuid`, `name`, `sheet_count`, `project_uuid` |
| `devices` | 器件定义（符号+封装+属性） | `uuid`, `title`, `display_title`, `source` |
| `components` | 元件图形数据 | `uuid`, `docType`, `dataStr`（base64） |
| `attributes` | 属性键值对（**含 LCSC 编号**） | `key`, `value`, `device_uuid` |
| `project_structures` | 工程结构树（JSON） | `structure`（boards/schematics/sheets/pcbs） |
| `documents` | 文档/图纸 | `uuid`, `docType`, `dataStr` |

### 2.2 LCSC 编号提取路径

```
attributes (key="Supplier Part", value="C6186")
    └── device_uuid → devices.uuid → devices.title = "AMS1117-3.3_C6186"
```

`attributes` 表中关键 key：

| Key | 示例值 | 说明 |
|---|---|---|
| `Supplier Part` | `C6186` | **LCSC 编号（核心）** |
| `LCSC Part Name` | `3.3V 1A` | 立创物料名称 |
| `Manufacturer` | `AMS` | 制造商 |
| `Manufacturer Part` | `AMS1117-3.3` | 制造商型号 |
| `Supplier Footprint` | `SOT-223` | 封装名称 |
| `JLCPCB Part Class` | `Extended Part` | 基础库/扩展库 |
| `Datasheet` | `https://...` | 规格书 URL |
| `Value` | `3.3V` | 参数值 |
| `Designator` | `U?` | 位号前缀 |

SQL 查询示例：
```sql
SELECT a.value AS LCSC_Part, d.title
FROM attributes a JOIN devices d ON a.device_uuid = d.uuid
WHERE a.key = 'Supplier Part';
```

### 2.3 标准版备份 Zip 结构

```
backup.zip
├── project.json        # 工程元数据 + devices/symbols/footprints/sheets/boards
├── meta.json           # 版本信息
├── SHEET/{uuid}/
│   └── {id}.esch       # 原理图图纸 (NDJSON)
├── PCB/{uuid}.epcb     # PCB 数据 (NDJSON)
├── SYMBOL/{uuid}.esym  # 原理图符号库 (NDJSON)
├── FOOTPRINT/{uuid}.efoo # PCB 封装库 (NDJSON)
├── BLOB/               # 二进制资源
├── FONT/               # 字体文件
├── POUR/               # 敷铜数据
└── INSTANCE/           # 复用模块实例
```

`project.json` 顶层结构：
```json
{
  "schematics": { "uuid": { "name": "...", "sheets": [...] } },
  "pcbs": { "uuid": "PCB名称" },
  "devices": { "uuid": { "title": "...", "attributes": { "Supplier Part": "C6186", ... } } },
  "boards": { "uuid": { "title": "...", "zIndex": 1 } },
  "symbols": {}, "footprints": {}, "config": {}
}
```

---

## 三、核心数据格式 — NDJSON (Newline Delimited JSON)

`.esch`、`.epcb`、`.esym`、`.efoo` 四种文件格式相同：**每行一个 JSON 数组**。

### 3.1 原理图 (.esch)

**COMPONENT** — 原理图元件实例：
```json
["COMPONENT","e270","电阻.1",210,745,0,0,{},0]
//           id     名称    X   Y  旋转 镜像 extra flag
```

**ATTR** — 属性绑定（核心关联数据）：
```json
// 位号
["ATTR","e273","e270","Designator","R1",null,1,200,750,null,"st4",0]
// 器件 UUID
["ATTR","e283","e270","Device","c5d04b2475df...",0,0,null,null,0,"st5",0]
// 网络名
["ATTR","e1834","e1832","NET","GND",0,0,345,235,90,"st4",0]
// 全局网络名
["ATTR","e1794","e1792","Global Net Name","VCC+5V",null,null,365,510,null,"st15",0]
```

**WIRE** — 连线（网络拓扑的关键）：
```json
["WIRE","e344",[[190,745,190,735],[165,735,190,735]],"st7",0]
//        id   线段数组 [[x1,y1,x2,y2], ...]      样式 flag
```

### 3.2 PCB (.epcb)

```json
["DOCTYPE","PCB","1.8"]
["HEAD",{"editorVersion":"2.2.43.2","importFlag":0}]
["CANVAS",0,0,"mil",5,5,5,5,1,1,2,0,5]
["LAYER",1,"TOP","Top Layer",3,"#ff0000",1,"#7f0000",0.5]
```

**COMPONENT** — PCB 元件实例：
```json
["COMPONENT","e27",0,1,511.811,-1141.7323,180,{"Unique ID":"gge4","Channel ID":"$1e530"},1]
//           id    层 面 X       Y         旋转 extra                                  锁定
// extra.Channel ID "$1e530" → 原理图 COMPONENT "e530"（关联机制）
```

**VIA** — 过孔：
```json
["VIA","e726",0,"GND","",1925,-685,12.0078,24.0158,0,null,null,0,[]]
//      id   类型 网络  名  X    Y   内径    外径
```

**TRACK** — 走线（诊断线宽的关键数据，含宽度、所在层、起止坐标）。

### 3.3 符号/封装库 (.esym / .efoo)

格式与原理图类似，包含图形元素（PIN、PAD、LINE、ARC 等），用于定义器件的原理图符号和 PCB 封装。

---

## 四、专业版 (.epro2) — Zip 压缩包

```
.epro2 (Zip)
├── project2.json     # 元数据 {"title":"...", "editorVersion":"...", "tags":"[]"}
├── IMAGE/            # 工程截图
└── {name}.epru       # 主数据文件
```

### .epru 文件格式

每行格式：`{type-header}||{json-body}|`

关键条目类型：

**META** — 器件定义（⭐ 含完整 LCSC 信息）：
```json
{"type":"META","ticket":149,"id":"META"}||{
  "title": "1N4148TR",
  "source": "aacd21fc0c714f...",
  "attributes": {
    "Supplier Part": "C84410",
    "Manufacturer": "onsemi(安森美)",
    "Manufacturer Part": "1N4148TR",
    "Supplier Footprint": "DO-35",
    "JLCPCB Part Class": "Extended Part",
    "Datasheet": "https://atta.szlcsc.com/upload/public/pdf/...",
    "Supplier": "LCSC",
    "Add into BOM": "yes",
    "Convert to PCB": "yes",
    "Designator": "D?",
    "Footprint": "a0dc422ae734465a...",
    "3D Model": "6e6fa891745c42b7..."
  }
}|
```

**ATTR** — 网络/属性：
```json
{"type":"ATTR","ticket":166,"id":"e4382"}||{"parentId":"e4380","key":"NET","value":"GND","x":1030,"y":-1060,...}|
```

---

## 五、两种格式对比

| 维度 | 标准版 (.eprj2) | 标准版备份 (.zip) | 专业版 (.epro2) |
|---|---|---|---|
| 容器 | SQLite | Zip | Zip |
| 原理图数据 | components.dataStr (base64) | .esch NDJSON | .epru 内嵌 |
| PCB 数据 | components.dataStr (base64) | .epcb NDJSON | .epru 内嵌 |
| LCSC 编号位置 | attributes 表 | project.json → devices | .epru → META.attributes |
| 读取难度 | ⭐⭐ (需 SQLite) | ⭐ (纯文本) | ⭐ (纯文本) |

---

## 六、原理图与 PCB 的关联机制

```
原理图 COMPONENT "e530"  ←──→  PCB COMPONENT extra.Channel ID = "$1e530"

原理图 ATTR("Device")      → devices.uuid → attributes("Supplier Part") = "C6186"
原理图 ATTR("Designator")  → "U1"
原理图 ATTR("Value")       → "3.3V"
原理图 WIRE                → 网络拓扑
原理图 ATTR("NET")         → "VCC+5V", "GND" 等网络名
```

---

## 七、对 Skill 开发的关键启示

### 7.1 提取 LCSC 编号

三种格式的读取路径：

```python
# 标准版 .eprj2：SQL 查询
SELECT a.value FROM attributes a
JOIN devices d ON a.device_uuid = d.uuid
WHERE a.key = 'Supplier Part'

# 标准版备份 .zip：读 project.json
with zipfile.ZipFile(path) as z:
    with z.open('project.json') as f:
        data = json.load(f)
for dev in data['devices'].values():
    lcsc = dev['attributes'].get('Supplier Part')

# 专业版 .epro2：解析 .epru 的 META 行
with zipfile.ZipFile(path) as z:
    for name in z.namelist():
        if name.endswith('.epru'):
            for line in z.open(name).read().decode().splitlines():
                if '"type":"META"' in line:
                    body = line.split('||')[1].rstrip('|')
                    attrs = json.loads(body).get('attributes', {})
                    lcsc = attrs.get('Supplier Part')
```

### 7.2 原理图审查 (ERC) 数据来源

1. `.esch` 中 `COMPONENT` → `ATTR("Device")` → `ATTR("Designator")` → `ATTR("Value")` 映射
2. `WIRE` 条目 → 重建网络连接拓扑
3. `ATTR("NET")` / `ATTR("Global Net Name")` → 获取网络名
4. 对照硬件知识库规则检查

### 7.3 PCB 诊断数据来源

1. `.epcb` 中 `TRACK`、`VIA`、`PAD` → 走线/过孔/焊盘
2. `COMPONENT.extra.Channel ID` → 关联原理图
3. 检查：走线宽度 vs 载流要求、去耦电容是否靠近芯片电源引脚

---

## 八、样本工程速查

| 工程 | 版本 | 主文件 | 器件数 |
|---|---|---|---|
| 51单片机最小系统板 | 标准版 | `.eprj2` (SQLite) | ~30 个 |
| 示例-波型产生与变换 | 专业版 | `.epro2` (Zip) | ~20 个 |
