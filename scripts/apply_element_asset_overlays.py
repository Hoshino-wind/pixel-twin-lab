#!/usr/bin/env python3
"""Overlay measured element assets onto an existing component rebuilt layer.

This is the element-level counterpart to `apply_island_overlays.py`: region islands keep
whole maps/photos/charts, while element assets repair missing icons, avatars, thumbnail
media, and other measured visual islands inside otherwise componentized regions.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import shutil
from pathlib import Path
from typing import Any


HTML_START_MARKER = "<!-- Pixel Twin Lab element asset overlays: start -->"
HTML_END_MARKER = "<!-- Pixel Twin Lab element asset overlays: end -->"
CSS_START_MARKER = "/* Pixel Twin Lab element asset overlays: start */"
CSS_END_MARKER = "/* Pixel Twin Lab element asset overlays: end */"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def asset_src(asset: dict[str, Any], asset_dir_name: str) -> str:
    src = str(asset.get("path") or asset.get("file") or "")
    if not src:
        raise ValueError(f"Asset has no path/file: {asset}")
    if src.startswith("./") or src.startswith("/") or "://" in src:
        return src
    if "/" in src:
        return f"./{src}"
    return f"./{asset_dir_name}/{src}"


def bounds(asset: dict[str, Any]) -> dict[str, int] | None:
    raw = asset.get("bounds")
    if not isinstance(raw, dict):
        return None
    try:
        b = {key: int(round(float(raw[key]))) for key in ("x", "y", "width", "height")}
    except (KeyError, TypeError, ValueError):
        return None
    if b["width"] <= 0 or b["height"] <= 0:
        return None
    return b


def normalized_assets(report: dict[str, Any], asset_dir_name: str) -> list[dict[str, Any]]:
    assets: list[dict[str, Any]] = []
    for asset in report.get("assets") or []:
        if not isinstance(asset, dict):
            continue
        b = bounds(asset)
        if not b:
            continue
        try:
            src = asset_src(asset, asset_dir_name)
        except ValueError:
            continue
        assets.append({**asset, "bounds": b, "src": src})
    assets.sort(key=lambda a: (a["bounds"]["y"], a["bounds"]["x"], str(a.get("id") or "")))
    return assets


def render_overlay(assets: list[dict[str, Any]]) -> str:
    lines = [
        f"          {HTML_START_MARKER}",
        '          <div class="pt-element-asset-overlay" data-pixel-twin-element-asset-overlay aria-hidden="true">',
    ]
    for index, asset in enumerate(assets, start=1):
        asset_id = html.escape(str(asset.get("id") or f"asset-{index}"), quote=True)
        asset_type = html.escape(str(asset.get("asset_type") or "asset"), quote=True)
        region = html.escape(str(asset.get("region") or ""), quote=True)
        source = html.escape(str(asset.get("source") or ""), quote=True)
        src = html.escape(str(asset["src"]), quote=True)
        lines.extend(
            [
                f'            <span class="pt-element-asset pt-element-asset--{asset_id}" data-element-asset-id="{asset_id}" data-asset-type="{asset_type}" data-region="{region}" data-source="{source}">',
                f'              <img src="{src}" alt="" draggable="false" />',
                "            </span>",
            ]
        )
    lines.extend(["          </div>", f"          {HTML_END_MARKER}"])
    return "\n".join(lines)


def render_css(assets: list[dict[str, Any]], z_index: int) -> str:
    lines = [
        "",
        CSS_START_MARKER,
        ".pt-element-asset-overlay {",
        "  position: absolute;",
        "  inset: 0;",
        f"  z-index: {z_index};",
        "  pointer-events: none;",
        "}",
        ".pt-element-asset {",
        "  position: absolute;",
        "  display: block;",
        "  overflow: hidden;",
        "  box-sizing: border-box;",
        "}",
        ".pt-element-asset > img {",
        "  display: block;",
        "  width: 100%;",
        "  height: 100%;",
        "  object-fit: fill;",
        "}",
    ]
    for asset in assets:
        b = asset["bounds"]
        asset_id = str(asset.get("id") or "asset")
        lines.extend(
            [
                f".pt-element-asset--{asset_id} {{",
                f"  left: {b['x']}px;",
                f"  top: {b['y']}px;",
                f"  width: {b['width']}px;",
                f"  height: {b['height']}px;",
                "}",
            ]
        )
    lines.append(CSS_END_MARKER)
    return "\n".join(lines) + "\n"


def strip_html_overlay(index_html: str) -> str:
    pattern = re.escape(HTML_START_MARKER) + r"[\s\S]*?" + re.escape(HTML_END_MARKER)
    return re.sub(r"\n?\s*" + pattern + r"\s*", "\n", index_html)


def inject_overlay(index_html: str, overlay_html: str) -> str:
    clean = strip_html_overlay(index_html)
    match = re.search(r"<div\s+[^>]*class=[\"'][^\"']*\brebuilt-layer\b[^\"']*[\"'][^>]*>", clean)
    if not match:
        raise SystemExit("Could not find `.rebuilt-layer` in index.html.")
    insert_at = match.end()
    return clean[:insert_at] + "\n" + overlay_html + clean[insert_at:]


def strip_css_overlay(css_text: str) -> str:
    start = css_text.find(CSS_START_MARKER)
    if start == -1:
        return css_text.rstrip() + "\n"
    end = css_text.find(CSS_END_MARKER, start)
    if end == -1:
        return css_text[:start].rstrip() + "\n"
    before = css_text[:start].rstrip()
    after = css_text[end + len(CSS_END_MARKER):].strip("\n").rstrip()
    parts = [part for part in (before, after) if part]
    return ("\n\n".join(parts) + "\n") if parts else ""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True, help="Pixel Twin Lab output directory")
    parser.add_argument("--element-assets", default="element-assets.json", help="element-assets JSON filename/path")
    parser.add_argument("--asset-dir", default="element-assets", help="Fallback asset directory name for bare asset filenames")
    parser.add_argument("--z-index", type=int, default=80, help="Overlay z-index inside rebuilt layer")
    parser.add_argument("--no-backup", action="store_true", help="Do not write .before-element-assets backups")
    args = parser.parse_args()

    out_dir = Path(args.out_dir).expanduser().resolve()
    assets_path = Path(args.element_assets).expanduser()
    if not assets_path.is_absolute():
        assets_path = out_dir / assets_path
    if not assets_path.exists():
        raise SystemExit(f"Missing element assets report: {assets_path}")
    index_path = out_dir / "index.html"
    styles_path = out_dir / "styles.css"
    if not index_path.exists() or not styles_path.exists():
        raise SystemExit("index.html and styles.css must exist in out-dir.")

    report = load_json(assets_path)
    assets = normalized_assets(report, args.asset_dir)
    if not assets:
        raise SystemExit(f"No usable assets found in {assets_path}.")

    if not args.no_backup:
        for path in (index_path, styles_path):
            backup = path.with_suffix(path.suffix + ".before-element-assets")
            if not backup.exists():
                shutil.copy2(path, backup)

    index_path.write_text(inject_overlay(index_path.read_text(encoding="utf-8"), render_overlay(assets)), encoding="utf-8")
    css_text = strip_css_overlay(styles_path.read_text(encoding="utf-8"))
    styles_path.write_text(css_text + render_css(assets, args.z_index), encoding="utf-8")

    counts: dict[str, int] = {}
    for asset in assets:
        asset_type = str(asset.get("asset_type") or "asset")
        counts[asset_type] = counts.get(asset_type, 0) + 1
    print(
        json.dumps(
            {
                "out_dir": str(out_dir),
                "element_assets": str(assets_path),
                "overlay_assets": len(assets),
                "asset_type_counts": counts,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
