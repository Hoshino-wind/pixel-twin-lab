#!/usr/bin/env python3
"""Create a pixel-twin workbench from a reference image."""

from __future__ import annotations

import argparse
import json
import re
import shutil
from collections import Counter, deque
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    raise SystemExit(
        "Pillow is required. Install dependencies with:\n"
        "  pip install -r scripts/requirements.txt"
    )

try:
    import numpy as np
except ImportError:
    np = None

# Connected-component detection runs on a downsampled copy above this size.
DETECT_MAX_PIXELS = 2_000_000


def hex_color(rgb: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def border_samples(image: Image.Image) -> list[tuple[int, int, int]]:
    width, height = image.size
    points: list[tuple[int, int]] = []
    step_x = max(1, width // 64)
    step_y = max(1, height // 64)
    for x in range(0, width, step_x):
        points.append((x, 0))
        points.append((x, height - 1))
    for y in range(0, height, step_y):
        points.append((0, y))
        points.append((width - 1, y))
    return [image.getpixel(point) for point in points]


def estimate_background(image: Image.Image) -> tuple[int, int, int]:
    """Most common border color: bucket border samples, take the per-channel median of the winning bucket."""
    samples = border_samples(image)
    buckets = Counter((r // 16, g // 16, b // 16) for r, g, b in samples)
    winner = buckets.most_common(1)[0][0]
    members = [s for s in samples if (s[0] // 16, s[1] // 16, s[2] // 16) == winner]
    return tuple(sorted(channel)[len(channel) // 2] for channel in zip(*members))


def background_uniformity(image: Image.Image, bg: tuple[int, int, int], threshold: int) -> float:
    """Fraction of border samples whose color stays within threshold of the estimated background."""
    samples = border_samples(image)
    within = sum(
        1
        for r, g, b in samples
        if abs(r - bg[0]) + abs(g - bg[1]) + abs(b - bg[2]) <= threshold
    )
    return within / len(samples)


def build_mask(image: Image.Image, bg: tuple[int, int, int], threshold: int) -> bytearray:
    width, height = image.size
    if np is not None:
        arr = np.asarray(image, dtype=np.int16)
        mask_arr = np.abs(arr - np.array(bg, dtype=np.int16)).sum(axis=2) > threshold
        return bytearray(mask_arr.astype(np.uint8).reshape(-1).tobytes())

    pixels = image.load()
    mask = bytearray(width * height)
    for y in range(height):
        row = y * width
        for x in range(width):
            r, g, b = pixels[x, y]
            if abs(r - bg[0]) + abs(g - bg[1]) + abs(b - bg[2]) > threshold:
                mask[row + x] = 1
    return mask


def detect_components(
    image: Image.Image,
    bg: tuple[int, int, int],
    threshold: int,
    min_area: int,
    max_slices: int,
) -> list[dict[str, int]]:
    full_width, full_height = image.size
    scale = 1.0
    work = image
    total = full_width * full_height
    if total > DETECT_MAX_PIXELS:
        scale = (DETECT_MAX_PIXELS / total) ** 0.5
        work = image.resize(
            (max(1, round(full_width * scale)), max(1, round(full_height * scale))),
            Image.BILINEAR,
        )
        min_area = max(1, int(min_area * scale * scale))

    width, height = work.size
    mask = build_mask(work, bg, threshold)

    seen = bytearray(width * height)
    components: list[tuple[int, int, int, int, int]] = []

    for y in range(height):
        for x in range(width):
            index = y * width + x
            if not mask[index] or seen[index]:
                continue

            queue: deque[tuple[int, int]] = deque([(x, y)])
            seen[index] = 1
            area = 0
            min_x = max_x = x
            min_y = max_y = y

            while queue:
                cx, cy = queue.popleft()
                area += 1
                min_x = min(min_x, cx)
                max_x = max(max_x, cx)
                min_y = min(min_y, cy)
                max_y = max(max_y, cy)

                for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                    if nx < 0 or ny < 0 or nx >= width or ny >= height:
                        continue
                    nindex = ny * width + nx
                    if mask[nindex] and not seen[nindex]:
                        seen[nindex] = 1
                        queue.append((nx, ny))

            if area >= min_area:
                components.append((area, min_x, min_y, max_x + 1, max_y + 1))

    components.sort(reverse=True)
    selected = sorted(components[:max_slices], key=lambda item: (item[1], item[2]))
    return [
        {
            "x": min(full_width - 1, int(x0 / scale)),
            "y": min(full_height - 1, int(y0 / scale)),
            "width": max(1, min(full_width, round((x1 - x0) / scale))),
            "height": max(1, min(full_height, round((y1 - y0) / scale))),
            "area": int(area / (scale * scale)),
        }
        for area, x0, y0, x1, y1 in selected
    ]


def copy_template(skill_root: Path, out_dir: Path) -> None:
    template = skill_root / "assets" / "prototype-template"
    for name in ("index.html", "styles.css", "script.js"):
        shutil.copy2(template / name, out_dir / name)


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return slug


def load_manifest(path: Path, width: int, height: int) -> list[dict[str, object]]:
    """Parse a slice manifest: {"slices": [...]}, {"regions": [...]}, or a bare list of rects."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        entries = data.get("slices", data.get("regions"))
        if entries is None:
            raise SystemExit(f"Manifest {path} must contain a 'slices' or 'regions' array (or be a bare array).")
    else:
        entries = data
    if not isinstance(entries, list) or not entries:
        raise SystemExit(f"Manifest {path} contains no slice entries.")

    rects: list[dict[str, object]] = []
    errors: list[str] = []
    for index, entry in enumerate(entries, start=1):
        try:
            x, y = int(entry["x"]), int(entry["y"])
            w, h = int(entry["width"]), int(entry["height"])
        except (KeyError, TypeError, ValueError):
            errors.append(f"entry {index}: requires integer x, y, width, height ({entry!r})")
            continue
        clamped_x, clamped_y = max(0, x), max(0, y)
        w = min(x + w, width) - clamped_x
        h = min(y + h, height) - clamped_y
        if w <= 0 or h <= 0:
            errors.append(f"entry {index}: empty after clamping to {width}x{height} canvas ({entry!r})")
            continue
        rect: dict[str, object] = {"x": clamped_x, "y": clamped_y, "width": w, "height": h}
        name = str(entry.get("name", "")).strip()
        if name:
            rect["name"] = name
        rects.append(rect)
    if errors:
        raise SystemExit("Invalid manifest entries:\n" + "\n".join(f"  - {error}" for error in errors))
    return rects


def uncovered_rects(rects: list[dict[str, object]], width: int, height: int) -> list[dict[str, int]]:
    """Decompose the canvas area not covered by rects into row bands of uncovered x-intervals,
    merging vertically adjacent bands with identical intervals."""
    breaks = {0, height}
    for rect in rects:
        breaks.add(int(rect["y"]))
        breaks.add(int(rect["y"]) + int(rect["height"]))
    ys = sorted(y for y in breaks if 0 <= y <= height)

    bands: list[tuple[int, int, tuple[tuple[int, int], ...]]] = []
    for y0, y1 in zip(ys, ys[1:]):
        covered = sorted(
            (int(r["x"]), int(r["x"]) + int(r["width"]))
            for r in rects
            if int(r["y"]) <= y0 and int(r["y"]) + int(r["height"]) >= y1
        )
        uncovered: list[tuple[int, int]] = []
        cursor = 0
        for x0, x1 in covered:
            if x0 > cursor:
                uncovered.append((cursor, min(x0, width)))
            cursor = max(cursor, x1)
            if cursor >= width:
                break
        if cursor < width:
            uncovered.append((cursor, width))
        bands.append((y0, y1, tuple(uncovered)))

    merged: list[dict[str, int]] = []
    open_band: tuple[int, int, tuple[tuple[int, int], ...]] | None = None
    for y0, y1, intervals in bands:
        if open_band and open_band[1] == y0 and open_band[2] == intervals:
            open_band = (open_band[0], y1, intervals)
            continue
        if open_band:
            merged.extend(
                {"x": x0, "y": open_band[0], "width": x1 - x0, "height": open_band[1] - open_band[0]}
                for x0, x1 in open_band[2]
            )
        open_band = (y0, y1, intervals)
    if open_band:
        merged.extend(
            {"x": x0, "y": open_band[0], "width": x1 - x0, "height": open_band[1] - open_band[0]}
            for x0, x1 in open_band[2]
        )
    return merged


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", required=True, help="Absolute path to the source UI image")
    parser.add_argument("--out-dir", required=True, help="Output lab directory")
    parser.add_argument("--threshold", type=int, default=70, help="Foreground threshold against estimated background")
    parser.add_argument("--min-area", type=int, default=10000, help="Minimum connected-component area to slice")
    parser.add_argument("--max-slices", type=int, default=12, help="Maximum auto-detected slices")
    parser.add_argument("--no-detect", action="store_true", help="Skip auto slice detection")
    parser.add_argument(
        "--manifest",
        help="JSON file of named slice rectangles ({'slices': [{name, x, y, width, height}, ...]}, "
        "same shape as regions.json); replaces threshold-based auto detection",
    )
    parser.add_argument(
        "--no-cover-gaps",
        action="store_true",
        help="With --manifest, do not generate gap slices for canvas area the manifest leaves uncovered",
    )
    parser.add_argument(
        "--full-bleed",
        action="store_true",
        help="Use the whole reference as a single slice (for gradient/photo backgrounds where a solid background fill cannot reach ~0%% in exact mode)",
    )
    args = parser.parse_args()

    reference_path = Path(args.reference).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    assets_dir = out_dir / "assets"
    skill_root = Path(__file__).resolve().parents[1]

    out_dir.mkdir(parents=True, exist_ok=True)
    assets_dir.mkdir(parents=True, exist_ok=True)
    copy_template(skill_root, out_dir)

    image = Image.open(reference_path).convert("RGB")
    width, height = image.size
    bg = estimate_background(image)
    reference_out = assets_dir / "reference.png"
    image.save(reference_out)

    uniformity = background_uniformity(image, bg, args.threshold)
    background_uniform = uniformity >= 0.9
    if not background_uniform and not args.full_bleed:
        print(
            f"WARNING: background does not look like a uniform solid color "
            f"({uniformity:.0%} of border samples match the estimated background). "
            "Exact mode fills the canvas with one color, so it may not reach ~0% mismatch. "
            "Consider --full-bleed.",
        )

    slices: list[dict[str, object]] = []
    if args.full_bleed:
        slice_source = "full-bleed"
        filename = "slice-01.png"
        image.save(assets_dir / filename)
        slices.append(
            {
                "src": f"./assets/{filename}",
                "x": 0,
                "y": 0,
                "width": width,
                "height": height,
                "area": width * height,
            }
        )
    elif args.manifest:
        slice_source = "manifest"
        manifest_path = Path(args.manifest).expanduser().resolve()
        if not manifest_path.exists():
            raise SystemExit(f"Manifest file does not exist: {manifest_path}")
        rects = load_manifest(manifest_path, width, height)
        if not args.no_cover_gaps:
            gaps = uncovered_rects(rects, width, height)
            rects.extend({**gap, "name": f"gap-{index:02d}"} for index, gap in enumerate(gaps, start=1))
        for index, rect in enumerate(rects, start=1):
            x, y = int(rect["x"]), int(rect["y"])
            w, h = int(rect["width"]), int(rect["height"])
            slug = slugify(str(rect.get("name", "")))
            filename = f"slice-{index:02d}-{slug}.png" if slug else f"slice-{index:02d}.png"
            image.crop((x, y, x + w, y + h)).save(assets_dir / filename)
            entry: dict[str, object] = {
                "src": f"./assets/{filename}",
                "x": x,
                "y": y,
                "width": w,
                "height": h,
                "area": w * h,
            }
            if rect.get("name"):
                entry["name"] = rect["name"]
            slices.append(entry)
    elif not args.no_detect:
        slice_source = "auto"
        components = detect_components(image, bg, args.threshold, args.min_area, args.max_slices)
        for index, component in enumerate(components, start=1):
            x = int(component["x"])
            y = int(component["y"])
            w = min(int(component["width"]), width - x)
            h = min(int(component["height"]), height - y)
            filename = f"slice-{index:02d}.png"
            image.crop((x, y, x + w, y + h)).save(assets_dir / filename)
            slices.append(
                {
                    "src": f"./assets/{filename}",
                    "x": x,
                    "y": y,
                    "width": w,
                    "height": h,
                    "area": int(component["area"]),
                }
            )
    else:
        slice_source = "none"

    uncovered_area = sum(
        rect["width"] * rect["height"] for rect in uncovered_rects(slices, width, height)
    )
    coverage_pct = round((1 - uncovered_area / (width * height)) * 100, 2)
    if args.manifest and args.no_cover_gaps and coverage_pct < 100:
        print(
            f"WARNING: manifest slices cover {coverage_pct}% of the canvas; exact mode will show "
            "the background fill in uncovered areas. Drop --no-cover-gaps to add gap slices.",
        )

    config = {
        "source": str(reference_path),
        "width": width,
        "height": height,
        "background": hex_color(bg),
        "background_uniform": background_uniform,
        "slice_source": slice_source,
        "coverage_pct": coverage_pct,
        "slices": slices,
    }
    (out_dir / "lab-config.json").write_text(json.dumps(config, indent=2), encoding="utf-8")

    print(
        json.dumps(
            {
                "out_dir": str(out_dir),
                "width": width,
                "height": height,
                "background_uniform": background_uniform,
                "slice_source": slice_source,
                "coverage_pct": coverage_pct,
                "slices": len(slices),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
