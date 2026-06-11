---
name: pixel-twin-lab
description: 从 UI 图片、截图、Image Gen 结果或 mockup 构建并验证本地像素还原工作台，然后把组件化流程写入目标项目。适用于需要精确复刻图片 UI、将实现与参考图对比、运行 reference/rebuilt/overlay/exact-slice 模式、生成像素 diff 截图、量化 mismatch、分离中间产物与最终项目代码，或判断某个设计应走一像素级位图还原还是组件化还原的任务。
---

# Pixel Twin Lab

使用这个 skill 可以把一张 UI 参考图变成本地视觉 QA 工作台：

- `Reference`：原始图片，作为视觉真值。
- `Rebuilt`：代码/组件重建结果。
- `Overlay`：把参考图以可调透明度叠在重建结果上。
- `Exact Slice`：把位图切片按测量坐标贴回去，用来展示位图级还原上限。

这个 skill 的目标不是假装所有代码 UI 都能一像素不差，而是让“位图级精确”和“可维护组件实现”之间的取舍变得可见、可测、可复用。

英文是默认运行语言。中文镜像用于人工阅读；如果要让 Codex 默认读取中文，可把本文件内容替换到 `SKILL.md`。`agents/openai.yaml` 仅供 Codex 调用；本 skill 的运行入口是 `SKILL.md` 和 `scripts/`。

完整的图片到组件实现流程见 `references/componentization-workflow.zh-CN.md`。

## 环境依赖

- Python：`pip install -r scripts/requirements.txt`(Pillow 必需;numpy 推荐——缺少时脚本回退到较慢的纯 PIL 路径)。
- 截图:完整 `playwright` 包(自带 Chromium),或 `playwright-core` 加系统 Chrome/Chromium(macOS/Linux/Windows 自动探测,也可设 `CHROME_PATH`)。
- 运行前自检:`python3 -c "import PIL"` 和 `node -e "require('playwright-core')"`(或 `playwright`)。

## 决策规则

- 如果用户要求“一像素不差”，优先保留原始位图或使用位图切片；说明真正 `0%` diff 不等于可维护的 App UI。
- 如果用户要求真实 App，使用 `Rebuilt` 模式构建组件，并用 diff 作为校准循环。
- 如果参考图来自 AI 生成 UI，默认没有真实图层、token 或素材源；需要从位图中提取。
- 如果 `prepare_lab.py` 警告背景不是均匀纯色(`lab-config.json` 中 `background_uniform: false`),先用 `--full-bleed` 重跑,再相信 exact 模式的数字。该警告是边框采样启发式;组件贴边也可能误触发。
- 如果参考图是浅色、低对比或复杂仪表盘,且自动切片明显漏检或不完整(切片很少、`lab-config.json` 里 `coverage_pct` 偏低),不要反复调 `--threshold`。直接从参考图测量组件边界,手写 `slice-manifest.json`,用 `--manifest` 重跑。manifest 区域命名与 `component-map.md` 保持一致,让切片、指标和 ledger 共用同一套词汇。
- 在相信任何 diff 数字之前,先用零基线证明环境可靠:截取 `reference` 模式并与参考图做 diff,必须是 `0%`。基线不为零说明渲染环境坏了(viewport 或 device scale 不对、色彩配置不是 sRGB、字体被替换、或者端口被别的服务占了)——先修环境,再碰重建代码。
- 对代码组件无法忠实还原的区域——地图、照片、头像、复杂图表、Logo/装饰文字——采用混合策略:组件化只负责外壳、布局和交互,这些区域作为位图切片岛保留,在 `slice-manifest.json` 中声明,并在 ledger 里标注为 island。
- 每个 run 只追一条保真度轨道:`bitmap exact`(允许位图切片和 SVG 复刻,`0%` 可达)或 `component faithful`(项目原生组件,允许少量残余误差)。交付时两套数字都报告,但不要用同一份产物同时追两个目标。
- 每轮 diff 后运行 `plan_calibration.py`,把分区指标变成修复计划。它会把每个未达标区域分类为未实现、布局偏移、token 色差、切片岛候选或重建,并按 pass 排序:skeleton → layout → visual tokens → asset islands → region rebuild loop。按这个顺序修——几何错会让后面所有对比满屏红,岛类内容应该切片,而不是反复重写代码。
- 最终交付物必须是项目内可维护组件,而不是整页位图。按 UI 结构把屏幕拆成区域级组件(例如 header、weather 卡片、trip map、timeline 图标/文字行、bottom nav),逐区域用测量过的 primitive 局部收敛。覆盖大半页面的整页 surface patch——无论匹配率多高、文件多大——只能作为 ceiling 证明或诊断产物,绝不能写进最终产品代码。`fidelity_gate.py` 用资产覆盖上限强制这一点(`--max-asset-coverage` 默认 40%、`--max-single-asset-coverage` 默认 30%);island 资产必须是区域级(一块地图、一张照片),不能是页面级。
- 所有保真度门禁现在都要求零基线先被证明(`reference-capture.png` mismatch 不超过 `--reference-target`,默认 0.01%);环境未证明时所有门禁判失败。
- 当 `component_only_98` 或 `componentized_islands_98` 未通过时,先运行 `component_primitives.py`,再进入下一轮重建。它会把命名区域指标、ledger 轨道和 DOM 证据合成 primitive 工单,明确哪些文字、列表行、卡片、控件、图标、表格和 SVG 标记必须用组件重建,避免继续靠位图资产蒙混过关。
- 修改 `component-required` 区域前先运行 `measure_primitives.py`。不要凭肉眼补 primitive;文字行、控件、图标、卡片、分隔线和 SVG 标记的坐标都应来自参考裁片测量。
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
4. 在生成的 `rebuilt-layer` 中实现代码重建。
5. 用本地 HTTP 服务运行 lab；避免 `file://`，因为浏览器工具可能会阻止访问。
6. 使用 `scripts/capture_modes.cjs` 按源图片原生尺寸捕获 `reference`、`rebuilt`、`exact` 模式。
7. 运行 `scripts/pixel_diff.py` 生成 diff 图片和 JSON 指标。
8. 运行 `scripts/plan_calibration.py` 生成 `calibration-plan.md`;下一轮迭代按其 pass 顺序执行(layout → tokens → islands → rebuild),出现 `slice-manifest.suggested.json` 时合并进你的 manifest。
9. 组件化门禁失败时,运行 `scripts/component_primitives.py`,再对最差的 `component-required` 区域运行 `scripts/measure_primitives.py`。
10. 按 `measured-primitives.md` 中的坐标规格重建 DOM/SVG primitive,然后重新截图和 diff。
11. 运行 `scripts/compare_region_metrics.py --baseline <clean-lab> --candidate <edited-lab>`,按分区 delta 决定保留或回退该组件策略。
12. 写一份简短 QA 结果：
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

`--browser bundled|system` 选择 Playwright 自带 Chromium 或系统 Chrome(用 `playwright-core` 时默认 `system`)。Chromium 启动时强制 sRGB 色彩配置,避免截图继承显示器配置导致整图颜色漂移(macOS 上尤其明显)。每次运行会写出 `capture-meta.json`,记录浏览器版本、色彩配置和 viewport,便于跨机器对比时归因。

生成 diff 指标：

```bash
python /path/to/pixel-twin-lab/scripts/pixel_diff.py \
  --reference /absolute/path/outputs/pixel-twin/assets/reference.png \
  --out-dir /absolute/path/outputs/pixel-twin
```

可选 `--tolerance N` 额外输出忽略单通道差值 `<= N` 的 mismatch(严格值始终保留);抗锯齿噪声占主导时可作为实际可收敛的目标。

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
- 图表:走 bitmap-exact 轨道时用 SVG path/rect 复刻,因为图表库自带的轴线、抗锯齿和点位都会不一样;走 component-faithful 轨道时用项目已有的图表库。
- 生成报告时使用绝对路径。
- 除了通过 `?capture=1`，不要隐藏 reference overlay 或工具栏。
- 针对具体 repo 调整流程时，把临时捕获脚本放在 `work/`。
- 面向用户的截图和 diff 图片优先放在 `outputs/`。
- 如果用户要求看效果，最终回答中附上截图。
- 不要把 lab 模板复制进生产源码当最终 App。lab 是测量工具；最终产物必须遵循目标项目的框架和约定。
- 不要因为参考图更容易实现，就随意引入 Tailwind、CSS Modules、图标库、图表库或 UI kit；只有项目没有合适约定且用户同意时才新增依赖。
