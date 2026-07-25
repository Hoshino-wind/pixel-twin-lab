---
name: pixel-twin-lab
description: Directly apply a screenshot, mockup, design reference, or visual change request to UI/UX in an existing frontend project. Use when Codex must restyle or restructure an existing page or component, improve visual hierarchy, responsiveness, accessibility, or presentational motion, or match a reference while preserving existing props, handlers, routes, APIs, state, data flows, and business behavior. Edit project files in place, use only system-temporary diagnostics, and leave the Git diff as the sole work artifact. Do not use for greenfield app creation, backend or data-model work, or intentional product-logic changes.
---

# Pixel Twin UI Editor

## Goal

Apply a visual reference directly to an existing frontend project.

- Treat the project's current components, design tokens, data flow, and runtime DOM as the engineering source of truth.
- Edit the files that already own the target UI. Add only final assets consumed by the product.
- Leave source changes and necessary product assets as the only persistent result.
- Do not create a second representation of the UI such as a lab, blueprint, manifest, packet, ledger, capture set, or QA report.

## Scope guard

Change presentation only:

- view structure and component composition;
- CSS, theme values, spacing, typography, color, elevation, and layout;
- responsive behavior and visual hierarchy;
- accessibility attributes;
- presentational animation and local disclosure state;
- images, icons, SVGs, and fonts actually referenced by the final UI.

Preserve behavior:

- public component props and callbacks;
- event-handler meaning and arguments;
- routes and navigation behavior;
- API requests, stores, queries, mutations, and data transformation;
- authentication, authorization, payments, persistence, and business validation;
- backend, schema, database, and infrastructure code.

Move or reattach an existing handler only when its behavior and arguments remain unchanged. If matching the reference requires a product-logic change, stop and ask for that separate authorization.

Read `references/ui-change-boundary.md` before editing a file that mixes UI and business logic, shared global styles, or an ambiguous support file.

## Workflow

### 1. Inspect without writing

Run:

```bash
python3 <skill-dir>/scripts/pixel_twin.py inspect --project /absolute/path/to/project
```

Then inspect only the target route/component, nearby reusable UI, the active style system, and relevant tokens. Keep findings in working context; do not write a project profile.

### 2. Protect the existing worktree

Start a temporary edit session before changing files:

```bash
python3 <skill-dir>/scripts/pixel_twin.py begin \
  --project /absolute/path/to/project
```

When the target page is already running and a reference image is available, establish the visual baseline in the same command:

```bash
python3 <skill-dir>/scripts/pixel_twin.py begin \
  --project /absolute/path/to/project \
  --url http://127.0.0.1:3000/dashboard \
  --reference /absolute/path/reference.png
```

This waits for fonts, successfully decoded images, and stable layout frames, then reports layered layout, structure, edge, continuous palette, and pixel signals plus at most three mismatch hotspots. With DOM evidence, it considers a bounded internal candidate pool and spends at most six repair solves so a smaller actionable mismatch is not hidden by larger unowned residuals; only the final three hotspots are exposed. Each hotspot may include one bounded DOM candidate, three current CSS levers, and one conservative `repair_hint` for target position, size, or color. A repair hint may also name at most two repository-relative UI source candidates from the session-approved Git-visible files. Typography or content differences that cannot be separated from image evidence remain `uncertain`. Treat all of these as edit evidence, not proven ownership or an instruction to change business logic. Canvas, video, and large complex SVG surfaces use a separate structural/edge/color track and no longer pollute static hotspots. Failed resources stop scoring, and contradictory regional movement stays visible as a regression risk. The reference, baseline capture, and regional evidence remain in the system temporary session.

Use repeatable `--dynamic-selector '.chart'` for a map or custom-rendered surface that conservative auto-detection cannot identify. Add `--no-auto-dynamic` only when those surfaces must stay in the static comparison. These capture options are saved at `begin` and reused by `check`.

If UI roots cannot be detected, pass one or more `--ui-root` and `--asset-root` values. Use `--editable path/to/file.ts` for a reviewed presentational support file, or for the exact intended dirty UI target after reviewing its existing diff.

Paths passed to these options are relative to `--project`. In a monorepo, pass the frontend package directory; the session protects the entire enclosing Git repository while project checks run from that package. For a root-level `src/App.tsx` layout, pass `--ui-root src`.

The session records Git-visible worktree and index state, plus ignored `.env*` and common diagnostic locations, in the system temporary directory. It never stashes, resets, stages, or copies files into the project. Do not modify pre-existing user changes unless that exact path was declared with `--editable` when the session began.

When the intended target UI file is already dirty, inspect its existing diff first, then declare that exact project-relative file with `--editable`. Preserve its prior hunks and edit only the task-owned UI portion.

### 3. Analyze once and edit in place

Inspect the reference once at useful resolution. Start with the highest-confidence repair hint and its source candidate, verify the owner in source, then edit the smallest set of owning files directly. `delta.x/y/width/height` are visual CSS-pixel differences from the current rendered box to the inferred target box; they do not prove which CSS declaration or parent layout rule owns the difference. When the hint is `uncertain`, inspect manually instead of inventing a numeric change.

- Reuse the project's components, tokens, icons, dependencies, and style conventions.
- Preserve existing data and handlers; do not replace real data with mock data.
- Prefer local CSS or component changes over new abstractions.
- Do not introduce a framework, styling system, or UI library without explicit approval.
- Do not cover the page or a large region with the reference bitmap.
- Add an extracted image only when it is a genuine product asset such as a photo, logo, illustration, texture, or nontrivial icon.

Do not fan out one agent per component or region by default. A single agent with the existing code in context has better ownership information and lower token cost.

### 4. Check once

Run one consolidated check after the edit:

```bash
python3 <skill-dir>/scripts/pixel_twin.py check \
  --session <session-id>
```

The command:

1. checks only changes made after the temporary baseline;
2. rejects protected paths, staging changes, unsafe SVGs, and high-confidence business-logic deltas;
3. runs the target project's existing `format:check`, `lint`, and `typecheck` scripts when present;
4. prints only a short result and failure tail;
5. when a visual baseline exists, reports static and dynamic improvement or regression against the pre-edit page, with bounded DOM, repair, and source-location hints for residual hotspots;
6. removes the temporary session automatically on success unless `--keep-session` is explicit.

For a session that captured a visual baseline at `begin`, the URL, reference, viewport, theme, and tolerance are reused automatically. Add `--keep-session` only when retaining the session for the single permitted repair:

```bash
python3 <skill-dir>/scripts/pixel_twin.py check \
  --session <session-id> \
  --keep-session
```

If no visual baseline was captured, a one-shot final measurement may still pass `--url` and `--reference` to `check`, but it cannot report pre-edit gains.

Read `references/visual-verification.md` before using browser comparison. Captures and diffs remain in a temporary directory and are deleted. Use `--keep-debug /external/path` only when the user explicitly needs files for debugging; the destination must be outside the target project.

Visual similarity is feedback by default, not a hard gate. Pass `--min-match <percent>` only when the user supplied that measurable acceptance target.

Run a project build only when the repository requires it for this UI change, using `check --run build`; this adds the build after the default project checks. Build output and caches are owned by the target project's normal toolchain, not by Pixel Twin; do not introduce a separate output directory.

### 5. Repair at most once

If the consolidated check identifies a concrete hotspot and the session was kept, use its repair hint, DOM candidate, and source locations to verify the owning UI code, edit only that area, and run `check` one more time without `--keep-session`; success then removes the session. Never apply a low-confidence or `uncertain` geometry value mechanically. Do not assume a candidate proves the winning CSS rule, restart full analysis, or resend the complete project/reference context.

If no repair is needed after a kept diagnostic check, or if abandoning a failed session, clean it explicitly:

```bash
python3 <skill-dir>/scripts/pixel_twin.py finish --session <session-id>
```

After one targeted repair, report the remaining limitation instead of entering an unbounded visual convergence loop.

## Cost and artifact budget

- One initial reference analysis.
- One deterministic pre-edit visual baseline when a runnable page is available.
- One consolidated project/visual check that returns at most three hotspots.
- At most one extra in-memory/temporary frame per capture, and only when a dynamic surface exists.
- Repair solving reuses the already-open reference and capture; it performs no new navigation or screenshot.
- At most one targeted visual repair and recheck.
- No region-agent or component-agent fan-out unless the user explicitly requests parallel exploration.
- Do not paste full build logs, screenshots, JSON reports, or unchanged source back into model context.
- Successful deterministic checks require no model interpretation.
- Project-local intermediate artifacts are forbidden.
- System-temporary state must be cleaned on success or with `finish` when abandoned.

## Completion

Before handing off:

- review the final Git diff;
- confirm every changed file is UI-owned or explicitly approved;
- confirm no pre-existing user change was overwritten;
- confirm project-native checks passed, or state the exact skipped check;
- confirm temporary diagnostics were removed;
- do not stage, commit, or push unless the user separately asks.

Report only the UI outcome, changed project files, checks executed, and any remaining visual limitation. The Git diff is the implementation record.
