---
name: pixel-twin-lab
description: 把 UI 图片(截图、Image Gen 结果或 mockup)拆解成六层工程蓝图——视觉布局、组件语义、设计 token、数据内容(mock 数据)、交互行为、项目实现——先切片分类路由(图表上 ECharts、图标/图片切图、地图/3D 上三方库),再从蓝图写代码,并用截图回测校验。适用于把 UI 图变成真实项目组件、忠实复刻图片 UI、将实现与参考图对比、生成像素 diff 截图、量化 mismatch,或判断某个设计应走位图还原还是组件化还原的任务。
---

# Pixel Twin Lab

这个 skill 不是"帮模型把图贴得更像",而是**强制模型先把 UI 图拆成页面工程结构(蓝图),再从蓝图写代码,再用截图回测校准**。直接看图生成代码必然失真,因为模型在目测几何;这里代码用到的每个数字都来自测量,每个断言都被截图 diff 验证。

静态 HTML lab 是量具,不是交付物:

- `Reference`:原始图片,作为视觉真值。
- `Rebuilt`:被测的代码/组件重建结果。
- `Overlay`:把参考图以可调透明度叠在重建结果上。
- `Exact Slice`:位图切片按测量坐标贴回——只用于天花板诊断,绝不是交付物。

英文是默认运行语言。中文镜像用于人工阅读；如果要让 Codex 默认读取中文，可把本文件内容替换到 `SKILL.md`。`agents/openai.yaml` 仅供 Codex 调用；本 skill 的运行入口是 `SKILL.md` 和 `scripts/`。

完整的图片到组件实现流程见 `references/componentization-workflow.zh-CN.md`。

## 环境依赖

- Python：`pip install -r scripts/requirements.txt`(Pillow 和 numpy 都是必需依赖)。
- 截图:`npm install` 安装完整 `playwright` 包;再运行 `npm run install:browsers` 安装自带 Chromium。自动回测必须使用自带 Chromium,不走系统 Chrome。
- 运行前自检:`python3 -c "import PIL, numpy"` 和 `node -e "require('playwright')"`。

## 蓝图工作流(主流程)

"把这张图做进我的项目"一律走这六个阶段,lab 测量循环只服务于 Phase 1 和 5:

- **Phase 0 探查**:`init_component_flow.py` 探查目标项目,读 `component-contract.json` 的 project_profile(框架/样式系统/UI 库/已有组件与 token),后续一切生成都受它约束。
- **Phase 1 测量**:`prepare_lab.py`(架设量具)→ 证明零基线 → `measure_primitives.py` 逐区域测量 → `extract_tokens.py`(颜色聚类/字号/间距)→ `infer_layout.py`(flex/grid 关系推断,带置信度)。下游用到的任何 bounds/颜色/字号必须出自这些产物,禁止目测。
- **Phase 1.5 分类路由**:`classify_slices.py --mode init` 生成 `classification-sheet.png`(全部裁片拼成一张带名字的 sheet,**只读一次图**)和 `slice-classification.json` 脚手架;逐切片标注 content_type 后 `--mode apply` 产出 `routing-manifest.json`,在写任何蓝图/代码之前就把轨道定死:`plain-dom` → component(DOM 重建);`icon`/`image` → island 资产(切图,或在非纯色底上重生成透明背景图);`chart` → approximation,第三方图表库(默认 **ECharts**)填 mock 数据;`map` → approximation,地图库(默认 leaflet);`scene-3d` → approximation,three + structural-only。可视化内容从一开始就不进 DOM 重建循环,杜绝"先手写、失败后再被 triage 改判"。`bootstrap_recovery.py`、`component_primitives.py`、`fidelity_gate.py` 都把这份 manifest 当作权威轨道来源。
- **Phase 2 蓝图**:对照参考裁片和测量数据,撰写 `ui-blueprint.json`(schema 见 `schemas/ui-blueprint.schema.json`),六层齐全:视觉布局(区域 + 轨道沿用 Phase 1.5 路由 + 布局关系,不可信的推断显式标 absolute-fallback)、组件语义(是什么 + Ant Design 风格 `category` + 文字内容 + maps_to 复用映射;密集或模糊组件参考 `references/component-taxonomy.md`)、设计 token(全部 CSS 值的唯一来源,优先映射项目已有 token)、**数据内容**(顶层 `data` 永远存在;没有数据驱动组件时写 `[]`。每个数据驱动组件声明 shape/fields/mock_data/binding/source:表格/列表把可见行逐字转写成结构化记录 `source: extracted`,写进数据层而不是拆成几十个 element;图表近似读取系列数/点数/数值范围/走势 `source: approximated` 并声明渲染库——代码从数据渲染,硬编码兄弟节点和手画数据形状都是蓝图缺陷)、交互行为(按"项目惯例 > 项目 token > 类型默认"推导并声明 source)、项目实现(每组件 reuse/extend/create + 顺序 + 验收)。然后跑 `validate_blueprint.py` 做 schema 校验 + 测量对账。**硬门禁:校验不过,禁止写任何项目代码。**
- **Phase 3 规划**:从蓝图实现层写 `implementation-plan.md`(生成顺序、复用映射、island/approximation 声明、逐组件验收)。
- **Phase 4 生成**:按计划顺序在目标项目写原生组件。**代码只读蓝图,不读原图**——发现不对先改蓝图、重新校验、再生成,不许对着图目测改 CSS。数据驱动组件从蓝图数据层渲染:一个行模板 map 一个 mock 数组(collection 合同),或把数据按 binding 喂给声明的库;mock 数据放独立 fixture 文件,方便日后换真实数据源。复用优先、生成时写成通用件:`maps_to` 先指向项目自己的表格/列表/卡片组件;项目没有等价物时,蓝图组件 `type` 就是组件原型(table/list/kpi 卡/tabs/badge/面板壳/图表容器)——**按目标项目的框架和样式体系实时写出这个组件**,形态是可复用通用件(props 收列定义/数据数组/label-value,内容只从 props 进),不是把内容写死的一次性 markup。组件代码每个项目现写;skill 固定的只是合同(数据驱动 props、token 驱动样式、`data-element` 透传)。图表/地图/3D 走 approximation 轨道,第三方库(默认 echarts/leaflet/three 或项目已有的)**配置出来,不是画出来**;照片和图标是区域级 island 资产(切图或透明重生成),只有箭头/加号这类简单几何 glyph 才手写 SVG。DOM 带与蓝图一致的 `data-element` id,collection 容器带 `data-element` + 每行 `data-element-item`;绝不把 lab HTML 塞进项目。
- **Phase 5 回测**:截取真实路由 → `pixel_diff.py` → `verify_elements.py` → `fidelity_gate.py`;每个失败项映射回蓝图条目或生成 bug,4↔5 循环(蓝图本身错则回 2),用"N/M 元素已验证"作为进度刻度。**迭代预算:逐轮记录 strict/tolerant 匹配率;连续两轮活动指标改善 < 0.5 个百分点就停止迭代,报告残余差距和原因,交用户决策**——在不收敛的指标上无界循环是这个 skill 最主要的 token/成本失败模式。文字密集的 component 区域用 tolerant(容差 8)做迭代信号、strict 并列报告;字体抗锯齿会让 strict 饱和,作为循环信号会误导。

## 编排式蓝图工作流(密集 UI)

区域多、组件数十个、带图表/表格/小字的 dashboard,走同样的六阶段,但用子 agent 扇出 + 信息隔离执行(完整手册与子 agent 提示词模板见 `references/orchestration-playbook.md`):

1. Phase 0-1 仍由主 agent 串行完成。
2. 主 agent 写 `blueprint-skeleton.json`(页面级区域 + implementation 头),`make_region_packets.py` 按区域切工作包:裁片 + 测量 + token + 片段模板 + 说明。
3. 每个区域并行派一个拆分子 agent,只看自己的工作包,产出 `fragment.json`——新鲜上下文逐区标注,是密集 UI 上标注精度的来源。
4. `merge_blueprint.py` 确定性合并片段(id 唯一性、token 去重并改写引用、默认 plan)成 `ui-blueprint.json`,再过 `validate_blueprint.py` 同一道硬门禁。
5. `make_codegen_packets.py`(校验未通过时拒绝运行)按组件切生成工作包,**包内不含任何图片路径**;每组件派一个生成子 agent。隔离让目测漂移从"被禁止"变成"不可能"。
6. Phase 5 回测由主 agent 执行;只对失败组件附带失败证据定点重派。

运行环境没有子 agent 能力时,按阶段顺序串行执行同样的工作包——产物和门禁与主流程完全一致。

## 决策规则

- 蓝图先于代码:没有通过 `validate_blueprint.py` 的 `ui-blueprint.json`,不写任何项目代码——发现自己在无蓝图写组件,停下回 Phase 2。
- 路由先于重建:一个区域"是图表还是图标还是照片"由 Phase 1.5 的 `classify_slices.py` 决定,不是靠先手写 DOM 再看 diff。triage 改判只是恢复路径,不是默认路径。
- 数据先于 markup:表格、列表、队列、feed、图表 = mock 数据 + 模板/库。生成代码里出现超过一行硬编码的重复内容、或任何手摆的图表标记,说明数据层缺失或被忽略——回去修蓝图,不是修 markup。
- 图表/地图/3D 永远不许手画(不写 SVG 坐标轴/marks,不画 canvas):任何轨道上都是"第三方库 + mock 数据"。SVG 复刻图表只允许作为不交付的 bitmap-exact 天花板诊断。
- 上下文经济:读 `.md`/brief 摘要和分区文件(`primitive-measurements/<区域>.json`),不读全量 `*.json` 报告;脚本默认只打印简要摘要,不要用 `--print full` 或 `cat` 重新灌全量。看图限于 Phase 1.5 的一张分类 sheet + 每轮回测至多一张 crop sheet;下一步修什么由分区数值指标决定,不靠反复目测截图。
- 代码生成只读蓝图,不读原图。原图只是测量、分类和蓝图撰写的输入。"对着截图目测调 CSS"正是这个 skill 要消灭的失败模式。
- 交互是推导不是提取:项目惯例 > 项目 token > 元素类型默认,逐条声明 source。单帧截图不含交互信息,不要假装能读出来。
- 如果用户要求“一像素不差”，优先保留原始位图或使用位图切片；说明真正 `0%` diff 不等于可维护的 App UI。
- 如果用户要求真实 App，使用 `Rebuilt` 模式构建组件，并用 diff 作为校准循环。
- 如果参考图来自 AI 生成 UI，默认没有真实图层、token 或素材源；需要从位图中提取。
- 如果 `prepare_lab.py` 警告背景不是均匀纯色(`lab-config.json` 中 `background_uniform: false`),先用 `--full-bleed` 重跑,再相信 exact 模式的数字。该警告是边框采样启发式;组件贴边也可能误触发。
- 如果参考图是浅色、低对比或复杂仪表盘,且自动切片明显漏检或不完整(切片很少、`lab-config.json` 里 `coverage_pct` 偏低),不要反复调 `--threshold`。直接从参考图测量组件边界,手写 `slice-manifest.json`,用 `--manifest` 重跑。manifest 区域命名与 `component-map.md` 保持一致,让切片、指标和 ledger 共用同一套词汇。
- 在相信任何 diff 数字之前,先用零基线证明环境可靠:截取 `reference` 模式并与参考图做 diff,必须是 `0%`。基线不为零说明渲染环境坏了(viewport 或 device scale 不对、色彩配置不是 sRGB、字体被替换、或者端口被别的服务占了)——先修环境,再碰重建代码。
- 对代码组件无法忠实还原的区域——地图、照片、头像、复杂图表、Logo/装饰文字——采用混合策略:组件化只负责外壳、布局和交互,这些区域作为位图切片岛保留,在 `slice-manifest.json` 中声明,并在 ledger 里标注为 island。
- 每个 run 只追一条保真度轨道:`bitmap exact`(允许位图切片和 SVG 复刻,`0%` 可达)或 `component faithful`(项目原生组件,允许少量残余误差)。交付时两套数字都报告,但不要用同一份产物同时追两个目标。
- 每轮 diff 后运行 `plan_calibration.py`,把分区指标变成修复计划。它会把每个未达标区域分类为未实现、布局偏移、token 色差、切片岛候选或重建,并按 pass 排序:skeleton → layout → visual tokens → asset islands → region rebuild loop。按这个顺序修——几何错会让后面所有对比满屏红,岛类内容应该切片,而不是反复重写代码。
- `bootstrap_recovery.py --asset-policy target` 不会给 component 轨道区域分配资产(除非显式传 `--allow-component-assets` 做位图天花板诊断);`materialize_recovery_lab.py` 默认只把 island/approximation 轨道的资产渲染成 `<img>`,component 区域即使 ledger 带了资产也只渲染骨架并打印警告。如果 lab 页面里所有组件都是贴图,说明这次 run 从未产出组件还原——该重建 component 区域,而不是继续 materialize。
- 最终交付物必须是项目内可维护组件,而不是整页位图。按 UI 结构把屏幕拆成区域级组件(例如 header、weather 卡片、trip map、timeline 图标/文字行、bottom nav),逐区域用测量过的 primitive 局部收敛。覆盖大半页面的整页 surface patch——无论匹配率多高、文件多大——只能作为 ceiling 证明或诊断产物,绝不能写进最终产品代码。`fidelity_gate.py` 用资产覆盖上限强制这一点(`--max-asset-coverage` 默认 40%、`--max-single-asset-coverage` 默认 30%);island 资产必须是区域级(一块地图、一张照片),不能是页面级。
- 所有保真度门禁现在都要求零基线先被证明(`reference-capture.png` mismatch 不超过 `--reference-target`,默认 0.01%);环境未证明时所有门禁判失败。
- 像素 diff 只统治浏览器排版引擎能确定性渲染的部分(DOM/CSS/SVG)。带独立渲染管线的内容——canvas 图表、地图瓦片、WebGL/三维场景、视频、Lottie——归入 `approximation` 轨道:用合适的第三方库构建(ECharts/Mapbox/three.js 等)并定制样式,按区域评价(tolerant mismatch + `compare_structure.py` 结构对比)而不是参与整页 strict;容器几何(位置、尺寸、圆角、边框)仍是 DOM,仍按 strict 卡。WebGL/三维区域在 ledger 里声明 `eval: structural-only`,跨 GPU 连 tolerant 像素对比都没有意义。对应门禁是 `componentized_approximation_98`:component 轨道 strict(整页减去 approximation 区域)≥ 98% + island 资产合规 + 每个 approximation 区域过自己的评价。图表截图前关动画、固定 `devicePixelRatio`;地图用固定 style 或 mock 瓦片,否则像素和结构对比都是噪声。
- 当 `component_only_98` 或 `componentized_islands_98` 未通过时,先运行 `component_primitives.py`,再进入下一轮重建。它会把命名区域指标、ledger/路由轨道和 DOM 证据合成 primitive 工单:component 区域重建文字、collection(从 mock 数组渲染的表格/列表行)、卡片、控件;approximation 区域只配置库 + mock 数据;图标走资产,不再出现在手写清单里。
- 修改 `component-required` 区域前先运行 `measure_primitives.py`,然后只读该区域的 `primitive-measurements/<区域>.json`。不要凭肉眼补 primitive;文字行、控件、卡片、分隔线的坐标都应来自参考裁片测量。
- 测量之后、写 DOM 之前,先建元素清单:运行 `init_element_manifest.py` 把测量框生成 `element-manifest.json`,然后由你(agent)对照参考裁片给每个元素标注 `type`(text/icon/control 等)、`content`(文字元素提取真实文字,图标写语义描述)和 `maps_to`(目标组件及槽位,如 `WeatherCard/temperature`)。它合并 `element-assets.json` 时只把同槽位素材合到 primitive,卡片顶部媒体图这类内嵌素材会保留为独立 image 元素,避免被拉伸成整张卡片;重跑且存在新的 `element-assets.json` 时,会清理不再由提取器产出的过期资产引用。`extract_element_assets.py` 会把小图标/头像、卡片媒体、突出底部/导航主控件、KPI sparkline/bar 片段、部署/队列进度条片段、时间线 marker 条、保守的彩色连通块图标/插画和已路由岛/近似区域提升成显式资产。可选运行 `extract_text_elements.py --merge-manifest` 用本机 Tesseract OCR 把高置信文字行加入 manifest;它会结合整图 OCR 与 region 放大 OCR,把过宽 OCR 行拆成视觉词组,剪掉重复的过度合并 OCR 框,并跳过大图/图表资产内部文字,避免重复叠字。再运行 `materialize_element_manifest_lab.py` 物化一版诊断 rebuilt 层:它按 manifest 坐标渲染所有元素,用 DOM `<img>` 消费 `element-assets.json` 中声明的图标/头像/图片资产,把表面型 primitive 采样成容器填充色,把 OCR 文本渲染成真实 DOM text,降低旧 text-line 占位不透明度,透明化被 OCR 或 asset 覆盖的旧占位块(包括文本形 control 片段),把嵌套父容器保留为无边框背景填充,并为 chip/button 类 OCR 行推断保守的文本/组级控件壳层,用于区分"素材没切出/没消费"、"文字没抽出"、"控件壳层没恢复"和"语义组件没生成";它不是最终项目代码。**重复内容用一条 `collection` 条目,不是 N 条单元格条目**:表格/列表保留容器元素,标 `type: collection`,声明 `item_count`(或 `min_items`)和 `first_item_content`,删掉行级子条目——DOM 合同是容器 `data-element` + 每行 `data-element-item`,恰好就是"从 mock 数组渲染"自然产生的结构。approximation 图表容器标 `type: chart-host`(以库渲染出的 canvas/svg 存在为验收)。这就是"元素 → 组件"映射层:算法给几何,你给语义。
- 重建后运行 `measure_dom_elements.cjs`(实测每个 data-element 节点在参考坐标系下的几何)和 `verify_elements.py`(逐元素校验存在性、几何偏差、文字内容、类型兼容)。贴图无法满足这份合同——它没有可逐个寻址的元素——所以蒙混通道在语义层被关死。声明了 manifest 后,componentized 门禁同时要求 `element_contract` 通过;`element_asset_contract` 只证明 `requires_asset` 元素都有素材并以非 overlay DOM 方式渲染,不等于组件化成功。"38/52 个元素已验证"同时就是迭代进度刻度。
- measured primitive 修改后,用 `compare_region_metrics.py --regions <name> --fail-on-strict-regression` 对比干净基线 lab。只看截图更完整不可靠; strict 分区指标退步时不能算收敛。
- 如果任务只是分析，不修改 App；只运行测量并报告可行性。
- 如果任务是实现，先创建工作台，再围绕截图迭代代码重建。
- 如果用户要求“全流程”或“组件化”，在写入最终产物前必须明确目标项目路径和项目内最终代码目录。
- 中间产物放在 `<project>/work/pixel-twin-lab/<run-name>/`；最终代码放在目标项目源码树内。
- 组件化前必须先检查目标项目，并遵循检测到的框架、路由、组件组织方式和样式体系。
- 只有在无法检测到既有前端框架或样式体系时，才默认 React + Tailwind。

## 工作流

1. 确认源图片，并复制到项目或输出目录。
2. 创建 lab 目录，通常是 `outputs/pixel-twin/` 或 `work/pixel-twin/`。
3. 运行 `scripts/prepare_lab.py` 创建工作台和自动检测切片。
4. 运行 `scripts/classify_slices.py --mode init`,对照 `classification-sheet.png` 标注每个切片的 content_type,再 `--mode apply`——先路由,后动工。
5. 在生成的 `rebuilt-layer` 中实现代码重建。重复内容(表格/列表/KPI 行/tab)由你在 lab 的 `script.js` 里现写的小渲染函数生成——一个模板函数 map 一个 mock 数据数组(容器 `data-element`、每行 `data-element-item`),不手写兄弟块;approximation 区域只放库容器,不手画标记。
6. 用本地 HTTP 服务运行 lab；避免 `file://`，因为浏览器工具可能会阻止访问。
7. 使用 `scripts/capture_modes.cjs` 按源图片原生尺寸捕获 `reference`、`rebuilt`、`exact` 模式。
8. 运行 `scripts/pixel_diff.py` 生成 diff 图片和 JSON 指标(stdout 是简要摘要,全量在 `pixel-diff-summary.json`)。
9. 运行 `scripts/plan_calibration.py` 生成 `calibration-plan.md`;下一轮迭代按其 pass 顺序执行(layout → tokens → islands → rebuild),出现 `slice-manifest.suggested.json` 时合并进你的 manifest。
10. 组件化门禁失败时,运行 `scripts/component_primitives.py`,再对最差的 `component-required` 区域运行 `scripts/measure_primitives.py`。
11. 按该区域的 `primitive-measurements/<区域>.json` 坐标重建 DOM primitive,然后重新截图和 diff。
12. 运行 `scripts/compare_region_metrics.py --baseline <clean-lab> --candidate <edited-lab>`,按分区 delta 决定保留或回退该组件策略。
13. 执行迭代预算:连续两轮 capture→diff 的活动指标(文字密集区域看 tolerant,其余看 strict)改善 < 0.5 个百分点,就停止并报告残余,不再循环。
14. 写一份简短 QA 结果：
   - 源图片路径
   - 实现截图路径
   - viewport
   - mismatch 百分比、MAE、max delta
   - 分区指标中最差的区域
   - 保真度轨道(`component faithful` 或 `bitmap exact`)及切片岛区域清单
   - 阻塞项或最终通过状态

## 全流程组件化

当用户希望把参考图变成可维护项目代码时使用这一流程。

1. 询问或推断：
   - 源图片路径
   - 目标项目目录
   - 项目内最终代码目录
   - run 名称
2. 初始化 run：

```bash
python /path/to/pixel-twin-lab/scripts/init_component_flow.py \
  --reference /absolute/path/reference.png \
  --project-dir /absolute/path/target-project \
  --final-dir src/features/radar-dashboard \
  --name radar-dashboard
```

3. 写最终代码前，先检查目标项目。
4. 读取 `component-contract.json`，把其中的 `project_profile` 作为实现约束。
5. 所有参考图、切片、截图、diff、计划和 ledger 都放在中间 run 目录。
6. 生产代码只写入目标项目最终目录，或目标路由/组件所属的现有项目文件。
7. 匹配目标项目：
   - React 项目：实现 React 组件。
   - Next 项目：遵循 App Router 或 Pages Router 约定。
   - Tailwind 项目：使用 Tailwind utilities 和已有 token/class。
   - CSS/CSS Modules 项目：使用项目现有 stylesheet/module 模式。
   - 已有 UI 库：在符合参考图和本地约定时复用本地组件和库原语。
   - 无可检测前端栈：默认 React + Tailwind。
8. 捕获真实最终 App 路由，并重新运行像素 diff。
9. 每轮迭代后更新 `implementation-ledger.md`。
10. 最终交付必须同时链接：
   - 中间工作台目录
   - 修改过的最终项目文件

## 脚本用法

所有脚本路径都应相对本 `SKILL.md` 所在目录解析。

准备 lab：

```bash
python /path/to/pixel-twin-lab/scripts/prepare_lab.py \
  --reference /absolute/path/reference.png \
  --out-dir /absolute/path/outputs/pixel-twin
```

加 `--full-bleed` 可把整张参考图作为单一切片(渐变/照片类背景)。超过约 2MP 的图会自动在降采样副本上做检测,切片坐标映射回原生尺寸。

用手工 slice manifest 准备 lab(阈值检测漏组件的低对比 UI):

```bash
python /path/to/pixel-twin-lab/scripts/prepare_lab.py \
  --reference /absolute/path/reference.png \
  --out-dir /absolute/path/outputs/pixel-twin \
  --manifest /absolute/path/slice-manifest.json
```

manifest 与 `regions.json` 同形(`slices` 或 `regions` 键,或裸数组;每项 `{"name", "x", "y", "width", "height"}`,name 可选),会完全取代基于阈值的自动检测。manifest 未覆盖的画布区域会自动生成 `gap-*` 切片补齐,保证任何背景下 exact 模式都完整;`--no-cover-gaps` 可关闭。命名切片的 `name` 会写进 `lab-config.json` 并自动进入分区 diff 指标。`lab-config.json` 同时记录 `slice_source`(`auto`/`manifest`/`full-bleed`/`none`)和 `coverage_pct`。

启动本地服务：

```bash
cd /absolute/path/outputs/pixel-twin
python3 -m http.server 8787 --bind 127.0.0.1
```

捕获浏览器截图：

```bash
node /path/to/pixel-twin-lab/scripts/capture_modes.cjs \
  --url http://127.0.0.1:8787/ \
  --out-dir /absolute/path/outputs/pixel-twin
```

`--browser bundled` 使用 Playwright 自带 Chromium，是自动回测的固定路径。`--browser system` 只允许一次性本地调试，并且必须额外设置 `PIXEL_TWIN_ALLOW_SYSTEM_BROWSER=1`，因为启动系统 Chrome 会触发 Codex GUI/沙箱提权。Chromium 启动时强制 sRGB 色彩配置,避免截图继承显示器配置导致整图颜色漂移(macOS 上尤其明显)。每次运行会写出 `capture-meta.json`,记录浏览器版本、色彩配置和 viewport,便于跨机器对比时归因。

生成 diff 指标：

```bash
python /path/to/pixel-twin-lab/scripts/pixel_diff.py \
  --reference /absolute/path/outputs/pixel-twin/assets/reference.png \
  --out-dir /absolute/path/outputs/pixel-twin
```

可选 `--tolerance N` 额外输出忽略单通道差值 `<= N` 的 mismatch(严格值始终保留);抗锯齿噪声占主导时可作为实际可收敛的目标。stdout 默认只打印简要摘要(整体指标 + 每个 capture 最差 5 个区域),全量分区数据在 `pixel-diff-summary.json`——需要具体区域就去文件里读对应条目,不要 `--print full` 重灌。

切片内容分类与路由(Phase 1.5):

```bash
python /path/to/pixel-twin-lab/scripts/classify_slices.py \
  --out-dir /absolute/path/outputs/pixel-twin --mode init
# 只读一次 classification-sheet.png,填好 slice-classification.json 里每个切片的 content_type
python /path/to/pixel-twin-lab/scripts/classify_slices.py \
  --out-dir /absolute/path/outputs/pixel-twin --mode apply
```

`init` 写出 `slice-classification.json`(每个命名切片一条;重跑只合并新切片不覆盖已有标注)和 `classification-sheet.png`(全部裁片一张 sheet——分类只花一次图片读取)。`apply` 校验标注并写出 `routing-manifest.json`(每区域 track/handling/library/eval),同时把 `track` 合并进 `regions.json`。`bootstrap_recovery.py`、`component_primitives.py`、`fidelity_gate.py` 都以该 manifest 为权威轨道来源。图标默认 `handling: crop-asset`,落在渐变/照片底上时逐条改 `regenerate-transparent`;图表/地图可逐条覆盖 `library`。

从最新 capture 生成校准计划:

```bash
python /path/to/pixel-twin-lab/scripts/plan_calibration.py \
  --reference /absolute/path/outputs/pixel-twin/assets/reference.png \
  --out-dir /absolute/path/outputs/pixel-twin
```

默认分析 out 目录下的 `rebuilt-capture.png`(`--capture` 可覆盖)。对每个区域探测整数布局偏移(±4px,`--shift-radius` 可调)、均匀色差、位图类内容复杂度和"capture 侧为平色的未实现状态",输出 `calibration-plan.json` 和 `calibration-plan.md`,把区域按 pass 分组并附一句话动作("往回移 (-3, 0)"、"参考色 #ffffff 实现为 #fafaff")。分类基于容差后的 mismatch(`--tolerance`,默认 8),严格值并列报告——容差为 0 时字体/抗锯齿残差会把所有区域饱和到 ~100%,平移和色差探测会双双失明。判为 `slice-island` 的区域生成可直接合并的 `slice-manifest.suggested.json`;判为 `not-built` 的区域生成 `skeleton.suggested.css`(参考位置的容器 + 采样填充色),用来启动 layout pass。残余误差仅为抗锯齿级别的区域列为 converged,无需处理。迭代 0 时大部分区域会落在 `not-built`/`slice-island`——layout/token 分类要等真实骨架就位、mismatch 进入中段后才有信息量。

分区指标默认开启(`--regions auto`):`lab-config.json` 里每个切片都有独立的 mismatch/MAE/max delta(切片 diff),输出目录里可选的 `regions.json` 可追加命名矩形(组件 diff)。区域结果按严重度降序写入 `pixel-diff-summary.json` 的 `regions` 字段;`--regions none` 关闭,也可传 JSON 文件路径。`regions.json` 的命名应与 `component-map.md` 的区域一致,让组件图、指标和 ledger 共用同一套词汇:

```json
{"regions": [{"name": "sidebar", "x": 0, "y": 0, "width": 220, "height": 800}]}
```

初始化完整组件化 run：

```bash
python /path/to/pixel-twin-lab/scripts/init_component_flow.py \
  --reference /absolute/path/reference.png \
  --project-dir /absolute/path/project \
  --final-dir src/path/for/final/component \
  --name short-run-name
```

加 `--manifest /absolute/path/slice-manifest.json`(可选配 `--no-cover-gaps`)可把手工 slice manifest 透传给 lab 准备步骤。

## 输出契约

创建或更新这些产物：

- `slice-classification.json`、`classification-sheet.png`、`routing-manifest.json`(Phase 1.5 分类路由,先于一切蓝图/代码工作)
- `ui-blueprint.json`(六层:layout/components/tokens/data/interactions/implementation)及目标项目里的 mock 数据 fixture 文件
- `index.html`、`styles.css`、`script.js`
- `assets/reference.png`
- 自动检测、manifest 定义或 gap 补齐产生的 `assets/slice-*.png`
- `lab-config.json`(含 `background_uniform`、`slice_source`、`coverage_pct`;manifest 切片带 `name`)
- `reference-capture.png`
- `rebuilt-capture.png`
- `exact-capture.png`
- `capture-meta.json`
- `*-diff.png`
- `pixel-diff-summary.json`(存在切片或 `regions.json` 时包含分区指标)
- 每轮 `plan_calibration.py` 产出的 `calibration-plan.json` 和 `calibration-plan.md`
- 组件化门禁失败后的 `component-primitives.json` 和 `component-primitives.md`
- 修改组件区前的 `measured-primitives.json`、`measured-primitives.md` 和 `primitive-measurements/*.png`
- 组件变体对比后的 `region-metric-comparison.json` 和 `region-metric-comparison.md`
- 可选的 `slice-manifest.suggested.json`,计划器建议的切片岛区域
- 可选的 `skeleton.suggested.css`,为计划器判为未实现的区域生成骨架容器
- 可选的 `slice-manifest.json`,记录手工测量的命名切片矩形
- 可选的 `regions.json`,为组件 diff 命名矩形区域
- 可选的交付报告 `design-qa.md`

完整组件化还应创建：

- `<project>/work/pixel-twin-lab/<run-name>/component-contract.json`
- `<project>/work/pixel-twin-lab/<run-name>/component-map.md`
- `<project>/work/pixel-twin-lab/<run-name>/implementation-ledger.md`
- `<project>/<final-dir>/` 下的最终组件/源码文件

`component-contract.json` 必须包含 `project_profile`，记录 framework、routing、style system、UI libraries、source roots、package manager 和 defaults applied。什么都检测不到时 `framework` 可能为 `unknown`;React + Tailwind 默认是给你的决策规则,不是探测器的断言。`init_component_flow.py` 不会创建 `<final-dir>`——写第一个最终代码文件时再创建。

## 保真度解读

- `0% mismatch`：浏览器截图与参考图像素完全一致。
- `Exact Slice` 接近 `0%`：只在切片覆盖了所有非背景内容时成立。自动检测要求背景是均匀纯色,且阈值能抓到每个组件;低对比区域(如浅灰底上的白色卡片)低于阈值时会被背景色填充——改用手写 `slice-manifest.json`(gap 切片自动补全覆盖),或改用 `--full-bleed`。即使 `0%` 也只是位图重建,不是组件化。
- `Rebuilt` mismatch 很高：第一轮常见;运行 `plan_calibration.py` 并按 pass 顺序修(layout → tokens → islands → rebuild),而不是肉眼看 diff 图或手工排序区域。
- MAE 接近 `0` 但 mismatch 非零：通常是边缘抗锯齿、背景噪声或压缩类漂移。
- max delta 很大：通常表示缺素材、颜色错误、空白区域、裁切错误或布局漂移。

不要只凭肉眼检查宣称一像素成功。必须在相同 viewport 和 device scale 下做截图比较。

## 实现注意事项

- 用源图原生尺寸作为截图 viewport。
- 保持 `deviceScaleFactor: 1`，确保截图数学稳定。
- 先调几何再调颜色:先对齐画布尺寸、边距、卡片位置、列宽和行高——布局漂 2px,所有颜色对比都会满屏红。
- 颜色从参考图位图采样(背景、边框、卡片底色、文字、阴影),统一沉淀为项目样式体系里的 token;绝不凭感觉写色值。
- 字体是组件化误差的最大来源:显式锁定 family、字重、字号和 line-height。AI 生成的参考图很少用标准字体,文字只能逼近——某个文字区域不再收敛时,把它转成 SVG 或切片岛,并记入 ledger。
- 图表是配置出来的,不是画出来的:component-faithful 轨道用项目已有图表库或默认 ECharts,喂数据层的 mock 系列;用 SVG path/rect 复刻图表只允许作为不交付的 bitmap-exact 天花板诊断。
- 生成报告时使用绝对路径。
- 除了通过 `?capture=1`，不要隐藏 reference overlay 或工具栏。
- 针对具体 repo 调整流程时，把临时捕获脚本放在 `work/`。
- 面向用户的截图和 diff 图片优先放在 `outputs/`。
- 如果用户要求看效果，最终回答中附上截图。
- 不要把 lab 模板复制进生产源码当最终 App。lab 是测量工具；最终产物必须遵循目标项目的框架和约定。
- 不要因为参考图更容易实现,就随意引入 Tailwind、CSS Modules、图标库或 UI kit;只有项目没有合适约定且用户同意时才新增依赖。**例外——approximation 轨道库**:路由到 chart/map/scene-3d 的区域天然需要真实渲染库。优先用项目已有的;没有就提议默认库(echarts / leaflet / three),每个 run 向用户确认一次——为了省依赖而手画可视化不是选项。
