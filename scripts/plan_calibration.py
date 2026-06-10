#!/usr/bin/env python3
"""Generate a next-round calibration plan from per-region pixel diffs.

Classifies each region's error so the fix happens in the right pass:
layout shift -> visual token -> asset slice island -> region rebuild loop.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

try:
    from PIL import Image, ImageChops, ImageFilter, ImageStat
except ImportError:
    raise SystemExit(
        "Pillow is required. Install dependencies with:\n"
        "  pip install -r scripts/requirements.txt"
    )

try:
    import numpy as np
except ImportError:
    np = None

import pixel_diff

# A shift candidate wins when it removes at least this share of the mismatch.
SHIFT_IMPROVEMENT = 0.3
# Only probe shifts / classify regions that are meaningfully wrong.
ACTION_MISMATCH_PCT = 1.0
SHIFT_PROBE_MISMATCH_PCT = 5.0
# Uniform color delta: low spread, clear mean offset.
TOKEN_MAX_STD = 8.0
TOKEN_MIN_MEAN = 3.0
# Asset-island content signals measured on the reference crop.
ISLAND_EDGE_DENSITY = 0.20
ISLAND_COLOR_COUNT = 2000
EDGE_THRESHOLD = 24
# Antialiasing-only convergence: tiny mean error, almost no strong deltas.
AA_MAX_MAE = 1.5
AA_BIG_DELTA = 24
AA_BIG_DELTA_FRACTION = 0.005
# Not built yet: the capture side is a near-uniform fill while the reference has real content.
NOT_BUILT_MIN_MISMATCH_PCT = 50.0
NOT_BUILT_MAX_STD = 6.0
NOT_BUILT_MIN_REF_STD = 10.0


def hex_color(rgb: tuple[float, float, float]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*(max(0, min(255, round(c))) for c in rgb))


def crop(image: Image.Image, region: dict) -> Image.Image:
    return image.crop(
        (region["x"], region["y"], region["x"] + region["width"], region["y"] + region["height"])
    )


def diff_metrics(ref: Image.Image, cap: Image.Image, tolerance: int = 0) -> dict[str, float]:
    diff = ImageChops.difference(ref, cap)
    if np is not None:
        arr = np.asarray(diff, dtype=np.uint8)
        magnitude = arr.max(axis=2)
        count = magnitude.size
        big = int(np.count_nonzero(magnitude > AA_BIG_DELTA))
        tolerant = int(np.count_nonzero(magnitude > tolerance))
        return {
            "mismatch_pct": round(float(np.count_nonzero(magnitude)) / count * 100, 4),
            "mismatch_pct_tolerant": round(tolerant / count * 100, 4),
            "mae": round(float(arr.mean()), 4),
            "max_delta": int(magnitude.max()),
            "big_delta_fraction": round(big / count, 6),
        }
    r, g, b = diff.split()
    magnitude = ImageChops.lighter(ImageChops.lighter(r, g), b)
    histogram = magnitude.histogram()
    count = magnitude.width * magnitude.height
    return {
        "mismatch_pct": round((count - histogram[0]) / count * 100, 4),
        "mismatch_pct_tolerant": round(sum(histogram[tolerance + 1 :]) / count * 100, 4),
        "mae": round(sum(ImageStat.Stat(diff).mean) / 3, 4),
        "max_delta": magnitude.getextrema()[1],
        "big_delta_fraction": round(sum(histogram[AA_BIG_DELTA + 1 :]) / count, 6),
    }


def probe_shift(ref: Image.Image, cap: Image.Image, radius: int, tolerance: int) -> dict | None:
    """Find the integer offset of the capture content that best explains the mismatch."""
    width, height = ref.size
    if width <= 2 * radius or height <= 2 * radius:
        return None
    inner = ref.crop((radius, radius, width - radius, height - radius))
    base = diff_metrics(inner, cap.crop((radius, radius, width - radius, height - radius)), tolerance)
    if base["mismatch_pct_tolerant"] == 0:
        return None
    best = {"dx": 0, "dy": 0, "mismatch_pct": base["mismatch_pct_tolerant"]}
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            if dx == 0 and dy == 0:
                continue
            window = cap.crop(
                (radius + dx, radius + dy, width - radius + dx, height - radius + dy)
            )
            mismatch = diff_metrics(inner, window, tolerance)["mismatch_pct_tolerant"]
            if mismatch < best["mismatch_pct"]:
                best = {"dx": dx, "dy": dy, "mismatch_pct": mismatch}
    if (best["dx"], best["dy"]) == (0, 0):
        return None
    if best["mismatch_pct"] > base["mismatch_pct_tolerant"] * SHIFT_IMPROVEMENT:
        return None
    return {**best, "base_mismatch_pct": base["mismatch_pct_tolerant"]}


def probe_uniform_color(ref: Image.Image, cap: Image.Image) -> dict | None:
    """Detect a near-constant color offset between reference and capture."""
    ref_mean = ImageStat.Stat(ref).mean
    cap_mean = ImageStat.Stat(cap).mean
    deltas = [r - c for r, c in zip(ref_mean, cap_mean)]
    if max(abs(d) for d in deltas) < TOKEN_MIN_MEAN:
        return None
    diff = ImageChops.difference(ref, cap)
    if np is not None:
        spread = float(np.asarray(diff, dtype=np.float32).std())
    else:
        spread = sum(ImageStat.Stat(diff).stddev) / 3
    if spread > TOKEN_MAX_STD:
        return None
    return {
        "reference_color": hex_color(tuple(ref_mean)),
        "capture_color": hex_color(tuple(cap_mean)),
        "channel_delta": [round(d, 2) for d in deltas],
        "spread": round(spread, 2),
    }


def content_complexity(ref: Image.Image) -> dict:
    """Edge density and color diversity of the reference crop — high values mean raster-like content."""
    edges = ref.convert("L").filter(ImageFilter.FIND_EDGES)
    if np is not None:
        arr = np.asarray(edges, dtype=np.uint8)
        density = float(np.count_nonzero(arr > EDGE_THRESHOLD)) / arr.size
        quantized = np.asarray(ref, dtype=np.uint8) // 8
        colors = len(np.unique(quantized.reshape(-1, 3), axis=0))
    else:
        histogram = edges.histogram()
        total = edges.width * edges.height
        density = sum(histogram[EDGE_THRESHOLD + 1 :]) / total
        sampled = ref.getcolors(maxcolors=ISLAND_COLOR_COUNT * 4)
        colors = len(sampled) if sampled else ISLAND_COLOR_COUNT * 4
    return {"edge_density": round(density, 4), "color_count": int(colors)}


def flatness(image: Image.Image) -> float:
    """Mean per-channel standard deviation; near zero means a uniform fill."""
    return round(sum(ImageStat.Stat(image).stddev) / 3, 2)


def dominant_color(image: Image.Image) -> str:
    """Most common coarse color bucket, refined to the bucket's most common color — robust against text/content."""
    small = image if image.width * image.height <= 65536 else image.resize((256, 256))
    counted = small.getcolors(maxcolors=small.width * small.height) or []
    buckets: Counter = Counter()
    for count, (r, g, b) in counted:
        buckets[(r // 16, g // 16, b // 16)] += count
    winner = buckets.most_common(1)[0][0]
    count, color = max(
        (entry for entry in counted if (entry[1][0] // 16, entry[1][1] // 16, entry[1][2] // 16) == winner),
        key=lambda entry: entry[0],
    )
    return hex_color(color)


def classify(region: dict, ref: Image.Image, cap: Image.Image, shift_radius: int, tolerance: int) -> dict:
    ref_crop = crop(ref, region)
    cap_crop = crop(cap, region)
    metrics = diff_metrics(ref_crop, cap_crop, tolerance)
    result = {**region, **metrics}
    effective = metrics["mismatch_pct_tolerant"]

    if effective < ACTION_MISMATCH_PCT or (
        metrics["mae"] <= AA_MAX_MAE and metrics["big_delta_fraction"] <= AA_BIG_DELTA_FRACTION
    ):
        result["classification"] = "converged"
        result["action"] = "No action; remaining error is antialiasing-level noise."
        return result

    if effective >= SHIFT_PROBE_MISMATCH_PCT:
        shift = probe_shift(ref_crop, cap_crop, shift_radius, tolerance)
        if shift:
            result["classification"] = "layout"
            result["evidence"] = shift
            result["action"] = (
                f"Build content is offset (dx={shift['dx']:+d}, dy={shift['dy']:+d}) from the reference; "
                f"move it by ({-shift['dx']:+d}, {-shift['dy']:+d}) — mismatch drops "
                f"{shift['base_mismatch_pct']}% -> {shift['mismatch_pct']}% when aligned."
            )
            return result

    complexity = content_complexity(ref_crop)
    if (
        complexity["edge_density"] >= ISLAND_EDGE_DENSITY
        or complexity["color_count"] >= ISLAND_COLOR_COUNT
    ):
        result["classification"] = "slice-island"
        result["evidence"] = complexity
        result["action"] = (
            f"Raster-like content (edge density {complexity['edge_density']}, "
            f"{complexity['color_count']} colors); keep as a bitmap slice island via the manifest."
        )
        return result

    capture_std = flatness(cap_crop)
    reference_std = flatness(ref_crop)
    if (
        effective >= NOT_BUILT_MIN_MISMATCH_PCT
        and capture_std <= NOT_BUILT_MAX_STD
        and reference_std >= NOT_BUILT_MIN_REF_STD
    ):
        fill = dominant_color(ref_crop)
        result["classification"] = "not-built"
        result["evidence"] = {
            "capture_color": dominant_color(cap_crop),
            "capture_std": capture_std,
            "reference_std": reference_std,
            "reference_fill": fill,
        }
        result["action"] = (
            f"Region is not built yet (capture is a flat {result['evidence']['capture_color']}); "
            f"start with a container filled {fill} — see skeleton.suggested.css."
        )
        return result

    color = probe_uniform_color(ref_crop, cap_crop)
    if color:
        result["classification"] = "token"
        result["evidence"] = color
        result["action"] = (
            f"Uniform color offset: reference {color['reference_color']} vs build "
            f"{color['capture_color']}; fix the token, not the layout."
        )
        return result

    result["classification"] = "rebuild"
    result["evidence"] = complexity
    result["action"] = "Structural mismatch; rebuild this region against the reference crop."
    return result


PASSES = [
    ("not-built", "Pass 0 - Skeleton (regions not built yet)"),
    ("layout", "Pass 1 - Layout (geometry first)"),
    ("token", "Pass 2 - Visual tokens (colors, borders, shadows)"),
    ("slice-island", "Pass 3 - Asset islands (slice, do not componentize)"),
    ("rebuild", "Pass 4 - Region rebuild loop (worst first)"),
]


def render_skeleton_css(regions: list[dict]) -> str:
    lines = [
        "/* Skeleton bootstrap for the lab rebuilt-layer (positions + reference fills).",
        "   Lab/measuring aid only — final project code must follow the target project's conventions. */",
        "",
    ]
    for region in regions:
        name = str(region["name"]).replace(" ", "-")
        lines.append(
            f".region-{name} {{ position: absolute; left: {region['x']}px; top: {region['y']}px; "
            f"width: {region['width']}px; height: {region['height']}px; "
            f"background: {region['evidence']['reference_fill']}; }}"
        )
    lines.append("")
    return "\n".join(lines)


def render_markdown(plan: dict) -> str:
    tolerance = plan["tolerance"]
    lines = [
        "# Calibration Plan",
        "",
        f"Capture: `{plan['capture']}` | tolerance {tolerance} | overall mismatch "
        f"{plan['overall']['mismatch_pct_tolerant']}% (strict {plan['overall']['mismatch_pct']}%) "
        f"| MAE {plan['overall']['mae']}",
        "",
    ]
    for key, title in PASSES:
        regions = plan["passes"][key]
        if not regions:
            continue
        lines.append(f"## {title}")
        lines.append("")
        for region in regions:
            lines.append(
                f"- `{region['name']}` (mismatch {region['mismatch_pct_tolerant']}%, "
                f"strict {region['mismatch_pct']}%): {region['action']}"
            )
        lines.append("")
    converged = plan["converged"]
    lines.append(f"## Converged ({len(converged)} regions, no action)")
    lines.append("")
    if converged:
        lines.append(", ".join(f"`{region['name']}`" for region in converged))
        lines.append("")
    if plan["passes"]["not-built"]:
        lines.append(
            "Skeleton CSS for unbuilt regions (positions + reference fills) written to "
            "`skeleton.suggested.css`; paste it into the rebuilt layer to bootstrap the layout pass."
        )
        lines.append("")
    if plan["passes"]["slice-island"]:
        lines.append(
            "Suggested manifest for island regions written to `slice-manifest.suggested.json`; "
            "merge it into your slice manifest and rerun `prepare_lab.py --manifest`."
        )
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", required=True, help="Absolute path to reference.png")
    parser.add_argument("--capture", help="Capture to plan against; defaults to <out-dir>/rebuilt-capture.png")
    parser.add_argument("--out-dir", required=True, help="Lab directory (regions sources + plan output)")
    parser.add_argument(
        "--regions",
        default="auto",
        help="Region sources, same semantics as pixel_diff.py: 'auto', 'none', or a JSON file path",
    )
    parser.add_argument("--shift-radius", type=int, default=4, help="Max layout shift to probe, in pixels")
    parser.add_argument(
        "--tolerance",
        type=int,
        default=8,
        help="Ignore per-channel deltas <= N when classifying (default 8); strict values are always "
        "reported alongside. Use 0 for strict-only classification — note that font/antialiasing noise "
        "blinds the shift and color probes at 0",
    )
    args = parser.parse_args()

    out_dir = Path(args.out_dir).expanduser().resolve()
    reference_path = Path(args.reference).expanduser().resolve()
    capture_path = (
        Path(args.capture).expanduser().resolve() if args.capture else out_dir / "rebuilt-capture.png"
    )
    if not capture_path.exists():
        raise SystemExit(f"Capture does not exist: {capture_path}")

    reference = Image.open(reference_path).convert("RGB")
    capture = Image.open(capture_path).convert("RGB")
    if reference.size != capture.size:
        raise SystemExit(
            f"Size mismatch: reference {reference.size} vs capture {capture.size}. "
            "Fix the capture viewport first (zero-baseline rule) before planning fixes."
        )

    regions = [
        clamped
        for region in pixel_diff.load_regions(out_dir, args.regions)
        if (clamped := pixel_diff.clamp_region(region, reference.size))
    ]
    if not regions:
        print("WARNING: no regions found (lab-config.json slices / regions.json); analyzing the full image as one region.")
        regions = [{"name": "full", "x": 0, "y": 0, "width": reference.size[0], "height": reference.size[1]}]

    results = [classify(region, reference, capture, args.shift_radius, args.tolerance) for region in regions]
    results.sort(key=lambda item: -float(item["mismatch_pct_tolerant"]))

    plan = {
        "reference": str(reference_path),
        "capture": capture_path.name,
        "tolerance": args.tolerance,
        "overall": diff_metrics(reference, capture, args.tolerance),
        "passes": {key: [r for r in results if r["classification"] == key] for key, _ in PASSES},
        "converged": [r for r in results if r["classification"] == "converged"],
    }

    (out_dir / "calibration-plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
    (out_dir / "calibration-plan.md").write_text(render_markdown(plan), encoding="utf-8")

    suggested = out_dir / "slice-manifest.suggested.json"
    islands = plan["passes"]["slice-island"]
    if islands:
        suggested.write_text(
            json.dumps(
                {
                    "slices": [
                        {k: region[k] for k in ("name", "x", "y", "width", "height")}
                        for region in islands
                    ]
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    elif suggested.exists():
        suggested.unlink()

    skeleton = out_dir / "skeleton.suggested.css"
    not_built = plan["passes"]["not-built"]
    if not_built:
        skeleton.write_text(render_skeleton_css(not_built), encoding="utf-8")
    elif skeleton.exists():
        skeleton.unlink()

    print(
        json.dumps(
            {
                "out_dir": str(out_dir),
                "capture": capture_path.name,
                "tolerance": args.tolerance,
                "overall_mismatch_pct": plan["overall"]["mismatch_pct"],
                "overall_mismatch_pct_tolerant": plan["overall"]["mismatch_pct_tolerant"],
                "plan": {key: len(value) for key, value in plan["passes"].items()},
                "converged": len(plan["converged"]),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
