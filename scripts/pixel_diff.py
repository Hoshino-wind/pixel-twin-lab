#!/usr/bin/env python3
"""Compare captured lab modes against a reference image."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    from PIL import Image, ImageChops, ImageStat
except ImportError:
    raise SystemExit(
        "Pillow is required. Install dependencies with:\n"
        "  pip install -r scripts/requirements.txt"
    )

try:
    import numpy as np
except ImportError:
    np = None


def clamp_region(region: dict, size: tuple[int, int]) -> dict | None:
    image_width, image_height = size
    x = max(0, int(region["x"]))
    y = max(0, int(region["y"]))
    width = min(int(region["width"]), image_width - x)
    height = min(int(region["height"]), image_height - y)
    if width <= 0 or height <= 0:
        return None
    return {"name": str(region["name"]), "x": x, "y": y, "width": width, "height": height}


def load_regions(out_dir: Path, regions_arg: str) -> list[dict]:
    """Collect diff regions: lab-config.json slices plus optional named regions.json entries."""
    if regions_arg == "none":
        return []

    sources: list[tuple[str, Path]] = []
    if regions_arg == "auto":
        sources = [("slices", out_dir / "lab-config.json"), ("regions", out_dir / "regions.json")]
    else:
        path = Path(regions_arg).expanduser().resolve()
        key = "slices" if path.name == "lab-config.json" else "regions"
        sources = [(key, path)]
        if not path.exists():
            raise SystemExit(f"Regions file does not exist: {path}")

    regions: list[dict] = []
    for key, path in sources:
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        entries = data if isinstance(data, list) else data.get(key, [])
        for index, entry in enumerate(entries, start=1):
            name = entry.get("name") or Path(str(entry.get("src", f"{key}-{index:02d}"))).stem
            regions.append({"name": name, **{k: entry[k] for k in ("x", "y", "width", "height")}})
    return regions


def analyze_numpy(diff: Image.Image, tolerance: int, regions: list[dict]) -> tuple[dict, Image.Image, list[dict]]:
    arr = np.asarray(diff, dtype=np.uint8)
    magnitude = arr.max(axis=2)

    def metrics_for(sub_arr, sub_mag) -> dict[str, object]:
        count = sub_mag.size
        result: dict[str, object] = {
            "mismatch_pixels": int(np.count_nonzero(sub_mag)),
            "mismatch_pct": round(np.count_nonzero(sub_mag) / count * 100, 4) if count else 0.0,
            "mae": round(float(sub_arr.mean()), 4) if count else 0.0,
            "max_delta": int(sub_mag.max()) if count else 0,
        }
        if tolerance > 0:
            tolerant = int(np.count_nonzero(sub_mag > tolerance))
            result["mismatch_pixels_tolerant"] = tolerant
            result["mismatch_pct_tolerant"] = round(tolerant / count * 100, 4) if count else 0.0
        return result

    overall = metrics_for(arr, magnitude)
    region_results = []
    for region in regions:
        x, y, w, h = region["x"], region["y"], region["width"], region["height"]
        region_results.append({**region, **metrics_for(arr[y : y + h, x : x + w], magnitude[y : y + h, x : x + w])})

    visual = np.full((*magnitude.shape, 3), 245, dtype=np.uint8)
    hot = magnitude > 0
    visual[hot, 0] = np.minimum(255, magnitude[hot].astype(np.uint16) * 4).astype(np.uint8)
    visual[hot, 1] = 20
    visual[hot, 2] = 20
    return overall, Image.fromarray(visual, "RGB"), region_results


def analyze_pil(diff: Image.Image, tolerance: int, regions: list[dict]) -> tuple[dict, Image.Image, list[dict]]:
    r, g, b = diff.split()
    magnitude = ImageChops.lighter(ImageChops.lighter(r, g), b)

    def metrics_for(sub_diff: Image.Image, sub_mag: Image.Image) -> dict[str, object]:
        histogram = sub_mag.histogram()
        count = sub_mag.width * sub_mag.height
        result: dict[str, object] = {
            "mismatch_pixels": count - histogram[0],
            "mismatch_pct": round((count - histogram[0]) / count * 100, 4) if count else 0.0,
            "mae": round(sum(ImageStat.Stat(sub_diff).mean) / 3, 4) if count else 0.0,
            "max_delta": sub_mag.getextrema()[1] if count else 0,
        }
        if tolerance > 0:
            tolerant = count - sum(histogram[: tolerance + 1])
            result["mismatch_pixels_tolerant"] = tolerant
            result["mismatch_pct_tolerant"] = round(tolerant / count * 100, 4) if count else 0.0
        return result

    overall = metrics_for(diff, magnitude)
    region_results = []
    for region in regions:
        box = (region["x"], region["y"], region["x"] + region["width"], region["y"] + region["height"])
        region_results.append({**region, **metrics_for(diff.crop(box), magnitude.crop(box))})

    heat = Image.merge(
        "RGB",
        (
            magnitude.point(lambda v: min(255, v * 4)),
            Image.new("L", diff.size, 20),
            Image.new("L", diff.size, 20),
        ),
    )
    base = Image.new("RGB", diff.size, (245, 245, 245))
    mask = magnitude.point(lambda v: 255 if v else 0)
    return overall, Image.composite(heat, base, mask), region_results


def compare(
    reference: Image.Image,
    capture_path: Path,
    out_dir: Path,
    tolerance: int,
    regions: list[dict],
) -> dict[str, object]:
    capture = Image.open(capture_path).convert("RGB")
    if capture.size != reference.size:
        return {
            "file": capture_path.name,
            "status": "size-mismatch",
            "reference_size": list(reference.size),
            "capture_size": list(capture.size),
        }

    diff = ImageChops.difference(reference, capture)
    if np is not None:
        overall, visual, region_results = analyze_numpy(diff, tolerance, regions)
    else:
        overall, visual, region_results = analyze_pil(diff, tolerance, regions)

    diff_name = capture_path.name.replace("-capture.png", "-diff.png")
    visual.save(out_dir / diff_name)

    summary: dict[str, object] = {
        "file": capture_path.name,
        "status": "ok",
        **overall,
        "bbox": list(diff.getbbox()) if diff.getbbox() else None,
        "diff": diff_name,
    }
    if tolerance > 0:
        summary["tolerance"] = tolerance
    if region_results:
        summary["regions"] = sorted(region_results, key=lambda item: -float(item["mismatch_pct"]))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", required=True, help="Absolute path to reference.png")
    parser.add_argument("--out-dir", required=True, help="Directory containing *-capture.png files")
    parser.add_argument("--captures", default="*-capture.png", help="Glob for capture files")
    parser.add_argument(
        "--tolerance",
        type=int,
        default=0,
        help="Also report mismatch ignoring per-channel deltas <= N (strict values are always reported)",
    )
    parser.add_argument(
        "--regions",
        default="auto",
        help="Per-region metrics: 'auto' uses lab-config.json slices plus regions.json named regions in --out-dir, 'none' disables, or pass a JSON file path",
    )
    parser.add_argument(
        "--print",
        dest="print_mode",
        choices=["brief", "full"],
        default="brief",
        help="Stdout verbosity: 'brief' prints overall metrics plus the worst regions per capture "
        "(full data is always in pixel-diff-summary.json); 'full' dumps the whole summary",
    )
    args = parser.parse_args()

    reference_path = Path(args.reference).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    reference = Image.open(reference_path).convert("RGB")

    regions = [
        clamped
        for region in load_regions(out_dir, args.regions)
        if (clamped := clamp_region(region, reference.size))
    ]

    summaries = []
    for capture_path in sorted(out_dir.glob(args.captures)):
        summaries.append(compare(reference, capture_path, out_dir, args.tolerance, regions))

    summary_path = out_dir / "pixel-diff-summary.json"
    summary_path.write_text(json.dumps(summaries, indent=2), encoding="utf-8")
    if args.print_mode == "full":
        print(json.dumps(summaries, indent=2))
        return
    brief = []
    for summary in summaries:
        entry = {
            key: summary[key]
            for key in ("file", "status", "mismatch_pct", "mismatch_pct_tolerant", "mae", "max_delta")
            if key in summary
        }
        regions_list = summary.get("regions")
        if isinstance(regions_list, list) and regions_list:
            entry["region_count"] = len(regions_list)
            entry["worst_regions"] = [
                {
                    "name": region.get("name"),
                    "mismatch_pct": region.get("mismatch_pct"),
                    **(
                        {"mismatch_pct_tolerant": region.get("mismatch_pct_tolerant")}
                        if "mismatch_pct_tolerant" in region
                        else {}
                    ),
                }
                for region in regions_list[:5]
            ]
        brief.append(entry)
    print(json.dumps({"captures": brief, "summary": str(summary_path)}, indent=2))


if __name__ == "__main__":
    main()
