#!/usr/bin/env python3
"""Overlay approved island assets onto an existing component rebuilt layer."""

from __future__ import annotations

import argparse
import html
import json
import re
import shutil
from pathlib import Path
from typing import Any


CSS_START_MARKER = "/* Pixel Twin Lab island overlays: component DOM stays underneath. */"
CSS_END_MARKER = "/* pixel-twin island overlays: end */"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def asset_src(asset: str, recovery_dir_name: str) -> str:
    if asset.startswith("./") or asset.startswith("/") or "://" in asset:
        return asset
    return f"./{recovery_dir_name}/{asset}"


def approved_asset_regions(report: dict[str, Any], allowed_tracks: set[str]) -> list[dict[str, Any]]:
    regions: list[dict[str, Any]] = []
    for region in report.get("regions", []):
        if not isinstance(region, dict):
            continue
        if not region.get("asset"):
            continue
        if str(region.get("track") or "") not in allowed_tracks:
            continue
        if str(region.get("asset_strategy") or "") == "placeholder":
            continue
        regions.append(region)
    return regions


def render_overlay(regions: list[dict[str, Any]], recovery_dir_name: str) -> str:
    lines = ['          <div class="pt-island-overlay" data-pixel-twin-island-overlay aria-hidden="true">']
    for region in regions:
        name = html.escape(str(region["name"]), quote=True)
        src = html.escape(asset_src(str(region["asset"]), recovery_dir_name), quote=True)
        lines.extend(
            [
                f'            <section class="pt-island-region pt-island-region--{name}" data-track="{html.escape(str(region.get("track") or "island"), quote=True)}" data-asset-provider="{html.escape(str(region.get("asset_provider") or "image2"), quote=True)}" data-asset-strategy="{html.escape(str(region.get("asset_strategy") or "image2-extract"), quote=True)}" data-has-asset="true" aria-label="{name}">',
                f'              <img src="{src}" alt="" draggable="false" />',
                "            </section>",
            ]
        )
    lines.append("          </div>")
    return "\n".join(lines)


def render_css(regions: list[dict[str, Any]]) -> str:
    lines = [
        "",
        CSS_START_MARKER,
        ".pt-island-overlay {",
        "  position: absolute;",
        "  inset: 0;",
        "  z-index: 50;",
        "  pointer-events: none;",
        "}",
        ".pt-island-region {",
        "  position: absolute;",
        "  overflow: hidden;",
        "  box-sizing: border-box;",
        "}",
        ".pt-island-region > img {",
        "  display: block;",
        "  width: 100%;",
        "  height: 100%;",
        "  object-fit: fill;",
        "}",
    ]
    for region in regions:
        lines.extend(
            [
                f".pt-island-region--{region['name']} {{",
                f"  left: {int(region['x'])}px;",
                f"  top: {int(region['y'])}px;",
                f"  width: {int(region['width'])}px;",
                f"  height: {int(region['height'])}px;",
                "}",
            ]
        )
    lines.append(CSS_END_MARKER)
    return "\n".join(lines) + "\n"


def remove_existing_overlay(index_html: str) -> str:
    return re.sub(
        r"\n?\s*<div class=\"pt-island-overlay\" data-pixel-twin-island-overlay[\s\S]*?</div>\s*",
        "\n",
        index_html,
        flags=re.IGNORECASE,
    )


def inject_overlay(index_html: str, overlay_html: str) -> str:
    clean = remove_existing_overlay(index_html)
    marker = "</div>\n      </section>"
    position = clean.find(marker)
    if position == -1:
        raise SystemExit("Could not find rebuilt-layer closing marker in index.html.")
    return clean[:position] + overlay_html + "\n" + clean[position:]


def strip_existing_css(css_text: str) -> str:
    start = css_text.find(CSS_START_MARKER)
    if start == -1:
        return css_text.rstrip() + "\n"
    end = css_text.find(CSS_END_MARKER, start)
    if end == -1:
        print(
            "Warning: island overlay start marker found without end marker (old format); "
            "stripping to end of file, which may remove styles appended after the overlay block."
        )
        return css_text[:start].rstrip() + "\n"
    before = css_text[:start].rstrip()
    after = css_text[end + len(CSS_END_MARKER):].strip("\n").rstrip()
    parts = [part for part in (before, after) if part]
    return ("\n\n".join(parts) + "\n") if parts else ""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True, help="Pixel Twin Lab output directory")
    parser.add_argument("--recovery-dir", default="recovery", help="Recovery directory name inside out-dir")
    parser.add_argument("--allowed-asset-tracks", default="island", help="Comma-separated tracks to overlay")
    parser.add_argument("--no-backup", action="store_true", help="Do not write .before-island-overlay backups")
    args = parser.parse_args()

    out_dir = Path(args.out_dir).expanduser().resolve()
    recovery_dir = out_dir / args.recovery_dir
    report_path = recovery_dir / "component-ledger.json"
    if not report_path.exists():
        raise SystemExit(f"Missing recovery report: {report_path}")

    allowed_tracks = {track.strip() for track in args.allowed_asset_tracks.split(",") if track.strip()}
    report = load_json(report_path)
    regions = approved_asset_regions(report, allowed_tracks)
    if not regions:
        raise SystemExit(f"No approved asset regions found for tracks: {sorted(allowed_tracks)}")

    index_path = out_dir / "index.html"
    styles_path = out_dir / "styles.css"
    if not index_path.exists() or not styles_path.exists():
        raise SystemExit("index.html and styles.css must exist in out-dir.")

    if not args.no_backup:
        for path in (index_path, styles_path):
            backup = path.with_suffix(path.suffix + ".before-island-overlay")
            if not backup.exists():
                shutil.copy2(path, backup)

    index_html = index_path.read_text(encoding="utf-8")
    overlay_html = render_overlay(regions, args.recovery_dir)
    index_path.write_text(inject_overlay(index_html, overlay_html), encoding="utf-8")

    css_text = strip_existing_css(styles_path.read_text(encoding="utf-8"))
    styles_path.write_text(css_text + render_css(regions), encoding="utf-8")

    print(
        json.dumps(
            {
                "out_dir": str(out_dir),
                "recovery_dir": str(recovery_dir),
                "overlay_regions": [region["name"] for region in regions],
                "allowed_asset_tracks": sorted(allowed_tracks),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
