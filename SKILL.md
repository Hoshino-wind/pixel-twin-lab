---
name: pixel-twin-lab
description: Decompose a UI image (screenshot, Image Gen result, or mockup) into a six-layer engineering blueprint — visual layout, component semantics, design tokens, data content (mock data), interaction behavior, project implementation — route each slice by content type (charts to ECharts, icons/images to cropped assets, maps/3D to third-party libraries), then generate a high-fidelity, interactive page in a target project and verify it with screenshot backtesting. Use when the user wants to turn a UI image into real project components, recreate an image-based UI faithfully, compare an implementation against a reference image, produce pixel-diff screenshots, quantify mismatch, or decide whether a design can be made one-pixel-perfect versus component-faithful.
---

# Pixel Twin Lab

This skill is not about pasting the picture back more accurately. It forces a decomposition-first pipeline: **decompose the UI image into page engineering structure (the blueprint), then write code from the blueprint, then backtest with screenshots.** Direct image-to-code generation loses fidelity because the model eyeballs geometry; here every number the code uses comes from measurement, and every claim is verified by capture and diff.

The static HTML lab is a measuring instrument, not the deliverable:

- `Reference`: the original image as ground truth.
- `Rebuilt`: the coded/component reconstruction under test.
- `Overlay`: reference over the reconstruction with adjustable opacity.
- `Exact Slice`: raster crops pasted back at measured coordinates — a diagnostic ceiling proof only, never a deliverable.

For a full image-to-component implementation with separate intermediate artifacts and final code written into a target project, read `references/componentization-workflow.md`.

English is the default runtime language for this skill. The Chinese mirror is available for human review at `SKILL.zh-CN.md` and `references/componentization-workflow.zh-CN.md`. `agents/openai.yaml` is a Codex-only interface descriptor; the runtime entry points for this skill are this file and `scripts/`.

## Requirements

- Python: `pip install -r scripts/requirements.txt` (Pillow is required; numpy is recommended — the scripts fall back to a slower pure-PIL path without it).
- Screenshots: the full `playwright` package (bundled Chromium), or `playwright-core` plus a system Chrome/Chromium (auto-detected on macOS/Linux/Windows, or set `CHROME_PATH`).
- Verify before a run: `python3 -c "import PIL"` and `node -e "require('playwright-core')"` (or `playwright`).

## Blueprint Workflow (primary)

This is the main flow for "turn this UI image into a page in my project". The lab measurement loop below serves Phases 1 and 5; it is not a separate goal.

**Phase 0 — Project probe.** Run `init_component_flow.py` against the target project. Read `component-contract.json`'s `project_profile`: framework, styling system, UI libraries, existing components and tokens. Everything generated later must follow this profile.

**Phase 1 — Measure.** `prepare_lab.py` (instrument setup) → capture `reference` mode and prove the zero baseline → `measure_primitives.py` for every region → `extract_tokens.py` (color clusters, type sizes, spacing scale) → `infer_layout.py` (flex/grid relations with confidence). Bounds, colors, and sizes used anywhere downstream must come from these outputs, never from eyeballing.

**Phase 1.5 — Classify & route.** Run `classify_slices.py --mode init`, look at `classification-sheet.png` **once** (all crops on one labeled sheet), set every slice's `content_type` in `slice-classification.json`, then run `--mode apply`. The resulting `routing-manifest.json` decides each region's track *before* any blueprint or code work:

   - `plain-dom` → component track (DOM/CSS rebuild, the strict pixel loop)
   - `icon` / `image` → island asset (crop, or regenerate with a transparent background when the icon sits on a non-uniform fill)
   - `chart` → approximation track: third-party chart library (default **echarts**) fed mock data
   - `map` → approximation track: third-party map library (default leaflet)
   - `scene-3d` → approximation track: third-party 3D library (default three), `eval: structural-only`

   Visualization content never enters the DOM rebuild loop, and nothing gets hand-built first only to be reclassified by triage later. `bootstrap_recovery.py`, `component_primitives.py`, and `fidelity_gate.py` all read this manifest as the authoritative track source.

**Phase 2 — Blueprint.** Author `ui-blueprint.json` (schema: `schemas/ui-blueprint.schema.json`) by reading the reference crops together with the Phase 1 measurements. Six layers, all required:
   1. *Visual layout* — regions with measured bounds, roles, tracks (adopt the Phase 1.5 routing), and layout relations (adopt `infer_layout.py` output; keep `absolute-fallback` entries explicit).
   2. *Component semantics* — what each thing IS (button, input, tabs, table-row, badge, chart-container...), its extracted text content, and `maps_to` (reuse a project component, extend one, or name a new one).
   3. *Design tokens* — curate `visual-tokens.json` into the blueprint; map to existing project tokens where they exist. Never write CSS values that are not in this layer.
   4. *Data content* — every data-driven component (table, list, chart, kpi group, timeline) declares `shape`, `fields`, `mock_data`, `binding`, and `source`. For lists/tables, `mock_data` is the verbatim transcription of the visible rows as structured records (`source: extracted`) — transcribe into the data layer, not into dozens of separate elements. For charts, read the data approximately (series count, point count, value range, trend; `source: approximated`) and declare the rendering `library`. Code renders FROM this data; hard-coded sibling nodes and hand-drawn data shapes are blueprint defects.
   5. *Interaction behavior* — which controls click, which tabs switch, which filters change data, dialogs, hover/active/loading/empty states. Derived from project conventions > project tokens > element-type defaults; never invented outside the project's system, never "extracted" from the image. Declare the `source` of every entry.
   6. *Project implementation* — per-component plan: reuse/extend/create, target path, generation order, acceptance criteria.

   Then run `validate_blueprint.py`. It schema-checks the blueprint and reconciles it against measurements (bounds vs measured boxes, colors vs reference sampling, references between layers). **Hard gate: no project code is written while validation fails.**

**Phase 3 — Plan.** Write `implementation-plan.md` from the blueprint's implementation layer: component order, reuse mapping, island/approximation declarations, per-component acceptance. This is forward planning, not repair.

**Phase 4 — Generate.** Write project-native components in the target project, in plan order. Rules:
   - Code is generated **from the blueprint, not from the image**. If something looks wrong, fix the blueprint first, revalidate, then regenerate — do not eyeball-patch the code against the picture.
   - Follow the project's framework/styling/UI library (Phase 0 profile). Never paste lab HTML into the project.
   - **Data-driven components render from the blueprint's data layer**: one row/item template mapped over `mock_data` (the collection contract), or the data fed to the declared library via `binding`. Keep `mock_data` in a separate fixture file so it is swappable for real data sources. Inlining the values as hard-coded sibling nodes is a generation bug.
   - **Reuse before writing, write generic when writing**: map `maps_to` to the project's own table/list/card components first. When the project has no equivalent, the blueprint component `type` is the archetype (table, list, kpi-card, tabs, badge, panel shell, chart container) — write that component live, in the project's framework and style system, shaped as a reusable generic component (props for column defs / items / label-value, content arriving only through props) rather than one-off markup with the content baked in. The component is written fresh per project; only its contract (data-driven props, token-driven styles, `data-element` passthrough) is fixed by this skill.
   - Charts/maps/3D go through the approximation track with a third-party library (default echarts / leaflet / three, or the project's existing one) **configured with mock data and restyled with tokens — never drawn**. Photos and icons stay region-scoped island assets (crop, or transparent-background regeneration); hand-draw an SVG icon only when it is a trivial geometric glyph (chevron, plus, dot).
   - Every blueprint component renders DOM with `data-element` ids matching the blueprint's element ids; collections render `data-element` on the container and `data-element-item` on every row.

**Phase 5 — Backtest.** Capture the real app route at the reference viewport → `pixel_diff.py` → `verify_elements.py` (against the blueprint's element ids) → `fidelity_gate.py`. Map every failure back to a blueprint entry or a generation bug; iterate Phases 4↔5 (or 2 when the blueprint itself was wrong) until the gates pass or the residual gap is explicitly reported. Use "N/M elements verified" as the progress meter. **Iteration budget:** track the strict and tolerant match per round; if two consecutive rounds improve the active metric by less than 0.5 percentage points, stop iterating, report the residual gap and its cause, and let the user decide — an unbounded convergence loop on a non-converging metric is the primary token/cost failure mode of this skill. For text-heavy component regions, use tolerant mismatch (tolerance 8) as the iteration signal and report strict alongside; font antialiasing keeps strict mismatch saturated and makes it a misleading loop signal.

Hard rules across phases:

- `ui-blueprint.json` validated before any project code — no exceptions, including "small obvious" components.
- In Phase 4 the reference image is off-limits as a coding input; the blueprint is the only visual source of truth.
- Routing before building: no region is hand-built as DOM before Phase 1.5 classifies it. Charts, maps, and 3D scenes are never drawn by hand (no bespoke SVG axes/marks, no canvas painting) — they are a third-party library plus mock data, on any track.
- Static lab HTML, exact slices, and recovery scaffolds never ship as project code.
- Context economy: read the `.md`/brief views and per-region files (`primitive-measurements/<region>.json`), not the full `*.json` reports; scripts print brief summaries by default — do not re-dump them with `--print full` or `cat`. Visual inspection is one classification sheet in Phase 1.5 plus at most one crop sheet per backtest round; per-region numeric metrics, not screenshots, decide what to fix next.

## Orchestrated Blueprint Workflow (dense UIs)

For dense references — many regions, dozens of components, dashboards with charts/tables/tiny labels — run the same phases with subagent fan-out and information isolation; the full playbook with subagent prompt templates is `references/orchestration-playbook.md`:

1. Phases 0-1 stay serial (orchestrator).
2. Author `blueprint-skeleton.json` (page-level regions + implementation header) yourself, then `make_region_packets.py` cuts one packet per region: crop, measurements, tokens, fragment template, instructions.
3. Dispatch one decompose subagent per region in parallel; each sees only its packet and writes `fragment.json`. Fresh context per region is what keeps labeling precise on dense UIs.
4. `merge_blueprint.py` deterministically merges fragments (id uniqueness, token dedup with reference rewriting, default plan) into `ui-blueprint.json`; then `validate_blueprint.py` — the same hard gate.
5. `make_codegen_packets.py` (it refuses to run while validation fails) cuts one packet per component with **no image paths inside**; dispatch one codegen subagent per component. Isolation makes eyeball-drift impossible rather than merely forbidden.
6. Phase 5 backtest stays with the orchestrator; re-dispatch only the failed components with their failure evidence attached.

If the runtime has no subagent capability, execute the same packets serially in phase order — artifacts and gates are identical to the primary workflow.

## Decision Rules

- Blueprint before code: for any "build this page in my project" task, follow the Blueprint Workflow phases in order. `ui-blueprint.json` validated by `validate_blueprint.py` is the precondition for writing project code; if you find yourself writing components without a validated blueprint, stop and go back to Phase 2.
- Route before rebuilding: if a region's content type is undecided (is this a chart? an icon? a photo?), the answer comes from `classify_slices.py` in Phase 1.5, not from attempting a DOM rebuild and reading the diff. Reclassification through triage is the recovery path, not the default path.
- Data before markup: tables, lists, queues, feeds, and charts are mock data plus a template/library. If generated code contains more than one hard-coded row of repeated content, or any hand-placed chart mark, the data layer is missing or ignored — fix the blueprint, not the markup.
- Code generation reads the blueprint, never the raw image. The image is input to measurement, classification, and blueprint authoring only. "Eyeball the screenshot and adjust the CSS" is the failure mode this skill exists to eliminate.
- Interaction design is derived, not extracted: project conventions first, then project tokens, then element-type defaults — declare the source per entry. A single screenshot carries no interaction information; do not pretend it does.
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
- A sampled-fill or absolute-rectangle skeleton is only a geometry diagnostic. It is not component-style restoration. For `component-only 98`, rebuild regions with semantic DOM primitives: real text nodes, table/list rows rendered from the data layer's mock arrays, nav items, and controls. Icons are island assets (crop or transparent regeneration; hand-drawn SVG only for trivial geometric glyphs), and chart marks are never primitives — charts belong to the approximation track's library.
- The final deliverable is maintainable in-project components, never a whole-page bitmap. Decompose the screen into region-level components named after the UI (e.g. header, weather card, trip map, timeline rows, bottom nav) and converge each region locally with measured primitives. A full-surface patch — one bitmap covering most or all of the page, however high its match or large its file — is a diagnostic/ceiling artifact only and must never ship as final product code. `fidelity_gate.py` enforces this with asset coverage caps (`--max-asset-coverage`, `--max-single-asset-coverage`); island assets must stay region-scoped (a map tile, a photo), not page-scoped.
- Pixel diff only governs what the browser layout engine renders deterministically (DOM/CSS/SVG). Content with an independent rendering pipeline — canvas charts, map tiles, WebGL/3D scenes, video, Lottie — belongs on the `approximation` track: build it with the routed third-party library (default echarts for charts, leaflet for maps, three for 3D, or the project's existing one) **fed mock data from the blueprint's data layer**, restyle it to the reference with tokens, and evaluate it per-region (tolerant mismatch + structural comparison via `compare_structure.py`) instead of whole-page strict. The container geometry (position, size, radius, border) is still DOM and still strict. Declare `eval: structural-only` in the ledger for WebGL/3D regions where even tolerant pixel comparison is meaningless across GPUs.
- When `component_only_98` or `componentized_islands_98` fails, run `component_primitives.py` before another rebuild pass. It converts named-region metrics, ledger tracks, and DOM evidence into a primitive worklist so the next iteration targets text, rows, cards, controls, icons, tables, and SVG marks instead of adding more bitmap assets.
- Before editing a `component-required` region, run `measure_primitives.py` on the reference crop. Do not add primitives by eye: measured boxes for text lines, controls, icons, cards, dividers, and SVG marks must guide CSS geometry.
- After measuring, build the element manifest before writing DOM: run `init_element_manifest.py` to scaffold one entry per measured box, then label every element yourself by looking at the reference crops — `type` (text/icon/control/...), `content` (extracted text or a semantic description), and `maps_to` (target component and slot). This is the element → component mapping; measurement gives geometry, you give semantics. Rebuilt DOM nodes must carry `data-element="<id>"` so the contract is checkable.
- After rebuilding, run `measure_dom_elements.cjs` (live geometry of every `data-element` node in reference coordinates) and `verify_elements.py` (presence, geometry, text content, type compatibility per element). A bitmap patch cannot satisfy this contract — it has no individually addressable elements — so it closes the collage loophole at the semantic level. Once `element-manifest.json` exists, the componentized gates require the element contract to pass.
- After editing measured primitives, run `compare_region_metrics.py` against the clean baseline lab. Treat visual completeness as untrusted when strict or tolerant region metrics regress.
- If the task is only analysis, do not edit the app; run measurement and report feasibility.
- If the task is implementation, create the workbench first, then iterate the coded reconstruction against screenshots.
- If the user wants "full flow" or "componentization", require a target project path and project-relative final source directory before writing final product files.
- Keep intermediate artifacts in `<project>/work/pixel-twin-lab/<run-name>/`; keep final code in the target project's source tree.
- Before componentizing, inspect the target project and follow its detected framework, router, component organization, and styling system.
- Default to React + Tailwind only when no existing frontend framework or style system is detectable.

## Lab Measurement Loop (instrument)

Use this loop inside Phases 1 and 5 of the Blueprint Workflow, or standalone when the task is pure analysis/comparison rather than generation.

1. Identify the source image and copy it into the project or output folder.
2. Create a lab folder, usually `outputs/pixel-twin/` or `work/pixel-twin/`.
3. Run `scripts/prepare_lab.py` to create the workbench and auto-detected slices.
4. Run `scripts/classify_slices.py --mode init`, label every slice from `classification-sheet.png`, then `--mode apply` — tracks are routed before anything is built.
5. Implement the coded reconstruction inside the generated `rebuilt-layer`. Repeated content (tables, lists, KPI rows, tabs) renders through small render-from-data helpers you write in the lab's `script.js` — one template function mapped over a mock data array (container `data-element`, per-row `data-element-item`), never hand-written sibling blocks; approximation regions get their library container, not hand-drawn marks.
6. Serve the lab over local HTTP; avoid `file://` because browser tools may block it.
7. Capture `reference`, `rebuilt`, and `exact` modes at the source image's native size with `scripts/capture_modes.cjs`.
8. Run `scripts/pixel_diff.py` to create diff images and JSON metrics.
9. Run `scripts/plan_calibration.py` to generate `calibration-plan.md`; follow its pass order (layout → tokens → islands → rebuild) for the next iteration, and merge `slice-manifest.suggested.json` into your manifest when it appears.
10. Run `scripts/triage_lab.py` to write `triage-report.md`; let that report pick the next optimization pass before making more code edits.
11. If triage calls for manual manifest, islands, or structural recovery, run `scripts/bootstrap_recovery.py` and use its `recovery/component-ledger.md` as the next-pass worklist.
12. For a measurable high-fidelity hybrid pass, run `scripts/materialize_recovery_lab.py` to write the recovery ledger/assets into the lab's `rebuilt` layer, capture again, and compare the actual match percentage.
13. Run `scripts/fidelity_gate.py --target-match 98` before claiming any success. `component_only_98` is the strict no-asset gate; `componentized_islands_98` is the practical componentized gate when only island tracks use extracted assets; `hybrid_asset_98` and `placeholder_contract` are secondary evidence only.
14. If a componentized gate fails, run `scripts/component_primitives.py`, then run `scripts/measure_primitives.py` for the worst `component-required` regions.
15. Rebuild those regions as semantic DOM primitives using the per-region `primitive-measurements/<region>.json` for geometry, then rerun capture and diff.
16. Run `scripts/compare_region_metrics.py --baseline <clean-lab> --candidate <edited-lab>` and keep or revert each component strategy based on region deltas.
17. Enforce the iteration budget: if two consecutive capture→diff rounds improve the active metric (tolerant for text-heavy component regions, strict otherwise) by less than 0.5 percentage points, stop and report the residual instead of looping.
18. Write a short QA result:
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

Optional `--tolerance N` additionally reports mismatch ignoring per-channel deltas `<= N` (strict values are always included); useful as a practical convergence target when antialiasing noise dominates. Stdout is a brief summary (overall metrics plus the five worst regions per capture); the full per-region data goes to `pixel-diff-summary.json` — read specific entries from there instead of re-running with `--print full`.

Classify slice content and route tracks (Phase 1.5):

```bash
python /path/to/pixel-twin-lab/scripts/classify_slices.py \
  --out-dir /absolute/path/outputs/pixel-twin --mode init
# read classification-sheet.png ONCE, fill content_type for every slice in slice-classification.json
python /path/to/pixel-twin-lab/scripts/classify_slices.py \
  --out-dir /absolute/path/outputs/pixel-twin --mode apply
```

`--mode init` writes `slice-classification.json` (one entry per named slice; re-running merges new slices without clobbering labels) and `classification-sheet.png` (every crop on one labeled sheet — classification costs one image read, not one per region). `--mode apply` validates the labels and writes `routing-manifest.json` (track, handling, library, eval per region) and merges `track` into `regions.json`. `bootstrap_recovery.py`, `component_primitives.py`, and `fidelity_gate.py` treat the manifest as the authoritative track source. Icons route to islands with `handling: crop-asset` by default; set `handling: regenerate-transparent` per entry when the icon sits on a gradient or photo. Charts/maps accept a per-entry `library` override.

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
- `element_contract`: passes when a fully labeled `element-manifest.json` is verified against the rendered DOM (`element-verification.json` from `verify_elements.py`). Once a manifest is declared, `component_only_98` and the componentized gates also require this contract — pixels prove how it looks, the element contract proves what it is made of.
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

`measure_primitives.py` reads the reference image and the component primitive worklist, then writes `measured-primitives.json`, `measured-primitives.md`, per-region `primitive-measurements/<region>.json`, and `primitive-measurements/*-primitive-overlay.png`. Use these measured boxes to place text, icon assets, controls, dividers, and cards. When fixing one region, read its per-region file, not the full `measured-primitives.json`. If a manual component edit makes the pixel diff worse, treat it as proof that the primitive geometry was guessed rather than measured.

Scaffold, label, and verify the element manifest (element → component mapping):

```bash
python /path/to/pixel-twin-lab/scripts/init_element_manifest.py \
  --out-dir /absolute/path/outputs/pixel-twin

# ...label type/content/maps_to in element-manifest.json by reading the reference crops...

node /path/to/pixel-twin-lab/scripts/measure_dom_elements.cjs \
  --url http://127.0.0.1:8787/ \
  --out-dir /absolute/path/outputs/pixel-twin

python /path/to/pixel-twin-lab/scripts/verify_elements.py \
  --out-dir /absolute/path/outputs/pixel-twin
```

`init_element_manifest.py` turns `measured-primitives.json` boxes into `element-manifest.json` entries with stable ids; re-running merges new boxes without clobbering existing labels. Labeling is the agent's job, not an algorithm's: set `type`, `content` (extract the actual text for text elements), and `maps_to` for every element. **Repeated content is one `collection` entry, not N cell entries:** for a table/list/queue, keep the container element, set `type: collection`, declare `item_count` (or `min_items`) and `first_item_content`, and delete the per-row entries — the DOM contract is `data-element` on the container plus `data-element-item` on every row, which is exactly what rendering from a mock-data array produces. Approximation chart containers are `type: chart-host` (verified by the presence of the library's canvas/svg output). `measure_dom_elements.cjs` dumps the rendered geometry of every `[data-element]` node in reference coordinates (including item counts and first-item text for collections); `verify_elements.py` checks each manifest element for presence (exactly one DOM node), position/size deltas (`--max-position-delta`, `--max-size-delta`), text content, type compatibility (an `icon` must contain svg/img; a `control` must be an interactive tag/role), and the collection contract. The result feeds the `element_contract` gate; "38/52 elements verified" is also the progress meter between iteration zero and 98%.

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

Extract design tokens from the reference (Phase 1):

```bash
python /path/to/pixel-twin-lab/scripts/extract_tokens.py \
  --out-dir /absolute/path/outputs/pixel-twin
```

`extract_tokens.py` clusters reference colors (each with a verifiable `sampled_at` coordinate), derives type sizes from measured text-line boxes, and derives a spacing scale from measured gaps, writing `visual-tokens.json`/`.md`. Curate this into the blueprint's `tokens` layer — rename meaningfully, set `usage`, and map to existing project tokens via `maps_to`. Radius/shadows/borders are not auto-extracted in v1; fill them in by reading the reference and record them in the same layer.

Infer layout relations from measured boxes (Phase 1):

```bash
python /path/to/pixel-twin-lab/scripts/infer_layout.py \
  --out-dir /absolute/path/outputs/pixel-twin
```

`infer_layout.py` groups boxes per region into `row`/`column`/`grid`/`stack` relations with gaps and confidence, writing `layout-relations.json`/`.md`. Adopt trustworthy relations into the blueprint's `layout.relations`; entries below `--min-confidence` come out as `absolute-fallback` with a reason — keep them explicit rather than guessing a responsive structure that is not supported by the geometry.

Validate the blueprint (the Phase 2 → Phase 4 gate):

```bash
python /path/to/pixel-twin-lab/scripts/validate_blueprint.py \
  --out-dir /absolute/path/outputs/pixel-twin
```

`validate_blueprint.py` schema-checks `ui-blueprint.json` (structure, enums, cross-layer references, interaction coverage for interactive component types) and reconciles it against reality: region/component bounds versus measured boxes, token colors versus actual reference pixels at `sampled_at`, typography versus measured text heights. Errors block code generation (exit 1); warnings are listed in `blueprint-validation.md`. A blueprint that fails reconciliation was written by eye — fix it from measurements, do not loosen the tolerances.

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

Blueprint flow artifacts (primary):

- `slice-classification.json`, `classification-sheet.png`, and `routing-manifest.json` from `classify_slices.py` — the Phase 1.5 content classification and track routing, authored before any blueprint or code work
- `ui-blueprint.json` — the six-layer decomposition (layout, components, tokens, data, interactions, implementation), schema-valid and measurement-reconciled; the single visual source of truth for code generation
- mock data fixture files in the target project (one per data-driven component, generated from the blueprint's data layer)
- `visual-tokens.json`/`.md` from `extract_tokens.py`, curated into the blueprint
- `layout-relations.json`/`.md` from `infer_layout.py`, adopted into the blueprint
- `blueprint-validation.json`/`.md` from `validate_blueprint.py` — must pass before project code is written
- `implementation-plan.md` — component order, reuse mapping, acceptance criteria
- `component-map.md` and `interaction-contract.md` — human-readable mirrors of the blueprint's layout/semantics and interaction layers

Lab/measurement artifacts, create or update as used:

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
- `element-manifest.json`/`.md` (labeled element → component mapping), `dom-elements.json`, and `element-verification.json`/`.md` for every component rebuild pass
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
- Charts are configured, never drawn: on the component-faithful track use the project's existing chart library, or echarts by default, fed the data layer's mock series. Replicating a chart with bespoke SVG paths/rects is allowed only as a bitmap-exact ceiling diagnostic that never ships.
- Use absolute paths in generated reports.
- Do not hide reference overlays or toolbars in capture except through `?capture=1`.
- Keep temporary capture scripts in `work/` when adapting the workflow for a repo.
- Prefer `outputs/` for user-facing captures and diff images.
- For frontend handoff, include screenshots in the final answer when the user asked to see the effect.
- Never copy the lab template into production source as the final app. The lab is a measuring tool; the final output must follow the target project's framework and conventions.
- Never introduce Tailwind, CSS Modules, an icon set, or a UI kit just because the reference would be easier to build that way; add dependencies only when the project has no suitable convention and the user approves. **Exception — approximation-track libraries:** regions routed to `chart`/`map`/`scene-3d` need a real rendering library by design. Prefer the project's existing one; otherwise propose the default (echarts / leaflet / three) and ask the user once per run — hand-drawing the visualization to avoid the dependency is not an option.
