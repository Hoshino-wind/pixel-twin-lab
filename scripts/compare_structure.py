#!/usr/bin/env python3
"""Structurally compare approximation regions (third-party charts/maps/3D) between reference and rebuilt capture."""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from measure_primitives import classify_box, connected_boxes, estimate_bg, load_json


def ledger_regions(out_dir: Path, recovery_dir: str, tracks: set[str]) -> list[dict[str, Any]]:
    ledger = load_json(out_dir / recovery_dir / "component-ledger.json", {})
    regions = ledger.get("regions") if isinstance(ledger, dict) else None
    selected: list[dict[str, Any]] = []
    for region in regions or []:
        if not isinstance(region, dict) or not region.get("name"):
            continue
        if str(region.get("track") or "") not in tracks:
            continue
        missing = [key for key in ("x", "y", "width", "height") if key not in region]
        if missing:
            print(f"Warning: ledger region '{region['name']}' is missing bounds fields {missing}; skipping.")
            continue
        selected.append(region)
    return selected


def regions_from_regions_json(out_dir: Path, wanted: set[str]) -> list[dict[str, Any]]:
    data = load_json(out_dir / "regions.json", {})
    raw = data.get("regions") if isinstance(data, dict) else data
    selected: list[dict[str, Any]] = []
    for region in raw if isinstance(raw, list) else []:
        if isinstance(region, dict) and str(region.get("name")) in wanted:
            missing = [key for key in ("x", "y", "width", "height") if key not in region]
            if missing:
                raise SystemExit(f"regions.json region '{region['name']}' is missing fields: {', '.join(missing)}")
            selected.append(region)
    return selected


def clamp_bounds(region: dict[str, Any], size: tuple[int, int]) -> tuple[int, int, int, int] | None:
    img_w, img_h = size
    x = max(0, min(int(region["x"]), img_w))
    y = max(0, min(int(region["y"]), img_h))
    w = max(0, min(int(region["width"]), img_w - x))
    h = max(0, min(int(region["height"]), img_h - y))
    if w <= 0 or h <= 0:
        return None
    return x, y, w, h


def detect_boxes(
    image: Image.Image,
    bounds: tuple[int, int, int, int],
    threshold: int,
    dilation: int,
    min_area: int,
) -> tuple[list[dict[str, Any]], np.ndarray, Image.Image]:
    x, y, w, h = bounds
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
    boxes = connected_boxes(np.asarray(mask_img) > 0, min_area, 3, 3)
    for box in boxes:
        box["kind"] = classify_box(box, w, h)
    boxes.sort(key=lambda b: (b["y"], b["x"]))
    return boxes, arr, crop


def foreground_mean(arr: np.ndarray, threshold: int) -> list[float] | None:
    bg = estimate_bg(arr)
    delta = np.max(np.abs(arr - bg), axis=2)
    mask = delta > threshold
    if not mask.any():
        return None
    return [float(v) for v in arr[mask].mean(axis=0)]


def center(box: dict[str, Any]) -> tuple[float, float]:
    return (box["x"] + box["width"] / 2.0, box["y"] + box["height"] / 2.0)


def match_positions(ref_boxes: list[dict[str, Any]], cap_boxes: list[dict[str, Any]]) -> list[float]:
    remaining = list(cap_boxes)
    deltas: list[float] = []
    for ref in ref_boxes:
        if not remaining:
            break
        rx, ry = center(ref)
        best = min(remaining, key=lambda c: math.hypot(center(c)[0] - rx, center(c)[1] - ry))
        deltas.append(math.hypot(center(best)[0] - rx, center(best)[1] - ry))
        remaining.remove(best)
    return deltas


def kind_counts(boxes: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for box in boxes:
        counts[box["kind"]] = counts.get(box["kind"], 0) + 1
    return counts


def save_overlay(crop: Image.Image, boxes: list[dict[str, Any]], path: Path) -> None:
    overlay = crop.copy()
    draw = ImageDraw.Draw(overlay)
    font = ImageFont.load_default()
    for index, box in enumerate(boxes, start=1):
        x0, y0 = box["x"], box["y"]
        x1, y1 = x0 + box["width"] - 1, y0 + box["height"] - 1
        draw.rectangle((x0, y0, x1, y1), outline="#1f6fff", width=2)
        draw.text((x0 + 2, max(0, y0 - 11)), str(index), fill="#1f6fff", font=font)
    overlay.save(path)


def compare_region(
    name: str,
    region: dict[str, Any],
    reference: Image.Image,
    capture: Image.Image,
    args: argparse.Namespace,
    overlay_dir: Path,
) -> dict[str, Any]:
    ref_bounds = clamp_bounds(region, reference.size)
    cap_bounds = clamp_bounds(region, capture.size)
    if ref_bounds is None or cap_bounds is None:
        return {"name": name, "pass": False, "notes": ["region bounds are outside the reference or capture image"]}

    ref_boxes, ref_arr, ref_crop = detect_boxes(reference, ref_bounds, args.threshold, args.dilation, args.min_area)
    cap_boxes, cap_arr, cap_crop = detect_boxes(capture, cap_bounds, args.threshold, args.dilation, args.min_area)
    save_overlay(ref_crop, ref_boxes, overlay_dir / f"{name}-structure-reference.png")
    save_overlay(cap_crop, cap_boxes, overlay_dir / f"{name}-structure-capture.png")

    notes: list[str] = []
    ref_count, cap_count = len(ref_boxes), len(cap_boxes)
    count_delta_pct = abs(ref_count - cap_count) / max(1, ref_count) * 100.0
    count_ok = count_delta_pct <= args.max_count_delta_pct
    if not count_ok:
        notes.append(f"primitive count {cap_count} vs reference {ref_count} ({count_delta_pct:.0f}% delta)")

    deltas = match_positions(ref_boxes, cap_boxes)
    mean_delta = sum(deltas) / len(deltas) if deltas else None
    position_ok = mean_delta is not None and mean_delta <= args.max_position_delta
    if mean_delta is None:
        position_ok = ref_count == 0 and cap_count == 0
        if not position_ok:
            notes.append("no primitives matched between reference and capture")
    elif not position_ok:
        notes.append(f"mean primitive position delta {mean_delta:.1f}px exceeds {args.max_position_delta:.1f}px")

    ref_palette = foreground_mean(ref_arr, args.threshold)
    cap_palette = foreground_mean(cap_arr, args.threshold)
    if ref_palette is None or cap_palette is None:
        palette_ok = ref_palette is None and cap_palette is None
        palette_delta = None
        if not palette_ok:
            notes.append("foreground content exists on only one side")
    else:
        palette_delta = math.dist(ref_palette, cap_palette)
        palette_ok = palette_delta <= args.max_palette_delta
        if not palette_ok:
            notes.append(f"foreground mean color delta {palette_delta:.1f} exceeds {args.max_palette_delta:.1f}")

    return {
        "name": name,
        "pass": count_ok and position_ok and palette_ok,
        "reference_count": ref_count,
        "capture_count": cap_count,
        "count_delta_pct": round(count_delta_pct, 2),
        "count_pass": count_ok,
        "mean_position_delta_px": round(mean_delta, 2) if mean_delta is not None else None,
        "position_pass": position_ok,
        "palette_delta": round(palette_delta, 2) if palette_delta is not None else None,
        "palette_pass": palette_ok,
        "reference_kinds": kind_counts(ref_boxes),
        "capture_kinds": kind_counts(cap_boxes),
        "notes": notes,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Structural Comparison",
        "",
        f"Generated: `{report['generated_at']}`",
        f"Reference: `{report['reference']}`",
        f"Capture: `{report['capture']}`",
        "",
        "| Region | Count (ref/cap) | Position delta | Palette delta | Result |",
        "| --- | --- | ---: | ---: | --- |",
    ]
    for name, item in report["regions"].items():
        lines.append(
            f"| `{name}` | {item.get('reference_count', 'n/a')}/{item.get('capture_count', 'n/a')} | "
            f"{item.get('mean_position_delta_px', 'n/a')}px | {item.get('palette_delta', 'n/a')} | "
            f"{'PASS' if item['pass'] else 'FAIL'} |"
        )
    lines.append("")
    for name, item in report["regions"].items():
        for note in item.get("notes") or []:
            lines.append(f"- `{name}`: {note}")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True, help="Pixel Twin Lab output directory")
    parser.add_argument("--reference", help="Reference image path; defaults to <out-dir>/assets/reference.png")
    parser.add_argument("--capture", default="rebuilt-capture.png", help="Capture filename inside out-dir, or an absolute path")
    parser.add_argument("--regions", help="Comma-separated region names; defaults to all approximation-track ledger regions")
    parser.add_argument("--recovery-dir", default="recovery", help="Recovery directory holding component-ledger.json")
    parser.add_argument("--tracks", default="approximation", help="Ledger tracks compared by default")
    parser.add_argument("--threshold", type=int, default=12, help="RGB max-channel delta from region border background")
    parser.add_argument("--dilation", type=int, default=7, help="Odd MaxFilter size used to merge glyphs into primitives")
    parser.add_argument("--min-area", type=int, default=24)
    parser.add_argument("--max-position-delta", type=float, default=8.0, help="Max mean matched-primitive center delta in px")
    parser.add_argument("--max-count-delta-pct", type=float, default=20.0, help="Max primitive count difference vs reference")
    parser.add_argument("--max-palette-delta", type=float, default=32.0, help="Max RGB distance between foreground mean colors")
    parser.add_argument("--json-name", default="structural-comparison.json")
    parser.add_argument("--md-name", default="structural-comparison.md")
    args = parser.parse_args()

    out_dir = Path(args.out_dir).expanduser().resolve()
    reference_path = Path(args.reference).expanduser().resolve() if args.reference else out_dir / "assets/reference.png"
    capture_path = Path(args.capture)
    capture_path = capture_path if capture_path.is_absolute() else out_dir / args.capture
    if not reference_path.exists():
        raise SystemExit(f"Reference image not found: {reference_path}")
    if not capture_path.exists():
        raise SystemExit(f"Capture image not found: {capture_path}")

    tracks = {track.strip() for track in args.tracks.split(",") if track.strip()}
    regions = ledger_regions(out_dir, args.recovery_dir, tracks)
    if args.regions:
        wanted = {name.strip() for name in args.regions.split(",") if name.strip()}
        regions = [region for region in regions if str(region.get("name")) in wanted]
        found = {str(region.get("name")) for region in regions}
        missing = wanted - found
        if missing:
            regions += regions_from_regions_json(out_dir, missing)
            found = {str(region.get("name")) for region in regions}
            if wanted - found:
                raise SystemExit(f"Regions not found in ledger or regions.json: {sorted(wanted - found)}")
    if not regions:
        raise SystemExit(
            f"No regions to compare. Declare approximation-track regions in the ledger (tracks {sorted(tracks)}) or pass --regions."
        )

    overlay_dir = out_dir / "structural-measurements"
    overlay_dir.mkdir(exist_ok=True)
    reference = Image.open(reference_path).convert("RGB")
    capture = Image.open(capture_path).convert("RGB")
    if reference.size != capture.size:
        print(
            f"Warning: reference {reference.size} and capture {capture.size} sizes differ; "
            "bounds are clamped per image, results may be skewed."
        )

    results: dict[str, dict[str, Any]] = {}
    for region in regions:
        name = str(region["name"])
        results[name] = compare_region(name, region, reference, capture, args, overlay_dir)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "out_dir": str(out_dir),
        "reference": str(reference_path),
        "capture": str(capture_path),
        "thresholds": {
            "max_position_delta_px": args.max_position_delta,
            "max_count_delta_pct": args.max_count_delta_pct,
            "max_palette_delta": args.max_palette_delta,
        },
        "regions": results,
    }
    (out_dir / args.json_name).write_text(json.dumps(report, indent=2), encoding="utf-8")
    (out_dir / args.md_name).write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "out_dir": str(out_dir),
                "regions": {name: item["pass"] for name, item in results.items()},
                "json": str(out_dir / args.json_name),
                "markdown": str(out_dir / args.md_name),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
