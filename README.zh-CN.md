# Pixel Twin Lab

[English](README.md)

把一张 UI 参考图——截图、设计稿或 AI 生成的界面图——变成一个本地可视化 QA 工作台。Pixel Twin Lab 用代码重建该 UI,在真实浏览器中截图,并与原图做像素级对比测量,让"看起来一样"从主观判断变成一个可量化的数字。

它被设计为 agent skill(Claude Code / Codex)运行,但每一步都是普通的 Python 或 Node 脚本,也可以完全手动执行。

## 为什么需要这个项目

vibe coding 做界面很快,但从图片还原 UI 时经常会卡在这些痛点里:

- "看起来差不多"是主观判断,不知道下一轮修改到底有没有变好。
- 全屏截图只能说明页面不一样,但说不清到底是哪一个组件、哪一块区域错了。
- AI 生成的 UI 图没有真实图层、tokens、组件边界和资产来源,只能从位图里反推。
- agent 容易为了接近原图而贴整张位图,结果看似高保真,其实不是可维护组件。
- 真实项目已经有路由、样式体系、设计 token 和 UI 库,通用重建很容易破坏项目约定。
- 缺少可复盘的回测记录,每次迭代都在凭肉眼猜。

Pixel Twin Lab 把这件事变成一条可验证流水线:固定同一个 viewport 截图,用同一套区域指标对比,记录每轮结果,再根据数据决定下一步修布局、修 token、合并切片岛,还是重建某个区域。

## 功能

针对每张参考图,工作台生成包含四种模式的 HTML 页面:

- **Reference(参考)** — 原图,作为基准真值。
- **Rebuilt(重建)** — 用代码/组件实现的重建版本。
- **Overlay(叠加)** — 参考图以可调透明度叠加在重建版本之上。
- **Exact Slice(精确切片)** — 按测量坐标贴回的位图裁切,展示位图级还原的上限。

目标不是假装所有代码实现的 UI 都能做到一像素不差,而是让保真度的取舍**可见、可测量、可重复**:输出像素差异图、mismatch 百分比、MAE、最大色差,以及按区域细分的指标,精确指出 UI 哪个部分差距最大。

在测量之外,该 skill 还驱动完整的组件化流程:检查目标项目,遵循其框架与样式约定,把生产级组件写入项目源码树,并把所有中间产物(切片、截图、diff、台账)隔离在独立的 work 目录中。

## 回测与评估数据

这个仓库当前不内置固定的公开 benchmark 数据集。它的"回测数据"是每次运行自动产出的可复盘证据,同一张参考图、同一个 viewport、同一组区域可以反复截图、对比和追踪:

- `capture-meta.json`:记录浏览器、viewport、device scale、色彩配置和截图模式。
- `pixel-diff-summary.json`:记录整体和分区域的 strict mismatch、tolerant mismatch、MAE、最大色差和差异包围盒。
- `*-diff.png`:输出像素差异热力图,直接看到哪里不一致。
- `calibration-plan.json` / `calibration-plan.md`:把错误区域归类为骨架、布局、token、切片岛或重建任务。
- `triage-report.json` / `triage-report.md`:在继续改代码前给出下一步判断,避免盲调。
- `fidelity-gate.json` / `fidelity-gate.md`:区分 component-only、componentized-islands、approximation、hybrid asset 和 placeholder 结果。
- `component-primitives.md`、`measured-primitives.md`、`region-metric-comparison.md`:当组件化未达标时,给出下一轮组件原语、测量框和前后指标对比。

核心评估信号包括:

- **零基线**: `reference-capture.png` 对原参考图必须接近 `0%` mismatch,否则说明截图环境本身不可信。
- **严格匹配率**: `100 - mismatch_pct`,用于 98% 保真门槛。
- **容错匹配率**:忽略极小通道差异,用于观察字体抗锯齿、浏览器渲染残差。
- **最差区域排序**:按区域 mismatch 排出下一轮最该修的组件。
- **资产覆盖率**:统计生成资产覆盖面积,防止用整页位图冒充组件化还原。

## 这个项目能做什么

- 从 UI 截图、设计稿或 AI 生成界面创建本地视觉 QA 工作台。
- 在真实浏览器里按原图尺寸截取 reference、rebuilt、overlay、exact-slice 模式。
- 输出整页和命名区域的像素 diff 热力图、JSON 指标和最差区域排序。
- 在修 CSS 或组件前先验证截图环境,避免把 viewport、色彩配置、字体问题误判成实现问题。
- 区分"位图精确"和"可维护组件还原",不把两种结果混在一起。
- 对图表、地图、照片、头像、复杂媒体等区域使用明确的切片岛策略,保留组件外壳和交互。
- 针对目标项目初始化完整组件化流程,自动识别框架、路由、样式系统和 UI 库。
- 为 agent 迭代生成 recovery scaffold、component ledger、primitive worklist 和 fidelity gate。

## 解决的痛点

- 把主观视觉评审变成可重复的量化指标。
- 防止 agent 用整页图片遮住组件实现质量问题。
- 让复杂 dashboard、低对比 SaaS 界面可以按区域逐步修复。
- 给每次重建迭代留下前后对比和回测记录。
- 把截图、切片、diff、台账等中间产物隔离在 work 目录,不污染生产源码。
- 明确交付物到底是组件式还原、位图精确、混合资产方案,还是只是 placeholder contract。

## 环境要求

- **Python 3**,需要 [Pillow](https://pillow.readthedocs.io/)(必需)和 numpy(推荐——没有 numpy 时脚本会回退到较慢的纯 PIL 路径):

  ```bash
  pip install -r scripts/requirements.txt
  ```

- **Node.js**,需要完整的 `playwright` 包(自带 Chromium),或 `playwright-core` 加系统 Chrome/Chromium(macOS/Linux/Windows 自动检测,也可设置 `CHROME_PATH`)。

## 快速开始

```bash
# 1. 从参考图创建工作台
python scripts/prepare_lab.py \
  --reference /absolute/path/reference.png \
  --out-dir /absolute/path/outputs/pixel-twin

# 2. 在生成的 rebuilt-layer 中实现重建版本,然后启动本地服务
cd /absolute/path/outputs/pixel-twin
python3 -m http.server 8787 --bind 127.0.0.1

# 3. 以原图原生尺寸截取 reference / rebuilt / exact 三种模式
node scripts/capture_modes.cjs \
  --url http://127.0.0.1:8787/ \
  --out-dir /absolute/path/outputs/pixel-twin

# 4. 生成差异图和指标
python scripts/pixel_diff.py \
  --reference /absolute/path/outputs/pixel-twin/assets/reference.png \
  --out-dir /absolute/path/outputs/pixel-twin
```

diff 步骤会输出 `pixel-diff-summary.json`(整体及分区域的 mismatch / MAE / 最大色差)和 `*-diff.png` 热力图。针对最差区域迭代修改、重新截图、重新 diff,直到指标收敛。

要把图片完整组件化落地到现有项目,请从 `scripts/init_component_flow.py` 开始,并阅读 [`references/componentization-workflow.zh-CN.md`](references/componentization-workflow.zh-CN.md)。

## 脚本一览

| 脚本 | 用途 |
| --- | --- |
| `scripts/prepare_lab.py` | 从参考图构建工作台,自动检测组件切片(渐变/照片背景用 `--full-bleed`;阈值检测漏掉的低对比 UI 用 `--manifest` 手工声明命名切片) |
| `scripts/capture_modes.cjs` | 在真实浏览器中以原生尺寸截取 reference/rebuilt/exact 模式,并输出 `capture-meta.json` 以便溯源 |
| `scripts/pixel_diff.py` | 生成差异图和 JSON 指标,含按切片和命名区域的细分 |
| `scripts/plan_calibration.py` | 把分区域 diff 变成有序修复计划(骨架 → layout → tokens → 切片岛 → 重建),并直接给出可用的骨架 CSS 和岛 manifest 建议 |
| `scripts/triage_lab.py` | 读取 lab 配置、diff 指标和校准计划,判断下一步应该修环境、手动 manifest、搭骨架、合并岛、修 layout/token,还是重建区域 |
| `scripts/bootstrap_recovery.py` | 把 triage/planner 结果转成 starter manifest、组件/岛台账、岛图片、骨架 CSS 和中间 React scaffold |
| `scripts/fidelity_gate.py` | 按 component-only / componentized-islands / approximation / hybrid / placeholder 分类验收,并强制检查零基线和资产覆盖 |
| `scripts/compare_structure.py` | 对 approximation 区域(第三方图表、地图、3D)做结构比较:原语数量、位置偏移、前景色板 |
| `scripts/init_component_flow.py` | 针对目标项目初始化组件化运行(contract、map、ledger) |

## 作为 agent skill 使用

- **Claude Code**:把本目录链接或复制到 `~/.claude/skills/`。运行时入口是 [`SKILL.md`](SKILL.md),其中包含 agent 遵循的决策规则、工作流和输出契约。
- **Codex**:`agents/openai.yaml` 是仅供 Codex 使用的接口描述文件,Claude Code 运行时不使用它。

英文是该 skill 的默认运行时语言。中文镜像供人工阅读:[`SKILL.zh-CN.md`](SKILL.zh-CN.md) 和 [`references/componentization-workflow.zh-CN.md`](references/componentization-workflow.zh-CN.md)。

## 仓库结构

```
SKILL.md                  Agent 运行时入口(决策规则、工作流、输出契约)
SKILL.zh-CN.md            SKILL.md 的中文镜像
scripts/                  Python/Node 工具(prepare、capture、diff、组件化初始化)
references/               完整组件化工作流文档(英文 + 中文)
assets/prototype-template 工作台 HTML/CSS/JS 模板
agents/openai.yaml        仅供 Codex 的接口描述文件
```

## 许可证

[Apache 2.0](LICENSE)
