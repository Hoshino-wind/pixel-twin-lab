# Pixel Twin UI Editor

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

[简体中文](README.zh-CN.md)

Apply a screenshot, mockup, or visual change request directly to an existing frontend project without changing its business behavior.

The target project's Git diff is the product. Pixel Twin does not generate a lab, blueprint, manifest, packet, report, capture directory, or parallel reconstruction project.

```text
reference image + existing frontend
  → protected worktree baseline
  → in-place UI edits
  → project-native checks + bounded visual comparison
  → reviewable Git diff
```

## In 15 seconds

Pixel Twin is an agent skill for changing the UI that already owns the product. It keeps APIs, stores, routes, persistence, schemas, backend code, business validation, and existing handler behavior outside the default edit boundary.

Its visual loop is deliberately bounded: one optional pre-edit baseline, one consolidated check, at most three actionable hotspots, and at most one targeted repair. Ambiguous visual correspondence is reported as `uncertain` instead of becoming a guessed code change.

## What it changes

- component markup and composition;
- CSS, themes, tokens, layout, responsive behavior, and visual hierarchy;
- accessibility attributes and presentational motion;
- final images, icons, SVGs, or fonts used by the product.

It protects APIs, stores, routes, queries, mutations, persistence, schemas, backend code, business validation, and existing handler behavior.

## Workflow

1. Inspect the existing project and target UI.
2. Start a temporary guard session that records the current Git worktree and, when available, a stable visual baseline.
3. Edit the owning UI files in place.
4. Run one consolidated scope/project/visual check.
5. Make at most one targeted visual repair.

All guard state, screenshots, logs, and diffs use the system temporary directory and are deleted by default. Only actual source changes and product assets remain in the target project.

## Commands

Inspect a project without writing files:

```bash
python3 scripts/pixel_twin.py inspect --project /absolute/path/to/project
```

Protect the current worktree before editing:

```bash
python3 scripts/pixel_twin.py begin --project /absolute/path/to/project
```

When a local page and reference are available, capture the pre-edit visual baseline at `begin`:

```bash
python3 scripts/pixel_twin.py begin \
  --project /absolute/path/to/project \
  --url http://127.0.0.1:3000/dashboard \
  --reference /absolute/path/reference.png
```

After editing, run the project's existing format/lint/type checks and audit only changes made after the baseline:

```bash
python3 scripts/pixel_twin.py check --session <session-id>
```

`check` automatically reuses a visual baseline captured at `begin` and reports layout, multi-scale structure, edges, continuous palette similarity, pixel residuals, at most three hotspots, and pre-edit gains/regression risks. When DOM evidence is available, a bounded internal pool keeps a smaller actionable mismatch from being starved by larger unowned residuals, while no more than six repair solves and three public hotspots are allowed. A hotspot can include a bounded DOM candidate, a conservative target position/size/color repair hint, and at most two repository-relative source candidates from session-approved UI files. Ambiguous, missing, typography-only, or content-changing visual correspondence is reported as `uncertain` instead of a guessed numeric repair. Canvas/video/complex SVG regions are removed from static noise and compared separately by structure, edge distribution, color, and temporal drift; add repeatable `--dynamic-selector` for custom chart or map containers. Failed image resources and unstable renders are rejected before scoring. Without a baseline, one-shot `check --url ... --reference ...` remains available but cannot report gains or session-scoped source candidates.

The comparison is feedback, not a hard pixel gate, unless the user supplied an acceptance target and you pass `--min-match <percent>`. Use `--keep-session` only when retaining the baseline for one targeted repair, then run the final `check` without it or clean the session with `finish`.

`check` does not build by default, so it does not create normal framework build caches. When the target repository requires a production build, `--run build` adds it after the automatic format/lint/type checks.

A successful check removes its temporary session. Clean an abandoned failed session with:

```bash
python3 scripts/pixel_twin.py finish --session <session-id>
```

Use project-relative `--ui-root`, `--asset-root`, or exact `--editable` paths when automatic ownership detection is insufficient. Explicit roots extend detected roots. In a monorepo, `--project` may point to the frontend package; the guard still protects the enclosing Git repository. `--editable` cannot override protected backend, API, store, schema, package, or infrastructure paths.

If the intended UI file already contains user changes, review its existing diff and declare that exact path with `--editable` before making task-owned UI edits.

## Safety model

- Existing Git-visible user changes are recorded at `begin` and are not counted as this edit. Ignored `.env*` files and common Pixel Twin-style diagnostic directories are separately protected.
- Editing a pre-existing dirty path is blocked unless that exact path was explicitly declared editable.
- Staging or unstaging during the session is blocked.
- Non-UI paths, high-confidence behavior deltas, unsafe SVGs, symlinks, and oversized assets are blocked.
- The tool never runs `stash`, `reset`, `checkout`, `clean`, `git add`, or `git commit`.
- Static checks cannot formally prove all behavior in mixed TSX/Vue files; final Git diff review remains required.

## Cost controls

- one initial visual analysis;
- one optional deterministic pre-edit visual baseline;
- one consolidated check with at most three visual hotspots;
- at most one repair hint and two source locations per hotspot, with no extra capture;
- at most one targeted repair and recheck;
- no default region/component subagent fan-out;
- successful checks print summaries, not full logs;
- no persistent QA artifacts in the target project.

## Requirements

- Git and Python 3.10+
- Pillow: `pip install -r scripts/requirements.txt`
- Node.js 18+ and Playwright for optional browser comparison:

  ```bash
  npm install
  npm run install:browsers
  ```

## Development

```bash
npm test
npm run check
```

The skill entry point is [SKILL.md](SKILL.md). Detailed UI-only boundaries and temporary visual verification are loaded conditionally from `references/`.

## License

[MIT](LICENSE)
