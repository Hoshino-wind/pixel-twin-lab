#!/usr/bin/env python3
"""Measure primitive boxes inside component-required reference regions."""

from __future__ import annotations

import argparse
import json
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont


def load_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return fallback


def find_component_primitives(out_dir: Path, explicit: str | None) -> Path | None:
    if explicit:
        path = Path(explicit)
        path = path if path.is_absolute() else out_dir / path
        if not path.exists():
            raise SystemExit(f"--component-primitives file not found: {path}")
        return path
    preferred = [
        "component-primitives.json",
    ]
    for name in preferred:
        path = out_dir / name
        if path.exists():
            return path
    candidates = sorted(out_dir.glob("component-primitives*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def load_regions(out_dir: Path, component_primitives: Path | None) -> list[dict[str, Any]]:
    if component_primitives:
        data = load_json(component_primitives, {})
        regions = data.get("regions") if isinstance(data, dict) else None
        if isinstance(regions, list):
            return [r for r in regions if isinstance(r, dict) and r.get("role") == "component-required" and r.get("bounds")]

    regions_json = load_json(out_dir / "regions.json", {})
    raw = regions_json.get("regions") if isinstance(regions_json, dict) else None
    if not isinstance(raw, list):
        return []
    regions: list[dict[str, Any]] = []
    for r in raw:
        if not (isinstance(r, dict) and r.get("name")):
            continue
        missing = [key for key in ("x", "y", "width", "height") if key not in r]
        if missing:
            raise SystemExit(f"regions.json region '{r['name']}' is missing fields: {', '.join(missing)}")
        regions.append({"name": r.get("name"), "bounds": r})
    return regions


def bounds_tuple(bounds: dict[str, Any]) -> tuple[int, int, int, int]:
    return (int(bounds["x"]), int(bounds["y"]), int(bounds["width"]), int(bounds["height"]))


def estimate_bg(arr: np.ndarray) -> np.ndarray:
    top = arr[:3, :, :]
    bottom = arr[-3:, :, :]
    left = arr[:, :3, :]
    right = arr[:, -3:, :]
    border = np.concatenate([top.reshape(-1, 3), bottom.reshape(-1, 3), left.reshape(-1, 3), right.reshape(-1, 3)], axis=0)
    return np.median(border, axis=0)


def connected_boxes(mask: np.ndarray, min_area: int, min_width: int, min_height: int) -> list[dict[str, int]]:
    height, width = mask.shape
    seen = np.zeros_like(mask, dtype=bool)
    boxes: list[dict[str, int]] = []
    for y in range(height):
        for x in range(width):
            if not mask[y, x] or seen[y, x]:
                continue
            queue: deque[tuple[int, int]] = deque([(x, y)])
            seen[y, x] = True
            min_x = max_x = x
            min_y = max_y = y
            area = 0
            while queue:
                cx, cy = queue.popleft()
                area += 1
                min_x = min(min_x, cx)
                max_x = max(max_x, cx)
                min_y = min(min_y, cy)
                max_y = max(max_y, cy)
                for nx in (cx - 1, cx, cx + 1):
                    for ny in (cy - 1, cy, cy + 1):
                        if nx < 0 or ny < 0 or nx >= width or ny >= height:
                            continue
                        if seen[ny, nx] or not mask[ny, nx]:
                            continue
                        seen[ny, nx] = True
                        queue.append((nx, ny))
            box_w = max_x - min_x + 1
            box_h = max_y - min_y + 1
            if area >= min_area and box_w >= min_width and box_h >= min_height:
                boxes.append({"x": min_x, "y": min_y, "width": box_w, "height": box_h, "area": area})
    return boxes


def classify_box(box: dict[str, int], region_w: int, region_h: int) -> str:
    w = box["width"]
    h = box["height"]
    area = box["area"]
    if w > region_w * 0.55 and h > region_h * 0.35:
        return "container-or-chart"
    if h <= 18 and w >= 24:
        return "text-line"
    if 18 < h <= 42 and w >= 44:
        return "control-or-card-fragment"
    if w <= 42 and h <= 42:
        return "icon-or-badge"
    if area > 1000 and w > 80:
        return "shape-or-sparkline"
    return "primitive"


def measure_region(
    image: Image.Image,
    region: dict[str, Any],
    threshold: int,
    dilation: int,
    min_area: int,
    min_width: int,
    min_height: int,
    overlay_dir: Path,
) -> dict[str, Any] | None:
    name = str(region["name"])
    x, y, w, h = bounds_tuple(region["bounds"])
    img_w, img_h = image.size
    x = max(0, min(x, img_w))
    y = max(0, min(y, img_h))
    w = max(0, min(w, img_w - x))
    h = max(0, min(h, img_h - y))
    if w <= 0 or h <= 0:
        print(f"Warning: region '{name}' has zero area after clamping to reference {img_w}x{img_h}; skipping.")
        return None
    crop = image.crop((x, y, x + w, y + h)).convert("RGB")
    arr = np.asarray(crop).astype(np.int16)
    bg = estimate_bg(arr)
    delta = np.max(np.abs(arr - bg), axis=2)
    mask = (delta > threshold).astype("uint8") * 255
    mask_img = Image.fromarray(mask, mode="L")
    if dilation > 1:
        if dilation % 2 == 0:
            dilation += 1
        mask_img = mask_img.filter(ImageFilter.MaxFilter(dilation))
    boxes = connected_boxes(np.asarray(mask_img) > 0, min_area, min_width, min_height)
    boxes.sort(key=lambda b: (b["y"], b["x"]))

    overlay = crop.copy()
    draw = ImageDraw.Draw(overlay)
    font = ImageFont.load_default()
    measured: list[dict[str, Any]] = []
    for index, box in enumerate(boxes, start=1):
        kind = classify_box(box, w, h)
        full = {"x": x + box["x"], "y": y + box["y"], "width": box["width"], "height": box["height"]}
        measured.append({**box, "full_bounds": full, "kind": kind})
        x0, y0 = box["x"], box["y"]
        x1, y1 = x0 + box["width"] - 1, y0 + box["height"] - 1
        draw.rectangle((x0, y0, x1, y1), outline="#ff3158", width=2)
        draw.rectangle((x0, max(0, y0 - 11), x0 + 18, y0), fill="#ff3158")
        draw.text((x0 + 2, max(0, y0 - 11)), str(index), fill="white", font=font)

    overlay_path = overlay_dir / f"{name}-primitive-overlay.png"
    crop_path = overlay_dir / f"{name}-reference-crop.png"
    overlay.save(overlay_path)
    crop.save(crop_path)
    return {
        "name": name,
        "bounds": {"x": x, "y": y, "width": w, "height": h},
        "background_rgb": [int(v) for v in bg],
        "threshold": threshold,
        "dilation": dilation,
        "primitive_count": len(measured),
        "overlay": str(overlay_path),
        "reference_crop": str(crop_path),
        "primitives": measured,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Measured Primitive Spec",
        "",
        f"Generated: `{report['generated_at']}`",
        f"Out dir: `{report['out_dir']}`",
        f"Reference: `{report['reference']}`",
        "",
        "## Regions",
        "",
        "| Region | Bounds | Primitive count | Overlay |",
        "| --- | --- | ---: | --- |",
    ]
    for region in report["regions"]:
        lines.append(
            f"| `{region['name']}` | `{region['bounds']}` | {region['primitive_count']} | `{region['overlay']}` |"
        )
    lines.append("")
    for region in report["regions"]:
        lines += [
            f"## `{region['name']}`",
            "",
            "| # | Kind | Region bounds | Full bounds | Area |",
            "| ---: | --- | --- | --- | ---: |",
        ]
        for index, box in enumerate(region["primitives"], start=1):
            region_bounds = {"x": box["x"], "y": box["y"], "width": box["width"], "height": box["height"]}
            lines.append(f"| {index} | `{box['kind']}` | `{region_bounds}` | `{box['full_bounds']}` | {box['area']} |")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True, help="Pixel Twin Lab output directory")
    parser.add_argument("--reference", help="Reference image path; defaults to <out-dir>/assets/reference.png")
    parser.add_argument("--component-primitives", help="component-primitives JSON filename/path")
    parser.add_argument("--regions", help="Comma-separated region names to measure")
    parser.add_argument("--threshold", type=int, default=12, help="RGB max-channel delta from region border background")
    parser.add_argument("--dilation", type=int, default=7, help="Odd MaxFilter size used to merge glyphs into primitives")
    parser.add_argument("--min-area", type=int, default=24)
    parser.add_argument("--min-width", type=int, default=3)
    parser.add_argument("--min-height", type=int, default=3)
    parser.add_argument("--json-name", default="measured-primitives.json")
    parser.add_argument("--md-name", default="measured-primitives.md")
    args = parser.parse_args()

    out_dir = Path(args.out_dir).expanduser().resolve()
    reference_path = Path(args.reference).expanduser().resolve() if args.reference else out_dir / "assets/reference.png"
    if not reference_path.exists():
        raise SystemExit(f"Reference image not found: {reference_path}")
    component_primitives = find_component_primitives(out_dir, args.component_primitives)
    regions = load_regions(out_dir, component_primitives)
    if args.regions:
        wanted = {name.strip() for name in args.regions.split(",") if name.strip()}
        regions = [region for region in regions if str(region.get("name")) in wanted]
    if not regions:
        raise SystemExit("No regions to measure. Run component_primitives.py first or provide regions.json.")

    overlay_dir = out_dir / "primitive-measurements"
    overlay_dir.mkdir(exist_ok=True)
    image = Image.open(reference_path).convert("RGB")
    measured = []
    for region in regions:
        result = measure_region(
            image,
            region,
            args.threshold,
            args.dilation,
            args.min_area,
            args.min_width,
            args.min_height,
            overlay_dir,
        )
        if result is not None:
            measured.append(result)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "out_dir": str(out_dir),
        "reference": str(reference_path),
        "component_primitives": str(component_primitives) if component_primitives else None,
        "regions": measured,
    }
    (out_dir / args.json_name).write_text(json.dumps(report, indent=2), encoding="utf-8")
    (out_dir / args.md_name).write_text(render_markdown(report), encoding="utf-8")
    # Per-region files: when fixing one region, read primitive-measurements/<region>.json
    # instead of the full measured-primitives.json (which grows with every region measured).
    for region in measured:
        (overlay_dir / f"{region['name']}.json").write_text(json.dumps(region, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "out_dir": str(out_dir),
                "regions": [{"name": r["name"], "primitive_count": r["primitive_count"]} for r in measured],
                "json": str(out_dir / args.json_name),
                "markdown": str(out_dir / args.md_name),
                "per_region_json": f"{overlay_dir}/<region>.json",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
