# Pixel Twin Lab

[中文说明](README.zh-CN.md)

Turn a UI reference image — a screenshot, a mockup, or an AI-generated design — into a high-fidelity, interactive page in a real project. Pixel Twin Lab forces a decomposition-first pipeline: route every slice by content type (charts to a chart library fed mock data, icons/images to cropped assets, maps/3D to third-party libraries), decompose the image into a six-layer engineering blueprint (visual layout, component semantics, design tokens, data content, interaction behavior, project implementation), generate project-native components from the blueprint, and verify the result with browser screenshots and pixel-level metrics, so "looks the same" becomes a number instead of an opinion.

It is designed to run as an agent skill (Claude Code / Codex), but every step is a plain Python or Node script you can also run by hand.

## Why this exists

Vibe coding can move fast, but image-to-UI work often gets stuck in a fuzzy loop:

- "Looks close" is subjective, and the reviewer has no number to decide whether the next edit helped.
- A full-page screenshot diff tells you something is wrong, but not which component is responsible.
- AI-generated UI images have no real layers, tokens, assets, or component boundaries to inspect.
- Agents can accidentally chase a bitmap-perfect collage when the real goal is maintainable components.
- Existing projects already have routers, styling systems, tokens, and UI libraries; a generic rebuild often ignores them.
- There is usually no repeatable backtest record, so every iteration starts from visual guesswork again.

Pixel Twin Lab turns that loop into a measurable workflow: capture the same viewport, compare the same regions, record the same metrics, and decide the next repair pass from evidence instead of vibes.

## What it does

For each reference image, the lab generates an HTML workbench with four modes:

- **Reference** — the original image as ground truth.
- **Rebuilt** — your coded/component reconstruction.
- **Overlay** — the reference layered over the reconstruction with adjustable opacity.
- **Exact Slice** — raster crops pasted back at measured coordinates, showing the bitmap-perfect ceiling.

The point is not to pretend every coded UI can be one-pixel-perfect. The point is to make the fidelity tradeoff **visible, measurable, and repeatable**: a pixel-diff image, a mismatch percentage, MAE, max delta, and per-region metrics that tell you exactly which part of the UI is worst.

Beyond measurement, the skill drives a full componentization flow: it inspects a target project, follows its framework and styling conventions, writes production components into the project's source tree, and keeps all intermediate artifacts (slices, captures, diffs, ledgers) in a separate work folder.

## Backtest and evaluation data

This repository does not ship a fixed benchmark corpus yet. Its backtest data is generated per run, so every reconstruction can be replayed and compared against the same reference image:

- `capture-meta.json` records the browser, viewport, device scale, color profile, and captured modes.
- `pixel-diff-summary.json` records strict mismatch, tolerant mismatch, MAE, max delta, bounding box, and per-region metrics.
- `*-diff.png` files show heatmaps of the actual visual error.
- `calibration-plan.json` and `calibration-plan.md` classify regions into skeleton, layout, token, slice-island, and rebuild passes.
- `triage-report.json` and `triage-report.md` explain the next action before another implementation edit.
- `fidelity-gate.json` and `fidelity-gate.md` separate component-only, componentized-islands, approximation, hybrid asset, and placeholder results.
- `component-primitives.md`, `measured-primitives.md`, and `region-metric-comparison.md` provide the next worklist when a componentized gate does not pass.

The key evaluation signals are:

- **Zero baseline**: `reference-capture.png` must diff near `0%` against the original reference before any fidelity number is trusted.
- **Strict match**: `100 - mismatch_pct`, used by the 98% gates.
- **Tolerant match**: ignores tiny per-channel deltas, useful for antialiasing and font residue.
- **Worst regions**: per-region mismatch ranks the next component to repair.
- **Asset coverage**: generated assets are counted and limited so a bitmap patch cannot be mislabeled as component restoration.

## What it can do

- Build a local visual QA workbench from a UI screenshot, design mockup, or AI-generated screen.
- Capture reference, rebuilt, overlay, and exact-slice modes in a real browser at the reference image's native size.
- Generate pixel-diff heatmaps and JSON metrics for the whole canvas and named regions.
- Prove whether the screenshot environment is valid before spending time on CSS or components.
- Separate bitmap-exact proof from maintainable component reconstruction.
- Keep charts, maps, photos, avatars, and dense media as explicit slice islands when hand-coding them would be the wrong tradeoff.
- Initialize a full componentization run inside a target project while respecting its framework, router, styling system, and UI libraries.
- Produce recovery scaffolds, component ledgers, primitive worklists, and fidelity gates for iterative agent workflows.

## Problems it solves

- Replaces subjective visual review with repeatable metrics.
- Prevents agents from hiding poor component work behind full-page raster patches.
- Makes dense dashboards and low-contrast SaaS UIs debuggable region by region.
- Gives teams a before/after record for every reconstruction pass.
- Keeps temporary lab artifacts out of production source code.
- Makes it clear when the deliverable is component-faithful, bitmap-exact, hybrid asset based, or only a placeholder contract.

## Requirements

- **Python 3** with [Pillow](https://pillow.readthedocs.io/) and numpy (both required):

  ```bash
  pip install -r scripts/requirements.txt
  ```

- **Node.js** with the full `playwright` package and bundled Chromium:

  ```bash
  npm install
  npm run install:browsers
  ```

## Quick start

```bash
# 1. Create the workbench from a reference image
python scripts/prepare_lab.py \
  --reference /absolute/path/reference.png \
  --out-dir /absolute/path/outputs/pixel-twin

# 2. Implement your reconstruction in the generated rebuilt-layer, then serve the lab
python3 -m http.server 8787 \
  --bind 127.0.0.1 \
  --directory /absolute/path/outputs/pixel-twin

# 3. From the project root, capture reference / rebuilt / exact modes at native size
node scripts/capture_modes.cjs \
  --url http://127.0.0.1:8787/ \
  --out-dir /absolute/path/outputs/pixel-twin \
  --browser bundled

# 4. Generate diff images and metrics
python scripts/pixel_diff.py \
  --reference /absolute/path/outputs/pixel-twin/assets/reference.png \
  --out-dir /absolute/path/outputs/pixel-twin
```

For Codex runs, execute `node scripts/capture_modes.cjs` directly from the project
root. Do not wrap the command in `/bin/zsh -lc`, do not prefix it with `env`,
`cd`, shell redirection, or an absolute Node path. The command must begin with
`node scripts/capture_modes.cjs` so the approved Codex prefix matches. The
default path uses bundled Playwright Chromium and does not automatically fall
back to system Chrome, because launching system Chrome requires GUI/sandbox
escalation in Codex. `capture-meta.json` records the requested and actual
browser channel. Backtests must use `--browser bundled`. `--browser system` is
blocked unless `PIXEL_TWIN_ALLOW_SYSTEM_BROWSER=1` is set for one-off local
debugging outside automated runs. The scripts ignore `PIXEL_TWIN_BROWSER=system`;
use the command-line flag plus the explicit debug environment variable instead.

The diff step writes `pixel-diff-summary.json` with overall and per-region mismatch / MAE / max-delta, plus `*-diff.png` heatmaps. Iterate on the worst region, recapture, and re-diff until the numbers converge.

For the full image-to-component flow into an existing project, start with `scripts/init_component_flow.py` and read [`references/componentization-workflow.md`](references/componentization-workflow.md).

## Scripts

| Script | Purpose |
| --- | --- |
| `scripts/prepare_lab.py` | Build the workbench from a reference image, auto-detect component slices (`--full-bleed` for gradient/photo backgrounds, `--manifest` for hand-measured named slices on low-contrast UIs that threshold detection misses) |
| `scripts/capture_modes.cjs` | Screenshot reference/rebuilt/exact modes in a real browser at native size, with `capture-meta.json` for attribution |
| `scripts/pixel_diff.py` | Produce diff images and JSON metrics, including per-slice and named-region breakdowns |
| `scripts/plan_calibration.py` | Turn per-region diffs into an ordered repair plan (skeleton → layout → tokens → slice islands → rebuild), with ready-to-use skeleton CSS and island manifest suggestions |
| `scripts/triage_lab.py` | Read lab config, diff metrics, and calibration output, then decide the next pass: environment, manual manifest, skeleton, islands, layout/tokens, or region rebuild |
| `scripts/bootstrap_recovery.py` | Convert triage/planner output into starter manifests, a component/island ledger, island image crops, skeleton CSS, and an intermediate React scaffold |
| `scripts/fidelity_gate.py` | Gate results without mixing types: component-only / componentized-islands / componentized-approximation / hybrid / placeholder, with baseline, asset-coverage, and element-asset policy enforcement so icon/image assets can pass while component/text crops cannot prove componentized restoration |
| `scripts/compare_structure.py` | Structurally compare approximation-track regions (third-party charts/maps/3D) between reference and rebuilt capture: primitive counts, position deltas, foreground palette |
| `scripts/extract_element_assets.py` | Promote measured icon/avatar/media primitives, prominent navigation controls, KPI sparkline/progress fragments, timeline marker strips, conservative colorful connected-component icons/illustrations, and routed island/approximation regions into `element-assets.json` plus cropped assets for codegen; accepts an explicit `asset-plan.json` so a perception/manual layer can choose asset islands while the script only crops, and rejects component crops in that plan |
| `scripts/extract_text_elements.py` | Extract high-confidence OCR text runs into `text-elements.json`, including upscaled per-region OCR and redundant merged-line pruning, and optionally merge them into `element-manifest.json` as verifiable text elements |
| `scripts/init_element_manifest.py` | Scaffold the element manifest (element → component mapping) from measured primitive boxes and merge `element-assets.json`; repeated row geometry is promoted into one data-driven `collection` element, stale asset references are pruned on reruns, and nested card media stays as separate image elements instead of being stretched into larger card primitives |
| `scripts/materialize_element_manifest_lab.py` | Render an element-manifest driven rebuilt layer for diagnostic layout/asset QA, consuming declared element assets as DOM `<img>` nodes, suppressing OCR/asset-covered placeholders, rendering nested parent containers as borderless background fills, and inferring conservative text/group control shells for chips/buttons |
| `scripts/measure_dom_elements.cjs` | Measure rendered `[data-element]` and `[data-component]` nodes in the rebuilt layer (geometry in reference coordinates, text, tag, svg/img evidence, asset identity, owner component, computed surface style) |
| `scripts/verify_elements.py` | Verify the rendered DOM against the element manifest and optional `ui-blueprint.json`: element presence/geometry/text/type/asset contracts plus component-root layout, surface style, and element ownership |
| `scripts/extract_tokens.py` | Extract design tokens from the reference: color clusters with verifiable sample coordinates, type sizes from measured text boxes, spacing scale from measured gaps |
| `scripts/infer_layout.py` | Infer flex/grid layout relations (row/column/grid/stack) from measured boxes, with confidence scores and explicit absolute-fallback |
| `scripts/validate_blueprint.py` | Gate the six-layer `ui-blueprint.json`: schema checks, cross-layer references, mandatory complete component surface/icon-asset contracts, component root bounds backed by measured region/primitive evidence, and reconciliation against measurements and reference pixels — code generation is blocked while it fails |
| `scripts/make_region_packets.py` | Cut per-region work packets (crop, measurements, tokens, fragment template) for parallel decompose subagents that each see only one region |
| `scripts/merge_blueprint.py` | Deterministically merge region fragments into `ui-blueprint.json`: id uniqueness, token dedup with reference rewriting, default implementation plan |
| `scripts/make_codegen_packets.py` | Cut per-component codegen packets containing no image paths — codegen subagents implement from blueprint data only; refuses to run while blueprint validation fails |
| `scripts/init_component_flow.py` | Initialize a componentization run against a target project (contract, map, ledger) |

## Using it as an agent skill

- **Claude Code**: link or copy this directory into `~/.claude/skills/`. The runtime entry point is [`SKILL.md`](SKILL.md), which contains the decision rules, workflow, and output contract the agent follows.
- **Codex**: `agents/openai.yaml` is a Codex-only interface descriptor; it is not used by the Claude Code runtime.

English is the default runtime language. Chinese mirrors are maintained for human review: [`SKILL.zh-CN.md`](SKILL.zh-CN.md) and [`references/componentization-workflow.zh-CN.md`](references/componentization-workflow.zh-CN.md).

## Repository layout

```
SKILL.md                  Agent runtime entry point (decision rules, workflow, output contract)
SKILL.zh-CN.md            Chinese mirror of SKILL.md
scripts/                  Python/Node tools (prepare, capture, diff, componentization init)
references/               Full componentization workflow (EN + zh-CN)
references/component-taxonomy.md  Ant Design-inspired component category/type taxonomy
assets/prototype-template Workbench HTML/CSS/JS template
agents/openai.yaml        Codex-only interface descriptor
```

## License

[Apache 2.0](LICENSE)
