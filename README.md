# Pixel Twin Lab

[中文说明](README.zh-CN.md)

Turn a UI reference image — a screenshot, a mockup, or an AI-generated design — into a local visual QA workbench. Pixel Twin Lab rebuilds the UI in code, screenshots it in a real browser, and measures the pixel-level difference against the original image, so "looks the same" becomes a number instead of an opinion.

It is designed to run as an agent skill (Claude Code / Codex), but every step is a plain Python or Node script you can also run by hand.

## What it does

For each reference image, the lab generates an HTML workbench with four modes:

- **Reference** — the original image as ground truth.
- **Rebuilt** — your coded/component reconstruction.
- **Overlay** — the reference layered over the reconstruction with adjustable opacity.
- **Exact Slice** — raster crops pasted back at measured coordinates, showing the bitmap-perfect ceiling.

The point is not to pretend every coded UI can be one-pixel-perfect. The point is to make the fidelity tradeoff **visible, measurable, and repeatable**: a pixel-diff image, a mismatch percentage, MAE, max delta, and per-region metrics that tell you exactly which part of the UI is worst.

Beyond measurement, the skill drives a full componentization flow: it inspects a target project, follows its framework and styling conventions, writes production components into the project's source tree, and keeps all intermediate artifacts (slices, captures, diffs, ledgers) in a separate work folder.

## Requirements

- **Python 3** with [Pillow](https://pillow.readthedocs.io/) (required) and numpy (recommended — scripts fall back to a slower pure-PIL path without it):

  ```bash
  pip install -r scripts/requirements.txt
  ```

- **Node.js** with the full `playwright` package (bundled Chromium), or `playwright-core` plus a system Chrome/Chromium (auto-detected on macOS/Linux/Windows, or set `CHROME_PATH`).

## Quick start

```bash
# 1. Create the workbench from a reference image
python scripts/prepare_lab.py \
  --reference /absolute/path/reference.png \
  --out-dir /absolute/path/outputs/pixel-twin

# 2. Implement your reconstruction in the generated rebuilt-layer, then serve the lab
cd /absolute/path/outputs/pixel-twin
python3 -m http.server 8787 --bind 127.0.0.1

# 3. Capture reference / rebuilt / exact modes at native size
node scripts/capture_modes.cjs \
  --url http://127.0.0.1:8787/ \
  --out-dir /absolute/path/outputs/pixel-twin

# 4. Generate diff images and metrics
python scripts/pixel_diff.py \
  --reference /absolute/path/outputs/pixel-twin/assets/reference.png \
  --out-dir /absolute/path/outputs/pixel-twin
```

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
assets/prototype-template Workbench HTML/CSS/JS template
agents/openai.yaml        Codex-only interface descriptor
```

## License

[Apache 2.0](LICENSE)
