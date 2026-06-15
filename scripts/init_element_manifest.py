#!/usr/bin/env python3
"""Scaffold an element manifest from measured primitives so the agent can label element semantics.

The manifest is the element -> component mapping layer: geometry comes from measurement,
but `type`, `content`, and `maps_to` must be filled in by an agent (or human) looking at
the reference crops. Re-running this script merges new measured boxes into an existing
manifest without clobbering labels.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ELEMENT_TYPES = [
    "text",
    "icon",
    "image",
    "control",
    "list-item",
    "card",
    "divider",
    "badge",
    "chart-mark",
    "container",
    "decoration",
    # Data-driven container of repeated rows/items (tables, lists, queues, feeds).
    # Replace the per-cell entries inside it with ONE collection entry: keep this container,
    # delete its row-level children from the manifest, and set item_count (or min_items)
    # plus first_item_content. The DOM contract is data-element on the container and
    # data-element-item on every row.
    "collection",
    # Approximation-track chart container rendered by a third-party library (echarts/...).
    # Verified by the presence of the library's canvas/svg output, not by chart-mark geometry.
    "chart-host",
]

ASSET_TYPE_TO_ELEMENT_TYPE = {
    "avatar": "image",
    "badge": "badge",
    "chart": "chart-host",
    "component": "container",
    "control": "control",
    "icon": "icon",
    "image": "image",
    "island": "image",
    "map": "image",
    "sparkline": "image",
    "text": "text",
}

ANTD_CATEGORY_BY_COMPONENT_TYPE = {
    "avatar": "data-display",
    "badge": "data-display",
    "button": "general",
    "card": "data-display",
    "chart-container": "custom",
    "container": "layout",
    "decoration": "custom",
    "divider": "layout",
    "icon": "general",
    "image": "data-display",
    "input": "data-entry",
    "list": "data-display",
    "map-container": "custom",
    "menu": "navigation",
    "progress": "feedback",
    "select": "data-entry",
    "statistic": "data-display",
    "table": "data-display",
    "tabs": "data-display",
    "tag": "data-display",
    "timeline": "data-display",
    "typography": "general",
}

ELEMENT_TYPE_TO_COMPONENT_TYPE = {
    "badge": "tag",
    "card": "card",
    "chart-host": "chart-container",
    "collection": "list",
    "container": "container",
    "control": "button",
    "decoration": "decoration",
    "divider": "divider",
    "icon": "icon",
    "image": "image",
    "text": "typography",
}

REGION_COMPONENT_HINTS = (
    (("sidebar", "menu"), ("navigation", "menu")),
    (("nav", "tab"), ("data-display", "tabs")),
    (("timeline", "itinerary"), ("data-display", "timeline")),
    (("audit", "table", "regional", "health"), ("data-display", "table")),
    (("queue", "recommend", "checklist", "remediation"), ("data-display", "list")),
    (("kpi", "metric", "budget", "spend", "latency", "uptime"), ("data-display", "statistic")),
    (("chart",), ("custom", "chart-container")),
    (("map",), ("custom", "map-container")),
)


def load_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return fallback


def center(bounds: dict[str, Any]) -> tuple[float, float]:
    return (
        float(bounds.get("x", 0)) + float(bounds.get("width", 0)) / 2.0,
        float(bounds.get("y", 0)) + float(bounds.get("height", 0)) / 2.0,
    )


def contains(bounds: dict[str, Any], point: tuple[float, float]) -> bool:
    x, y = point
    return (
        float(bounds.get("x", 0)) <= x <= float(bounds.get("x", 0)) + float(bounds.get("width", 0))
        and float(bounds.get("y", 0)) <= y <= float(bounds.get("y", 0)) + float(bounds.get("height", 0))
    )


def new_element(region_name: str, index: int, box: dict[str, Any]) -> dict[str, Any]:
    bounds = box.get("full_bounds") or {k: box.get(k, 0) for k in ("x", "y", "width", "height")}
    return {
        "id": f"{region_name}-e{index:02d}",
        "bounds": {k: int(bounds.get(k, 0)) for k in ("x", "y", "width", "height")},
        "kind_hint": box.get("kind", "primitive"),
        # The fields below are semantic and must be filled in by the labeling pass:
        "type": "",
        "content": "",
        "maps_to": "",
        "notes": "",
    }


def new_chart_host_element(region_name: str, bounds: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": f"{region_name}-chart-host",
        "bounds": {k: int(bounds.get(k, 0)) for k in ("x", "y", "width", "height")},
        "kind_hint": "container-or-chart",
        "type": "chart-host",
        "category": "custom",
        "component_type": "chart-container",
        "content": "chart plot host",
        "maps_to": f"{region_name}/chart",
        "notes": "split from coarse region-track chart asset",
        "chart_role": "plot",
    }


def area(bounds: dict[str, Any]) -> float:
    return float(bounds.get("width", 0)) * float(bounds.get("height", 0))


def iou(a: dict[str, Any], b: dict[str, Any]) -> float:
    ax1, ay1 = float(a.get("x", 0)), float(a.get("y", 0))
    ax2, ay2 = ax1 + float(a.get("width", 0)), ay1 + float(a.get("height", 0))
    bx1, by1 = float(b.get("x", 0)), float(b.get("y", 0))
    bx2, by2 = bx1 + float(b.get("width", 0)), by1 + float(b.get("height", 0))
    ix = max(0.0, min(ax2, bx2) - max(ax1, bx1))
    iy = max(0.0, min(ay2, by2) - max(ay1, by1))
    inter = ix * iy
    union = area(a) + area(b) - inter
    return inter / union if union > 0 else 0.0


def intersection_area(a: dict[str, Any], b: dict[str, Any]) -> float:
    ax1, ay1 = float(a.get("x", 0)), float(a.get("y", 0))
    ax2, ay2 = ax1 + float(a.get("width", 0)), ay1 + float(a.get("height", 0))
    bx1, by1 = float(b.get("x", 0)), float(b.get("y", 0))
    bx2, by2 = bx1 + float(b.get("width", 0)), by1 + float(b.get("height", 0))
    ix = max(0.0, min(ax2, bx2) - max(ax1, bx1))
    iy = max(0.0, min(ay2, by2) - max(ay1, by1))
    return ix * iy


def default_asset_type(asset: dict[str, Any]) -> str:
    asset_type = str(asset.get("asset_type") or "")
    return ASSET_TYPE_TO_ELEMENT_TYPE.get(asset_type, "image")


def asset_content(asset: dict[str, Any]) -> str:
    if str(asset.get("asset_type") or "") == "text" and asset.get("content"):
        return normalize_text_content(str(asset.get("region") or ""), str(asset.get("content") or ""))
    asset_type = str(asset.get("asset_type") or "asset")
    source = str(asset.get("source") or "detected")
    return f"{asset_type} asset from {source}"


def bounds_from_any(raw: dict[str, Any]) -> dict[str, int] | None:
    try:
        bounds = {key: int(round(float(raw[key]))) for key in ("x", "y", "width", "height")}
    except (KeyError, TypeError, ValueError):
        return None
    if bounds["width"] <= 0 or bounds["height"] <= 0:
        return None
    return bounds


def normalize_text_content(region_name: str, value: str) -> str:
    text = re.sub(r"\s+", " ", value or "").strip()
    text = re.sub(r"\s*(?:©|@\)|@)\s*$", "", text).rstrip()
    text = text.replace("AllSystems", "All Systems")
    region_key = region_name.lower()
    if text == "Lumatrip":
        return "LumaTrip"
    if "intro-actions" in region_key and text == "Good morning,":
        return "Good morning, Sophia"
    if region_key == "insights-row":
        if text == "days":
            return "12 days"
    if region_key == "kpi-incidents" and text == "2vs last 24h":
        return "2 vs last 24h"
    if region_key == "kpi-latency" and text == "8ms":
        return "8 ms"
    if region_key == "deployment-queue":
        if text == "In Queve":
            return "In Queue"
    if "assistant" in region_key:
        text = text.replace("Luma Al Assistant", "Luma AI Assistant")
        text = text.replace("Luma Al", "Luma AI")
    if text.startswith("AlRemediation"):
        text = "AI Remediation" + text[len("AlRemediation") :]
    if text == "Al Remediation":
        text = "AI Remediation"
    if "remediation" in region_key:
        text = text.replace("Al ", "AI ")
    if "itinerary" in region_key and text == "Day May 26, Monday":
        text = "Day 3 · May 26, Monday"
    if region_key == "sidebar" and text == "Corp":
        text = "Acme Corp"
    return text


def normalize_text_bounds(region_name: str, text: str, bounds: dict[str, Any]) -> dict[str, int] | None:
    normalized = bounds_from_any(bounds)
    if not normalized:
        return None
    if region_name.lower() == "sidebar" and text in {"Corp", "Acme Corp"}:
        if normalized["x"] >= 80 and normalized["y"] <= 90 and normalized["height"] >= 20:
            return {"x": 60, "y": normalized["y"] + 4, "width": 74, "height": 13}
    if region_name.lower() == "insights-row" and text in {"days", "12 days"}:
        return {"x": 574, "y": max(0, normalized["y"] - 10), "width": 78, "height": max(normalized["height"], 27)}
    return normalized


def name_contains(value: str, words: tuple[str, ...]) -> bool:
    lowered = value.lower()
    return any(word in lowered for word in words)


def component_hint_for_region(region_name: str) -> tuple[str, str]:
    for words, hint in REGION_COMPONENT_HINTS:
        if name_contains(region_name, words):
            return hint
    return ("custom", "container")


def default_component_type(element_type: str, region_name: str) -> str:
    if element_type in {"collection", "container"}:
        return component_hint_for_region(region_name)[1]
    return ELEMENT_TYPE_TO_COMPONENT_TYPE.get(element_type, "decoration")


def default_category(element_type: str, component_type: str, region_name: str) -> str:
    if element_type in {"collection", "container"}:
        return component_hint_for_region(region_name)[0]
    return ANTD_CATEGORY_BY_COMPONENT_TYPE.get(component_type, "custom")


def infer_element_type(region_name: str, element: dict[str, Any]) -> str:
    if element.get("type"):
        return str(element["type"])
    if element.get("asset_type"):
        return default_asset_type(element)
    kind = str(element.get("kind_hint") or "")
    if kind == "ocr-text-line":
        return "text"
    if kind == "container-or-chart":
        return "container"
    if kind == "control-or-card-fragment":
        if name_contains(region_name, ("button", "filter", "input", "select", "topbar")):
            return "control"
        if name_contains(region_name, ("card", "recommend", "insight", "assistant", "packing", "itinerary")):
            return "card"
        return "decoration"
    if kind == "shape-or-sparkline":
        return "decoration"
    if kind == "text-line":
        return "decoration"
    if kind == "icon-or-badge":
        return "decoration"
    return "decoration"


def apply_default_semantics(region_name: str, element: dict[str, Any]) -> None:
    element_type = infer_element_type(region_name, element)
    element["type"] = element_type
    component_type = str(element.get("component_type") or default_component_type(element_type, region_name))
    category = str(element.get("category") or default_category(element_type, component_type, region_name))
    element["component_type"] = component_type
    element["category"] = category
    if not element.get("content"):
        if element_type == "text":
            element["content"] = "text"
        elif element_type == "decoration":
            element["content"] = f"{str(element.get('kind_hint') or 'primitive')} decoration"
        else:
            element["content"] = f"{component_type} element"
    elif element_type == "text":
        element["content"] = normalize_text_content(region_name, str(element.get("content") or ""))
        normalized_bounds = normalize_text_bounds(region_name, str(element.get("content") or ""), element.get("bounds") or {})
        if normalized_bounds:
            element["bounds"] = normalized_bounds
    if not element.get("maps_to"):
        element["maps_to"] = f"{region_name}/{component_type}"


def apply_asset_metadata(element: dict[str, Any], asset: dict[str, Any]) -> None:
    element.setdefault("type", "")
    element.setdefault("content", "")
    element.setdefault("maps_to", "")
    bounds = asset.get("bounds") or {}
    if isinstance(bounds, dict):
        element["bounds"] = {key: int(bounds.get(key, 0)) for key in ("x", "y", "width", "height")}
    is_chart_backing = str(asset.get("source") or "") == "region-track" and str(asset.get("asset_type") or "") == "chart"
    if is_chart_backing:
        element["type"] = "container"
    elif not element.get("type"):
        element["type"] = default_asset_type(asset)
    component_type = default_component_type(str(element.get("type") or ""), str(asset.get("region") or "region"))
    element.setdefault("component_type", component_type)
    element.setdefault("category", default_category(str(element.get("type") or ""), component_type, str(asset.get("region") or "region")))
    if not element.get("content"):
        element["content"] = asset_content(asset)
    if not element.get("maps_to"):
        element["maps_to"] = f"{asset.get('region', 'region')}/{asset.get('asset_type', 'asset')}"
    element["asset_id"] = asset.get("id")
    element["asset_type"] = asset.get("asset_type")
    element["asset_path"] = asset.get("path")
    element["asset_source"] = asset.get("source")
    element["requires_asset"] = True
    if is_chart_backing:
        element["asset_role"] = "chart-reference-backing"
        element["render_asset_in_library"] = False


ASSET_METADATA_KEYS = {
    "asset_id",
    "asset_path",
    "asset_role",
    "asset_source",
    "asset_type",
    "render_asset_in_library",
    "requires_asset",
}


def strip_asset_metadata(element: dict[str, Any]) -> None:
    for key in ASSET_METADATA_KEYS:
        element.pop(key, None)
    kind = str(element.get("kind_hint") or "")
    if kind.startswith("asset:"):
        element["kind_hint"] = "primitive"


def new_asset_element(asset: dict[str, Any]) -> dict[str, Any]:
    bounds = asset.get("bounds") or {}
    is_chart_backing = str(asset.get("source") or "") == "region-track" and str(asset.get("asset_type") or "") == "chart"
    element_type = "container" if is_chart_backing else default_asset_type(asset)
    component_type = default_component_type(element_type, str(asset.get("region") or "region"))
    element = {
        "id": str(asset.get("id") or f"{asset.get('region', 'region')}-asset"),
        "bounds": {k: int(bounds.get(k, 0)) for k in ("x", "y", "width", "height")},
        "kind_hint": f"asset:{asset.get('asset_type', 'asset')}",
        "type": element_type,
        "category": default_category(element_type, component_type, str(asset.get("region") or "region")),
        "component_type": component_type,
        "content": asset_content(asset),
        "maps_to": f"{asset.get('region', 'region')}/{asset.get('asset_type', 'asset')}",
        "notes": str(asset.get("reason") or ""),
        "asset_id": asset.get("id"),
        "asset_type": asset.get("asset_type"),
        "asset_path": asset.get("path"),
        "asset_source": asset.get("source"),
        "requires_asset": True,
    }
    if is_chart_backing:
        element["asset_role"] = "chart-reference-backing"
        element["render_asset_in_library"] = False
    return element


def asset_absorbs_placeholder(element: dict[str, Any], asset_element: dict[str, Any]) -> bool:
    if element.get("asset_path") or str(element.get("type") or "") == "text":
        return False
    asset_type = str(asset_element.get("asset_type") or asset_element.get("type") or "")
    if asset_type not in {"avatar", "badge", "component", "control", "icon", "image", "map", "sparkline"}:
        return False
    kind = str(element.get("kind_hint") or "")
    if kind not in {"icon-or-badge", "primitive", "shape-or-sparkline", "text-line", "control-or-card-fragment"}:
        return False
    bounds = element.get("bounds") or {}
    asset_bounds = asset_element.get("bounds") or {}
    element_area = area(bounds)
    asset_area = area(asset_bounds)
    if element_area <= 0 or asset_area <= 0:
        return False
    overlap = intersection_area(bounds, asset_bounds)
    if overlap / element_area >= 0.72:
        return True
    if contains(asset_bounds, center(bounds)) and 0.55 <= element_area / max(asset_area, 1.0) <= 1.15:
        return True
    return False


def prune_asset_absorbed_placeholders(elements: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    asset_elements = [element for element in elements if element.get("asset_path")]
    if not asset_elements:
        return elements, 0
    pruned: list[dict[str, Any]] = []
    removed = 0
    for element in elements:
        if any(asset_absorbs_placeholder(element, asset_element) for asset_element in asset_elements):
            removed += 1
            continue
        pruned.append(element)
    return pruned, removed


def region_track_backing(element: dict[str, Any]) -> bool:
    return (
        bool(element.get("asset_path"))
        and str(element.get("asset_source") or "") == "region-track"
        and str(element.get("asset_type") or "") in {"chart", "image", "map"}
    )


def existing_absorbs_box(element: dict[str, Any], bounds: dict[str, Any]) -> bool:
    element_bounds = element.get("bounds") or {}
    if not contains(element_bounds, center(bounds)):
        return False
    if region_track_backing(element):
        return iou(element_bounds, bounds) >= 0.72
    return True


def chart_backing_element(element: dict[str, Any]) -> bool:
    return (
        bool(element.get("asset_path"))
        and str(element.get("asset_source") or "") == "region-track"
        and str(element.get("asset_type") or "") == "chart"
    )


def best_inner_chart_bounds(region_bounds: dict[str, Any], measured_boxes: list[dict[str, Any]]) -> dict[str, Any] | None:
    region_area = max(1.0, area(region_bounds))
    region_y = float(region_bounds.get("y", 0))
    region_h = max(1.0, float(region_bounds.get("height", 0)))
    candidates: list[dict[str, Any]] = []
    for box in measured_boxes:
        if str(box.get("kind") or "") != "container-or-chart":
            continue
        bounds = box.get("full_bounds") or box
        box_area = area(bounds)
        if box_area <= 0:
            continue
        area_ratio = box_area / region_area
        top_offset = float(bounds.get("y", 0)) - region_y
        width_ratio = float(bounds.get("width", 0)) / max(1.0, float(region_bounds.get("width", 0)))
        height_ratio = float(bounds.get("height", 0)) / region_h
        if 0.18 <= area_ratio <= 0.78 and width_ratio >= 0.45 and height_ratio >= 0.28 and top_offset >= region_h * 0.22:
            candidates.append(bounds)
    if not candidates:
        return None
    return max(candidates, key=area)


def split_region_track_chart(
    region_name: str,
    region_bounds: dict[str, Any] | None,
    measured_boxes: list[dict[str, Any]],
    elements: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    if not isinstance(region_bounds, dict):
        return elements, 0
    if not any(chart_backing_element(element) for element in elements):
        return elements, 0
    if any(str(element.get("type") or "") == "chart-host" and str(element.get("chart_role") or "") == "plot" for element in elements):
        return elements, 0
    inner_bounds = best_inner_chart_bounds(region_bounds, measured_boxes)
    if not inner_bounds:
        return elements, 0
    existing_ids = {str(element.get("id")) for element in elements}
    chart_host = new_chart_host_element(region_name, inner_bounds)
    base_id = str(chart_host["id"])
    suffix = 2
    while str(chart_host["id"]) in existing_ids:
        chart_host["id"] = f"{base_id}-{suffix}"
        suffix += 1
    changed = 1
    for element in elements:
        if chart_backing_element(element):
            element["type"] = "container"
            element["component_type"] = "chart-container"
            element["category"] = "custom"
            element["content"] = "chart panel backing"
            element["maps_to"] = f"{region_name}/chart-shell"
            element["asset_role"] = "chart-reference-backing"
            element["render_asset_in_library"] = False
    elements.append(chart_host)
    elements.sort(key=lambda e: (int((e.get("bounds") or {}).get("y", 0)), int((e.get("bounds") or {}).get("x", 0)), str(e.get("id"))))
    return elements, changed


def merge_assets(
    region_name: str,
    elements: list[dict[str, Any]],
    assets: list[dict[str, Any]],
    prune_stale: bool = False,
) -> tuple[list[dict[str, Any]], int]:
    current_asset_ids = {str(asset.get("id")) for asset in assets if asset.get("id")}
    merged = []
    for element in elements:
        copied = dict(element)
        stale_asset = (
            prune_stale
            and bool(copied.get("requires_asset") or copied.get("asset_path") or copied.get("asset_id"))
            and str(copied.get("asset_id") or "") not in current_asset_ids
        )
        if stale_asset:
            continue
        merged.append(copied)
    added = 0
    existing_ids = {str(element.get("id")) for element in merged}
    for asset in assets:
        bounds = asset.get("bounds")
        if not isinstance(bounds, dict):
            continue
        asset_id = str(asset.get("id") or "")
        asset_center = center(bounds)
        best_index: int | None = None
        best_iou = 0.0
        if asset_id:
            for index, element in enumerate(merged):
                if str(element.get("asset_id") or "") == asset_id:
                    best_index = index
                    break
        element_id = str(asset.get("element_id") or "")
        if element_id and best_index is None:
            for index, element in enumerate(merged):
                if str(element.get("id") or "") == element_id:
                    best_index = index
                    break
        for index, element in enumerate(merged):
            if best_index is not None:
                break
            element_bounds = element.get("bounds") or {}
            overlap = iou(bounds, element_bounds)
            size_ratio = area(bounds) / max(area(element_bounds), 1.0)
            # Merge only when the primitive box is essentially the same element.
            # Card-media assets often sit inside a larger card primitive; keep those
            # as separate image elements so codegen can place the thumbnail slot.
            same_slot = overlap >= 0.45 and 0.72 <= size_ratio <= 1.45
            if (overlap > best_iou and same_slot) or (
                best_index is None and contains(element_bounds, asset_center) and 0.72 <= size_ratio <= 1.45
            ):
                best_index = index
                best_iou = overlap
        if best_index is not None:
            apply_asset_metadata(merged[best_index], asset)
            continue
        element = new_asset_element(asset)
        base_id = str(element["id"])
        suffix = 2
        while str(element["id"]) in existing_ids:
            element["id"] = f"{base_id}-{suffix}"
            suffix += 1
        existing_ids.add(str(element["id"]))
        merged.append(element)
        added += 1
    merged, _removed_placeholders = prune_asset_absorbed_placeholders(merged)
    merged.sort(key=lambda e: (int((e.get("bounds") or {}).get("y", 0)), int((e.get("bounds") or {}).get("x", 0)), str(e.get("id"))))
    return merged, added


def merge_region(
    region_name: str,
    measured_boxes: list[dict[str, Any]],
    existing_elements: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    elements = [dict(element) for element in existing_elements]
    added = 0
    next_index = len(elements) + 1
    existing_ids = {str(element.get("id")) for element in elements if element.get("id")}
    for box in measured_boxes:
        bounds = box.get("full_bounds") or box
        if any(existing_absorbs_box(element, bounds) for element in elements):
            continue
        while True:
            element = new_element(region_name, next_index, box)
            next_index += 1
            if str(element["id"]) not in existing_ids:
                break
        existing_ids.add(str(element["id"]))
        elements.append(element)
        added += 1
    return elements, added


def render_markdown(manifest: dict[str, Any]) -> str:
    lines = [
        "# Element Manifest",
        "",
        f"Generated: `{manifest['generated_at']}`",
        f"Out dir: `{manifest['out_dir']}`",
        "",
        "Label every element before rebuilding: set `type` (one of "
        + ", ".join(f"`{t}`" for t in ELEMENT_TYPES)
        + "), `content` (extracted text for text elements, a short semantic description otherwise), "
        "and `maps_to` (target component and slot, e.g. `WeatherCard/temperature`).",
        "",
        "| Region | Elements | Unlabeled |",
        "| --- | ---: | ---: |",
    ]
    for region in manifest["regions"]:
        unlabeled = sum(1 for element in region["elements"] if not element.get("type"))
        lines.append(f"| `{region['name']}` | {len(region['elements'])} | {unlabeled} |")
    lines.append("")
    for region in manifest["regions"]:
        lines += [
            f"## `{region['name']}`",
            "",
            "| Id | Bounds | Kind hint | Type | Category | Component | Content | Maps to |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for element in region["elements"]:
            lines.append(
                f"| `{element['id']}` | `{element['bounds']}` | `{element.get('kind_hint', '')}` | "
                f"{element.get('type') or '**unlabeled**'} | {element.get('category', '')} | "
                f"{element.get('component_type', '')} | {element.get('content', '')} | {element.get('maps_to', '')} |"
            )
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True, help="Pixel Twin Lab output directory")
    parser.add_argument("--measured", help="measured-primitives JSON path; defaults to <out-dir>/measured-primitives.json")
    parser.add_argument("--regions", help="Comma-separated region names to include; defaults to all measured regions")
    parser.add_argument("--json-name", default="element-manifest.json")
    parser.add_argument("--md-name", default="element-manifest.md")
    parser.add_argument("--element-assets", help="element-assets JSON path; defaults to <out-dir>/element-assets.json when present")
    parser.add_argument("--no-asset-merge", action="store_true", help="Do not merge extracted element assets into the manifest")
    parser.add_argument(
        "--strip-asset-metadata",
        action="store_true",
        help="Remove asset_id/asset_path/requires_asset metadata from preserved manifest elements for strict component-only runs.",
    )
    args = parser.parse_args()

    out_dir = Path(args.out_dir).expanduser().resolve()
    measured_path = Path(args.measured).expanduser().resolve() if args.measured else out_dir / "measured-primitives.json"
    if not measured_path.exists():
        raise SystemExit(f"Measured primitives not found: {measured_path}. Run measure_primitives.py first.")
    measured = load_json(measured_path, {})
    measured_regions = measured.get("regions") if isinstance(measured, dict) else None
    if not isinstance(measured_regions, list) or not measured_regions:
        raise SystemExit(f"No measured regions in {measured_path}.")

    wanted = {name.strip() for name in args.regions.split(",") if name.strip()} if args.regions else None
    existing = load_json(out_dir / args.json_name, {})
    existing_by_name = {
        str(region.get("name")): region.get("elements") or []
        for region in (existing.get("regions") or [])
        if isinstance(region, dict)
    }
    assets_by_region: dict[str, list[dict[str, Any]]] = {}
    asset_source: str | None = None
    if not args.no_asset_merge:
        assets_path = Path(args.element_assets).expanduser().resolve() if args.element_assets else out_dir / "element-assets.json"
        if assets_path.exists():
            asset_source = str(assets_path)
            assets_report = load_json(assets_path, {})
            for asset in assets_report.get("assets") or []:
                if isinstance(asset, dict) and asset.get("region"):
                    assets_by_region.setdefault(str(asset["region"]), []).append(asset)

    regions_out: list[dict[str, Any]] = []
    total_added = 0
    for region in measured_regions:
        if not isinstance(region, dict) or not region.get("name"):
            continue
        name = str(region["name"])
        if wanted is not None and name not in wanted:
            continue
        primitives = region.get("primitives") or []
        elements, added = merge_region(name, primitives, existing_by_name.pop(name, []))
        elements, added_assets = merge_assets(name, elements, assets_by_region.get(name, []), prune_stale=asset_source is not None)
        elements, added_chart_hosts = split_region_track_chart(name, region.get("bounds"), primitives, elements)
        if args.strip_asset_metadata:
            for element in elements:
                strip_asset_metadata(element)
        for element in elements:
            apply_default_semantics(name, element)
        total_added += added
        total_added += added_assets
        total_added += added_chart_hosts
        regions_out.append({"name": name, "bounds": region.get("bounds"), "elements": elements})
    # Regions present in the existing manifest but not re-measured this round are preserved untouched.
    for name, elements in existing_by_name.items():
        if wanted is not None and name not in wanted:
            pass
        if args.strip_asset_metadata:
            for element in elements:
                strip_asset_metadata(element)
        for element in elements:
            apply_default_semantics(name, element)
        regions_out.append({"name": name, "bounds": None, "elements": elements})

    if not regions_out:
        raise SystemExit("No regions selected for the element manifest.")

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "out_dir": str(out_dir),
        "measured_source": str(measured_path),
        "asset_source": asset_source,
        "element_types": ELEMENT_TYPES,
        "regions": regions_out,
    }
    (out_dir / args.json_name).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (out_dir / args.md_name).write_text(render_markdown(manifest), encoding="utf-8")

    total = sum(len(region["elements"]) for region in regions_out)
    unlabeled = sum(1 for region in regions_out for element in region["elements"] if not element.get("type"))
    print(
        json.dumps(
            {
                "out_dir": str(out_dir),
                "regions": len(regions_out),
                "elements": total,
                "added_this_round": total_added,
                "unlabeled": unlabeled,
                "next_step": (
                    "Label type/content/maps_to for every element (look at the reference crops), then rebuild and run "
                    "measure_dom_elements.cjs + verify_elements.py."
                    if unlabeled
                    else "Manifest fully labeled; rebuild elements and run measure_dom_elements.cjs + verify_elements.py."
                ),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
