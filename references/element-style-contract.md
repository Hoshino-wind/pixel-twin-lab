# Element Style Contract — 集成规格

> 状态：**已实现并验证**（非设计稿）。这是 `layer-aware-vlm-redesign.md` 里 lever #1
> ("逐元素测量差当修复信号") 的第一块落地砖。
> 形式定义见 `schemas/element-style-contract.schema.json`。

把"对着截图调 CSS"换成"对着规格值比对账"。组件渲染进 lab → 测渲染后的 DOM 计算样式 →
和契约里每个元素/run 的期望值逐属性比 → 输出**可直接照着改的差值清单**（pass/fail）。
关键：精度来自**测 DOM**（精确、免费），不是测位图（我们卡住的那条）。

## 四端如何串起来

```
1. 契约(作者/VLM)   element-manifest.json 的 element 加 style / runs
                    (schemas/element-style-contract.schema.json)
        │
2. 渲染(codegen)    make_codegen_packets.py 规则 10:run 渲成 data-run 节点,
                    style/runs 的期望值经 token 显式落 CSS,禁用标签默认字重
        │
3. 测量(CV)         measure_dom_elements.cjs:每个 [data-element] 及其 [data-run]
                    子节点用 getComputedStyle 采 size/weight/color/line-height/
                    letter-spacing/align/vertical-align/family/bg/radius
        │
4. 对账(门禁)       verify_elements.py:逐属性比期望 vs 实测,超容差即 problem,
                    每条带 Δ;runs 逐 run 比文本+样式;warn 级只报不 fail
```

**职责边界**：几何/语义识别仍是 VLM 那半的事；本契约只管"识别出来之后，样式有没有忠实落地"。
`style`/`runs` 字段由**作者或 VLM 填**（`init_element_manifest.py` 不强制脚手架它们——契约是可选/增量的，
旧 manifest 无这俩字段时 verify 行为不变）。run 的 `size_hint/weight_hint` 走 VLM 估计→量化到 token
（评审 F3 的路径），**逐 run 像素 OCR 不需要**——一旦渲染成 span，逐 run 字号字重就是 `getComputedStyle` 直接读到的。

## 容差与默认值（verify_elements.py）

| 属性 | 默认容差 | CLI 覆盖 |
|---|---|---|
| font_size_px | ±1.0px | `--max-font-size-delta` |
| font_weight | 0（精确） | `--max-font-weight-delta` |
| color / bg / border 颜色 | 单通道 ≤8 (0-255) | `--max-color-delta` |
| line_height_px | ±2.0px | `--max-line-height-delta` |
| letter_spacing_px | ±0.5px | `--max-letter-spacing-delta` |
| border_radius_px | ±2.0px | `--max-radius-delta` |
| text_align / vertical_align / text_transform | 精确相等 | — |
| font_family | 松匹配（首 family 子串） | — |

per-element / per-run 可用 `style.tolerance` 局部覆盖；`style.severity: warn` 让某条只报不 fail
（用于 font_family 等低置信属性）。named weight（normal/bold）两端都归一化后再比。

## 实测走查（CO₂e，已跑通）

契约（manifest，节选）：
```json
{ "id": "carbon-stat-value", "type": "text", "content": "128 kg CO₂e",
  "runs": [
    {"text":"128","role":"number","style":{"source":"vlm-hint","expected":{"font_size_px":28,"font_weight":600,"color":"#0f172a","vertical_align":"baseline"}}},
    {"text":" kg","role":"unit","style":{"source":"vlm-hint","expected":{"font_size_px":14,"font_weight":400,"color":"#334155","vertical_align":"baseline"}}},
    {"text":"CO₂e","role":"suffix","style":{"source":"vlm-hint","expected":{"font_size_px":11,"font_weight":400,"color":"#94a3b8","vertical_align":"sub"}}}
  ] }
```

bug 渲染（三 run 全 28px/700/baseline，即 `<strong>` 扁平串）→ verify 输出的修复清单：
```
carbon-stat-value (failed):
  run[0] font_weight: 700 vs expected 600 (Δ100)
  run[1] font_size_px: 28 vs expected 14.0 (Δ14.0 > 1.0)
  run[1] font_weight: 700 vs expected 400 (Δ300)
  run[1] color: #0f172a vs expected #334155 (Δch43 > 8)
  run[2] font_size_px: 28 vs expected 11.0 (Δ17.0 > 1.0)
  run[2] font_weight: 700 vs expected 400 (Δ300)
  run[2] color: #0f172a vs expected #94a3b8 (Δch142 > 8)
  run[2] vertical_align: 'baseline' vs expected 'sub'   ← CO₂e 下标丢失,正是溢出裁切的根
```
修对（逐 run 正确样式）→ `pass: true, ok 1/1`。

## 在 rev2 蓝图里的位置

- 这是 §3.5 "细节验收靠元素/run 契约,不靠 strict 像素" 的执行体——契约门给的是 pass/fail + 逐属性 Δ,
  不是像素百分比。tolerant 像素退回回归兜底。
- `runs` 字段与 §1.1 的 `element.runs[]` 同构;`vertical_align` 即 §1.1 的 `baseline_shift`。
- 与 §1.2 `composition`(图地/z 序)正交:那个管"浮层是不是独立 DOM",这个管"每个文字/控件样式忠不忠实"。

## 待接

- `composition`(bg/fg/z)的契约与 verify(浮层必须是可寻址 DOM、计数正交复核)——下一块砖。
- VLM 感知自动产出 `runs`/`style`(目前由作者/VLM 手填;自动化是 §2 的事)。
