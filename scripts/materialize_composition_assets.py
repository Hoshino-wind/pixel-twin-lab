#!/usr/bin/env python3
"""Materialize clean background assets from region composition contracts.

Given an element-manifest region like:

  composition.background.asset_policy = "inpaint-clean"
  composition.background.asset_element_id = "trip-map-bg"
  composition.foreground = [{component_id: "booking-card", bounds: ...}]

this script crops the region from the reference image, removes the declared
foreground overlay rectangles from that crop with a deterministic blur fill,
saves a region-scoped background asset, and records the asset path back into the
manifest. It is intentionally conservative: icons/photos/maps stay assets, while
the foreground cards, stats, lists, labels, and controls remain addressable DOM
nodes verified later by verify_elements.py.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageFilter


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"missing JSON file: {path}") from None
    except json.JSONDecodeError as exc:
        raise SystemExit(f"invalid JSON in {path}: {exc}") from None
    if not isinstance(value, dict):
        raise SystemExit(f"expected object JSON in {path}")
    return value


def safe_slug(value: Any, fallback: str) -> str:
    text = str(value or fallback).strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text or fallback


def as_bounds(value: Any) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    out: dict[str, int] = {}
    for key in ("x", "y", "width", "height"):
        raw = value.get(key)
        if not isinstance(raw, int) or raw < (1 if key in {"width", "height"} else 0):
            return None
        out[key] = raw
    return out


def clamp_rect(bounds: dict[str, int], width: int, height: int) -> tuple[int, int, int, int] | None:
    left = max(0, int(bounds["x"]))
    top = max(0, int(bounds["y"]))
    right = min(width, int(bounds["x"] + bounds["width"]))
    bottom = min(height, int(bounds["y"] + bounds["height"]))
    if right <= left or bottom <= top:
        return None
    return (left, top, right, bottom)


def intersect_rect(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> tuple[int, int, int, int] | None:
    left = max(a[0], b[0])
    top = max(a[1], b[1])
    right = min(a[2], b[2])
    bottom = min(a[3], b[3])
    if right <= left or bottom <= top:
        return None
    return (left, top, right, bottom)


def expand_rect(rect: tuple[int, int, int, int], pad: int, width: int, height: int) -> tuple[int, int, int, int]:
    return (
        max(0, rect[0] - pad),
        max(0, rect[1] - pad),
        min(width, rect[2] + pad),
        min(height, rect[3] + pad),
    )


def iter_elements(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for region in manifest.get("regions") or []:
        if not isinstance(region, dict):
            continue
        for element in region.get("elements") or []:
            if isinstance(element, dict) and element.get("id"):
                by_id.setdefault(str(element["id"]), element)
    return by_id


def ensure_background_element(region: dict[str, Any], element_id: str, bounds: dict[str, int]) -> dict[str, Any]:
    elements = region.setdefault("elements", [])
    if not isinstance(elements, list):
        region["elements"] = []
        elements = region["elements"]
    for element in elements:
        if isinstance(element, dict) and str(element.get("id")) == element_id:
            return element
    element = {
        "id": element_id,
        "type": "image",
        "bounds": bounds,
        "content": "composition background asset",
        "maps_to": f"{region.get('name', 'region')}:background",
    }
    elements.insert(0, element)
    return element


def clean_crop(crop: Image.Image, mask_rects: list[tuple[int, int, int, int]], radius: int) -> Image.Image:
    if not mask_rects:
        return crop
    base = crop.convert("RGBA")
    blur = base.filter(ImageFilter.GaussianBlur(radius=max(1, radius)))
    for rect in mask_rects:
        base.paste(blur.crop(rect), rect)
    return base.convert("RGB")


def relative_to_out(path: Path, out_dir: Path) -> str:
    try:
        return path.relative_to(out_dir).as_posix()
    except ValueError:
        return str(path)


def materialize_region(
    *,
    region: dict[str, Any],
    region_index: int,
    reference: Image.Image,
    out_dir: Path,
    asset_dir: Path,
    element_by_id: dict[str, dict[str, Any]],
    pad: int,
    blur_radius: int,
) -> dict[str, Any]:
    name = safe_slug(region.get("name"), f"region-{region_index}")
    composition = region.get("composition") or {}
    background = composition.get("background") or {}
    foreground = composition.get("foreground") or []
    result: dict[str, Any] = {
        "region": name,
        "status": "skipped",
        "warnings": [],
        "mask_count": 0,
    }

    if not isinstance(background, dict):
        result["warnings"].append("composition.background is not an object")
        return result

    policy = background.get("asset_policy") or "inpaint-clean"
    if policy == "none":
        result["status"] = "dom-background"
        return result

    bg_id = background.get("asset_element_id")
    if not isinstance(bg_id, str) or not bg_id:
        result["warnings"].append("background.asset_element_id is required for asset materialization")
        return result

    region_bounds = as_bounds(region.get("bounds"))
    if region_bounds is None:
        bg_element = element_by_id.get(bg_id) or {}
        region_bounds = as_bounds(bg_element.get("bounds"))
    if region_bounds is None:
        result["warnings"].append("region.bounds or background element bounds are required")
        return result

    crop_rect = clamp_rect(region_bounds, reference.width, reference.height)
    if crop_rect is None:
        result["warnings"].append(f"region bounds outside reference image: {region_bounds}")
        return result

    crop = reference.crop(crop_rect).convert("RGB")
    crop_w, crop_h = crop.size
    mask_rects: list[tuple[int, int, int, int]] = []
    if isinstance(foreground, list):
        for entry in foreground:
            if not isinstance(entry, dict):
                continue
            bounds = as_bounds(entry.get("bounds"))
            if bounds is None and entry.get("component_id"):
                element = element_by_id.get(str(entry["component_id"])) or {}
                bounds = as_bounds(element.get("bounds"))
            if bounds is None:
                result["warnings"].append(f"foreground '{entry.get('component_id')}' has no usable bounds")
                continue
            fg_rect_abs = clamp_rect(bounds, reference.width, reference.height)
            if fg_rect_abs is None:
                result["warnings"].append(f"foreground '{entry.get('component_id')}' bounds outside reference")
                continue
            intersect = intersect_rect(crop_rect, fg_rect_abs)
            if intersect is None:
                result["warnings"].append(f"foreground '{entry.get('component_id')}' does not intersect region")
                continue
            local = (
                intersect[0] - crop_rect[0],
                intersect[1] - crop_rect[1],
                intersect[2] - crop_rect[0],
                intersect[3] - crop_rect[1],
            )
            mask_rects.append(expand_rect(local, pad, crop_w, crop_h))

    if policy == "crop" and mask_rects:
        result["warnings"].append("asset_policy 'crop' keeps foreground pixels; use 'inpaint-clean' for overlay regions")

    image = clean_crop(crop, mask_rects, blur_radius) if policy == "inpaint-clean" else crop
    suffix = "background-clean" if policy == "inpaint-clean" else "background-crop"
    asset_path = asset_dir / f"{name}-{suffix}.png"
    image.save(asset_path)

    rel_asset = relative_to_out(asset_path, out_dir)
    background["generated_asset_path"] = rel_asset
    element = ensure_background_element(region, bg_id, region_bounds)
    element.update(
        {
            "type": "image",
            "bounds": element.get("bounds") or region_bounds,
            "asset_path": rel_asset,
            "asset_role": "composition-background",
            "asset_policy": policy,
            "generated_from_composition": True,
        }
    )

    result.update(
        {
            "status": "written",
            "asset_path": rel_asset,
            "background_element_id": bg_id,
            "policy": policy,
            "bounds": region_bounds,
            "mask_count": len(mask_rects),
        }
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True, help="Pixel Twin Lab output directory")
    parser.add_argument("--manifest-name", default="element-manifest.json")
    parser.add_argument("--reference", default="assets/reference.png", help="Reference image path, absolute or relative to out-dir")
    parser.add_argument("--asset-dir", default="composition-assets", help="Asset output directory, relative to out-dir unless absolute")
    parser.add_argument("--json-name", default="composition-assets.json")
    parser.add_argument("--pad", type=int, default=4, help="Extra px around every foreground mask")
    parser.add_argument("--blur-radius", type=int, default=18, help="Blur radius used to clean foreground rectangles")
    parser.add_argument("--no-update-manifest", action="store_true", help="Write assets and report without updating element-manifest.json")
    args = parser.parse_args()

    out_dir = Path(args.out_dir).expanduser().resolve()
    manifest_path = out_dir / args.manifest_name
    manifest = load_json(manifest_path)
    reference_path = Path(args.reference).expanduser()
    if not reference_path.is_absolute():
        reference_path = out_dir / reference_path
    if not reference_path.exists():
        raise SystemExit(f"reference image not found: {reference_path}")
    asset_dir = Path(args.asset_dir).expanduser()
    if not asset_dir.is_absolute():
        asset_dir = out_dir / asset_dir
    asset_dir.mkdir(parents=True, exist_ok=True)

    reference = Image.open(reference_path).convert("RGB")
    element_by_id = iter_elements(manifest)
    regions = manifest.get("regions") or []
    results: list[dict[str, Any]] = []
    for index, region in enumerate(regions):
        if not isinstance(region, dict) or not isinstance(region.get("composition"), dict):
            continue
        result = materialize_region(
            region=region,
            region_index=index,
            reference=reference,
            out_dir=out_dir,
            asset_dir=asset_dir,
            element_by_id=element_by_id,
            pad=max(0, args.pad),
            blur_radius=max(1, args.blur_radius),
        )
        results.append(result)

    if not args.no_update_manifest:
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "out_dir": str(out_dir),
        "manifest": str(manifest_path),
        "reference": str(reference_path),
        "asset_dir": str(asset_dir),
        "updated_manifest": not args.no_update_manifest,
        "regions": results,
        "summary": {
            "declared": len(results),
            "written": sum(1 for item in results if item.get("status") == "written"),
            "warnings": sum(len(item.get("warnings") or []) for item in results),
        },
    }
    (out_dir / args.json_name).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], indent=2))


if __name__ == "__main__":
    main()
