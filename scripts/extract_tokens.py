#!/usr/bin/env python3
"""Extract design tokens (colors/typography/spacing) from the reference image."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

QUANT_LEVELS = 32
MERGE_DISTANCE = 24.0
TYPE_BUCKET_TOLERANCE = 2
SPACING_BUCKET_TOLERANCE = 2
DOWNSAMPLE_MAX = 256


def load_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return fallback


def quantize_colors(image: Image.Image) -> list[dict[str, Any]]:
    """Quantize each channel to QUANT_LEVELS, count frequencies, merge close buckets."""
    small = image.copy()
    small.thumbnail((DOWNSAMPLE_MAX, DOWNSAMPLE_MAX))
    arr = np.asarray(small.convert("RGB")).reshape(-1, 3).astype(np.int64)
    step = 256 // QUANT_LEVELS
    quant = arr // step
    keys = quant[:, 0] * QUANT_LEVELS * QUANT_LEVELS + quant[:, 1] * QUANT_LEVELS + quant[:, 2]
    unique_keys, inverse, counts = np.unique(keys, return_inverse=True, return_counts=True)
    sums = np.zeros((unique_keys.size, 3), dtype=np.float64)
    for channel in range(3):
        sums[:, channel] = np.bincount(inverse, weights=arr[:, channel], minlength=unique_keys.size)
    means = sums / counts[:, None]

    order = np.argsort(counts)[::-1]
    clusters: list[dict[str, Any]] = []
    for index in order:
        color = means[index]
        count = int(counts[index])
        merged = False
        for cluster in clusters:
            if float(np.linalg.norm(cluster["color"] - color)) < MERGE_DISTANCE:
                total = cluster["count"] + count
                cluster["color"] = (cluster["color"] * cluster["count"] + color * count) / total
                cluster["count"] = total
                merged = True
                break
        if not merged:
            clusters.append({"color": color, "count": count})
    clusters.sort(key=lambda c: c["count"], reverse=True)
    total_pixels = arr.shape[0]
    for cluster in clusters:
        cluster["coverage"] = cluster["count"] / total_pixels
    return clusters


def snap_to_reference(arr: np.ndarray, color: np.ndarray) -> tuple[list[int], list[int]]:
    """Snap the cluster mean to the closest real pixel so sampled_at verifies exactly."""
    target = np.round(color).astype(np.int16)
    delta = np.abs(arr - target).max(axis=2)
    flat = int(np.argmin(delta))
    y, x = divmod(flat, arr.shape[1])
    value = [int(v) for v in arr[y, x]]
    return value, [int(x), int(y)]


def build_colors(image: Image.Image, max_colors: int) -> list[dict[str, Any]]:
    arr = np.asarray(image.convert("RGB")).astype(np.int16)
    colors: list[dict[str, Any]] = []
    seen: dict[str, dict[str, Any]] = {}
    for cluster in quantize_colors(image):
        value, sampled_at = snap_to_reference(arr, cluster["color"])
        hex_value = "#{:02x}{:02x}{:02x}".format(*value)
        if hex_value in seen:
            seen[hex_value]["_coverage"] += cluster["coverage"]
            continue
        if len(colors) >= max_colors:
            continue
        token: dict[str, Any] = {
            "name": f"color-{len(colors) + 1:02d}",
            "value": hex_value,
            "sampled_at": sampled_at,
            "_coverage": cluster["coverage"],
        }
        if not colors:
            token["usage"] = "surface"
        seen[hex_value] = token
        colors.append(token)
    return colors


def bucket_values(entries: list[tuple[float, Any]], tolerance: float) -> list[dict[str, Any]]:
    """Greedy 1-D clustering: sort, open a bucket, extend while within tolerance of the bucket base."""
    buckets: list[dict[str, Any]] = []
    for value, meta in sorted(entries, key=lambda e: e[0]):
        if buckets and value - buckets[-1]["values"][0] <= tolerance:
            buckets[-1]["values"].append(value)
            buckets[-1]["meta"].append(meta)
        else:
            buckets.append({"values": [value], "meta": [meta]})
    for bucket in buckets:
        bucket["median"] = float(np.median(bucket["values"]))
        bucket["count"] = len(bucket["values"])
    return buckets


def build_typography(measured: dict[str, Any]) -> list[dict[str, Any]]:
    entries: list[tuple[float, str]] = []
    for region in measured.get("regions", []):
        for index, box in enumerate(region.get("primitives", []), start=1):
            if box.get("kind") == "text-line":
                entries.append((float(box["height"]), f"{region['name']} #{index}"))
    buckets = bucket_values(entries, TYPE_BUCKET_TOLERANCE)
    buckets.sort(key=lambda b: b["median"], reverse=True)
    typography: list[dict[str, Any]] = []
    for index, bucket in enumerate(buckets, start=1):
        typography.append(
            {
                "name": f"type-{index:02d}",
                "size_px": bucket["median"],
                "measured_from": bucket["meta"][0],
                "_count": bucket["count"],
            }
        )
    return typography


def overlap_ratio(a0: int, a1: int, b0: int, b1: int) -> float:
    overlap = min(a1, b1) - max(a0, b0)
    shortest = min(a1 - a0, b1 - b0)
    if shortest <= 0:
        return 0.0
    return overlap / shortest


def collect_gaps(measured: dict[str, Any]) -> list[tuple[float, str]]:
    gaps: list[tuple[float, str]] = []
    for region in measured.get("regions", []):
        boxes = [box["full_bounds"] for box in region.get("primitives", []) if box.get("full_bounds")]
        name = region["name"]
        for box in boxes:
            right_gap = None
            below_gap = None
            for other in boxes:
                if other is box:
                    continue
                if (
                    overlap_ratio(box["y"], box["y"] + box["height"], other["y"], other["y"] + other["height"]) >= 0.5
                    and other["x"] >= box["x"] + box["width"]
                ):
                    gap = other["x"] - (box["x"] + box["width"])
                    right_gap = gap if right_gap is None else min(right_gap, gap)
                if (
                    overlap_ratio(box["x"], box["x"] + box["width"], other["x"], other["x"] + other["width"]) >= 0.5
                    and other["y"] >= box["y"] + box["height"]
                ):
                    gap = other["y"] - (box["y"] + box["height"])
                    below_gap = gap if below_gap is None else min(below_gap, gap)
            if right_gap is not None:
                gaps.append((float(right_gap), f"{name} horizontal"))
            if below_gap is not None:
                gaps.append((float(below_gap), f"{name} vertical"))
    return gaps


def build_spacing(measured: dict[str, Any]) -> list[dict[str, Any]]:
    buckets = [b for b in bucket_values(collect_gaps(measured), SPACING_BUCKET_TOLERANCE) if b["count"] >= 2]
    buckets.sort(key=lambda b: b["median"])
    spacing: list[dict[str, Any]] = []
    for index, bucket in enumerate(buckets, start=1):
        spacing.append({"name": f"space-{index:02d}", "px": bucket["median"], "_count": bucket["count"]})
    return spacing


def strip_private(tokens: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{k: v for k, v in token.items() if not k.startswith("_")} for token in tokens]


def render_markdown(report: dict[str, Any], colors: list[dict[str, Any]], typography: list[dict[str, Any]], spacing: list[dict[str, Any]], measured_path: Path | None) -> str:
    lines = [
        "# Visual Tokens",
        "",
        f"Generated: `{report['generated_at']}`",
        f"Out dir: `{report['out_dir']}`",
        f"Reference: `{report['reference']}`",
        f"Measured primitives: `{measured_path}`" if measured_path else "Measured primitives: not found (typography/spacing empty)",
        "",
        "## Colors",
        "",
        "| Name | Value | Usage | Coverage | Sampled at |",
        "| --- | --- | --- | ---: | --- |",
    ]
    for token in colors:
        lines.append(
            f"| `{token['name']}` | `{token['value']}` | {token.get('usage', '')} "
            f"| {token['_coverage'] * 100:.1f}% | `{token['sampled_at']}` |"
        )
    lines += [
        "",
        "## Typography",
        "",
        "`size_px` is the measured text-line box height (roughly font-size x 1.2), not the CSS font-size.",
        "",
        "| Name | size_px | Samples | Measured from |",
        "| --- | ---: | ---: | --- |",
    ]
    for token in typography:
        lines.append(f"| `{token['name']}` | {token['size_px']} | {token['_count']} | `{token['measured_from']}` |")
    lines += [
        "",
        "## Spacing",
        "",
        "Gaps between adjacent measured boxes within the same region (buckets seen at least twice).",
        "",
        "| Name | px | Samples |",
        "| --- | ---: | ---: |",
    ]
    for token in spacing:
        lines.append(f"| `{token['name']}` | {token['px']} | {token['_count']} |")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True, help="Pixel Twin Lab output directory")
    parser.add_argument("--reference", help="Reference image path; defaults to <out-dir>/assets/reference.png")
    parser.add_argument("--measured-primitives", help="measured-primitives JSON filename/path")
    parser.add_argument("--max-colors", type=int, default=12, help="Number of color tokens to keep")
    parser.add_argument("--json-name", default="visual-tokens.json")
    parser.add_argument("--md-name", default="visual-tokens.md")
    args = parser.parse_args()

    out_dir = Path(args.out_dir).expanduser().resolve()
    reference_path = Path(args.reference).expanduser().resolve() if args.reference else out_dir / "assets/reference.png"
    if not reference_path.exists():
        raise SystemExit(f"Reference image not found: {reference_path}")
    if args.measured_primitives:
        measured_path = Path(args.measured_primitives)
        measured_path = measured_path if measured_path.is_absolute() else out_dir / measured_path
    else:
        measured_path = out_dir / "measured-primitives.json"
    measured = load_json(measured_path, None)
    has_measured = isinstance(measured, dict)

    image = Image.open(reference_path).convert("RGB")
    colors = build_colors(image, args.max_colors)
    typography = build_typography(measured) if has_measured else []
    spacing = build_spacing(measured) if has_measured else []

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "out_dir": str(out_dir),
        "reference": str(reference_path),
        "colors": strip_private(colors),
        "typography": strip_private(typography),
        "spacing": strip_private(spacing),
        "radius": [],
        "shadows": [],
        "borders": [],
    }
    (out_dir / args.json_name).write_text(json.dumps(report, indent=2), encoding="utf-8")
    markdown = render_markdown(report, colors, typography, spacing, measured_path if has_measured else None)
    (out_dir / args.md_name).write_text(markdown, encoding="utf-8")
    summary = {
        "out_dir": str(out_dir),
        "colors": len(colors),
        "typography": len(typography),
        "spacing": len(spacing),
        "json": str(out_dir / args.json_name),
        "markdown": str(out_dir / args.md_name),
    }
    if not has_measured:
        summary["note"] = f"measured-primitives not found at {measured_path}; typography/spacing left empty"
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
