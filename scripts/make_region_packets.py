#!/usr/bin/env python3
"""Build per-region work packets for decomposition sub-agents that only see one crop."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image

INSTRUCTIONS_TEMPLATE = """# Region packet: `{name}`

You are a decomposition sub-agent. Your entire visual world is this packet:

- `crop.png` - the reference crop for region `{name}` (bounds {bounds} in the full reference image).
- `measurements.json` - measured primitive boxes inside this region (may be empty).
- `tokens.json` - already-extracted color/typography tokens for token_refs reference.
- `fragment.template.json` - the blueprint fragment you must fill in.

## Task

Fill in the template and save it as `fragment.json` **in this same directory**. Do not modify
`fragment.template.json` itself. Base everything only on `crop.png` plus `measurements.json`;
you have no access to the rest of the reference image, and you must not ask for it.

## Rules

1. All `bounds` use **absolute reference-image coordinates**, not crop-local coordinates.
   `measurements.json` gives every box in both systems (`full_bounds` is absolute,
   `region_bounds` is crop-local) - use it as your coordinate reference.
2. Every component `id` must start with the `{name}-` prefix, use only `[a-z0-9-]`,
   and set `"region": "{name}"`. Component entries must follow the ui-blueprint schema
   exactly: required `id`, `region`, `type`, `bounds`; optional `content`, `maps_to`,
   `elements` (each element: `id`, `type`, `bounds`, optional `content`, `token_refs`), `notes`.
3. Every interactive component type (`button`, `input`, `select`, `tabs`, `checkbox`,
   `switch`) must have at least one entry in `interactions` with required fields
   `target`, `trigger`, `behavior`, `source` - and `source` must be declared as one of
   `project-convention`, `project-token`, `type-default`.
4. Text-bearing components must transcribe the **actual text visible in the crop** into
   `content`. Never invent placeholder copy.
5. Token proposals go in `token_proposals` as
   `{{"kind": "colors|typography|spacing", "name": "...", "value"/"size_px"/"px": ...,
   "sampled_at": [x, y], "usage": "..."}}`. For `kind: "colors"`, `sampled_at` (absolute
   reference coordinates) is mandatory. Prefer reusing names already present in `tokens.json`
   via element `token_refs` instead of proposing duplicates.
"""


def load_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return fallback


def load_regions(out_dir: Path) -> list[dict[str, Any]]:
    regions_path = out_dir / "regions.json"
    if not regions_path.exists():
        raise SystemExit(f"regions.json not found: {regions_path}")
    data = load_json(regions_path, None)
    if isinstance(data, dict):
        raw = data.get("regions")
    elif isinstance(data, list):
        raw = data
    else:
        raise SystemExit(f"regions.json must be an object with a 'regions' array or a bare array: {regions_path}")
    if not isinstance(raw, list):
        raise SystemExit(f"regions.json has no 'regions' array: {regions_path}")
    regions: list[dict[str, Any]] = []
    for r in raw:
        if not (isinstance(r, dict) and r.get("name")):
            continue
        missing = [key for key in ("x", "y", "width", "height") if key not in r]
        if missing:
            raise SystemExit(f"regions.json region '{r['name']}' is missing fields: {', '.join(missing)}")
        regions.append(r)
    if not regions:
        raise SystemExit(f"regions.json contains no usable regions: {regions_path}")
    return regions


def clamp_bounds(region: dict[str, Any], img_w: int, img_h: int) -> dict[str, int] | None:
    x = max(0, min(int(region["x"]), img_w))
    y = max(0, min(int(region["y"]), img_h))
    w = max(0, min(int(region["width"]), img_w - x))
    h = max(0, min(int(region["height"]), img_h - y))
    if w <= 0 or h <= 0:
        return None
    return {"x": x, "y": y, "width": w, "height": h}


def region_measurements(measured: Any, name: str, bounds: dict[str, int]) -> list[dict[str, Any]] | None:
    regions = measured.get("regions") if isinstance(measured, dict) else None
    if not isinstance(regions, list):
        return None
    for entry in regions:
        if not (isinstance(entry, dict) and entry.get("name") == name):
            continue
        boxes: list[dict[str, Any]] = []
        for primitive in entry.get("primitives") or []:
            if not isinstance(primitive, dict):
                continue
            full = primitive.get("full_bounds") or {
                "x": primitive.get("x", 0),
                "y": primitive.get("y", 0),
                "width": primitive.get("width", 0),
                "height": primitive.get("height", 0),
            }
            boxes.append(
                {
                    "kind": primitive.get("kind", "primitive"),
                    "full_bounds": {key: int(full[key]) for key in ("x", "y", "width", "height")},
                    "region_bounds": {
                        "x": int(full["x"]) - bounds["x"],
                        "y": int(full["y"]) - bounds["y"],
                        "width": int(full["width"]),
                        "height": int(full["height"]),
                    },
                }
            )
        return boxes
    return None


def build_fragment_template(name: str, bounds: dict[str, int]) -> dict[str, Any]:
    return {
        "region": name,
        "region_bounds": bounds,
        "components": [],
        "interactions": [],
        "token_proposals": [],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True, help="Pixel Twin Lab output directory")
    parser.add_argument("--reference", help="Reference image path; defaults to <out-dir>/assets/reference.png")
    parser.add_argument("--regions", help="Comma-separated region names to pack")
    args = parser.parse_args()

    out_dir = Path(args.out_dir).expanduser().resolve()
    reference_path = Path(args.reference).expanduser().resolve() if args.reference else out_dir / "assets/reference.png"
    if not reference_path.exists():
        raise SystemExit(f"Reference image not found: {reference_path}")

    regions = load_regions(out_dir)
    if args.regions:
        wanted = {name.strip() for name in args.regions.split(",") if name.strip()}
        regions = [region for region in regions if str(region.get("name")) in wanted]
        if not regions:
            raise SystemExit(f"No regions matched --regions filter: {args.regions}")

    measured = load_json(out_dir / "measured-primitives.json", None)
    visual_tokens = load_json(out_dir / "visual-tokens.json", None)
    tokens_payload = {}
    if isinstance(visual_tokens, dict):
        tokens_payload = {key: visual_tokens[key] for key in ("colors", "typography") if key in visual_tokens}

    image = Image.open(reference_path).convert("RGB")
    img_w, img_h = image.size
    packets_dir = out_dir / "packets" / "regions"
    packets_dir.mkdir(parents=True, exist_ok=True)

    packed: list[dict[str, Any]] = []
    missing_measurements: list[str] = []
    for region in regions:
        name = str(region["name"])
        bounds = clamp_bounds(region, img_w, img_h)
        if bounds is None:
            print(f"Warning: region '{name}' has zero area after clamping to reference {img_w}x{img_h}; skipping.")
            continue
        packet_dir = packets_dir / name
        packet_dir.mkdir(parents=True, exist_ok=True)

        crop = image.crop((bounds["x"], bounds["y"], bounds["x"] + bounds["width"], bounds["y"] + bounds["height"]))
        crop.save(packet_dir / "crop.png")

        boxes = region_measurements(measured, name, bounds)
        if boxes is None:
            missing_measurements.append(name)
            boxes = []
        (packet_dir / "measurements.json").write_text(
            json.dumps({"region": name, "region_bounds": bounds, "boxes": boxes}, indent=2), encoding="utf-8"
        )
        (packet_dir / "tokens.json").write_text(json.dumps(tokens_payload, indent=2), encoding="utf-8")
        (packet_dir / "fragment.template.json").write_text(
            json.dumps(build_fragment_template(name, bounds), indent=2), encoding="utf-8"
        )
        (packet_dir / "INSTRUCTIONS.md").write_text(
            INSTRUCTIONS_TEMPLATE.format(name=name, bounds=json.dumps(bounds)), encoding="utf-8"
        )
        packed.append({"name": name, "bounds": bounds, "measured_boxes": len(boxes)})

    if not packed:
        raise SystemExit("No region packets were produced.")

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "out_dir": str(out_dir),
        "reference": str(reference_path),
        "packets_dir": str(packets_dir),
        "regions": packed,
        "missing_measurements": missing_measurements,
    }
    (packets_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "regions": [entry["name"] for entry in packed],
                "packets_dir": str(packets_dir),
                "missing_measurements": missing_measurements,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
