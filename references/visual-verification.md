# One-pass visual verification

Read this reference only when the target page can run locally and a screenshot or mockup provides a visual comparison target.

## Purpose

Visual comparison is a bounded feedback signal, not a separate production artifact or an optimization loop. Capture one pre-edit baseline, make the direct UI edit, then compare the final page against both the reference and baseline. If the result identifies a specific defect, make one targeted repair and check once more.

## Preconditions

- The target app is already running on localhost and the target state is reachable without injecting secrets into this tool.
- The URL resolves to the actual edited page.
- The reference represents the same viewport, route state, data state, and theme.
- Fonts and required product assets have loaded.
- Animations are not required for the captured state.

The helper uses Playwright's bundled Chromium, device scale factor 1, reduced motion, and forced sRGB rendering. Before capture it waits, with bounded timeouts, for the load event, `document.fonts.ready`, image decoding, and two consecutive stable layout signatures. It decodes at most 200 images to bound waiting, but checks the final pending/failed state of every image before scoring. For selector captures it scrolls the selector into view before settling and records all evidence in selector-local coordinates. A pending or failed image resource, font timeout, or unstable layout fails instead of producing a misleading score. It defaults to a light color scheme; pass `--color-scheme dark` for a dark reference. Remote URLs and main-frame redirects are refused unless `--allow-remote` is explicit.

The helper intentionally has no cookie, storage-state, login, or scripted-interaction interface. For an authenticated or interaction-dependent state, use the target project's existing E2E/browser setup for the one visual inspection, or skip automated pixel comparison. Never serialize credentials into Pixel Twin arguments or temporary files.

## Command

```bash
python3 <skill-dir>/scripts/pixel_twin.py begin \
  --project /absolute/path/to/project \
  --url http://127.0.0.1:3000/path \
  --reference /absolute/path/reference.png
```

The reference dimensions become the viewport. For a component crop, add `--selector`. Use `--viewport 1440x900` only when the reference dimensions are not the desired page viewport. A reference, viewport, or selected element is limited to 10,000 pixels per side and 9,000,000 total pixels so malformed or accidental giant captures fail with a controlled error instead of exhausting memory. The returned baseline includes at most three high-impact mismatch rectangles.

Visible Canvas/video elements and large complex SVG surfaces are detected conservatively. Declare up to three additional chart or map containers when needed:

```bash
--dynamic-selector '.recharts-wrapper' \
--dynamic-selector '#map'
```

Disable automatic detection with `--no-auto-dynamic`. When a dynamic surface exists, the helper takes one additional frame after about 120 ms without navigating again. That frame is used only to estimate temporal uncertainty and is deleted immediately after analysis.

After editing, reuse the exact capture contract automatically:

```bash
python3 <skill-dir>/scripts/pixel_twin.py check \
  --session <session-id>
```

Add `--keep-session` only when retaining the baseline for one targeted repair. If `begin` did not capture a visual baseline, `check --url ... --reference ...` remains available as a one-shot measurement without pre-edit gain reporting.

The visual result is a vector rather than one compensating total score:

- coarse normalized layout match;
- multi-scale local structural similarity;
- edge alignment for boundaries and typography;
- continuous palette-distribution similarity for surfaces and color;
- strict and tolerant pixel residuals;
- at most three connected mismatch hotspots; with DOM evidence, selection may inspect at most twelve internal candidates and solve at most six unique high-confidence static targets, preserving the highest-severity residual while promoting higher-confidence actionable repairs before the remaining severity-ranked candidates;
- one DOM candidate and up to three computed CSS levers per hotspot;
- one conservative repair hint per hotspot, containing only an applicable target visual delta/color or an explicit `uncertain` reason;
- at most two repository-relative source candidates per hotspot, scanned only from Git-visible UI files allowed by the edit session and returned without source snippets;
- 4×4 regional improvements and regression risks versus the baseline; a region with contradictory pixel-match and error-severity movement is marked mixed and kept in the regression-risk list.
- a separate dynamic-region vector containing coarse structure, edge distribution, palette similarity, and temporal drift.

Dynamic pixels are excluded from the top-level static match, MAE, layout, structure, edge, color, hotspot, and 4×4 signals. They are not ignored: their separate vector remains visible and its baseline delta is reported as improved, regressed, mixed, unchanged, or indeterminate. Temporal drift establishes an uncertainty band and never raises a fidelity score. Dynamic results are informational by default and do not enter `--min-match`; when less than 20% of the screenshot remains static, a requested hard match threshold fails as insufficient evidence.

If a dynamic region moves or resizes between the baseline and candidate, its geometry delta is reported separately and the top-level static delta becomes indeterminate because the excluded pixel basis changed. Current candidate fidelity remains available; the tool does not subtract incomparable static samples or label that subtraction as an improvement.

DOM hints contain only a bounded selector, tag/role, visible and full rectangles, confidence, a few safe computed values, and CSS levers. They never include text, form values, URLs, `data-*`, HTML, or stylesheet rules. Only high-confidence DOM ownership can produce an actionable repair; medium/low, truncated, non-unique, duplicate, clipped, dynamic-overlapping, missing-content, and competing matches remain `uncertain`. Source hints are scanned only for actionable repairs, ignore commented-out matches, and contain only a repository-relative path, line number, match kind, confidence, and matched lever names—never a source snippet or declaration value. Color inference samples a fixed bounded pixel budget and requires corresponding text foreground masks before suggesting a text color. A visual delta describes the inferred target rendered box, not the definitive source component, parent layout mechanism, or winning CSS declaration.

The default tolerant comparison ignores channel differences up to 8 and reports a match percentage without making that percentage a hard gate. If the user has supplied a measurable acceptance target, pass it explicitly with `--min-match 98`. Treat any threshold as a regression signal, not a universal definition of good UX. Fonts, platform rendering, dynamic content, canvas, maps, video, and WebGL may have legitimate residual differences.

HTTP errors and uncaught page errors fail the check. Console errors are summarized but do not fail by default because existing applications often contain unrelated noise; use `--fail-console-errors` only when the target project treats a clean console as an acceptance requirement.

## Artifact policy

The copied reference, baseline capture, comparison grid, final screenshot, diff, raw DOM geometry, and optional temporal frame exist only in memory or inside the system-temporary session. Raw DOM geometry and the temporal frame are discarded immediately after their compact analysis; successful final checks remove the session automatically, and `finish` removes a kept or abandoned session.

Do not create `outputs`, `work`, `captures`, `diffs`, or report files in the target project.

When a concrete rendering bug requires inspection, the user may explicitly request:

```bash
--keep-debug /absolute/path/outside/the/target/project
```

This copies only `actual.png` and `diff.png` to that external directory. Never use it as the default handoff.

## Failure handling

On failure:

1. Use the layered signals and highest-impact hotspot to locate one concrete visual defect.
2. Use its repair hint and source candidates to inspect only the likely component and relevant reference area; verify ownership and the controlling CSS/layout rule before editing.
3. Make one targeted UI patch without changing behavior.
4. Reuse the kept session and run `check` once more without `--keep-session`.
5. If a dynamic metric is indeterminate because temporal drift exceeds the signal, report it and stop instead of optimizing animation phase.

Do not restart whole-page decomposition, generate region packets, or feed full logs and repeated screenshots back into the model.
