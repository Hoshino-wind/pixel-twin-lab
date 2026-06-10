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
8. 写一份简短 QA 结果：
   - 源图片路径
   - 实现截图路径
   - viewport
   - mismatch 百分比、MAE、max delta
   - 分区指标中最差的区域
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

`--browser bundled|system` 选择 Playwright 自带 Chromium 或系统 Chrome(用 `playwright-core` 时默认 `system`)。每次运行会写出 `capture-meta.json`,记录浏览器版本和 viewport,便于跨机器对比时归因。

生成 diff 指标：

```bash
python /path/to/pixel-twin-lab/scripts/pixel_diff.py \
  --reference /absolute/path/outputs/pixel-twin/assets/reference.png \
  --out-dir /absolute/path/outputs/pixel-twin
```

可选 `--tolerance N` 额外输出忽略单通道差值 `<= N` 的 mismatch(严格值始终保留);抗锯齿噪声占主导时可作为实际可收敛的目标。

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

## 输出契约

创建或更新这些产物：

- `index.html`、`styles.css`、`script.js`
- `assets/reference.png`
- 自动检测到切片时的 `assets/slice-*.png`
- `lab-config.json`(含 `background_uniform`)
- `reference-capture.png`
- `rebuilt-capture.png`
- `exact-capture.png`
- `capture-meta.json`
- `*-diff.png`
- `pixel-diff-summary.json`(存在切片或 `regions.json` 时包含分区指标)
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
- `Exact Slice` 接近 `0%`：只在切片覆盖了所有非背景内容时成立——这要求背景是均匀纯色,且阈值能抓到每个组件。低对比区域(如浅灰底上的白色卡片)低于阈值时会被背景色填充;可调低 `--threshold` 或改用 `--full-bleed`。即使 `0%` 也只是位图重建,不是组件化。
- `Rebuilt` mismatch 很高：第一轮常见;用分区指标驱动校准循环——修最差的区域、重新截图、重复——而不是肉眼看 diff 图猜。
- MAE 接近 `0` 但 mismatch 非零：通常是边缘抗锯齿、背景噪声或压缩类漂移。
- max delta 很大：通常表示缺素材、颜色错误、空白区域、裁切错误或布局漂移。

不要只凭肉眼检查宣称一像素成功。必须在相同 viewport 和 device scale 下做截图比较。

## 实现注意事项

- 用源图原生尺寸作为截图 viewport。
- 保持 `deviceScaleFactor: 1`，确保截图数学稳定。
- 生成报告时使用绝对路径。
- 除了通过 `?capture=1`，不要隐藏 reference overlay 或工具栏。
- 针对具体 repo 调整流程时，把临时捕获脚本放在 `work/`。
- 面向用户的截图和 diff 图片优先放在 `outputs/`。
- 如果用户要求看效果，最终回答中附上截图。
- 不要把 lab 模板复制进生产源码当最终 App。lab 是测量工具；最终产物必须遵循目标项目的框架和约定。
- 不要因为参考图更容易实现，就随意引入 Tailwind、CSS Modules、图标库、图表库或 UI kit；只有项目没有合适约定且用户同意时才新增依赖。
