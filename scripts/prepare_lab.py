#!/usr/bin/env python3
"""Create a pixel-twin workbench from a reference image."""

from __future__ import annotations

import argparse
import json
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", required=True, help="Absolute path to the source UI image")
    parser.add_argument("--out-dir", required=True, help="Output lab directory")
    parser.add_argument("--threshold", type=int, default=70, help="Foreground threshold against estimated background")
    parser.add_argument("--min-area", type=int, default=10000, help="Minimum connected-component area to slice")
    parser.add_argument("--max-slices", type=int, default=12, help="Maximum auto-detected slices")
    parser.add_argument("--no-detect", action="store_true", help="Skip auto slice detection")
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
    elif not args.no_detect:
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

    config = {
        "source": str(reference_path),
        "width": width,
        "height": height,
        "background": hex_color(bg),
        "background_uniform": background_uniform,
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
                "slices": len(slices),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
