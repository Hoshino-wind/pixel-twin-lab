# Pixel Twin Lab

[English](README.md)

把一张 UI 参考图——截图、设计稿或 AI 生成的界面图——变成一个本地可视化 QA 工作台。Pixel Twin Lab 用代码重建该 UI,在真实浏览器中截图,并与原图做像素级对比测量,让"看起来一样"从主观判断变成一个可量化的数字。

它被设计为 agent skill(Claude Code / Codex)运行,但每一步都是普通的 Python 或 Node 脚本,也可以完全手动执行。

## 功能

针对每张参考图,工作台生成包含四种模式的 HTML 页面:

- **Reference(参考)** — 原图,作为基准真值。
- **Rebuilt(重建)** — 用代码/组件实现的重建版本。
- **Overlay(叠加)** — 参考图以可调透明度叠加在重建版本之上。
- **Exact Slice(精确切片)** — 按测量坐标贴回的位图裁切,展示位图级还原的上限。

目标不是假装所有代码实现的 UI 都能做到一像素不差,而是让保真度的取舍**可见、可测量、可重复**:输出像素差异图、mismatch 百分比、MAE、最大色差,以及按区域细分的指标,精确指出 UI 哪个部分差距最大。

在测量之外,该 skill 还驱动完整的组件化流程:检查目标项目,遵循其框架与样式约定,把生产级组件写入项目源码树,并把所有中间产物(切片、截图、diff、台账)隔离在独立的 work 目录中。

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
