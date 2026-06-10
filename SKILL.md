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
8. Write a short QA result:
   - source path
   - implementation screenshot path
   - viewport
   - mismatch percentage, MAE, max delta
   - worst regions from the per-region metrics
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
9. Update `implementation-ledger.md` after each iteration.
10. Final handoff must link both:
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

`--browser bundled|system` picks Playwright's bundled Chromium or a system Chrome (`playwright-core` implies `system`). The run writes `capture-meta.json` with the browser version and viewport so cross-machine diffs stay attributable.

Generate diff metrics:

```bash
python /path/to/pixel-twin-lab/scripts/pixel_diff.py \
  --reference /absolute/path/outputs/pixel-twin/assets/reference.png \
  --out-dir /absolute/path/outputs/pixel-twin
```

Optional `--tolerance N` additionally reports mismatch ignoring per-channel deltas `<= N` (strict values are always included); useful as a practical convergence target when antialiasing noise dominates.

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

## Output Contract

Create or update these artifacts:

- `index.html`, `styles.css`, `script.js`
- `assets/reference.png`
- `assets/slice-*.png` when slices are detected
- `lab-config.json` (includes `background_uniform`)
- `reference-capture.png`
- `rebuilt-capture.png`
- `exact-capture.png`
- `capture-meta.json`
- `*-diff.png`
- `pixel-diff-summary.json` (includes per-region metrics when slices or `regions.json` exist)
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
- `Exact Slice` near `0%`: only when the slices cover all non-background content — that requires a uniform solid background and a threshold that catches every component. Low-contrast regions (e.g. white cards on light gray) fall below the threshold and are filled with the background color instead; lower `--threshold` or use `--full-bleed`. Even at `0%` it is raster reconstruction, not componentized.
- `Rebuilt` high mismatch: expected for a first pass; drive the calibration loop with the per-region metrics — fix the worst region, recapture, repeat — instead of eyeballing the diff image.
- MAE near `0` with nonzero mismatch: usually edge antialiasing, background noise, or compression-like drift.
- Large max delta: usually missing assets, wrong colors, blank regions, wrong crop, or layout drift.

Never claim one-pixel success from visual inspection alone. Use screenshot comparison at the same viewport and device scale.

## Implementation Notes

- Use native image dimensions as the capture viewport.
- Keep `deviceScaleFactor: 1` for deterministic screenshot math.
- Use absolute paths in generated reports.
- Do not hide reference overlays or toolbars in capture except through `?capture=1`.
- Keep temporary capture scripts in `work/` when adapting the workflow for a repo.
- Prefer `outputs/` for user-facing captures and diff images.
- For frontend handoff, include screenshots in the final answer when the user asked to see the effect.
- Never copy the lab template into production source as the final app. The lab is a measuring tool; the final output must follow the target project's framework and conventions.
- Never introduce Tailwind, CSS Modules, an icon set, chart library, or UI kit just because the reference would be easier to build that way; add dependencies only when the project has no suitable convention and the user approves.
