# 组件化工作流

当用户需要完整的图片到组件流程，而不仅是像素工作台时，使用这份参考。

## 目录契约

所有中间产物都放在目标项目内：

```text
<project>/
  work/
    pixel-twin-lab/
      <run-name>/
        component-contract.json
        component-map.md
        implementation-ledger.md
        lab/
          index.html
          styles.css
          script.js
          assets/reference.png
          assets/slice-*.png
          *-capture.png
          capture-meta.json
          *-diff.png
          pixel-diff-summary.json
          regions.json
  <final-dir>/
    final component/source files written here
```

`<final-dir>` 由 `init_component_flow.py` 记录但不创建;写入第一个最终代码文件时再创建。

不要把截图、diff 图、精确切片或 lab 专用 HTML 放进产品源码目录。

## 必需输入

- `reference`：源 UI 图片的绝对路径。
- `project-dir`：目标项目的绝对路径。
- `final-dir`：项目相对路径，最终组件代码写入这里。
- `run-name`：工作台 run 的短 slug。
- `framework`：实际项目技术栈；能从文件推断时自动推断。
- `project_profile`：由 `init_component_flow.py` 生成，包含包管理器、框架、路由、样式体系、UI 库、源码根目录、scripts 和默认策略。

如果 `project-dir` 或 `final-dir` 不明确，写最终产物前先询问用户。

## 组件化循环

1. 使用 `scripts/init_component_flow.py` 初始化 run。
2. 写最终代码前先检查目标项目。这是硬性门槛，不是可选优化：
   - 包管理器和 scripts
   - 框架和路由
   - 现有组件/样式约定
   - 资产导入约定
   - 已安装的 UI/组件库
   - 已有图表/图标库
3. 读取 `component-contract.json` 并遵循 `project_profile`：
   - `next-react`：按检测结果遵循 Next App Router 或 Pages Router。
   - `vite-react` 或 `react`：按项目风格编写 React 组件。
   - `tailwind`：使用 Tailwind utilities 和已有 token/class。
   - `css`、`css-modules`、`sass`：使用项目现有样式模式。
   - 已安装 UI 库：只有符合参考图和本地约定时才复用。
   - `unknown`:什么都检测不到;由你按 skill 决策规则自行应用 React + Tailwind 默认。
   - `style_system.defaulted: true`(同步记录在 `defaults_applied`)表示 Tailwind 是兜底,不是检测到的项目约定。
4. 在 `component-map.md` 中填写参考图区域：
   - app shell
   - 导航/侧边栏
   - 顶部应用栏
   - 筛选器/操作区
   - 主数据区/表格区
   - 详情面板
   - 图表/卡片/内容列表
   - 必要的空、加载、错误状态
   把这些区域名同步写进 lab 截图旁的 `regions.json`,让 `pixel_diff.py` 用同一套词汇输出组件级指标。
5. 分片构建：
   - 先做页面 shell 和布局几何。
   - 再加字体和 token。
   - 再做重复组件。
   - 只有当源图内容无法用代码表达时，才把图像资产加入最终项目。
   - 静态几何稳定后，再加交互状态。
6. 用与参考图相同的 viewport 捕获最终 App 路由。
7. 对 App 截图运行 `pixel_diff.py`，或把 App 截图复制到 lab 作为 `rebuilt-capture.png`。分区指标(切片 + `regions.json`)会告诉你下一个该修哪个组件。
8. 每轮迭代在 `implementation-ledger.md` 中记录：
   - 修改过的文件
   - 截图路径
   - mismatch 指标(整体 + 最差区域)
   - 剩余 P0/P1/P2 问题

## 技术栈和样式规则

- 项目已有样式体系时，不要引入新的样式体系。
- 现有项目如果使用 CSS Modules、Sass、styled-components、Emotion、Ant Design、MUI、Chakra 或其他明确体系，不要擅自安装 Tailwind，除非用户同意。
- 不要把 Next 项目改成 Vite，也不要把 Vite 项目改成 Next route。
- 只有空项目或无法检测到前端框架与样式体系时，才默认 React + Tailwind。
- 如果检测结果模糊，先检查 `src`、`app`、`pages`、`components` 下的代表性文件，再决定。
- 如果项目已有 design tokens、theme 文件或共享 UI primitives，优先使用它们，不要另起一套局部 token。

## 最终产物规则

- 最终代码写入目标项目自己的源码树，不写入 lab 文件夹。
- 遵循项目现有框架和样式系统，不要把 lab 模板硬塞进生产代码。
- 最终源码中只保留项目实际使用的资产；参考图和切片留在中间 run 文件夹。
- 如果像素完美结果依赖位图切片，标记为“bitmap-perfect”，不要称为可维护组件实现。
- 如果组件实现仍有视觉差异，报告具体 mismatch 和下一步校准动作。

## 交付检查清单

- 中间 run 文件夹存在，并包含 `component-contract.json`。
- 最终代码文件位于 `<project>/<final-dir>` 下。
- 可行时，目标项目可以 build 或 run。
- 已按相同 viewport 捕获截图。
- 已附上 `pixel-diff-summary.json` 或等价指标。
- 最终回答同时链接最终文件和中间工作台。
