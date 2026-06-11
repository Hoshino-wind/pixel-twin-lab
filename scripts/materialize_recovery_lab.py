#!/usr/bin/env python3
"""Materialize recovery assets into a Pixel Twin Lab rebuilt layer for screenshot QA."""

from __future__ import annotations

import argparse
import html
import json
import shutil
from pathlib import Path
from typing import Any


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def asset_src(asset: str | None, recovery_dir_name: str) -> str | None:
    if not asset:
        return None
    if asset.startswith("./") or asset.startswith("/") or "://" in asset:
        return asset
    return f"./{recovery_dir_name}/{asset}"


def render_rebuilt_layer(report: dict[str, Any], recovery_dir_name: str) -> str:
    lines = [
        '          <div class="pt-recovery" aria-label="Recovery materialized rebuilt layer">',
    ]
    for region in report.get("regions", []):
        name = html.escape(str(region.get("name", "region")), quote=True)
        track = html.escape(str(region.get("track", "component")), quote=True)
        provider = html.escape(str(region.get("asset_provider", "none")), quote=True)
        strategy = html.escape(str(region.get("asset_strategy", "none")), quote=True)
        asset = asset_src(region.get("asset"), recovery_dir_name)
        has_asset = "true" if asset else "false"
        lines.append(
            f'            <section class="pt-region pt-region--{name}" data-track="{track}" '
            f'data-asset-provider="{provider}" data-asset-strategy="{strategy}" '
            f'data-has-asset="{has_asset}" aria-label="{name}">'
        )
        if asset:
            lines.append(f'              <img src="{html.escape(asset, quote=True)}" alt="" draggable="false" />')
        lines.append("            </section>")
    lines.append("          </div>")
    return "\n".join(lines)


def render_index(report: dict[str, Any], recovery_dir_name: str) -> str:
    rebuilt_layer = render_rebuilt_layer(report, recovery_dir_name)
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Pixel Twin Lab Recovery</title>
    <link rel="stylesheet" href="./styles.css" />
  </head>
  <body>
    <div class="toolbar" data-capture-hidden>
      <div>
        <strong>Pixel Twin Lab</strong>
        <span id="canvasMeta">Loading reference...</span>
      </div>
      <div class="toolbar-actions" role="group" aria-label="View mode">
        <button class="mode-button is-active" type="button" data-mode-button="rebuilt">Rebuilt</button>
        <button class="mode-button" type="button" data-mode-button="reference">Reference</button>
        <button class="mode-button" type="button" data-mode-button="overlay">Overlay</button>
        <button class="mode-button" type="button" data-mode-button="exact">Exact Slice</button>
      </div>
      <label class="opacity-control">
        Overlay
        <input id="overlayOpacity" type="range" min="0" max="100" value="42" />
      </label>
    </div>

    <main class="stage-shell">
      <section class="stage" aria-label="Pixel twin stage">
        <img class="reference-layer" src="./assets/reference.png" alt="Reference UI" />
        <div class="slice-layer" aria-hidden="true"></div>
        <div class="rebuilt-layer">
{rebuilt_layer}
        </div>
      </section>
    </main>

    <script src="./script.js"></script>
  </body>
</html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True, help="Pixel Twin Lab output directory")
    parser.add_argument("--recovery-dir", default="recovery", help="Recovery directory name inside out-dir")
    parser.add_argument(
        "--template-root",
        help="Optional skill root; defaults to parent of this script. Used for the lab template CSS/JS.",
    )
    parser.add_argument("--no-backup", action="store_true", help="Do not write .before-recovery backups")
    args = parser.parse_args()

    out_dir = Path(args.out_dir).expanduser().resolve()
    recovery_dir = out_dir / args.recovery_dir
    report_path = recovery_dir / "component-ledger.json"
    if not report_path.exists():
        raise SystemExit(f"Missing recovery report: {report_path}")

    skill_root = Path(args.template_root).expanduser().resolve() if args.template_root else Path(__file__).resolve().parents[1]
    template = skill_root / "assets" / "prototype-template"
    if not (template / "styles.css").exists():
        raise SystemExit(f"Missing prototype template: {template}")

    report = load_json(report_path)
    index_path = out_dir / "index.html"
    styles_path = out_dir / "styles.css"
    script_path = out_dir / "script.js"

    if not args.no_backup:
        for path in (index_path, styles_path, script_path):
            if path.exists():
                backup = path.with_suffix(path.suffix + ".before-recovery")
                if not backup.exists():
                    shutil.copy2(path, backup)

    index_path.write_text(render_index(report, args.recovery_dir), encoding="utf-8")
    base_css = (template / "styles.css").read_text(encoding="utf-8")
    recovery_css = (recovery_dir / "recovery-skeleton.css").read_text(encoding="utf-8")
    styles_path.write_text(base_css + "\n\n" + recovery_css + "\n", encoding="utf-8")
    if not script_path.exists():
        shutil.copy2(template / "script.js", script_path)

    print(
        json.dumps(
            {
                "out_dir": str(out_dir),
                "index": str(index_path),
                "styles": str(styles_path),
                "regions": len(report.get("regions", [])),
                "assets": sum(1 for region in report.get("regions", []) if region.get("asset")),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
