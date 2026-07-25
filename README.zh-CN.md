# Pixel Twin UI Editor

[English](README.md)

把截图、设计稿或视觉修改要求直接应用到已有前端项目，同时保持业务行为不变。

最终产物就是目标项目的 Git diff。Pixel Twin 不再生成 Lab、蓝图、manifest、packet、报告、截图目录或另一份重建项目。

## 修改范围

允许修改：

- 组件视图结构和组合方式；
- CSS、主题、Token、布局、响应式和视觉层级；
- 无障碍属性和呈现型动效；
- 最终页面实际使用的图片、图标、SVG 和字体。

默认保护 API、Store、路由、请求、Query、Mutation、持久化、Schema、后端、业务校验及已有事件处理行为。

## 工作流

1. 只读检查已有项目和目标 UI。
2. 在系统临时目录记录当前 Git 工作树，并在条件允许时记录稳定的修改前视觉基线。
3. 直接修改真正拥有该 UI 的项目文件。
4. 统一执行一次范围、项目原生检查和可选视觉检查。
5. 最多进行一次定向视觉修复。

Guard 状态、截图、日志和 diff 默认只存在于系统临时目录，并自动删除。目标项目中只留下真实源码修改和产品最终资产。

## 命令

只读检查项目：

```bash
python3 scripts/pixel_twin.py inspect --project /absolute/path/to/project
```

修改前保护当前工作树：

```bash
python3 scripts/pixel_twin.py begin --project /absolute/path/to/project
```

存在本地页面和参考图时，在 `begin` 阶段同时建立修改前视觉基线：

```bash
python3 scripts/pixel_twin.py begin \
  --project /absolute/path/to/project \
  --url http://127.0.0.1:3000/dashboard \
  --reference /absolute/path/reference.png
```

完成修改后，只检查基线之后的变化，并运行项目已有格式、Lint 和类型检查：

```bash
python3 scripts/pixel_twin.py check --session <session-id>
```

`check` 会自动复用 `begin` 保存的视觉基线，并输出布局、多尺度结构、边缘、连续调色板相似度、像素残差、最多三个热点以及相对修改前的改善/回退风险。有 DOM 证据时，内部会在固定候选池中避免较小但可修复的热点被大型无归属残差挤掉，同时严格限制为最多六次修复求解和三个公开热点。每个热点可附带一个限量 DOM 候选、一个保守的位置/尺寸/颜色修复提示，以及最多两个来自会话许可 UI 文件的仓库相对源码位置。视觉对应关系重复、缺失、不可靠，或无法从内容变化中唯一分离出排版差异时，会明确返回 `uncertain`，不会伪造数值修复。Canvas、视频和复杂 SVG 会从静态噪声中移出，单独比较结构、边缘分布、颜色和时间漂移；自定义图表或地图容器可重复传入 `--dynamic-selector`。图片资源加载失败或渲染不稳定时会在评分前直接失败。没有基线时仍可使用一次性的 `check --url ... --reference ...`，但无法报告前后增益或会话范围内的源码候选。

视觉结果默认只作为反馈，不把像素百分比当成硬门禁。只有用户明确给出量化验收目标时，才传入 `--min-match <百分比>`。只有准备进行一次定向修复时才使用 `--keep-session`；最终检查不要保留，或用 `finish` 清理。

`check` 默认不执行构建，因此不会制造常规框架构建缓存。目标仓库确实要求生产构建时，`--run build` 会在自动格式、Lint 和类型检查之后追加构建。

检查成功后会删除临时会话。放弃失败会话时执行：

```bash
python3 scripts/pixel_twin.py finish --session <session-id>
```

自动识别不足时，可指定相对 `--project` 的 `--ui-root`、`--asset-root` 或精确的 `--editable` 文件；显式根目录会追加到自动识别结果。在 monorepo 中，`--project` 可以指向前端包，Guard 仍会保护整个外层 Git 仓库。`--editable` 不能放开后端、API、Store、Schema、包配置和基础设施路径。

如果目标 UI 文件已经包含用户修改，先审查原有 diff，再用 `--editable` 精确声明该文件，并保留原有 hunk。

## 安全边界

- `begin` 会记录 Git 可见的用户已有修改，这些修改不会被误算成本次任务；被忽略的 `.env*` 和常见 Pixel Twin 诊断目录会单独保护。
- 默认禁止再次修改已有脏文件；确需修改时必须在开始时精确声明。
- 会话中 stage 或 unstage 会被阻止。
- 非 UI 路径、高置信度业务逻辑变化、危险 SVG、符号链接和超大资产会被阻止。
- 工具不会执行 `stash`、`reset`、`checkout`、`clean`、`git add` 或 `git commit`。
- 静态检查无法形式化证明混合 TSX/Vue 文件完全没有行为变化，最终仍需审查 Git diff。

## 成本控制

- 一次初始视觉分析；
- 一次可选的确定性修改前视觉基线；
- 一次统一检查，最多返回三个视觉热点；
- 每个热点最多一个修复提示和两个源码位置，不增加截图；
- 最多一次定向修复和复查；
- 默认不按区域或组件拆分多个 Agent；
- 成功时只输出摘要，不回传完整日志；
- 目标项目内零 QA 中间产物。

## 环境

- Git 和 Python 3.10+
- Pillow：`pip install -r scripts/requirements.txt`
- 可选浏览器对比需要 Node.js 18+ 和 Playwright：

  ```bash
  npm install
  npm run install:browsers
  ```

## 开发检查

```bash
npm test
npm run check
```

Skill 入口是 [SKILL.md](SKILL.md)。UI-only 详细边界和临时视觉检查说明位于 `references/`，仅在需要时读取。
