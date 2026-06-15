# 分层感知重构设计稿（VLM 引擎 + CV 精修）

> 状态：设计稿 rev2（已折入一轮对抗式自评审），未改动任何现有代码。
> 目标读者：本项目维护者。
> rev2 评审修正：F1 北极星不再用 tolerant（§3.5 重写，改元素/run 契约验收）；F2 无吞层门禁改正交 VLM 复核对账（§3.4.3 重写）；F3 run 字号改 VLM 估计 + token 量化，删"逐 run 像素测字"（§2.1/§3.2）；F4 新增成本预算（§3.7）；F5 新增上线前置信先验（§3.8）；F6 内容硬编码检查降级为告警（§3.6）。
> 背景结论（已用 nebula-ops / luma-trip 双案例 + 三类缺陷证据闭环）：
> 拆解识别（找框）是对的；成品"差"的统一病根是 **pipeline 在每个尺度上把"叠在一起的层"拍成了"一个平面"**——丢了图层 / z 序。
> Figma/蓝湖 原生带图层，跳过它们用自研算法时，必须让算法自己恢复图层；而图层是感知判断，纯 CV 有硬天花板，因此 **VLM 当引擎、CV 退化成精修、确定性脚本守规则**。本稿是模型驱动的插件式架构，模型始终在回路。

---

## 0. 三类缺陷 → 统一病根 → 修复点

| 缺陷（实测） | 现场 | 丢失的"层" | 本稿修复点 |
|---|---|---|---|
| `128 kg CO₂e` 渲染成 `128 kg`、字重偏粗 | 一个值塞进一个 `<strong>` + 一个 `font-size:28px` | **字符串内部的排版分层（run）** | §1.1 `element.runs[]` + §2 VLM run 切分 + §3 CV 逐 run 测字 |
| 地图浮层进度条被裁进地图贴图 | 地图区整块矩形裁成 `approximation` asset | **区域内部的图层 / z 序（前景 vs 背景）** | §1.2 `region.composition` + §2 VLM 图地分离 + §3 CV inpaint |
| Topbar / 区块标题文字写死在 markup | 纯展示组件 inline 文本 | **内容层与样式层分离** | §1.1 runs 结构化内容 + §3 codegen 静态检查 |

设计三大件：**§1 数据契约升级**（让"层"有地方落）、**§2 VLM 输出契约**（产出层）、**§3 流水线落位**（谁产、谁精修、谁守门）。

---

## 1. 数据契约升级（`ui-blueprint.schema.json`）

原则：**只新增、可选、向后兼容**。旧蓝图不带新字段仍能通过校验；新字段出现时，校验和 codegen 才启用分层逻辑。下面是相对现有 schema 的 delta。

### 1.1 元素分层：`element.runs[]`（解决复合排版）

现状：`components[].elements[]` 每个元素只有一个 `content` 字符串 + `token_refs[]`，装不下"数字+单位+下标"。

新增 `runs[]`（可选）。**当 `runs` 存在时，`content` 退化为只读校验字段（必须等于各 run.text 拼接），codegen 一个 run 渲染一个 `<span>`。**

```jsonc
// components[].elements[] 内新增：
{
  "id": "carbon-stat-value",
  "type": "text",
  "bounds": { "x": 567, "y": 268, "width": 132, "height": 30 },
  "content": "128 kg CO₂e",            // 校验用 = runs 拼接
  "runs": [
    { "text": "128",  "role": "number", "type_ref": "stat-number", "color_ref": "ink-900" },
    { "text": " kg",  "role": "unit",   "type_ref": "stat-unit",   "color_ref": "ink-700" },
    { "text": "CO₂e", "role": "suffix", "type_ref": "stat-suffix", "color_ref": "ink-400",
      "baseline_shift": "sub" }
  ]
}
```

`run` 定义：

| 字段 | 必填 | 取值 / 说明 |
|---|---|---|
| `text` | ✓ | 该 run 的文本 |
| `role` | ✓ | `number / unit / suffix / prefix / label / delta / superscript / subscript / body / emphasis` |
| `type_ref` | ✓ | 指向 `tokens.typography[].name`（字号/字重/行高来自 token，**不写裸值**） |
| `color_ref` |  | 指向 `tokens.colors[].name` |
| `baseline_shift` |  | `none / sub / super`（解决 `CO₂e`、`m²`、KPI 角标） |

校验规则（§3 validate 扩展）：`join(runs.text) == content`；每个 `type_ref/color_ref` 必须在 tokens 中存在。

### 1.2 区域分层：`region.composition`（解决前景浮层被吞）

现状：`layout.regions[].track` 是单值 `component | island | approximation`，一个区域只能是一个平面。地图区被判 `approximation` 就整块裁走，浮层一起进贴图。

新增可选 `composition`：把一个区域显式拆成**背景平面 + 前景层（带 z 序）**。`track` 保留为"背景平面的轨道"。

```jsonc
// layout.regions[] 内新增：
{
  "name": "trip-route-map",
  "bounds": { "x": 539, "y": 223, "width": 285, "height": 326 },
  "role": "media",
  "track": "approximation",            // 指背景：地图用第三方库/贴图
  "composition": {
    "background": {
      "kind": "map",                   // photo / map / gradient / solid / chart
      "asset_policy": "inpaint-clean", // 必须把前景擦掉后再产背景，禁止整块裁
      "library": "leaflet"
    },
    "foreground": [
      { "component_id": "trip-bookings-overlay", "z": 1,
        "bounds": { "x": 707, "y": 489, "width": 132, "height": 48 } }
    ]
  }
}
```

约束：
- `foreground[].component_id` 必须是 `components[]` 里的**真实 DOM 组件**（走 component 轨道），不能是 asset。
- `asset_policy: inpaint-clean` 告诉 CV：**先 inpaint 擦掉所有 foreground 框，再产背景资产**（自动化现在手工的 `trip-map-clean.png`）。
- z 序：`z` 越大越靠前；背景恒为 z=0。

### 1.3 通用 z 序：`component.z` / `element.z`（可选）

同一容器内有重叠（角标盖在头像上、徽标压在卡片角）时，给 `components[]` 和 `elements[]` 各加可选 `"z": <int>`，默认 0。codegen 据此决定 `position`/`z-index`，而不是靠 DOM 顺序赌。

### 1.4 兼容性
- 三个新字段（`runs`、`composition`、`z`）全部 `additionalProperties` 友好、可选。
- 旧蓝图：无新字段 → 走旧的"单串单样式 / 单平面"逻辑，行为不变。
- 新蓝图：有新字段 → 启用分层校验和分层 codegen。

---

## 2. VLM 输出契约（感知引擎）

### 2.1 定位
VLM **产"感知判断 + 粗几何估计"，不产精确几何**。它看一张区域裁图，输出"有几层、谁在谁之上、文字怎么分 run、是什么语义、哪些是重复模板"。

几何分两类，不能一刀切（评审 F3 修正）：
- **可测几何**（组件/元素的外框、整段文字框）：VLM 给 `approx_bounds`，CV snap 到测量边缘。
- **难测几何**（run 内部的逐段字号/字重/基线）：要逐 run 像素切分等于小号下标的字符级 OCR——**最不可靠的环节**。所以 **run 的字号/字重由 VLM 出估计值**（它能看出"后缀更小、是下标"），CV **只把估计值量化到整页已测的 typography token 集合里最近的一个**（整页字号本就有限），**不承诺逐 run 像素测量**。

每个区域产出一份 `region-perception.json`，由确定性 `merge_blueprint` 映射进升级后的蓝图。

### 2.2 输出 schema（`region-perception.schema.json`，新增）

```jsonc
{
  "region": "trip-route-map",
  "figure_ground": {
    "background": { "kind": "map", "approx_bounds": [539,223,285,326] },
    "foreground": [
      { "ref": "fg-1", "semantic": "overlay-card", "z": 1,
        "approx_bounds": [707,489,132,48],
        "sits_above": "background", "confidence": 0.93 }
    ]
  },
  "components": [
    { "ref": "c1", "type": "card", "category": "data-display",
      "approx_bounds": [707,489,132,48], "z": 1,
      "elements": [
        { "ref": "e1", "type": "text", "approx_bounds": [715,495,70,16],
          "runs": [
            { "text": "Bookings", "role": "label" },
            { "text": "7/10", "role": "number" }
          ]
        },
        { "ref": "e2", "type": "control", "semantic": "progress", "approx_bounds": [715,515,110,6] }
      ]
    }
  ],
  "repetition": [
    // 集合检测：N 个子树是同一模板 → 喂 data 层，禁止硬编码 N 个兄弟节点
    { "template_ref": "row", "members": ["e10","e11","e12"], "kind": "list" }
  ],
  "notes": "progress bar floats ON the map; must be DOM, map must be inpainted clean"
}
```

字段要点：
- `figure_ground`：**这是 VLM 最核心的新增价值**——判 z 序、分图地。CV 永远算不出"卡片浮在地图之上"。
- `runs[]`：**每段文字的角色**。OCR 只能给 "128 kg CO₂e" 一串字，判不出 `CO₂e` 是下标后缀——这是语义，归 VLM。
- `repetition[]`：集合/模板识别，直接决定 data 层（一个模板 + mock 数组），堵"硬编码 N 行"。
- 每个判断带 `confidence`；低于阈值的进 §3 的人工复核队列，不静默通过。
- `approx_bounds` 一律标"approx"，提醒下游：**这是给 CV snap 的近似框，不是最终几何**。
- run 可带 `size_hint`（估计 px）和 `weight_hint`（regular/medium/semibold/bold）；下游 CV **量化到最近 token**，不做像素回测（F3）。
- `figure_ground.foreground` 必须带 `count` 总数字段（供 §3.4.3 的正交复核对账，F2）。

### 2.3 Prompt 契约（与 SKILL 现有子 agent 模板对齐）

沿用 orchestration-playbook 的"一区域一子 agent、信息隔离"骨架，把 decompose 子 agent 的职责从"打语义标签"升级成"判图层"：

```
你在做一个 UI 区域的【分层感知】。只看 packets/regions/<region>/crop.png 与 measurements.json。
按 region-perception.schema.json 输出，且严格遵守：

1. 图地分离（figure_ground）：判断这个区域是单层平面，还是"背景 + 前景浮层"。
   - 背景类型 kind：photo / map / chart / gradient / solid。
   - 任何"浮在背景之上"的卡片/进度条/按钮/角标，列入 foreground，并标 z 与 sits_above。
   - 关键：浮层是独立 DOM，绝不能算作背景的一部分。
2. 文字分 run：每个文字元素，按排版层级切成 runs，给每段 role
   （number/unit/suffix/delta/superscript/...）。形如 "128 kg CO₂e" 必须切成三段。
3. 语义类型：components/elements 用 schema 的类型与 Ant Design 分类。
4. 重复识别（repetition）：若有 N 个相似子树，标成一个 template + members，注明 kind（list/grid/...）。
5. 坐标一律给近似值（approx_bounds），不要试图精确——精确几何由测量脚本负责。
6. 每个判断给 confidence；拿不准就标低分并写 notes，不要猜一个高分。

返回 region-perception.json。不要写任何代码、不要读其它区域。
```

### 2.4 模型
按项目"用最新最强 Claude"的约定，VLM 走 **Claude Opus 4.8**（多模态）。低置信项可二次让另一视角复核（对抗式：一个判图地、一个专判 run），分歧进人工队列。

---

## 3. 流水线落位（谁产、谁精修、谁守门）

### 3.1 三层角色（铁律）

| 层 | 组件 | 负责 | 不负责 |
|---|---|---|---|
| **引擎** | VLM（Opus 4.8） | 图地分离、z 序、run 切分、语义类型、重复识别 | 不出几何数字、不写代码 |
| **精修** | CV 脚本 | 把 approx 框 snap 到像素边缘、采色、**把 run 的字号估计量化到已测 typography token**、**inpaint 擦背景**、产 clean 资产 | 不做语义/图层判断、不做逐 run 像素测字（F3） |
| **守门** | 确定性脚本 | merge / validate / fidelity-gate / pixel-diff / codegen 静态检查 | 不交给模型，保证可复现可审计 |

口诀：**模型看懂、CV 量准、脚本卡死。**

### 3.2 改造后的 Phase 流（对照现有 SKILL）

```
Phase 0  项目探测                         不变（init_component_flow.py）
Phase 1  CV 测量：候选框 + 像素真值        保留（measure_primitives / prepare_lab）
                                          —— 提供 measurements.json 给 VLM 与精修
Phase 1.5【新】VLM 分层感知               每区域 → region-perception.json
          （替代旧的 classify 人工打标 +    （图地/z 序/run/语义/重复）
           extract_element_assets 的启发式猜测）
Phase 1.6【新】CV 精修                     按 perception 执行：
          - snap approx_bounds → 测量边缘
          - run 的 size_hint/weight_hint → 量化到最近 typography token（不逐 run 像素测，F3）
          - 对 composition.background=inpaint-clean 的区域：擦掉 foreground 框 → 产 clean 资产
          - 采色 → tokens.colors（带 sampled_at）
Phase 2  merge_blueprint → 升级蓝图        perception + 精修测量 → runs[]/composition/z
Phase 2e validate_blueprint【扩展】        旧校验 + 新分层校验（§3.4）—— 硬门禁
Phase 3-4 计划 / 生成【扩展 codegen】      span-per-run；背景资产 + 前景 DOM 浮层；静态检查
Phase 5  回测【改北极星】                  细节验收用元素/run 契约，tolerant 仅回归兜底（§3.5，F1）
```

### 3.3 现有脚本的去留

| 脚本 | 去留 | 说明 |
|---|---|---|
| `measure_primitives.py` / `prepare_lab.py` / `extract_tokens.py` | **保留**（精修） | 测量是 CV 的本职，继续做几何/颜色真值 |
| `pixel_diff.py` / `capture_modes.cjs` / `measure_dom_elements.cjs` / `verify_elements.py` | **保留**（守门） | 回测与契约校验 |
| `merge_blueprint.py` / `validate_blueprint.py` / `fidelity_gate.py` | **保留 + 扩展** | 加分层字段的合并/校验/门禁（§3.4、§3.5） |
| `classify_slices.py` | **降级为 fallback** | 内容路由并入 VLM 的 `figure_ground.background.kind`；无模型时才用 |
| `extract_element_assets.py` | **保留裁切/inpaint 机制，删启发式选取** | "near-square colored primitive""icon-or-badge primitive"这类**猜测**改由 VLM 给清单；脚本只按清单裁/擦 |
| `extract_text_elements.py`（OCR） | **降级为校验** | OCR 给文本+几何，用于**验证** VLM 的 runs（`join(runs)≈OCR text`）；run 角色判断不靠 OCR |
| `infer_layout.py` | **降级为校验** | VLM 给布局意图（row/grid/stack），infer_layout 用测量的 gap **验证**，不再自治推断 |

净效果：**所有"靠阈值猜语义/图层"的 CV 逻辑被 VLM 取代；所有"测量/守门"的确定性逻辑保留。** 现在 skill 满地的 `--threshold` 调参和手工 `-clean.png`，正是被取代和被自动化的那部分。

### 3.4 `validate_blueprint.py` 新增硬门禁

1. **run 完整性**：有 `runs` 的元素，`join(runs.text) == content`；每个 `type_ref/color_ref` 在 tokens 中存在。
2. **图层闭合**：`region.composition.foreground[].component_id` 必须是真实 component 且走 component 轨道；不得指向 asset。
3. **无吞层（重写，F2）**：原"inpaint 残差检查"作废——它只能验证**已登记**浮层被擦了，抓不住真正危险的失败模式（**VLM 漏判某浮层 → 它从未进 foreground → 永不 inpaint → 静默烤进背景**，没有 bbox 可查残差），且残差检测本身又是被我们否定的 CV 阈值启发式。改为**正交感知复核**：第二个 VLM 用不同 prompt 独立在原图上数"有几个浮层/可交互前景"，与第一遍 `figure_ground.foreground.count` 对账；不一致 → 判可能吞层，进人工队列，不静默通过。吞层是感知问题，用感知冗余抓，不用像素阈值抓。
4. **重复即数据**：`repetition[]` 标了模板的，对应组件必须在 `data` 层有数据项；codegen 不得出现 > 1 个同模板硬编码兄弟节点。

### 3.5 `fidelity_gate.py` 北极星修正（重写，F1）

**关键纠错**：tolerant **不能**当"细节保真"的验收门。证据：用户不能忍的 CO₂e 裁切+字重粗所在的 `insights-row`，**strict 53.75% 但 tolerant 仅 4.72%（tolerant 给它打 95 分）**。tolerant 恰好把这类文字细节差**几乎不计分**——拿它当北极星 = 在用户唯一在乎的缺陷上亮绿灯。tolerant 的正确用途只有两个：判"结构对不对"、判"该不该停止追抗锯齿"。

修正后的门禁分层：

| 门 | 指标 | 管什么 |
|---|---|---|
| **细节保真（主验收门）** | **元素/run 结构契约**（`verify_elements` 扩展） | 渲染 DOM 是否真有 number+unit+suffix 三个 span、各 span 的 `type_ref` 字号/`baseline_shift` 对不对、foreground 浮层是否都有可寻址 DOM。**这是结构断言，不是像素百分比**——CO₂e 这类缺陷只有这道门抓得住。 |
| **结构正确 / 止血** | tolerant 匹配 + `compare_structure.py` | 判整体布局对不对、终结"抗锯齿空转 190 版"。**仅作回归兜底，不作细节验收。** |
| **bitmap-exact 轨道** | strict | 这条轨道本就追 0%，保留。 |

口径：**细节由契约判（pass/fail），像素只判回归（不退步）。** 位图补丁天然没有可寻址元素 → 过不了契约门 → 顺带堵贴图作弊。

### 3.6 codegen 静态检查（新增，分级守门，F6）

对生成代码做 AST/模板扫描，但**两类泄漏的判定可靠度不同，分级处理**：

1. **手绘矢量（硬门禁）**：被 VLM 判为 `chart/map` 的区域内，禁止手写 `<path d="M..."/>`/`<polyline>`/`<polygon>`。这条**可机械判定**（区域 + 标签明确），直接报错。
2. **内容硬编码（告警，非硬门禁）**：列出模板里**无法追溯到 `data`/`runs` 的可见文本字面量**，交人/模型复核——**不 0/1 判定**。因为静态分辨"该数据化的硬编码"与"本就合法的静态标签（如 `Search...` 占位符）"本身是判断不是机械规则，且要逐框架解析模板（JSX/Vue SFC/template string），假阳性高。降级为 lint 告警 + 复核队列。

### 3.7 成本预算（新增，F4）

每区域一个多模态 VLM 调用，密集仪表盘 ~15–20 区域 × 2 参考 ×（感知 + 可能的正交复核 + 低置信重派）= 几十次调用/轮。**不控制就是把 strict 空转换成 VLM 空转。** 预算策略：

- **粗判先行**：整图一次 VLM 概览，先判"哪些区域有浮层/复合排版/重复"——只有这些区域才进逐区域精判。单层纯 DOM 区域不必精判。
- **正交复核按需**：§3.4.3 的第二遍 VLM 只对"判出 foreground 非空"的区域跑，不全量。
- **置信门控**：高置信直接采纳；只有低置信项才上对抗复核 + 人工。
- **缓存**：region-perception 按区域裁图 hash 缓存，重跑不重算。
- 沿用 SKILL 既有的"迭代预算"硬规则：两轮 <0.5pp 改进即停。

### 3.8 上线前先验（新增，F5 —— 现在就能做，不用等实现）

本设计能否真省人，取决于 **VLM 图地/z 序判断的置信分布**。flush 贴合型浮层（齐平 chip、看似嵌入的 sticky header）人都难判，VLM 大概率给一堆中间分；若中间分体量大 → 人工复核队列膨胀 → **复活 nebula/luma 那个"靠手工 manifest 救场"的原始病根**。

**先验动作**（实现前即可执行）：拿 luma + nebula 全部区域跑一遍 §2 的感知，统计 `confidence` 分布与"低置信区域占比"。占比低 → 路线成立；占比高 → 说明 z 序歧义是硬伤，需要在 §2 prompt 里加更强的图地线索（阴影/边框/层叠提示）或接受这条路省不了多少人。**这是 go/no-go 的数据，应在投入实现前拿到。**

---

## 4. 三类缺陷的端到端走查（验证设计自洽）

**缺陷①（CO₂e/字重）**：VLM 把值切成 `[128=number, kg=unit, CO₂e=suffix+sub]` 并给 size/weight hint（§2）→ CV 把 hint 量化到最近 typography token（§3.2，**不逐 run 像素测**，F3）→ 蓝图 `runs[]` 落位（§1.1）→ validate 校验 run 完整（§3.4.1）→ codegen 出三个 `<span>`，后缀小号下标，不再溢出裁切 → **元素/run 契约门验收**（§3.5，不是 tolerant，F1）。

**缺陷②（地图浮层）**：VLM 判 `figure_ground`：地图=背景、Bookings 卡=前景 z1，并给 `foreground.count`（§2）→ 蓝图 `composition` 落位（§1.2）→ CV inpaint 擦掉前景框产 clean 地图资产（§3.2，自动化原手工 `-clean.png`，且 inpaint 被浮层 DOM 完全遮住所以补得对不对不影响渲染）→ validate 正交 VLM 复核对账 `count`（§3.4.3 重写，F2）→ codegen 出 `背景资产 + 前景 DOM route-card/progress`。

**缺陷③（硬编码文本）**：内容进 `runs`/`data`（§1）→ codegen 静态扫描列出不可追溯字面量为**告警**交复核（§3.6.2，非硬门禁，F6）。

---

## 5. 落地顺序建议（增量、可回退）

0. **先验先行（§3.8，F5）**：实现前先拿 luma+nebula 跑感知，量低置信占比，拿 go/no-go 数据。
1. **先扩 schema（§1）+ validate（§3.4）**：纯加可选字段，旧流程不受影响，先让"层"有地方落、有门禁。
2. **再接 VLM 感知（§2）+ merge 映射**：单区域试点（先拿 luma `trip-route-map` 和 `insights-row` 两个已知缺陷区验证）。
3. **CV 精修补齐**：inpaint 擦背景 + run hint 量化到 token（§3.2，非像素测字）；把 `extract_element_assets` 的选取改为吃 VLM 清单。
4. **codegen 分层渲染 + 分级静态检查（§3.6）**。
5. **门禁分层（§3.5，F1）**：细节验收切元素/run 契约，tolerant 退为回归兜底。
6. 旧 CV 启发式（classify/infer_layout/OCR run 猜测）降级为 fallback/校验。

每步都可单独回测对比 nebula/luma，确认不回归再进下一步。
```
