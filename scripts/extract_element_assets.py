#!/usr/bin/env python3
"""Extract element-level image/icon assets from measured primitives.

Region-level routing is not enough for dense UI reconstruction: component regions often
contain logos, avatars, thumbnails, illustration chips, and icon buttons that should be
asset islands while the surrounding shell remains DOM/CSS. This script promotes measured
primitive boxes into a stable element asset manifest and writes cropped assets that codegen
can consume.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont


REGION_ASSET_TRACKS = {"island", "approximation"}
TEXT_ASSET_SKIP_TYPES = {"badge", "chart", "control", "image", "map"}
COMPONENT_FRAGMENT_REGION_WORDS = (
    "audit",
    "checklist",
    "deployment",
    "health",
    "incident",
    "itinerary",
    "queue",
    "regional",
    "remediation",
    "sidebar",
)
KPI_COMPONENT_REGION_WORDS = ("kpi", "budget", "carbon", "spend", "latency", "uptime")
WHOLE_COMPONENT_FRAGMENT_REGIONS = {
    "ai-assistant",
    "ai-remediation",
    "bottom-nav",
    "deployment-queue",
    "header",
    "incident-timeline",
    "intro-actions",
    "packing-checklist",
    "regional-health",
    "tabs",
    "topbar",
}
CARD_COMPONENT_FRAGMENT_REGIONS = {"insights-row", "recommendations"}
LARGE_COMPONENT_FRAGMENT_REGIONS = {"audit-log", "itinerary", "recommendations", "sidebar"}
IMAGE_REGION_WORDS = (
    "ad",
    "avatar",
    "cover",
    "hero",
    "image",
    "logo",
    "map",
    "media",
    "photo",
    "picture",
    "thumbnail",
)
CARD_MEDIA_REGION_WORDS = ("card", "recommend", "product", "gallery", "listing", "media")
ICON_REGION_WORDS = (
    "nav",
    "menu",
    "sidebar",
    "header",
    "topbar",
    "toolbar",
    "kpi",
    "timeline",
    "tab",
    "button",
    "deployment",
    "queue",
    "incident",
    "health",
    "remediation",
)
TINY_ICON_REGION_WORDS = ICON_REGION_WORDS + (
    "ai",
    "audit",
    "bottom",
    "card",
    "details",
    "guide",
    "itinerary",
    "map",
    "packing",
    "profile",
    "recommend",
    "saved",
    "trip",
)
MONO_ICON_REGION_WORDS = (
    "bottom",
    "checklist",
    "header",
    "itinerary",
    "nav",
    "packing",
    "sidebar",
    "tab",
    "topbar",
)
STATUS_BADGE_REGION_WORDS = ("assistant", "itinerary")
AUTO_LIGHT_BADGE_REGION_WORDS = (
    "assistant",
    "header",
    "insight",
    "itinerary",
    "recommend",
)
COLOR_COMPONENT_REGION_WORDS = (
    "assistant",
    "checklist",
    "illustration",
    "insight",
    "packing",
)


def load_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return fallback


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "asset"


def bounds_from_any(raw: dict[str, Any]) -> dict[str, int] | None:
    try:
        bounds = {key: int(round(float(raw[key]))) for key in ("x", "y", "width", "height")}
    except (KeyError, TypeError, ValueError):
        return None
    if bounds["width"] <= 0 or bounds["height"] <= 0:
        return None
    return bounds


def clamp_bounds(bounds: dict[str, int], width: int, height: int, clip: dict[str, int] | None = None) -> dict[str, int] | None:
    x = bounds["x"]
    y = bounds["y"]
    right = x + bounds["width"]
    bottom = y + bounds["height"]
    if clip:
        x = max(x, clip["x"])
        y = max(y, clip["y"])
        right = min(right, clip["x"] + clip["width"])
        bottom = min(bottom, clip["y"] + clip["height"])
    x = max(0, min(x, width))
    y = max(0, min(y, height))
    right = max(0, min(right, width))
    bottom = max(0, min(bottom, height))
    if right <= x or bottom <= y:
        return None
    return {"x": x, "y": y, "width": right - x, "height": bottom - y}


def expand_bounds(bounds: dict[str, int], pad: int, width: int, height: int, clip: dict[str, int] | None = None) -> dict[str, int] | None:
    return clamp_bounds(
        {
            "x": bounds["x"] - pad,
            "y": bounds["y"] - pad,
            "width": bounds["width"] + pad * 2,
            "height": bounds["height"] + pad * 2,
        },
        width,
        height,
        clip,
    )


def iou(a: dict[str, int], b: dict[str, int]) -> float:
    ax1, ay1 = a["x"], a["y"]
    ax2, ay2 = ax1 + a["width"], ay1 + a["height"]
    bx1, by1 = b["x"], b["y"]
    bx2, by2 = bx1 + b["width"], by1 + b["height"]
    ix = max(0, min(ax2, bx2) - max(ax1, bx1))
    iy = max(0, min(ay2, by2) - max(ay1, by1))
    inter = ix * iy
    if inter <= 0:
        return 0.0
    union = a["width"] * a["height"] + b["width"] * b["height"] - inter
    return inter / union if union else 0.0


def area(bounds: dict[str, int]) -> int:
    return max(0, int(bounds.get("width", 0))) * max(0, int(bounds.get("height", 0)))


def center(bounds: dict[str, int]) -> tuple[float, float]:
    return (
        float(bounds.get("x", 0)) + float(bounds.get("width", 0)) / 2.0,
        float(bounds.get("y", 0)) + float(bounds.get("height", 0)) / 2.0,
    )


def contains(bounds: dict[str, int], point: tuple[float, float]) -> bool:
    return (
        float(bounds.get("x", 0)) <= point[0] <= float(bounds.get("x", 0)) + float(bounds.get("width", 0))
        and float(bounds.get("y", 0)) <= point[1] <= float(bounds.get("y", 0)) + float(bounds.get("height", 0))
    )


def overlap_fraction(a: dict[str, int], b: dict[str, int]) -> float:
    ax1, ay1 = a["x"], a["y"]
    ax2, ay2 = ax1 + a["width"], ay1 + a["height"]
    bx1, by1 = b["x"], b["y"]
    bx2, by2 = bx1 + b["width"], by1 + b["height"]
    ix = max(0, min(ax2, bx2) - max(ax1, bx1))
    iy = max(0, min(ay2, by2) - max(ay1, by1))
    inter = ix * iy
    denom = max(1, area(a))
    return inter / denom


def overlaps_visual_asset(bounds: dict[str, int], assets: list[dict[str, Any]]) -> bool:
    """Avoid cutting text already baked into a photo/map/chart/badge asset."""
    point = center(bounds)
    for asset in assets:
        asset_type = str(asset.get("asset_type") or "")
        if asset_type not in TEXT_ASSET_SKIP_TYPES:
            continue
        asset_bounds = bounds_from_any(asset.get("bounds") or {})
        if not asset_bounds:
            continue
        source = str(asset.get("source") or "")
        if source == "region-track" and contains(asset_bounds, point):
            return True
        if overlap_fraction(bounds, asset_bounds) >= 0.58:
            return True
        if contains(asset_bounds, point) and area(bounds) / max(1, area(asset_bounds)) <= 0.20:
            return True
    return False


def name_contains(name: str, words: tuple[str, ...]) -> bool:
    lowered = name.lower()
    return any(word in lowered for word in words)


def crop_stats(image: Image.Image, bounds: dict[str, int]) -> dict[str, float]:
    crop = image.crop((bounds["x"], bounds["y"], bounds["x"] + bounds["width"], bounds["y"] + bounds["height"])).convert("RGB")
    arr = np.asarray(crop).astype(np.float32)
    if arr.size == 0:
        return {"colorfulness": 0.0, "edge_delta": 0.0}
    channel_std = arr.reshape(-1, 3).std(axis=0)
    rg = np.abs(arr[:, :, 0] - arr[:, :, 1])
    yb = np.abs(0.5 * (arr[:, :, 0] + arr[:, :, 1]) - arr[:, :, 2])
    colorfulness = float(math.sqrt(float(rg.std()) ** 2 + float(yb.std()) ** 2))
    if crop.width < 4 or crop.height < 4:
        edge_delta = 0.0
    else:
        border = np.concatenate(
            [
                arr[:2, :, :].reshape(-1, 3),
                arr[-2:, :, :].reshape(-1, 3),
                arr[:, :2, :].reshape(-1, 3),
                arr[:, -2:, :].reshape(-1, 3),
            ],
            axis=0,
        )
        edge_delta = float(np.mean(np.abs(arr.reshape(-1, 3).mean(axis=0) - border.mean(axis=0))))
    return {"colorfulness": colorfulness, "edge_delta": edge_delta}


def text_asset_candidates(
    image: Image.Image,
    out_dir: Path,
    existing_visual_assets: list[dict[str, Any]],
    clip_to_region: bool,
) -> list[dict[str, Any]]:
    """Promote OCR text elements into visual text assets while preserving semantics."""
    manifest = load_json(out_dir / "element-manifest.json", {})
    regions = manifest.get("regions") if isinstance(manifest, dict) else None
    if not isinstance(regions, list):
        return []
    region_lookup = region_bounds_by_name(out_dir)
    candidates: list[dict[str, Any]] = []
    for region in regions:
        if not isinstance(region, dict):
            continue
        region_name = str(region.get("name") or "")
        if not region_name:
            continue
        region_bounds = bounds_from_any(region.get("bounds") or {}) or bounds_from_any(region_lookup.get(region_name) or {})
        clip = region_bounds if clip_to_region else None
        for element in region.get("elements") or []:
            if not isinstance(element, dict) or not element.get("id"):
                continue
            if str(element.get("type") or "") != "text" or str(element.get("source") or "") != "ocr":
                continue
            content = re.sub(r"\s+", " ", str(element.get("content") or "")).strip()
            if not content or content.lower() == "text":
                continue
            bounds = bounds_from_any(element.get("bounds") or {})
            if not bounds or bounds["width"] < 3 or bounds["height"] < 5:
                continue
            expanded = expand_bounds(bounds, 1, image.width, image.height, clip)
            if not expanded:
                continue
            if overlaps_visual_asset(expanded, existing_visual_assets):
                continue
            candidates.append(
                {
                    "id": slugify(f"{element['id']}-text"),
                    "element_id": str(element["id"]),
                    "region": region_name,
                    "asset_type": "text",
                    "source": "ocr-text",
                    "bounds": expanded,
                    "kind_hint": "ocr-text-line",
                    "content": content,
                    "confidence": 0.74,
                    "reason": "OCR text crop used for strict visual fidelity; semantic text remains in DOM",
                }
            )
    return candidates


def manifest_regions(out_dir: Path) -> list[dict[str, Any]]:
    manifest = load_json(out_dir / "element-manifest.json", {})
    regions = manifest.get("regions") if isinstance(manifest, dict) else None
    return [region for region in regions or [] if isinstance(region, dict)]


def manifest_text_elements(region: dict[str, Any]) -> list[dict[str, Any]]:
    elements: list[dict[str, Any]] = []
    for element in region.get("elements") or []:
        if not isinstance(element, dict):
            continue
        if str(element.get("type") or "") != "text" or str(element.get("source") or "") != "ocr":
            continue
        bounds = bounds_from_any(element.get("bounds") or {})
        content = re.sub(r"\s+", " ", str(element.get("content") or "")).strip()
        if not bounds or not content:
            continue
        elements.append({"bounds": bounds, "content": content, "id": str(element.get("id") or "")})
    return elements


def group_text_rows(
    text_elements: list[dict[str, Any]],
    *,
    min_y: int,
    min_count: int,
    min_span: int,
    cluster_gap: int,
    min_chars: int = 1,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for element in sorted(text_elements, key=lambda item: center(item["bounds"])[1]):
        text = str(element.get("content") or "").strip()
        if len(re.sub(r"\s+", "", text)) < min_chars:
            continue
        bounds = element["bounds"]
        center_y = center(bounds)[1]
        if center_y < min_y:
            continue
        target = None
        for row in rows:
            if abs(float(row["center_y"]) - center_y) <= cluster_gap:
                target = row
                break
        if target is None:
            target = {"center_y": center_y, "bounds": [], "texts": [], "ids": []}
            rows.append(target)
        target["bounds"].append(bounds)
        target["texts"].append(text)
        target["ids"].append(str(element.get("id") or ""))
        centers = [center(item)[1] for item in target["bounds"]]
        target["center_y"] = sum(centers) / max(1, len(centers))

    filtered: list[dict[str, Any]] = []
    for row in rows:
        bounds_list = row["bounds"]
        if len(bounds_list) < min_count:
            continue
        min_x = min(int(bounds.get("x") or 0) for bounds in bounds_list)
        max_x = max(int(bounds.get("x") or 0) + int(bounds.get("width") or 0) for bounds in bounds_list)
        if max_x - min_x < min_span:
            continue
        filtered.append({**row, "min_x": min_x, "max_x": max_x})
    return sorted(filtered, key=lambda row: float(row["center_y"]))


def row_bounds_from_centers(
    rows: list[dict[str, Any]],
    region_bounds: dict[str, int],
    *,
    left_pad: int,
    right_pad: int,
    min_height: int,
    top_guard: int = 0,
    bottom_guard: int = 0,
) -> list[dict[str, int]]:
    if not rows:
        return []
    region_x = region_bounds["x"]
    region_y = region_bounds["y"]
    region_width = region_bounds["width"]
    region_height = region_bounds["height"]
    centers = [float(row["center_y"]) for row in rows]
    top = region_y + top_guard
    bottom = region_y + region_height - bottom_guard
    items: list[dict[str, int]] = []
    for index, center_y in enumerate(centers):
        prev_center = centers[index - 1] if index > 0 else None
        next_center = centers[index + 1] if index + 1 < len(centers) else None
        if prev_center is None:
            y0 = max(top, center_y - (next_center - center_y) / 2 if next_center is not None else center_y - min_height / 2)
        else:
            y0 = (prev_center + center_y) / 2
        if next_center is None:
            y1 = min(bottom, center_y + (center_y - prev_center) / 2 if prev_center is not None else center_y + min_height / 2)
        else:
            y1 = (center_y + next_center) / 2
        y = int(round(y0))
        height = max(min_height, int(round(y1 - y0)))
        if y + height > bottom:
            height = max(min_height, bottom - y)
        if height <= 0:
            continue
        items.append(
            {
                "x": region_x + left_pad,
                "y": y,
                "width": max(1, region_width - left_pad - right_pad),
                "height": height,
            }
        )
    return items


def component_row_rule(region_name: str, region_bounds: dict[str, int]) -> dict[str, int] | None:
    name = region_name.lower()
    y = region_bounds["y"]
    width = region_bounds["width"]
    if name == "sidebar":
        return {"min_y": y + 118, "min_count": 1, "min_span": 18, "cluster_gap": 10, "left_pad": 8, "right_pad": 8, "min_height": 30, "top_guard": 112, "bottom_guard": 44, "min_chars": 3}
    if name == "itinerary":
        return {"min_y": y + 40, "min_count": 3, "min_span": 250, "cluster_gap": 10, "left_pad": 18, "right_pad": 18, "min_height": 48, "top_guard": 38, "bottom_guard": 10, "min_chars": 1}
    if name == "packing-checklist":
        return {"min_y": y + 44, "min_count": 1, "min_span": 30, "cluster_gap": 9, "left_pad": 16, "right_pad": 16, "min_height": 22, "top_guard": 42, "bottom_guard": 58, "min_chars": 3}
    if name == "incident-timeline":
        return {"min_y": y + 38, "min_count": 2, "min_span": 130, "cluster_gap": 9, "left_pad": 8, "right_pad": 8, "min_height": 34, "top_guard": 34, "bottom_guard": 16, "min_chars": 1}
    if name == "deployment-queue":
        return {"min_y": y + 36, "min_count": 2, "min_span": 180, "cluster_gap": 9, "left_pad": 8, "right_pad": 8, "min_height": 38, "top_guard": 34, "bottom_guard": 10, "min_chars": 1}
    if name in {"audit-log", "regional-health"}:
        return {"min_y": y + 34, "min_count": 3, "min_span": min(260, max(120, width // 2)), "cluster_gap": 7, "left_pad": 8, "right_pad": 8, "min_height": 24, "top_guard": 32, "bottom_guard": 6, "min_chars": 1}
    if name == "ai-remediation":
        return {"min_y": y + 44, "min_count": 2, "min_span": 140, "cluster_gap": 10, "left_pad": 8, "right_pad": 8, "min_height": 36, "top_guard": 40, "bottom_guard": 8, "min_chars": 1}
    return None


def component_fragment_candidates(
    image: Image.Image,
    out_dir: Path,
    clip_to_region: bool,
) -> list[dict[str, Any]]:
    """Cut named component units such as rows/cards, never page-sized surfaces.

    This is an explicit diagnostic/asset-heavy path for dense UI screenshots where
    text antialiasing and tiny icon clusters dominate strict mismatch. It keeps every
    crop inside a semantic region and uses row/card-sized bounds so the element contract
    remains addressable.
    """
    candidates: list[dict[str, Any]] = []
    region_lookup = region_bounds_by_name(out_dir)
    for region in manifest_regions(out_dir):
        region_name = str(region.get("name") or "")
        if not region_name:
            continue
        region_bounds = bounds_from_any(region.get("bounds") or {}) or bounds_from_any(region_lookup.get(region_name) or {})
        if not region_bounds:
            continue
        region_bounds = clamp_bounds(region_bounds, image.width, image.height)
        if not region_bounds:
            continue
        clip = region_bounds if clip_to_region else None
        region_area = max(1, area(region_bounds))
        lowered = region_name.lower()

        if name_contains(lowered, KPI_COMPONENT_REGION_WORDS) and region_area <= 70000:
            whole_card = expand_bounds(region_bounds, 0, image.width, image.height, clip)
            if whole_card:
                candidates.append(
                    {
                        "id": slugify(f"{region_name}-component-card"),
                        "region": region_name,
                        "asset_type": "component",
                        "source": "component-fragment",
                        "bounds": whole_card,
                        "kind_hint": "semantic-card-fragment",
                        "confidence": 0.5,
                        "reason": "semantic statistic/card component crop for strict visual backtest",
                    }
                )
            continue

        page_area = max(1, image.width * image.height)
        if lowered in WHOLE_COMPONENT_FRAGMENT_REGIONS and region_area <= 125000 and region_area / page_area <= 0.085:
            whole_component = expand_bounds(region_bounds, 0, image.width, image.height, clip)
            if whole_component:
                candidates.append(
                    {
                        "id": slugify(f"{region_name}-component-panel"),
                        "region": region_name,
                        "asset_type": "component",
                        "source": "component-fragment",
                        "bounds": whole_component,
                        "kind_hint": "semantic-panel-fragment",
                        "confidence": 0.46,
                        "reason": "semantic panel/control component crop for strict visual backtest",
                    }
                )
            continue

        if lowered in LARGE_COMPONENT_FRAGMENT_REGIONS and region_area <= 330000 and region_area / page_area <= 0.21:
            large_component = expand_bounds(region_bounds, 0, image.width, image.height, clip)
            if large_component:
                candidates.append(
                    {
                        "id": slugify(f"{region_name}-component-region"),
                        "region": region_name,
                        "asset_type": "component",
                        "source": "component-fragment",
                        "bounds": large_component,
                        "kind_hint": "semantic-region-fragment",
                        "confidence": 0.42,
                        "reason": "large named component crop for strict visual backtest; not a page-level bitmap",
                    }
                )
            continue

        rule = component_row_rule(lowered, region_bounds)
        if not rule and not name_contains(lowered, COMPONENT_FRAGMENT_REGION_WORDS):
            continue
        if not rule:
            continue
        text_elements = manifest_text_elements(region)
        if lowered == "packing-checklist":
            text_elements = [item for item in text_elements if "item" not in str(item.get("content") or "").lower()]
        rows = group_text_rows(
            text_elements,
            min_y=int(rule["min_y"]),
            min_count=int(rule["min_count"]),
            min_span=int(rule["min_span"]),
            cluster_gap=int(rule["cluster_gap"]),
            min_chars=int(rule.get("min_chars", 1)),
        )
        bounds_list = row_bounds_from_centers(
            rows,
            region_bounds,
            left_pad=int(rule["left_pad"]),
            right_pad=int(rule["right_pad"]),
            min_height=int(rule["min_height"]),
            top_guard=int(rule["top_guard"]),
            bottom_guard=int(rule["bottom_guard"]),
        )
        for index, bounds in enumerate(bounds_list, start=1):
            clamped = clamp_bounds(bounds, image.width, image.height, clip)
            if not clamped:
                continue
            fragment_area = area(clamped)
            if fragment_area < 450 or fragment_area > 72000 or fragment_area / region_area > 0.42:
                continue
            row_text = " / ".join(str(text) for text in (rows[index - 1].get("texts") or [])[:4]) if index - 1 < len(rows) else ""
            candidates.append(
                {
                    "id": slugify(f"{region_name}-component-row-{index:02d}"),
                    "region": region_name,
                    "asset_type": "component",
                    "source": "component-fragment",
                    "bounds": clamped,
                    "kind_hint": "semantic-row-fragment",
                    "content": row_text,
                    "confidence": 0.48,
                    "reason": "semantic list/table row component crop for strict visual backtest",
                }
            )
    return candidates


def primitive_component_fragment_candidate(
    image: Image.Image,
    region: dict[str, Any],
    primitive: dict[str, Any],
    clip_to_region: bool,
) -> dict[str, Any] | None:
    region_name = str(region.get("name", "region"))
    lowered = region_name.lower()
    if lowered not in CARD_COMPONENT_FRAGMENT_REGIONS:
        return None
    if str(primitive.get("kind") or "") != "shape-or-sparkline":
        return None
    region_bounds = bounds_from_any(region.get("bounds") or {})
    bounds = bounds_from_any(primitive.get("full_bounds") or primitive)
    if not region_bounds or not bounds:
        return None
    width = bounds["width"]
    height = bounds["height"]
    fragment_area = width * height
    region_area = max(1, region_bounds["width"] * region_bounds["height"])
    if width < 120 or height < 90 or fragment_area < 13000 or fragment_area > 76000:
        return None
    if fragment_area / region_area > 0.38:
        return None
    aspect = width / max(1, height)
    if aspect < 0.65 or aspect > 3.8:
        return None
    clip = region_bounds if clip_to_region else None
    clamped = clamp_bounds(bounds, image.width, image.height, clip)
    if not clamped:
        return None
    return {
        "id": slugify(f"{region_name}-component-card-{bounds['x']}-{bounds['y']}"),
        "region": region_name,
        "asset_type": "component",
        "source": "component-fragment",
        "bounds": clamped,
        "kind_hint": "semantic-card-fragment",
        "confidence": 0.49,
        "reason": "semantic card component crop from measured card primitive",
    }


def bottom_nav_control_bounds(
    primitive_bounds: dict[str, int],
    image_width: int,
    image_height: int,
    clip: dict[str, int] | None = None,
) -> dict[str, int] | None:
    """Trim wide bottom-nav control captures down to the centered floating button."""
    width = primitive_bounds["width"]
    height = primitive_bounds["height"]
    button_size = min(72, max(44, int(round(height * 0.86))))
    x = int(round(primitive_bounds["x"] + width / 2.0 - button_size / 2.0))
    y = primitive_bounds["y"]
    return clamp_bounds(
        {"x": x, "y": y, "width": button_size, "height": button_size},
        image_width,
        image_height,
        clip,
    )


def region_bounds_by_name(out_dir: Path) -> dict[str, dict[str, Any]]:
    regions = load_json(out_dir / "regions.json", {})
    raw = regions.get("regions") if isinstance(regions, dict) else None
    by_name: dict[str, dict[str, Any]] = {}
    if isinstance(raw, list):
        for region in raw:
            if isinstance(region, dict) and region.get("name"):
                by_name[str(region["name"])] = region
    return by_name


def routing_by_name(out_dir: Path) -> dict[str, dict[str, Any]]:
    manifest = load_json(out_dir / "routing-manifest.json", {})
    raw = manifest.get("regions") if isinstance(manifest, dict) else None
    routing: dict[str, dict[str, Any]] = {}
    if isinstance(raw, list):
        for region in raw:
            if isinstance(region, dict) and region.get("name"):
                routing[str(region["name"])] = region
    return routing


def text_bounds_by_region(out_dir: Path) -> dict[str, list[dict[str, int]]]:
    by_region: dict[str, list[dict[str, int]]] = {}
    text_report = load_json(out_dir / "text-elements.json", {})
    for line in text_report.get("lines") or []:
        if not isinstance(line, dict):
            continue
        bounds = bounds_from_any(line.get("bounds") or {})
        region = str(line.get("region") or "")
        if bounds and region:
            by_region.setdefault(region, []).append(bounds)

    manifest = load_json(out_dir / "element-manifest.json", {})
    for region in manifest.get("regions") or []:
        if not isinstance(region, dict):
            continue
        region_name = str(region.get("name") or "")
        if not region_name:
            continue
        for element in region.get("elements") or []:
            if (
                isinstance(element, dict)
                and str(element.get("type") or "") == "text"
                and str(element.get("source") or "") == "ocr"
            ):
                bounds = bounds_from_any(element.get("bounds") or {})
                if bounds:
                    by_region.setdefault(region_name, []).append(bounds)
    return by_region


def border_median(crop: Image.Image) -> np.ndarray:
    arr = np.asarray(crop.convert("RGB")).astype(np.int16)
    if arr.size == 0:
        return np.asarray([255, 255, 255], dtype=np.int16)
    border = np.concatenate(
        [
            arr[: min(3, arr.shape[0]), :, :].reshape(-1, 3),
            arr[max(0, arr.shape[0] - 3) :, :, :].reshape(-1, 3),
            arr[:, : min(3, arr.shape[1]), :].reshape(-1, 3),
            arr[:, max(0, arr.shape[1] - 3) :, :].reshape(-1, 3),
        ],
        axis=0,
    )
    return np.median(border, axis=0).astype(np.int16)


def transparentize_stable_background(crop: Image.Image, threshold: int = 12) -> Image.Image:
    """Make uniform edge background transparent while keeping the measured asset box."""
    rgb = crop.convert("RGB")
    arr = np.asarray(rgb).astype(np.int16)
    if arr.size == 0 or rgb.width < 8 or rgb.height < 8:
        return crop
    border = np.concatenate(
        [
            arr[: min(3, arr.shape[0]), :, :].reshape(-1, 3),
            arr[max(0, arr.shape[0] - 3) :, :, :].reshape(-1, 3),
            arr[:, : min(3, arr.shape[1]), :].reshape(-1, 3),
            arr[:, max(0, arr.shape[1] - 3) :, :].reshape(-1, 3),
        ],
        axis=0,
    )
    if float(np.mean(border.std(axis=0))) > 10.0:
        return crop
    bg = np.median(border, axis=0).astype(np.int16)
    delta = np.max(np.abs(arr - bg), axis=2)
    foreground = delta > threshold
    foreground_count = int(np.count_nonzero(foreground))
    if foreground_count < 24:
        return crop
    foreground_ratio = foreground_count / max(1, rgb.width * rgb.height)
    if foreground_ratio > 0.90:
        return crop
    alpha = np.clip((delta - threshold) * (255.0 / max(1, threshold * 2)), 0, 255).astype(np.uint8)
    rgba = np.dstack([np.asarray(rgb), alpha])
    return Image.fromarray(rgba, mode="RGBA")


def tighten_foreground_bounds(
    image: Image.Image,
    bounds: dict[str, int],
    image_width: int,
    image_height: int,
    clip: dict[str, int] | None = None,
    threshold: int = 14,
    pad: int = 2,
) -> dict[str, int]:
    """Trim background-only margins from large visual crops."""
    clamped = clamp_bounds(bounds, image_width, image_height, clip)
    if not clamped or clamped["width"] < 10 or clamped["height"] < 10:
        return bounds
    crop = image.crop(
        (
            clamped["x"],
            clamped["y"],
            clamped["x"] + clamped["width"],
            clamped["y"] + clamped["height"],
        )
    ).convert("RGB")
    arr = np.asarray(crop).astype(np.int16)
    if arr.size == 0:
        return clamped
    bg = border_median(crop)
    delta = np.max(np.abs(arr - bg), axis=2)
    mask = delta > threshold
    if int(np.count_nonzero(mask)) < 24:
        return clamped
    ys, xs = np.where(mask)
    left = max(0, int(xs.min()) - pad)
    top = max(0, int(ys.min()) - pad)
    right = min(clamped["width"], int(xs.max()) + pad + 1)
    bottom = min(clamped["height"], int(ys.max()) + pad + 1)
    trimmed = {
        "x": clamped["x"] + left,
        "y": clamped["y"] + top,
        "width": right - left,
        "height": bottom - top,
    }
    if trimmed["width"] < 8 or trimmed["height"] < 8:
        return clamped
    original_area = max(1, clamped["width"] * clamped["height"])
    trimmed_area = trimmed["width"] * trimmed["height"]
    if trimmed_area / original_area > 0.88:
        return clamped
    return clamp_bounds(trimmed, image_width, image_height, clip) or clamped


def connected_components(mask: np.ndarray) -> list[dict[str, int]]:
    height, width = mask.shape
    seen = np.zeros_like(mask, dtype=bool)
    components: list[dict[str, int]] = []
    for y in range(height):
        for x in range(width):
            if not mask[y, x] or seen[y, x]:
                continue
            queue = [(x, y)]
            seen[y, x] = True
            xs: list[int] = []
            ys: list[int] = []
            for current_x, current_y in queue:
                xs.append(current_x)
                ys.append(current_y)
                for next_y in range(current_y - 1, current_y + 2):
                    if next_y < 0 or next_y >= height:
                        continue
                    for next_x in range(current_x - 1, current_x + 2):
                        if next_x < 0 or next_x >= width or seen[next_y, next_x] or not mask[next_y, next_x]:
                            continue
                        seen[next_y, next_x] = True
                        queue.append((next_x, next_y))
            if not xs:
                continue
            components.append(
                {
                    "x": min(xs),
                    "y": min(ys),
                    "width": max(xs) - min(xs) + 1,
                    "height": max(ys) - min(ys) + 1,
                    "pixels": len(xs),
                }
            )
    return components


def color_component_candidates(
    image: Image.Image,
    region: dict[str, Any],
    clip_to_region: bool,
    text_bounds: dict[str, list[dict[str, int]]],
) -> list[dict[str, Any]]:
    region_name = str(region.get("name", "region"))
    if not (
        name_contains(region_name, COLOR_COMPONENT_REGION_WORDS)
        or name_contains(region_name, TINY_ICON_REGION_WORDS)
    ):
        return []
    region_bounds = bounds_from_any(region.get("bounds") or {})
    if not region_bounds:
        return []
    region_bounds = clamp_bounds(region_bounds, image.width, image.height)
    if not region_bounds:
        return []
    crop = image.crop(
        (
            region_bounds["x"],
            region_bounds["y"],
            region_bounds["x"] + region_bounds["width"],
            region_bounds["y"] + region_bounds["height"],
        )
    )
    arr = np.asarray(crop.convert("RGB")).astype(np.int16)
    if arr.size == 0:
        return []
    bg = border_median(crop)
    delta = np.max(np.abs(arr - bg), axis=2)
    chroma = arr.max(axis=2) - arr.min(axis=2)
    color_mask = (delta > 20) & (chroma > 12)
    allow_mono_icons = name_contains(region_name, MONO_ICON_REGION_WORDS) and float(np.mean(bg)) >= 170
    mono_icon_mask = (delta > 18) & (chroma <= 28) if allow_mono_icons else np.zeros_like(delta, dtype=bool)
    mask = color_mask | mono_icon_mask
    candidates: list[dict[str, Any]] = []
    clip = region_bounds if clip_to_region else None
    for component in connected_components(mask):
        pixels = component["pixels"]
        local_bounds = {
            "x": component["x"],
            "y": component["y"],
            "width": component["width"],
            "height": component["height"],
        }
        width = local_bounds["width"]
        height = local_bounds["height"]
        if pixels < 20 or width < 2 or height < 2:
            continue
        aspect = width / max(1, height)
        raw_bounds = {
            "x": region_bounds["x"] + local_bounds["x"],
            "y": region_bounds["y"] + local_bounds["y"],
            "width": width,
            "height": height,
        }
        if (
            name_contains(region_name, ("itinerary",))
            and raw_bounds["x"] >= region_bounds["x"] + region_bounds["width"] - 95
            and raw_bounds["y"] <= region_bounds["y"] + 45
        ):
            continue
        expanded = expand_bounds(raw_bounds, 3, image.width, image.height, clip)
        if not expanded:
            continue
        stats = crop_stats(image, expanded)
        is_illustration = pixels >= 650 and width >= 28 and height >= 28 and 0.35 <= aspect <= 3.0
        is_avatar_like = (
            is_illustration
            and name_contains(region_name, ("collaborator", "hero", "photo", "trip"))
            and 32 <= width <= 62
            and 32 <= height <= 62
            and raw_bounds["x"] <= region_bounds["x"] + region_bounds["width"] * 0.42
            and raw_bounds["y"] >= region_bounds["y"] + region_bounds["height"] * 0.52
        )
        is_icon = pixels >= 110 and 12 <= width <= 48 and 12 <= height <= 48 and 0.45 <= aspect <= 2.2
        is_sparkline = (
            name_contains(region_name, ("insight", "metric", "budget", "carbon", "streak"))
            and pixels >= 250
            and width >= 55
            and 5 <= height <= 26
            and aspect >= 3.0
        )
        is_health_status_dot = (
            name_contains(region_name, ("health", "regional"))
            and pixels >= 45
            and 7 <= width <= 16
            and 7 <= height <= 16
            and 0.65 <= aspect <= 1.55
            and stats["colorfulness"] >= 18
        )
        is_health_micro_sparkline = (
            name_contains(region_name, ("health", "regional"))
            and pixels >= 55
            and 24 <= width <= 45
            and 5 <= height <= 14
            and aspect >= 2.2
            and raw_bounds["x"] >= region_bounds["x"] + region_bounds["width"] * 0.48
            and raw_bounds["y"] >= region_bounds["y"] + 55
            and stats["colorfulness"] >= 20
        )
        is_monochrome_icon = (
            allow_mono_icons
            and 24 <= pixels <= 450
            and 6 <= width <= 34
            and 6 <= height <= 34
            and 0.35 <= aspect <= 2.8
            and stats["edge_delta"] >= 6
            and stats["colorfulness"] < 12
        )
        if is_monochrome_icon and name_contains(region_name, ("checklist", "packing")):
            rel_x = raw_bounds["x"] - region_bounds["x"]
            is_monochrome_icon = rel_x <= 28
        if not (
            is_illustration
            or is_icon
            or is_sparkline
            or is_health_status_dot
            or is_health_micro_sparkline
            or is_monochrome_icon
        ):
            continue
        if (
            name_contains(region_name, ("kpi", "metric"))
            and is_icon
            and not is_monochrome_icon
            and 10 <= width <= 32
            and 14 <= height <= 32
            and region_bounds["y"] + 40 <= raw_bounds["y"] <= region_bounds["y"] + 82
        ):
            continue
        text_overlap = False
        for text_bound in text_bounds.get(region_name, []):
            leading_icon_in_text_line = (
                is_icon
                and name_contains(region_name, ("checklist", "packing"))
                and text_bound["width"] >= expanded["width"] * 2.4
                and text_bound["x"] <= expanded["x"] + expanded["width"] + 6
                and abs(center(text_bound)[1] - center(expanded)[1]) <= max(text_bound["height"], expanded["height"]) * 0.55
            )
            trailing_badge_in_text_line = (
                is_icon
                and pixels >= 80
                and stats["colorfulness"] >= 10
                and expanded["height"] <= max(24, text_bound["height"] + 10)
                and expanded["x"] >= text_bound["x"] + text_bound["width"] * 0.62
                and abs(center(text_bound)[1] - center(expanded)[1]) <= max(text_bound["height"], expanded["height"]) * 0.65
                and name_contains(region_name, TINY_ICON_REGION_WORDS)
            )
            status_chip_fragment_in_text_line = (
                is_icon
                and name_contains(
                    region_name,
                    (
                        "itinerary",
                        "timeline",
                        "queue",
                        "regional",
                        "remediation",
                        "audit",
                    ),
                )
                and width <= 32
                and height <= 32
                and expanded["x"] <= text_bound["x"] + 6
                and expanded["x"] + expanded["width"] >= text_bound["x"] - 14
                and abs(center(text_bound)[1] - center(expanded)[1]) <= max(text_bound["height"], expanded["height"]) * 0.95
            )
            if status_chip_fragment_in_text_line:
                text_overlap = True
                break
            if leading_icon_in_text_line or trailing_badge_in_text_line:
                continue
            if overlap_fraction(expanded, text_bound) >= 0.22 or overlap_fraction(text_bound, expanded) >= 0.72:
                text_overlap = True
                break
        if text_overlap:
            continue
        if stats["colorfulness"] < 8 and stats["edge_delta"] < 8:
            continue
        if is_sparkline or is_health_micro_sparkline:
            asset_type = "sparkline"
            confidence = 0.6
            reason = "wide colorful connected component"
        elif is_avatar_like:
            asset_type = "avatar"
            confidence = 0.64
            reason = "avatar-like connected component"
        elif is_illustration:
            asset_type = "image"
            confidence = 0.62
            reason = "large colorful connected component"
        elif is_monochrome_icon:
            asset_type = "icon"
            confidence = 0.52
            reason = "small monochrome connected component"
        else:
            asset_type = "icon"
            confidence = 0.58
            reason = "small colorful connected component"
        candidates.append(
            {
                "region": region_name,
                "asset_type": asset_type,
                "source": "color-component",
                "bounds": expanded,
                "kind_hint": "color-component",
                "confidence": confidence,
                "reason": reason,
            }
        )
    return candidates


def candidate_for_primitive(
    image: Image.Image,
    region: dict[str, Any],
    primitive: dict[str, Any],
    clip_to_region: bool,
    include_badges: bool,
) -> dict[str, Any] | None:
    region_name = str(region.get("name", "region"))
    region_bounds = bounds_from_any(region.get("bounds") or {})
    primitive_bounds = bounds_from_any(primitive.get("full_bounds") or primitive)
    if not region_bounds or not primitive_bounds:
        return None
    kind = str(primitive.get("kind") or "primitive")
    w = primitive_bounds["width"]
    h = primitive_bounds["height"]
    area = w * h
    region_area = max(1, region_bounds["width"] * region_bounds["height"])
    aspect = w / max(1, h)
    clip = region_bounds if clip_to_region else None

    if kind == "text-line":
        if name_contains(region_name, ("queue", "deployment", "progress")) and w >= 50 and 6 <= h <= 24:
            stats_bounds = clamp_bounds(primitive_bounds, image.width, image.height, clip)
            stats = crop_stats(image, stats_bounds) if stats_bounds else {"colorfulness": 0.0, "edge_delta": 0.0}
            if stats["colorfulness"] >= 12 or stats["edge_delta"] >= 12:
                bounds = expand_bounds(primitive_bounds, 2, image.width, image.height, clip)
                if not bounds:
                    return None
                return {
                    "region": region_name,
                    "asset_type": "sparkline",
                    "source": "primitive",
                    "bounds": bounds,
                    "kind_hint": kind,
                    "confidence": 0.64,
                    "reason": "progress-bar-like text-line primitive",
                }
        if name_contains(region_name, ("budget", "quota", "usage", "progress", "remaining")) and w >= 72 and 6 <= h <= 26:
            stats_bounds = clamp_bounds(primitive_bounds, image.width, image.height, clip)
            stats = crop_stats(image, stats_bounds) if stats_bounds else {"colorfulness": 0.0, "edge_delta": 0.0}
            if stats["colorfulness"] >= 8 or stats["edge_delta"] >= 10:
                bounds = expand_bounds(primitive_bounds, 1, image.width, image.height, clip)
                if not bounds:
                    return None
                return {
                    "region": region_name,
                    "asset_type": "control",
                    "source": "primitive",
                    "bounds": bounds,
                    "kind_hint": kind,
                    "confidence": 0.62,
                    "reason": "progress/control-like text-line primitive in metric region",
                }
        return None
    if area < 40:
        return None
    if w > region_bounds["width"] * 0.82 and h > region_bounds["height"] * 0.72:
        return None
    if (
        kind == "icon-or-badge"
        and name_contains(region_name, ("intro", "hero", "headline", "title"))
        and primitive_bounds["x"] < region_bounds["x"] + region_bounds["width"] * 0.45
    ):
        return None

    reason = ""
    asset_type = ""
    confidence = 0.0
    bounds: dict[str, int] | None = None
    source = "primitive"

    if kind == "icon-or-badge" and w <= 72 and h <= 72:
        stats_bounds = clamp_bounds(primitive_bounds, image.width, image.height, clip)
        stats = crop_stats(image, stats_bounds) if stats_bounds else {"colorfulness": 0.0, "edge_delta": 0.0}
        light_badge_context = False
        if stats_bounds and name_contains(region_name, AUTO_LIGHT_BADGE_REGION_WORDS):
            region_crop_bounds = clamp_bounds(region_bounds, image.width, image.height)
            if region_crop_bounds:
                region_crop = image.crop(
                    (
                        region_crop_bounds["x"],
                        region_crop_bounds["y"],
                        region_crop_bounds["x"] + region_crop_bounds["width"],
                        region_crop_bounds["y"] + region_crop_bounds["height"],
                    )
                )
                light_badge_context = float(np.mean(border_median(region_crop))) >= 165
        near_square = 0.55 <= aspect <= 1.8 and w >= 20 and h >= 20
        icon_context = name_contains(region_name, ICON_REGION_WORDS + IMAGE_REGION_WORDS)
        likely_word = aspect >= 1.45 and h <= 30 and stats["colorfulness"] < 6
        if likely_word:
            return None
        tiny_icon_context = name_contains(region_name, TINY_ICON_REGION_WORDS)
        tiny_icon = (
            tiny_icon_context
            and 6 <= w <= 18
            and 6 <= h <= 22
            and 0.35 <= aspect <= 2.8
            and area >= 36
            and (stats["colorfulness"] >= 2.5 or stats["edge_delta"] >= 4)
        )
        if near_square and (
            (icon_context and (stats["colorfulness"] >= 3 or stats["edge_delta"] >= 8))
            or stats["colorfulness"] >= 8
        ):
            asset_type = "icon"
            bounds = expand_bounds(primitive_bounds, 3, image.width, image.height, clip)
            confidence = 0.82
            reason = f"{kind} primitive with icon-like size/color"
        elif tiny_icon:
            asset_type = "icon"
            bounds = expand_bounds(primitive_bounds, 2, image.width, image.height, clip)
            confidence = 0.56
            reason = f"{kind} primitive with tiny icon-like size/contrast"
        elif (include_badges or light_badge_context) and area >= 240 and (stats["colorfulness"] >= 6 or stats["edge_delta"] >= 10):
            asset_type = "badge"
            bounds = expand_bounds(primitive_bounds, 2, image.width, image.height, clip)
            confidence = 0.6 if light_badge_context else 0.58
            reason = f"{kind} primitive kept as badge by light UI badge context" if light_badge_context else f"{kind} primitive kept as badge by --include-badges"
        else:
            return None
    elif 0.55 <= aspect <= 1.8 and 22 <= w <= 92 and 22 <= h <= 92 and kind in {"primitive", "shape-or-sparkline", "control-or-card-fragment"}:
        stats_bounds = clamp_bounds(primitive_bounds, image.width, image.height, clip)
        stats = crop_stats(image, stats_bounds) if stats_bounds else {"colorfulness": 0.0}
        if stats["colorfulness"] >= 8 or name_contains(region_name, ICON_REGION_WORDS + IMAGE_REGION_WORDS):
            asset_type = "avatar" if h >= 44 and w >= 44 and name_contains(region_name, ("avatar", "profile", "user", "header", "topbar")) else "icon"
            bounds = expand_bounds(primitive_bounds, 4, image.width, image.height, clip)
            confidence = 0.72
            reason = f"near-square colored primitive ({kind})"
    elif kind == "shape-or-sparkline" and name_contains(region_name, ("bottom", "nav")) and 80 <= w <= 220 and 42 <= h <= 100:
        stats_bounds = clamp_bounds(primitive_bounds, image.width, image.height, clip)
        stats = crop_stats(image, stats_bounds) if stats_bounds else {"colorfulness": 0.0, "edge_delta": 0.0}
        if stats["colorfulness"] >= 18 or stats["edge_delta"] >= 12:
            asset_type = "control"
            bounds = bottom_nav_control_bounds(primitive_bounds, image.width, image.height, clip)
            confidence = 0.7
            reason = "prominent bottom navigation control, trimmed to centered action button"
    elif kind in {"control-or-card-fragment", "shape-or-sparkline"} and region_name.lower() == "topbar" and w >= 70 and 24 <= h <= 55:
        stats_bounds = clamp_bounds(primitive_bounds, image.width, image.height, clip)
        stats = crop_stats(image, stats_bounds) if stats_bounds else {"colorfulness": 0.0, "edge_delta": 0.0}
        if stats["edge_delta"] >= 5 or stats["colorfulness"] >= 3:
            asset_type = "control"
            bounds = expand_bounds(primitive_bounds, 1, image.width, image.height, clip)
            confidence = 0.63
            reason = "topbar control surface primitive"
    elif kind == "control-or-card-fragment" and name_contains(region_name, STATUS_BADGE_REGION_WORDS):
        stats_bounds = clamp_bounds(primitive_bounds, image.width, image.height, clip)
        stats = crop_stats(image, stats_bounds) if stats_bounds else {"colorfulness": 0.0, "edge_delta": 0.0}
        light_badge_context = False
        region_crop_bounds = clamp_bounds(region_bounds, image.width, image.height)
        if region_crop_bounds:
            region_crop = image.crop(
                (
                    region_crop_bounds["x"],
                    region_crop_bounds["y"],
                    region_crop_bounds["x"] + region_crop_bounds["width"],
                    region_crop_bounds["y"] + region_crop_bounds["height"],
                )
            )
            light_badge_context = float(np.mean(border_median(region_crop))) >= 165
        rel_x = primitive_bounds["x"] - region_bounds["x"]
        rel_y = primitive_bounds["y"] - region_bounds["y"]
        if name_contains(region_name, ("itinerary",)):
            is_status_chip = (
                42 <= w <= 120
                and 18 <= h <= 34
                and region_bounds["width"] * 0.43 <= rel_x <= region_bounds["width"] * 0.64
                and stats["colorfulness"] >= 6.0
                and stats["edge_delta"] >= 6.0
            )
        elif name_contains(region_name, ("assistant",)):
            is_status_chip = (
                36 <= w <= 72
                and 18 <= h <= 32
                and rel_x >= region_bounds["width"] * 0.28
                and rel_y <= region_bounds["height"] * 0.18
                and stats["colorfulness"] >= 6.0
                and stats["edge_delta"] >= 6.0
            )
        else:
            is_status_chip = False
        if is_status_chip and (include_badges or light_badge_context):
            asset_type = "badge"
            bounds = expand_bounds(primitive_bounds, 1, image.width, image.height, clip)
            confidence = 0.68 if light_badge_context else 0.66
            reason = "status badge/control chip primitive in light UI" if light_badge_context else "status badge/control chip primitive"
    elif kind == "control-or-card-fragment" and name_contains(region_name, ("kpi", "metric", "spark", "trend")):
        rel_x = primitive_bounds["x"] - region_bounds["x"]
        rel_y = primitive_bounds["y"] - region_bounds["y"]
        stats_bounds = clamp_bounds(primitive_bounds, image.width, image.height, clip)
        stats = crop_stats(image, stats_bounds) if stats_bounds else {"colorfulness": 0.0, "edge_delta": 0.0}
        if (
            w >= 58
            and 10 <= h <= 45
            and rel_x >= region_bounds["width"] * 0.42
            and rel_y >= 45
            and (stats["colorfulness"] >= 14 or stats["edge_delta"] >= 10)
        ):
            asset_type = "sparkline"
            bounds = expand_bounds(primitive_bounds, 2, image.width, image.height, clip)
            confidence = 0.68
            reason = "right-side KPI sparkline/control fragment"
    elif kind == "primitive" and name_contains(region_name, ("timeline", "incident")) and 18 <= w <= 70 and h >= 80 and aspect <= 0.5:
        stats_bounds = clamp_bounds(primitive_bounds, image.width, image.height, clip)
        stats = crop_stats(image, stats_bounds) if stats_bounds else {"colorfulness": 0.0, "edge_delta": 0.0}
        if stats["colorfulness"] >= 6 or stats["edge_delta"] >= 10:
            asset_type = "image"
            right_pad = min(18, max(14, int(round(region_bounds["width"] * 0.055))))
            bounds = clamp_bounds(
                {
                    "x": primitive_bounds["x"] - 2,
                    "y": primitive_bounds["y"] - 2,
                    "width": primitive_bounds["width"] + right_pad + 2,
                    "height": primitive_bounds["height"] + 4,
                },
                image.width,
                image.height,
                clip,
            )
            confidence = 0.62
            reason = "timeline marker strip primitive with priority badges"
    elif kind == "shape-or-sparkline" and name_contains(region_name, ("audit",)) and 70 <= w <= 190 and 32 <= h <= 90 and 1.4 <= aspect <= 5.0:
        stats_bounds = clamp_bounds(primitive_bounds, image.width, image.height, clip)
        stats = crop_stats(image, stats_bounds) if stats_bounds else {"colorfulness": 0.0, "edge_delta": 0.0}
        if stats["colorfulness"] >= 18 and stats["edge_delta"] >= 6:
            asset_type = "badge"
            bounds = expand_bounds(primitive_bounds, 1, image.width, image.height, clip)
            confidence = 0.57
            reason = "dark audit/table status tag cluster"
    elif kind == "shape-or-sparkline" and w >= 48 and 10 <= h <= 45 and name_contains(region_name, ("kpi", "metric", "spark", "trend")):
        asset_type = "sparkline"
        bounds = expand_bounds(primitive_bounds, 2, image.width, image.height, clip)
        confidence = 0.66
        reason = "small sparkline-like shape in metric region"

    if not bounds:
        return None
    return {
        "region": region_name,
        "asset_type": asset_type,
        "source": source,
        "bounds": bounds,
        "kind_hint": kind,
        "confidence": round(confidence, 3),
        "reason": reason,
    }


def surface_fragment_candidate(
    image: Image.Image,
    region: dict[str, Any],
    primitive: dict[str, Any],
    clip_to_region: bool,
) -> dict[str, Any] | None:
    """Crop small UI surfaces that carry subtle chrome/text pixels.

    These are element-scoped control/card fragments, not full panels. They catch KPI
    value blocks, badges, list row labels, segmented controls, and light card controls
    whose shadows/antialiasing do not converge through CSS alone.
    """
    region_name = str(region.get("name", "region"))
    if name_contains(region_name, ("trip-photo-panel", "trip-map-panel")):
        return None
    kind = str(primitive.get("kind") or "")
    if kind != "control-or-card-fragment":
        return None
    region_bounds = bounds_from_any(region.get("bounds") or {})
    bounds = bounds_from_any(primitive.get("full_bounds") or primitive)
    if not region_bounds or not bounds:
        return None
    width = bounds["width"]
    height = bounds["height"]
    asset_area = width * height
    if width < 18 or height < 7 or asset_area < 180 or asset_area > 12000:
        return None
    if width > region_bounds["width"] * 0.72 and height > region_bounds["height"] * 0.32:
        return None
    aspect = width / max(1, height)
    if aspect > 18 or aspect < 0.18:
        return None
    clip = region_bounds if clip_to_region else None
    cropped_bounds = expand_bounds(bounds, 1, image.width, image.height, clip)
    if not cropped_bounds:
        return None
    stats = crop_stats(image, cropped_bounds)
    if stats["edge_delta"] < 3.0 and stats["colorfulness"] < 1.5:
        return None
    return {
        "region": region_name,
        "asset_type": "control",
        "source": "surface-fragment",
        "bounds": cropped_bounds,
        "kind_hint": kind,
        "confidence": 0.55,
        "reason": "small measured control/card surface fragment for strict visual fidelity",
    }


def card_media_candidate(
    image: Image.Image,
    region: dict[str, Any],
    primitive: dict[str, Any],
    clip_to_region: bool,
) -> dict[str, Any] | None:
    region_name = str(region.get("name", "region"))
    if not name_contains(region_name, CARD_MEDIA_REGION_WORDS):
        return None
    if str(primitive.get("kind")) != "shape-or-sparkline":
        return None
    region_bounds = bounds_from_any(region.get("bounds") or {})
    box = bounds_from_any(primitive.get("full_bounds") or primitive)
    if not region_bounds or not box:
        return None
    if box["width"] < 90 or box["height"] < 110:
        return None
    if box["width"] > region_bounds["width"] * 0.95 and box["height"] > region_bounds["height"] * 0.9:
        return None
    media_h = max(44, min(int(round(box["height"] * 0.6)), 150))
    media = {
        "x": box["x"] + 1,
        "y": box["y"] + min(6, max(1, box["height"] // 30)),
        "width": max(1, box["width"] - 2),
        "height": media_h,
    }
    bounds = clamp_bounds(media, image.width, image.height, region_bounds if clip_to_region else None)
    if not bounds:
        return None
    return {
        "region": region_name,
        "asset_type": "image",
        "source": "card-media-top",
        "bounds": bounds,
        "kind_hint": str(primitive.get("kind")),
        "confidence": 0.7,
        "reason": "top media area inferred from card-like primitive",
    }


def card_media_overlay_candidates(
    image: Image.Image,
    region: dict[str, Any],
    media: dict[str, Any],
    clip_to_region: bool,
) -> list[dict[str, Any]]:
    """Infer small controls/badges painted on top of card media crops.

    Primitive detection usually returns one large photo/card box, so overlay controls
    such as favorite hearts and rating chips are swallowed by the media asset. Keeping
    them as addressable assets improves the component contract without changing the
    visual pixels because the crop is taken from the same reference area.
    """
    region_name = str(region.get("name", "region"))
    if not name_contains(region_name, CARD_MEDIA_REGION_WORDS):
        return []
    region_bounds = bounds_from_any(region.get("bounds") or {})
    media_bounds = bounds_from_any(media.get("bounds") or {})
    if not region_bounds or not media_bounds:
        return []
    clip = region_bounds if clip_to_region else None
    candidates: list[dict[str, Any]] = []

    heart_size = max(24, min(36, int(round(min(media_bounds["width"], media_bounds["height"]) * 0.24))))
    heart_raw = {
        "x": media_bounds["x"] + media_bounds["width"] - heart_size - 8,
        "y": media_bounds["y"] + 8,
        "width": heart_size,
        "height": heart_size,
    }
    heart_bounds = clamp_bounds(heart_raw, image.width, image.height, clip)
    if heart_bounds:
        stats = crop_stats(image, heart_bounds)
        if stats["edge_delta"] >= 4 or stats["colorfulness"] >= 2:
            candidates.append(
                {
                    "region": region_name,
                    "asset_type": "icon",
                    "source": "card-media-overlay",
                    "bounds": heart_bounds,
                    "kind_hint": "overlay-icon",
                    "confidence": 0.54,
                    "reason": "top-right media overlay control",
                    "parent_asset_source": media.get("source"),
                }
            )

    badge_width = max(42, min(62, int(round(media_bounds["width"] * 0.28))))
    badge_height = max(20, min(30, int(round(media_bounds["height"] * 0.2))))
    badge_raw = {
        "x": media_bounds["x"] + media_bounds["width"] - badge_width - 1,
        "y": media_bounds["y"] + media_bounds["height"] - badge_height - 1,
        "width": badge_width,
        "height": badge_height,
    }
    badge_bounds = clamp_bounds(badge_raw, image.width, image.height, clip)
    if badge_bounds:
        stats = crop_stats(image, badge_bounds)
        if stats["colorfulness"] >= 8 or stats["edge_delta"] >= 8:
            candidates.append(
                {
                    "region": region_name,
                    "asset_type": "badge",
                    "source": "card-media-overlay",
                    "bounds": badge_bounds,
                    "kind_hint": "overlay-badge",
                    "confidence": 0.56,
                    "reason": "bottom-right media overlay badge",
                    "parent_asset_source": media.get("source"),
                }
            )

    return candidates


def region_track_candidates(
    image: Image.Image,
    out_dir: Path,
    include_tracks: set[str],
) -> list[dict[str, Any]]:
    regions = region_bounds_by_name(out_dir)
    routing = routing_by_name(out_dir)
    candidates: list[dict[str, Any]] = []
    for name, route in routing.items():
        track = str(route.get("track") or "")
        if track not in include_tracks:
            continue
        region_raw = regions.get(name)
        bounds = bounds_from_any(region_raw or {})
        if not bounds:
            continue
        bounds = clamp_bounds(bounds, image.width, image.height)
        if not bounds:
            continue
        content_type = str(route.get("content_type") or "")
        if content_type == "icon":
            asset_type = "icon"
        elif content_type == "image" or name_contains(name, IMAGE_REGION_WORDS):
            asset_type = "image"
        elif content_type == "map":
            asset_type = "map"
        elif content_type == "chart":
            asset_type = "chart"
        else:
            asset_type = track
        candidates.append(
            {
                "region": name,
                "asset_type": asset_type,
                "source": "region-track",
                "track": track,
                "bounds": bounds,
                "kind_hint": content_type,
                "confidence": 1.0,
                "reason": f"region routed to {track}",
            }
        )
    return candidates


def dedupe(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Prefer explicit region tracks, then larger card media, then primitive icons.
    priority = {
        "region-track": 3,
        "component-fragment": 2.6,
        "card-media-top": 2,
        "card-media-overlay": 2,
        "surface-fragment": 2,
        "ocr-text": 2,
        "primitive": 1,
        "color-component": 1,
    }

    def preserve_nested_asset(candidate: dict[str, Any], other: dict[str, Any]) -> bool:
        """Keep addressable small assets even when a large backing asset contains them."""
        region_name = str(candidate.get("region") or "").lower()
        candidate_bounds = candidate["bounds"]
        other_bounds = other["bounds"]
        if not contains(other_bounds, center(candidate_bounds)):
            return False
        candidate_type = str(candidate.get("asset_type") or "")
        if candidate_type not in {"avatar", "badge", "control", "icon", "image"}:
            return False
        candidate_area = max(1, area(candidate_bounds))
        other_area = max(1, area(other_bounds))
        if candidate_area / other_area > 0.18:
            return False
        width = int(candidate_bounds.get("width") or 0)
        height = int(candidate_bounds.get("height") or 0)
        if width > 96 or height > 96:
            return False
        other_source = str(other.get("source") or "")
        other_type = str(other.get("asset_type") or "")
        other_reason = str(other.get("reason") or "")
        if (
            other_source == "region-track"
            and other_type in {"chart", "image", "map"}
            and ("dependency" in region_name or "diagram" in region_name)
        ):
            return True
        if other_source == "region-track" and candidate_type == "avatar":
            return True
        if other_type == "control" and candidate_type in {"badge", "icon"}:
            return True
        if (
            other_source == "primitive"
            and other_type == "image"
            and "timeline marker strip" in other_reason
            and candidate_type in {"badge", "icon"}
        ):
            return True
        return False

    ordered = sorted(
        candidates,
        key=lambda c: (
            str(c.get("region")),
            -priority.get(str(c.get("source")), 0),
            -(c["bounds"]["width"] * c["bounds"]["height"]),
        ),
    )
    kept: list[dict[str, Any]] = []
    for candidate in ordered:
        candidate_area = max(1, area(candidate["bounds"]))
        is_card_overlay = str(candidate.get("source")) == "card-media-overlay"
        should_drop = False
        for other in kept:
            nested_card_overlay = (
                is_card_overlay
                and str(other.get("source")) == "card-media-top"
                and contains(other["bounds"], center(candidate["bounds"]))
            )
            conflict = (
                iou(candidate["bounds"], other["bounds"]) > 0.72
                or (
                    contains(other["bounds"], center(candidate["bounds"]))
                    and candidate_area / max(1, area(other["bounds"])) <= 0.82
                )
            )
            if conflict and not nested_card_overlay and not preserve_nested_asset(candidate, other):
                should_drop = True
                break
        if should_drop:
            continue
        kept.append(candidate)
    kept.sort(key=lambda c: (c["bounds"]["y"], c["bounds"]["x"], str(c.get("region"))))
    for index, candidate in enumerate(kept, start=1):
        if candidate.get("id"):
            base = slugify(str(candidate["id"]))
        else:
            base = slugify(f"{candidate['region']}-{candidate['asset_type']}-{index:03d}")
            candidate["id"] = base
        candidate["file"] = f"{base}.png"
    return kept


def tighten_small_visual_assets(
    image: Image.Image,
    assets: list[dict[str, Any]],
    regions: dict[str, dict[str, Any]],
    clip_to_region: bool,
) -> list[dict[str, Any]]:
    tightened: list[dict[str, Any]] = []
    for asset in assets:
        asset_type = str(asset.get("asset_type") or "")
        source = str(asset.get("source") or "")
        if asset_type != "icon" or source == "region-track":
            tightened.append(asset)
            continue
        bounds = bounds_from_any(asset.get("bounds") or {})
        if not bounds:
            tightened.append(asset)
            continue
        if bounds["width"] < 8 or bounds["height"] < 8:
            tightened.append(asset)
            continue
        clip = None
        if clip_to_region:
            region = regions.get(str(asset.get("region") or ""))
            clip = bounds_from_any(region or {}) if region else None
        next_bounds = tighten_foreground_bounds(image, bounds, image.width, image.height, clip)
        if next_bounds != bounds:
            asset = {
                **asset,
                "bounds": next_bounds,
                "reason": f"{asset.get('reason')}; foreground-trimmed from {bounds}",
            }
        tightened.append(asset)
    return tightened


def crop_assets(
    image: Image.Image,
    assets: list[dict[str, Any]],
    asset_dir: Path,
    transparent_icon_backgrounds: bool = False,
) -> None:
    asset_dir.mkdir(parents=True, exist_ok=True)
    for stale in asset_dir.glob("*.png"):
        stale.unlink()
    for asset in assets:
        b = asset["bounds"]
        crop = image.crop((b["x"], b["y"], b["x"] + b["width"], b["y"] + b["height"]))
        if transparent_icon_backgrounds and str(asset.get("asset_type") or "") in {"icon", "control", "badge"}:
            crop = transparentize_stable_background(crop)
        rel = Path(str(asset["file"]))
        crop.save(asset_dir / rel)
        asset["path"] = str(Path(asset_dir.name) / rel)


def render_sheet(image: Image.Image, assets: list[dict[str, Any]], path: Path) -> None:
    if not assets:
        return
    thumb_w, thumb_h = 148, 96
    label_h = 40
    cols = 5
    rows = math.ceil(len(assets) / cols)
    sheet = Image.new("RGB", (cols * thumb_w, rows * (thumb_h + label_h)), "#f8fafc")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()
    for index, asset in enumerate(assets):
        col = index % cols
        row = index // cols
        ox = col * thumb_w
        oy = row * (thumb_h + label_h)
        b = asset["bounds"]
        crop = image.crop((b["x"], b["y"], b["x"] + b["width"], b["y"] + b["height"])).convert("RGB")
        crop.thumbnail((thumb_w - 16, thumb_h - 12), Image.LANCZOS)
        px = ox + (thumb_w - crop.width) // 2
        py = oy + (thumb_h - crop.height) // 2
        sheet.paste(crop, (px, py))
        label = f"{index+1}. {asset['asset_type']} {asset['region']}"
        draw.rectangle((ox, oy + thumb_h, ox + thumb_w, oy + thumb_h + label_h), fill="#e2e8f0")
        draw.text((ox + 6, oy + thumb_h + 5), label[:26], fill="#0f172a", font=font)
        draw.text((ox + 6, oy + thumb_h + 20), str(asset["bounds"]), fill="#475569", font=font)
    sheet.save(path)


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Element Asset Extraction",
        "",
        f"Generated: `{report['generated_at']}`",
        f"Out dir: `{report['out_dir']}`",
        f"Reference: `{report['reference']}`",
        f"Assets: `{report['asset_dir']}`",
        f"Sheet: `{report.get('sheet', '')}`",
        "",
        "| # | Id | Region | Type | Source | Bounds | Confidence | Reason |",
        "| ---: | --- | --- | --- | --- | --- | ---: | --- |",
    ]
    for index, asset in enumerate(report["assets"], start=1):
        lines.append(
            f"| {index} | `{asset['id']}` | `{asset['region']}` | `{asset['asset_type']}` | "
            f"`{asset['source']}` | `{asset['bounds']}` | {asset['confidence']:.2f} | {asset['reason']} |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True, help="Pixel Twin Lab output directory")
    parser.add_argument("--reference", help="Reference image path; defaults to <out-dir>/assets/reference.png")
    parser.add_argument("--measured", help="Measured primitives JSON; defaults to <out-dir>/measured-primitives.json")
    parser.add_argument("--asset-dir", default="element-assets", help="Output asset directory inside out-dir")
    parser.add_argument("--json-name", default="element-assets.json")
    parser.add_argument("--md-name", default="element-assets.md")
    parser.add_argument("--sheet-name", default="element-assets-sheet.png")
    parser.add_argument(
        "--include-region-tracks",
        default="island,approximation",
        help="Comma-separated routed region tracks to crop as region-level assets",
    )
    parser.add_argument("--no-card-media", action="store_true", help="Disable top-media inference for card-like primitives")
    parser.add_argument("--no-clip-to-region", action="store_true", help="Allow primitive crops to expand outside their region")
    parser.add_argument("--include-badges", action="store_true", help="Also crop badge-like primitives; off by default to avoid text-fragment noise")
    parser.add_argument(
        "--foreground-trim-icons",
        action="store_true",
        help="Experiment: trim uniform margins from large icon crops. Off by default because it can regress layout fidelity.",
    )
    parser.add_argument(
        "--transparent-icon-backgrounds",
        action="store_true",
        help="Experiment: make stable icon/control crop backgrounds transparent. Off by default because the rebuilt shell must already match.",
    )
    parser.add_argument(
        "--no-text-assets",
        action="store_true",
        help="Disable OCR text crops; by default text elements become visual assets with hidden semantic text.",
    )
    parser.add_argument(
        "--include-component-fragments",
        action="store_true",
        help="Experiment: crop semantic row/card component units for dense UI backtests. Keeps bounds region-scoped, but is asset-heavy.",
    )
    args = parser.parse_args()

    out_dir = Path(args.out_dir).expanduser().resolve()
    reference_path = Path(args.reference).expanduser().resolve() if args.reference else out_dir / "assets/reference.png"
    measured_path = Path(args.measured).expanduser().resolve() if args.measured else out_dir / "measured-primitives.json"
    if not reference_path.exists():
        raise SystemExit(f"Reference image not found: {reference_path}")
    if not measured_path.exists():
        raise SystemExit(f"Measured primitives not found: {measured_path}. Run measure_primitives.py first.")

    image = Image.open(reference_path).convert("RGB")
    measured = load_json(measured_path, {})
    measured_regions = measured.get("regions") if isinstance(measured, dict) else None
    if not isinstance(measured_regions, list) or not measured_regions:
        raise SystemExit(f"No measured regions found in {measured_path}.")

    include_tracks = {track.strip() for track in args.include_region_tracks.split(",") if track.strip()}
    candidates = region_track_candidates(image, out_dir, include_tracks)
    text_bounds = text_bounds_by_region(out_dir)
    for region in measured_regions:
        if not isinstance(region, dict):
            continue
        candidates.extend(color_component_candidates(image, region, not args.no_clip_to_region, text_bounds))
        for primitive in region.get("primitives") or []:
            if not isinstance(primitive, dict):
                continue
            if args.include_component_fragments:
                component_card = primitive_component_fragment_candidate(image, region, primitive, not args.no_clip_to_region)
                if component_card:
                    candidates.append(component_card)
            if not args.no_card_media:
                card = card_media_candidate(image, region, primitive, not args.no_clip_to_region)
                if card:
                    candidates.append(card)
                    candidates.extend(card_media_overlay_candidates(image, region, card, not args.no_clip_to_region))
            surface = surface_fragment_candidate(image, region, primitive, not args.no_clip_to_region)
            if surface:
                candidates.append(surface)
            primitive_candidate = candidate_for_primitive(image, region, primitive, not args.no_clip_to_region, args.include_badges)
            if primitive_candidate:
                candidates.append(primitive_candidate)
    if args.include_component_fragments:
        candidates.extend(component_fragment_candidates(image, out_dir, not args.no_clip_to_region))

    non_text_assets = dedupe(candidates)
    if args.no_text_assets:
        assets = non_text_assets
    else:
        assets = dedupe(candidates + text_asset_candidates(image, out_dir, non_text_assets, not args.no_clip_to_region))
    if args.foreground_trim_icons:
        assets = tighten_small_visual_assets(
            image,
            assets,
            {str(region.get("name") or ""): region for region in measured_regions if isinstance(region, dict)},
            not args.no_clip_to_region,
        )
    asset_dir = out_dir / args.asset_dir
    crop_assets(image, assets, asset_dir, args.transparent_icon_backgrounds)
    sheet_path = out_dir / args.sheet_name
    render_sheet(image, assets, sheet_path)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "out_dir": str(out_dir),
        "reference": str(reference_path),
        "measured_source": str(measured_path),
        "asset_dir": str(asset_dir),
        "sheet": str(sheet_path),
        "assets": assets,
    }
    (out_dir / args.json_name).write_text(json.dumps(report, indent=2), encoding="utf-8")
    (out_dir / args.md_name).write_text(render_markdown(report), encoding="utf-8")

    counts: dict[str, int] = {}
    for asset in assets:
        counts[str(asset["asset_type"])] = counts.get(str(asset["asset_type"]), 0) + 1
    print(
        json.dumps(
            {
                "out_dir": str(out_dir),
                "assets": len(assets),
                "asset_type_counts": counts,
                "json": str(out_dir / args.json_name),
                "sheet": str(sheet_path),
                "next_step": "Run init_element_manifest.py; it will merge this element-assets.json into the element contract.",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
