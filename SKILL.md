---
name: pixel-twin-lab
description: Build and verify a local pixel-twin workbench from a UI image, screenshot, Image Gen result, or mockup, then drive a full componentization flow into a target project. Use when the user wants to recreate an image-based UI exactly, compare an implementation against a reference image, run reference/rebuilt/overlay/exact-slice modes, produce pixel-diff screenshots, quantify mismatch, separate intermediate artifacts from final project code, or decide whether a design can be made one-pixel-perfect versus component-faithful.
---

# Pixel Twin Lab

Use this skill to turn a UI reference image into a local visual QA workbench:

- `Reference`: the original image as ground truth.
- `Rebuilt`: the coded/component reconstruction.
- `Overlay`: reference over the reconstruction with adjustable opacity.
- `Exact Slice`: raster crops pasted back at measured coordinates to show the bitmap-perfect ceiling.

The goal is not to pretend every coded UI can be one-pixel-perfect. The goal is to make the fidelity tradeoff visible, measurable, and repeatable.

For a full image-to-component implementation with separate intermediate artifacts and final code written into a target project, read `references/componentization-workflow.md`.

English is the default runtime language for this skill. The Chinese mirror is available for human review at `SKILL.zh-CN.md` and `references/componentization-workflow.zh-CN.md`. `agents/openai.yaml` is a Codex-only interface descriptor; the runtime entry points for this skill are this file and `scripts/`.

## Requirements

- Python: `pip install -r scripts/requirements.txt` (Pillow is required; numpy is recommended — the scripts fall back to a slower pure-PIL path without it).
- Screenshots: the full `playwright` package (bundled Chromium), or `playwright-core` plus a system Chrome/Chromium (auto-detected on macOS/Linux/Windows, or set `CHROME_PATH`).
- Verify before a run: `python3 -c "import PIL"` and `node -e "require('playwright-core')"` (or `playwright`).

## Decision Rules

- If the user asks for "one pixel exact", preserve the original bitmap or use raster slices; say that true `0%` diff is not the same as a maintainable app UI.
- If the user asks for a real app, build components in `Rebuilt` mode and use the diff as a calibration loop.
- If the reference is an AI-generated UI image, assume there are no real layers, tokens, or asset sources; extract them from the bitmap.
- If `prepare_lab.py` warns that the background is not a uniform solid color (`background_uniform: false` in `lab-config.json`), rerun with `--full-bleed` before trusting exact-mode numbers. The warning is a border-sampling heuristic; a component touching the image edge can also trigger it.
- If the reference is a light, low-contrast, or complex dashboard and auto-detected slices are missing or obviously incomplete (few slices, low `coverage_pct` in `lab-config.json`), do not keep tuning `--threshold`. Measure component bounds from the reference image, write a `slice-manifest.json`, and rerun with `--manifest`. Name manifest regions after `component-map.md` regions so slices, metrics, and the ledger share one vocabulary.
- If `exact-capture.png` is far from `0%` while `slice_source` is `auto`, treat auto slicing as untrusted before judging the implementation. Run a full-bleed exact proof, then create a named manual manifest for reusable exact/island regions.
- Before trusting any diff number, prove the environment with a zero baseline: capture `reference` mode and diff it against the reference image — it must be `0%`. A nonzero baseline means the rendering environment is broken (wrong viewport or device scale, color profile not sRGB, font substitution, or the wrong server/port answering), so fix the environment before touching the reconstruction.
- For regions a coded component cannot faithfully reproduce — maps, photos, avatars, complex charts, logo/display text — use the hybrid strategy: componentize the shell, layout, and interactions, and keep those regions as bitmap slice islands declared in `slice-manifest.json` and marked as islands in the ledger.
- Pick one fidelity track per run: `bitmap exact` (raster slices and SVG replicas allowed; `0%` is achievable) or `component faithful` (project-native components; small residual error is expected and acceptable). Report both numbers at handoff, but do not chase both targets with the same artifact.
- After each diff round, run `plan_calibration.py` and `triage_lab.py`. The planner turns per-region metrics into repair passes; triage decides whether the next action is environment repair, manual manifest, skeleton bootstrap, slice-island merge, layout/token repair, or region rebuild.
- When triage says `manual-manifest`, `merge-islands`, or the component pass is still structurally far off, run `bootstrap_recovery.py` to produce starter manifests, a component/island ledger, island crops, and a React/CSS scaffold before editing final project code again.
- For a high-fidelity pass that may use model-generated or extracted visual elements, run `bootstrap_recovery.py --asset-provider image2 --asset-policy target --target-match 98`. Treat the generated crops as Image2 element-extraction stand-ins: in production, replace them with actual Image2 extracted/generated assets when available. The `target` policy never assigns assets to component-track regions; if the estimate cannot reach the target with island assets alone, the remaining gap is component rebuild work, not a reason to add more crops.
- If the current model cannot extract or recreate an element, rerun recovery with `--asset-provider placeholder`; it creates same-size placeholder images so layout and component contracts stay stable without pretending the bitmap asset exists.
- Component-style restoration is the primary standard. A strict no-asset run satisfies `component_only_98` only when `rebuilt-capture.png` reaches at least 98% strict match with zero generated Image2/placeholder assets. For real UIs with photos/maps/avatars/charts, the practical componentized gate is `componentized_islands_98`: strict match >= 98%, generated assets only in approved `island` tracks, and all component tracks rebuilt as DOM/SVG.
- A sampled-fill or absolute-rectangle skeleton is only a geometry diagnostic. It is not component-style restoration. For `component-only 98`, rebuild regions with semantic DOM/SVG primitives: real text nodes, table/list rows, nav items, controls, vector icons, and chart paths/marks.
- The final deliverable is maintainable in-project components, never a whole-page bitmap. Decompose the screen into region-level components named after the UI (e.g. header, weather card, trip map, timeline rows, bottom nav) and converge each region locally with measured primitives. A full-surface patch — one bitmap covering most or all of the page, however high its match or large its file — is a diagnostic/ceiling artifact only and must never ship as final product code. `fidelity_gate.py` enforces this with asset coverage caps (`--max-asset-coverage`, `--max-single-asset-coverage`); island assets must stay region-scoped (a map tile, a photo), not page-scoped.
- Pixel diff only governs what the browser layout engine renders deterministically (DOM/CSS/SVG). Content with an independent rendering pipeline — canvas charts, map tiles, WebGL/3D scenes, video, Lottie — belongs on the `approximation` track: build it with the appropriate third-party library, restyle it to the reference, and evaluate it per-region (tolerant mismatch + structural comparison via `compare_structure.py`) instead of whole-page strict. The container geometry (position, size, radius, border) is still DOM and still strict. Declare `eval: structural-only` in the ledger for WebGL/3D regions where even tolerant pixel comparison is meaningless across GPUs.
- When `component_only_98` or `componentized_islands_98` fails, run `component_primitives.py` before another rebuild pass. It converts named-region metrics, ledger tracks, and DOM evidence into a primitive worklist so the next iteration targets text, rows, cards, controls, icons, tables, and SVG marks instead of adding more bitmap assets.
- Before editing a `component-required` region, run `measure_primitives.py` on the reference crop. Do not add primitives by eye: measured boxes for text lines, controls, icons, cards, dividers, and SVG marks must guide CSS geometry.
- After editing measured primitives, run `compare_region_metrics.py` against the clean baseline lab. Treat visual completeness as untrusted when strict or tolerant region metrics regress.
- If the task is only analysis, do not edit the app; run measurement and report feasibility.
- If the task is implementation, create the workbench first, then iterate the coded reconstruction against screenshots.
- If the user wants "full flow" or "componentization", require a target project path and project-relative final source directory before writing final product files.
- Keep intermediate artifacts in `<project>/work/pixel-twin-lab/<run-name>/`; keep final code in the target project's source tree.
- Before componentizing, inspect the target project and follow its detected framework, router, component organization, and styling system.
- Default to React + Tailwind only when no existing frontend framework or style system is detectable.

## Workflow

1. Identify the source image and copy it into the project or output folder.
2. Create a lab folder, usually `outputs/pixel-twin/` or `work/pixel-twin/`.
3. Run `scripts/prepare_lab.py` to create the workbench and auto-detected slices.
4. Implement the coded reconstruction inside the generated `rebuilt-layer`.
5. Serve the lab over local HTTP; avoid `file://` because browser tools may block it.
6. Capture `reference`, `rebuilt`, and `exact` modes at the source image's native size with `scripts/capture_modes.cjs`.
7. Run `scripts/pixel_diff.py` to create diff images and JSON metrics.
8. Run `scripts/plan_calibration.py` to generate `calibration-plan.md`; follow its pass order (layout → tokens → islands → rebuild) for the next iteration, and merge `slice-manifest.suggested.json` into your manifest when it appears.
9. Run `scripts/triage_lab.py` to write `triage-report.md`; let that report pick the next optimization pass before making more code edits.
10. If triage calls for manual manifest, islands, or structural recovery, run `scripts/bootstrap_recovery.py` and use its `recovery/component-ledger.md` as the next-pass worklist.
11. For a measurable high-fidelity hybrid pass, run `scripts/materialize_recovery_lab.py` to write the recovery ledger/assets into the lab's `rebuilt` layer, capture again, and compare the actual match percentage.
12. Run `scripts/fidelity_gate.py --target-match 98` before claiming any success. `component_only_98` is the strict no-asset gate; `componentized_islands_98` is the practical componentized gate when only island tracks use extracted assets; `hybrid_asset_98` and `placeholder_contract` are secondary evidence only.
13. If a componentized gate fails, run `scripts/component_primitives.py`, then run `scripts/measure_primitives.py` for the worst `component-required` regions.
14. Rebuild those regions as semantic DOM/SVG primitives using `measured-primitives.md` for geometry, then rerun capture and diff.
15. Run `scripts/compare_region_metrics.py --baseline <clean-lab> --candidate <edited-lab>` and keep or revert each component strategy based on region deltas.
16. Write a short QA result:
   - source path
   - implementation screenshot path
   - viewport
   - mismatch percentage, MAE, max delta
   - worst regions from the per-region metrics
   - fidelity track (`component faithful` or `bitmap exact`) and the list of slice-island regions
   - blocker list or final pass status

## Full Componentization Workflow

Use this when the user wants to turn the reference into maintainable project code.

1. Ask for or infer:
   - source image path
   - target project directory
   - project-relative final code directory
   - run name
2. Initialize the run:

```bash
python /path/to/pixel-twin-lab/scripts/init_component_flow.py \
  --reference /absolute/path/reference.png \
  --project-dir /absolute/path/target-project \
  --final-dir src/features/radar-dashboard \
  --name radar-dashboard
```

3. Inspect the target project before editing final code.
4. Read `component-contract.json` and use its `project_profile` as the implementation constraint.
5. Use the intermediate run folder for all reference images, slices, captures, diffs, plans, and ledgers.
6. Write production code only into the target project final directory or existing project files that own the target route/component.
7. Match the target project:
   - React project: implement React components.
   - Next project: follow App Router or Pages Router conventions.
   - Tailwind project: use Tailwind utilities and existing tokens/classes.
   - CSS/CSS Modules project: use project-native stylesheet/module patterns.
   - Existing UI library: reuse local components and library primitives when they match the reference.
   - No detectable frontend stack: default to React + Tailwind.
8. Capture the actual final app route and rerun pixel diff.
9. Run `plan_calibration.py` and `triage_lab.py` after each iteration; use `triage-report.md` as the pass gate before touching final code again.
10. Run `bootstrap_recovery.py` when the pass gate calls for manual manifest, islands, or structural recovery; adapt its scaffold into project-native code rather than copying it blindly.
11. Update `implementation-ledger.md` after each iteration.
12. Final handoff must link both:
   - the intermediate workbench directory
   - the final project files changed

## Script Usage

Resolve script paths relative to this `SKILL.md`.

Prepare a lab:

```bash
python /path/to/pixel-twin-lab/scripts/prepare_lab.py \
  --reference /absolute/path/reference.png \
  --out-dir /absolute/path/outputs/pixel-twin
```

Add `--full-bleed` to use the whole reference as a single slice (gradient/photo backgrounds). Images above ~2MP are detected on a downsampled copy automatically; slice coordinates are mapped back to native size.

Prepare a lab with a manual slice manifest (low-contrast UIs where threshold detection misses components):

```bash
python /path/to/pixel-twin-lab/scripts/prepare_lab.py \
  --reference /absolute/path/reference.png \
  --out-dir /absolute/path/outputs/pixel-twin \
  --manifest /absolute/path/slice-manifest.json
```

The manifest has the same shape as `regions.json` (a `slices` or `regions` key, or a bare array; each entry `{"name", "x", "y", "width", "height"}`, name optional). It replaces threshold-based auto detection entirely. Canvas area the manifest leaves uncovered is filled with auto-generated `gap-*` slices so exact mode stays complete on any background; pass `--no-cover-gaps` to disable. Named slices carry their `name` into `lab-config.json` and flow into per-region diff metrics automatically. `lab-config.json` records `slice_source` (`auto`/`manifest`/`full-bleed`/`none`) and `coverage_pct`.

Serve the lab:

```bash
cd /absolute/path/outputs/pixel-twin
python3 -m http.server 8787 --bind 127.0.0.1
```

Capture browser screenshots:

```bash
node /path/to/pixel-twin-lab/scripts/capture_modes.cjs \
  --url http://127.0.0.1:8787/ \
  --out-dir /absolute/path/outputs/pixel-twin
```

`--browser bundled|system` picks Playwright's bundled Chromium or a system Chrome (`playwright-core` implies `system`). Chromium is launched with a forced sRGB color profile so captures do not inherit the display profile (otherwise every pixel drifts, especially on macOS). The run writes `capture-meta.json` with the browser version, color profile, and viewport so cross-machine diffs stay attributable.

Generate diff metrics:

```bash
python /path/to/pixel-twin-lab/scripts/pixel_diff.py \
  --reference /absolute/path/outputs/pixel-twin/assets/reference.png \
  --out-dir /absolute/path/outputs/pixel-twin
```

Optional `--tolerance N` additionally reports mismatch ignoring per-channel deltas `<= N` (strict values are always included); useful as a practical convergence target when antialiasing noise dominates.

Generate a calibration plan from the latest capture:

```bash
python /path/to/pixel-twin-lab/scripts/plan_calibration.py \
  --reference /absolute/path/outputs/pixel-twin/assets/reference.png \
  --out-dir /absolute/path/outputs/pixel-twin
```

Defaults to `rebuilt-capture.png` in the out dir (`--capture` overrides). Per region it probes for an integer layout shift (±4px, `--shift-radius`), a uniform color offset, raster-like content complexity, and a flat not-built-yet capture, then writes `calibration-plan.json` and `calibration-plan.md` grouping regions into passes with one-line actions ("move by (-3, 0)", "reference #ffffff vs build #fafaff"). Classification runs on tolerant mismatch (`--tolerance`, default 8) with strict values reported alongside — at tolerance 0, font/antialiasing residue saturates every region to ~100% and blinds the shift and color probes. Regions classified `slice-island` are emitted as a ready-to-merge `slice-manifest.suggested.json`; regions classified `not-built` are emitted as `skeleton.suggested.css` (containers at reference positions with sampled fills) to bootstrap the layout pass. Regions whose residue is antialiasing-level are listed as converged and need no action. Expect mostly `not-built`/`slice-island` on iteration zero — layout/token classifications only become informative once a real skeleton exists (mid-range mismatch).

Generate a triage report from the lab state:

```bash
python /path/to/pixel-twin-lab/scripts/triage_lab.py \
  --out-dir /absolute/path/outputs/pixel-twin
```

Triage reads `lab-config.json`, `capture-meta.json`, `pixel-diff-summary.json`, and the latest `calibration-plan.json` when present, then writes `triage-report.json` and `triage-report.md`. Use it as the pass gate:

- `fix-environment`: reference capture is not `0%`; repair viewport, scale, color profile, or server path first.
- `manual-manifest`: auto slices have low coverage or auto exact mode is far from `0%`; measure named regions, rerun `prepare_lab.py --manifest`, and keep gap slices enabled.
- `bitmap-ceiling-ok`: full-bleed or manifest exact is already near `0%`; use it as proof of the exact ceiling, then return to component work.
- `skeleton-first`: regions are not built yet; apply `skeleton.suggested.css` before styling details.
- `merge-islands`: raster-like regions should become slice islands, not hand-coded components.
- `layout-token-pass`: geometry or token drift is measurable; fix layout before color.
- `region-rebuild`: remaining structural mismatches need region-by-region rebuilds.
- `converged`: the current fidelity target is met or no blocking issue was detected.

Bootstrap concrete recovery artifacts:

```bash
python /path/to/pixel-twin-lab/scripts/bootstrap_recovery.py \
  --out-dir /absolute/path/outputs/pixel-twin \
  --asset-provider image2 \
  --asset-policy target \
  --target-match 98
```

`bootstrap_recovery.py` reads `regions.json` when present (otherwise lab slices), `triage-report.json`, `calibration-plan.json`, and `pixel-diff-summary.json`, then writes:

- `recovery/slice-manifest.starter.json`: all named regions for a manifest exact proof.
- `recovery/island-manifest.starter.json`: only regions classified as bitmap islands.
- `recovery/asset-manifest.starter.json`: regions selected for Image2 extraction or placeholder assets.
- `recovery/component-ledger.md` and `.json`: region bounds, track (`component`/`island`/`approximation`), mismatch, and reason.
- `recovery/recovery-skeleton.css`: absolute geometry scaffold with sampled fills.
- `recovery/RecoveryScaffold.jsx`: intermediate React scaffold for blank or React/Tailwind defaults.
- `recovery/assets/*.png`: Image2 extraction stand-ins or same-size placeholders, depending on `--asset-provider`.

Treat these as intermediate workbench artifacts. For a real project, adapt the ledger and geometry into the detected framework/style system; do not copy the scaffold into production unchanged.

Asset provider rules:

- `--asset-provider image2`: use reference crops as stand-ins for Image2-extracted elements; replace with real Image2 output when the workflow has access to it.
- `--asset-provider placeholder`: create same-size neutral placeholder images for models that cannot extract/regenerate the element.
- `--asset-provider none`: write the ledger and scaffold without generated assets.

Asset policy rules:

- `ledger-islands`: only regions classified as islands receive assets.
- `non-component`: islands and approximation regions receive assets.
- `target`: promote the worst regions to assets until the estimated match reaches `--target-match`. Component-track regions are never promoted unless `--allow-component-assets` is passed (bitmap-ceiling diagnostic only); when assets alone cannot reach the target, the estimate stays honest and names the gap as DOM/SVG rebuild work.
- `all-regions`: every named region receives an asset; use only for a bitmap-exact ceiling or a diagnostic run.

Materialize recovery assets into the lab for screenshot QA:

```bash
python /path/to/pixel-twin-lab/scripts/materialize_recovery_lab.py \
  --out-dir /absolute/path/outputs/pixel-twin
```

This rewrites the lab's `rebuilt` layer from `recovery/component-ledger.json`, preserving `.before-recovery` backups. By default only `island,approximation` track assets render as `<img>` (`--asset-tracks`); component-track regions render as skeleton sections even when the ledger assigned them assets, with a warning listing what was skipped — component regions must be rebuilt as DOM/SVG, never materialized as crops. Pass `--asset-tracks all` only for an explicit bitmap-collage diagnostic. Note it overwrites `index.html`: hand-written component HTML in the lab is only preserved in the first `.before-recovery` backup.

Gate fidelity without mixing result types:

```bash
python /path/to/pixel-twin-lab/scripts/fidelity_gate.py \
  --out-dir /absolute/path/outputs/pixel-twin \
  --target-match 98
```

`fidelity_gate.py` reads `pixel-diff-summary.json`, `recovery/component-ledger.json`, and `index.html`/`regions.json` asset evidence, then writes `fidelity-gate.json` and `fidelity-gate.md`. All fidelity gates additionally require a proven zero baseline (`reference-capture.png` mismatch <= `--reference-target`, default 0.01%); an unproven environment fails every gate.

- `component_only_98`: passes only when strict match is at least 98% and no generated assets are present.
- `componentized_islands_98`: passes when strict match is at least 98%, generated assets appear only on approved island tracks (`--allowed-asset-tracks`, default `island`), and asset coverage stays region-scoped: total generated-asset area <= `--max-asset-coverage` (default 40% of the page) and no single asset above `--max-single-asset-coverage` (default 30%). A page-sized surface patch fails this gate by design. Asset regions without verifiable bounds (in the ledger or `regions.json`) also fail it.
- `componentized_approximation_98`: for runs with declared `approximation`-track regions (third-party charts/maps/3D). Passes when the component-track strict match (whole page minus approximation regions) is at least 98%, island assets are compliant, and every approximation region passes its own evaluation: tolerant mismatch <= `--approximation-tolerant-max` (default 25%) for `eval: tolerant+structural`, pixel-exempt for `eval: structural-only`. Structural results are read from `structural-comparison.json` (run `compare_structure.py`); pass `--require-structural` to make a missing structural report a failure instead of a warning.
- `hybrid_asset_98`: passes when strict match is at least 98% with Image2-extract assets; this is not component-style success.
- `placeholder_contract`: records same-size placeholders for models that cannot extract elements; this is never a fidelity pass by itself.
- If `component_only_98` and `componentized_islands_98` both fail, do not try to pass by materializing `recovery-skeleton.css` or by placing assets over component regions; rebuild the worst named component regions as project-native DOM/SVG components and rerun the gate.

Structurally verify approximation regions (third-party charts/maps/3D) against the reference:

```bash
python /path/to/pixel-twin-lab/scripts/compare_structure.py \
  --out-dir /absolute/path/outputs/pixel-twin
```

`compare_structure.py` reads approximation-track regions from `recovery/component-ledger.json` (or `--regions` plus `regions.json`), detects primitive boxes on both the reference crop and the rebuilt capture crop, then compares primitive count (`--max-count-delta-pct`), mean matched position delta (`--max-position-delta`), and foreground mean color (`--max-palette-delta`). It writes `structural-comparison.json`/`.md` and overlay PNGs under `structural-measurements/`; `fidelity_gate.py` consumes the JSON for `componentized_approximation_98`. For charts disable animations and pin `devicePixelRatio` before capturing; for maps use a fixed style or mocked tiles, otherwise both pixel and structural comparisons are noise.

Generate a component primitive worklist after a failed componentized gate:

```bash
python /path/to/pixel-twin-lab/scripts/component_primitives.py \
  --out-dir /absolute/path/outputs/pixel-twin \
  --target-match 98
```

`component_primitives.py` reads `regions.json`, `pixel-diff-summary.json`, `recovery/component-ledger.json`, and `index.html`, then writes `component-primitives.json` and `component-primitives.md`. Use the worklist for the next rebuild pass. Regions marked `component-required` must be rebuilt as project-native DOM/SVG primitives; regions marked `approved-island` may keep measured assets; regions marked `asset-disallowed` cannot be used to satisfy a componentized gate.

Measure primitive boxes before editing a component-required region:

```bash
python /path/to/pixel-twin-lab/scripts/measure_primitives.py \
  --out-dir /absolute/path/outputs/pixel-twin \
  --regions kpi-row,topbar
```

`measure_primitives.py` reads the reference image and the component primitive worklist, then writes `measured-primitives.json`, `measured-primitives.md`, and `primitive-measurements/*-primitive-overlay.png`. Use these measured boxes to place text, icons, controls, dividers, cards, and SVG marks. If a manual component edit makes the pixel diff worse, treat it as proof that the primitive geometry was guessed rather than measured.

Compare an edited region against a clean baseline:

```bash
python /path/to/pixel-twin-lab/scripts/compare_region_metrics.py \
  --baseline /absolute/path/outputs/pixel-twin-baseline \
  --candidate /absolute/path/outputs/pixel-twin-candidate \
  --regions kpi-row \
  --fail-on-strict-regression
```

`compare_region_metrics.py` writes `region-metric-comparison.json` and `.md` in the candidate lab. Use it after A/B variants such as DOM text vs SVG primitives. A candidate that improves tolerant mismatch but regresses strict mismatch is only partial evidence; continue tuning before claiming componentized progress.

Per-region metrics are on by default (`--regions auto`): every slice in `lab-config.json` gets its own mismatch/MAE/max-delta entry (slice diff), and an optional `regions.json` in the out dir adds named rectangles (component diff). Region results are sorted worst-first under `regions` in `pixel-diff-summary.json`; `--regions none` disables, or pass a JSON file path. Name `regions.json` entries after `component-map.md` regions so map, metrics, and ledger share one vocabulary:

```json
{"regions": [{"name": "sidebar", "x": 0, "y": 0, "width": 220, "height": 800}]}
```

Initialize a full componentization run:

```bash
python /path/to/pixel-twin-lab/scripts/init_component_flow.py \
  --reference /absolute/path/reference.png \
  --project-dir /absolute/path/project \
  --final-dir src/path/for/final/component \
  --name short-run-name
```

Pass `--manifest /absolute/path/slice-manifest.json` (and optionally `--no-cover-gaps`) to forward a manual slice manifest to the lab preparation step.

## Output Contract

Create or update these artifacts:

- `index.html`, `styles.css`, `script.js`
- `assets/reference.png`
- `assets/slice-*.png` when slices are detected, manifest-defined, or gap-generated
- `lab-config.json` (includes `background_uniform`, `slice_source`, `coverage_pct`; manifest slices carry `name`)
- `reference-capture.png`
- `rebuilt-capture.png`
- `exact-capture.png`
- `capture-meta.json`
- `*-diff.png`
- `pixel-diff-summary.json` (includes per-region metrics when slices or `regions.json` exist)
- `calibration-plan.json` and `calibration-plan.md` after each `plan_calibration.py` round
- `triage-report.json` and `triage-report.md` after each `triage_lab.py` round
- `fidelity-gate.json` and `fidelity-gate.md` before claiming component, hybrid, or placeholder success
- `structural-comparison.json`, `structural-comparison.md`, and `structural-measurements/*.png` when approximation-track regions (third-party charts/maps/3D) are part of the run
- `component-primitives.json` and `component-primitives.md` after failed componentized gates
- `measured-primitives.json`, `measured-primitives.md`, and `primitive-measurements/*.png` before component-required region edits
- `region-metric-comparison.json` and `region-metric-comparison.md` after component variants are tested against a clean baseline
- optional `recovery/` folder from `bootstrap_recovery.py` with starter manifests, Image2/placeholder assets, a component ledger, skeleton CSS, and a React scaffold
- optional `slice-manifest.suggested.json` with island regions proposed by the planner
- optional `skeleton.suggested.css` bootstrapping containers for regions the planner marks as not built
- optional `slice-manifest.json` with manually measured named slice rectangles
- optional `regions.json` naming component-diff rectangles
- optional `design-qa.md` for handoff

For full componentization, also create:

- `<project>/work/pixel-twin-lab/<run-name>/component-contract.json`
- `<project>/work/pixel-twin-lab/<run-name>/component-map.md`
- `<project>/work/pixel-twin-lab/<run-name>/implementation-ledger.md`
- `<project>/<final-dir>/` containing the final component/source files

`component-contract.json` must include `project_profile` with framework, routing, style system, UI libraries, source roots, package manager, and defaults applied. `framework` may be `unknown` when nothing is detectable; the React + Tailwind default is a decision rule for you, not something the detector asserts. `init_component_flow.py` does not create `<final-dir>` — it is created when final code is written.

## Fidelity Interpretation

- `0% mismatch`: the browser screenshot is pixel-identical to the reference.
- `Exact Slice` near `0%`: only when the slices cover all non-background content. With auto detection that requires a uniform solid background and a threshold that catches every component; low-contrast regions (e.g. white cards on light gray) fall below the threshold and are filled with the background color instead — write a `slice-manifest.json` (gap slices complete the coverage), or use `--full-bleed`. Even at `0%` it is raster reconstruction, not componentized.
- `Rebuilt` high mismatch: expected for a first pass; run `plan_calibration.py` and fix in pass order (layout → tokens → islands → rebuild) instead of eyeballing the diff image or hand-sorting regions.
- `Auto exact` high mismatch with `full-bleed exact` at `0%`: the slice detector missed content; the next optimization is manifest work, not component CSS.
- Simple app UIs with large visual blocks can converge through component CSS quickly; dense dashboards, maps, charts, tables, and tiny text usually need the hybrid manifest/slice-island path before component diff becomes meaningful.
- `hybrid_asset_98` is useful evidence that geometry and extraction are correct, but it does not prove component restoration quality when assets cover component tracks. If `componentized_islands_98` fails, continue component-region rebuilds even when hybrid is visually perfect.
- MAE near `0` with nonzero mismatch: usually edge antialiasing, background noise, or compression-like drift.
- Large max delta: usually missing assets, wrong colors, blank regions, wrong crop, or layout drift.

Never claim one-pixel success from visual inspection alone. Use screenshot comparison at the same viewport and device scale.

## Implementation Notes

- Use native image dimensions as the capture viewport.
- Keep `deviceScaleFactor: 1` for deterministic screenshot math.
- Calibrate geometry before color: align canvas size, margins, card positions, column widths, and line heights first — a 2px layout drift turns every color comparison red.
- Sample colors from the reference bitmap (background, borders, card fills, text, shadows) and centralize them as tokens in the project's style system; never write colors by eye.
- Fonts are the largest componentization error source: pin family, weight, size, and line-height explicitly. AI-generated references rarely use a standard font, so text can only be approximated — when a text region stops converging, turn it into an SVG or slice island and record that in the ledger.
- Charts: on the bitmap-exact track replicate with SVG paths/rects, because chart libraries impose their own axes, antialiasing, and point placement; on the component-faithful track use the project's existing chart library.
- Use absolute paths in generated reports.
- Do not hide reference overlays or toolbars in capture except through `?capture=1`.
- Keep temporary capture scripts in `work/` when adapting the workflow for a repo.
- Prefer `outputs/` for user-facing captures and diff images.
- For frontend handoff, include screenshots in the final answer when the user asked to see the effect.
- Never copy the lab template into production source as the final app. The lab is a measuring tool; the final output must follow the target project's framework and conventions.
- Never introduce Tailwind, CSS Modules, an icon set, chart library, or UI kit just because the reference would be easier to build that way; add dependencies only when the project has no suitable convention and the user approves.
