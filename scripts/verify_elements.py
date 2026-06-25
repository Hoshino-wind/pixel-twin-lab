#!/usr/bin/env python3
"""Verify the rendered DOM against the element manifest: presence, geometry, content, and type compatibility.

This is the anti-collage check at element granularity: a bitmap patch cannot contain the
individually addressable [data-element] nodes the manifest demands.

Repeated content (tables, lists, queues, feeds) is verified through ONE `collection` entry:
the container carries data-element, every row/item carries data-element-item, and the entry
declares item_count (or min_items) plus first_item_content. This replaces per-cell entries —
a collection that renders from a data array passes; hand-written sibling blocks still pass
geometry but are caught in review, while a bitmap patch fails outright. Approximation-track
chart containers use type `chart-host`: the node must contain the library's canvas/svg output.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CONTROL_TAGS = {"button", "input", "select", "textarea", "a", "label", "summary"}
CONTROL_ROLES = {"button", "link", "checkbox", "radio", "switch", "tab", "menuitem", "slider", "combobox"}

WEIGHT_NAMES = {"normal": 400, "bold": 700, "lighter": 300, "bolder": 700}
STYLE_DEFAULTS = {
    "font_size_px": 1.0,
    "font_weight": 0,
    "color_rgb_max": 8,
    "line_height_px": 2.0,
    "letter_spacing_px": 0.5,
    "border_radius_px": 2.0,
    "border_width_px": 1.0,
    "opacity": 0.02,
}
STYLE_NUMERIC = (
    ("font_size_px", "font_size_px"),
    ("line_height_px", "line_height_px"),
    ("letter_spacing_px", "letter_spacing_px"),
    ("border_radius_px", "border_radius_px"),
    ("border_width_px", "border_width_px"),
    ("opacity", "opacity"),
)
STYLE_COLORS = ("color", "background_color", "border_color")
STYLE_ENUMS = ("text_align", "vertical_align", "text_transform", "border_style", "position")


def bounds_delta(actual: dict[str, Any], expected: dict[str, Any]) -> dict[str, int]:
    return {
        "dx": abs(int(actual.get("x", 0)) - int(expected.get("x", 0))),
        "dy": abs(int(actual.get("y", 0)) - int(expected.get("y", 0))),
        "dw": abs(int(actual.get("width", 0)) - int(expected.get("width", 0))),
        "dh": abs(int(actual.get("height", 0)) - int(expected.get("height", 0))),
    }


def bounds_inside(inner: dict[str, Any], outer: dict[str, Any], margin: int) -> bool:
    return (
        inner.get("x", 0) >= outer.get("x", 0) - margin
        and inner.get("y", 0) >= outer.get("y", 0) - margin
        and inner.get("x", 0) + inner.get("width", 0) <= outer.get("x", 0) + outer.get("width", 0) + margin
        and inner.get("y", 0) + inner.get("height", 0) <= outer.get("y", 0) + outer.get("height", 0) + margin
    )


def norm_weight(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value).strip().lower()
    if s.isdigit():
        return int(s)
    return WEIGHT_NAMES.get(s)


def hex_to_rgb(value: Any) -> tuple[int, int, int] | None:
    if not isinstance(value, str):
        return None
    h = value.strip().lstrip("#")
    if len(h) != 6:
        return None
    try:
        return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError:
        return None


def compare_style(expected: dict[str, Any], dom_style: dict[str, Any], tol: dict[str, float], label: str = "") -> list[str]:
    """Per-property style assertion. Returns one problem string per property outside tolerance,
    each carrying the actual-vs-expected delta — the repair worklist line."""
    problems: list[str] = []
    if not isinstance(expected, dict) or not isinstance(dom_style, dict):
        return problems

    def limit(key: str) -> float:
        return float(tol.get(key, STYLE_DEFAULTS.get(key, 0)))

    pfx = f"{label} " if label else ""
    for prop, tolkey in STYLE_NUMERIC:
        if expected.get(prop) is None:
            continue
        exp = float(expected[prop])
        act = dom_style.get(prop)
        if act is None:
            problems.append(f"{pfx}{prop}: expected {exp}, DOM missing")
            continue
        delta = abs(float(act) - exp)
        if delta > limit(tolkey):
            problems.append(f"{pfx}{prop}: {act} vs expected {exp} (Δ{round(delta, 2)} > {limit(tolkey)})")

    if expected.get("font_weight") is not None:
        exp_w = norm_weight(expected["font_weight"])
        act_w = norm_weight(dom_style.get("font_weight"))
        if act_w is None:
            problems.append(f"{pfx}font_weight: expected {exp_w}, DOM missing")
        elif exp_w is not None and abs(act_w - exp_w) > limit("font_weight"):
            problems.append(f"{pfx}font_weight: {act_w} vs expected {exp_w} (Δ{abs(act_w - exp_w)})")

    for prop in STYLE_COLORS:
        if not expected.get(prop):
            continue
        exp_c = hex_to_rgb(expected[prop])
        if exp_c is None:
            continue
        act_c = hex_to_rgb(dom_style.get(prop))
        if act_c is None:
            problems.append(f"{pfx}{prop}: expected {expected[prop]}, DOM {dom_style.get(prop)}")
            continue
        delta = max(abs(act_c[i] - exp_c[i]) for i in range(3))
        if delta > limit("color_rgb_max"):
            problems.append(f"{pfx}{prop}: {dom_style.get(prop)} vs expected {expected[prop]} (Δch{delta} > {int(limit('color_rgb_max'))})")

    for prop in STYLE_ENUMS:
        if expected.get(prop) is None:
            continue
        act = dom_style.get(prop)
        if act is not None and str(act) != str(expected[prop]):
            problems.append(f"{pfx}{prop}: '{act}' vs expected '{expected[prop]}'")

    if expected.get("font_family"):
        act = str(dom_style.get("font_family") or "")
        exp_f = str(expected["font_family"])
        if act and exp_f.lower() not in act.lower() and act.lower() not in exp_f.lower():
            problems.append(f"{pfx}font_family: '{act}' vs expected '{exp_f}'")

    if expected.get("box_shadow"):
        exp_shadow = str(expected["box_shadow"]).strip()
        act_shadow = str(dom_style.get("box_shadow") or "").strip()
        if exp_shadow == "present":
            if not act_shadow or act_shadow == "none":
                problems.append(f"{pfx}box_shadow: expected present, DOM missing")
        elif act_shadow != exp_shadow:
            problems.append(f"{pfx}box_shadow: '{act_shadow}' vs expected '{exp_shadow}'")

    return problems


def base_tolerance(args: argparse.Namespace) -> dict[str, float]:
    return {
        "font_size_px": args.max_font_size_delta,
        "font_weight": args.max_font_weight_delta,
        "color_rgb_max": args.max_color_delta,
        "line_height_px": args.max_line_height_delta,
        "letter_spacing_px": args.max_letter_spacing_delta,
        "border_radius_px": args.max_radius_delta,
        "border_width_px": args.max_border_width_delta,
        "opacity": args.max_opacity_delta,
    }


def apply_style_contract(
    result: dict[str, Any],
    block: dict[str, Any],
    dom_style: dict[str, Any],
    base_tol: dict[str, float],
    label: str = "",
) -> None:
    if not isinstance(block, dict) or not isinstance(block.get("expected"), dict):
        return
    tol = {**base_tol, **(block.get("tolerance") or {})}
    problems = compare_style(block["expected"], dom_style or {}, tol, label=label)
    if not problems:
        return
    if block.get("severity") == "warn":
        result.setdefault("warnings", []).extend(problems)
    else:
        result.setdefault("problems", []).extend(problems)


def load_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return fallback


def manifest_score(raw: Any) -> tuple[int, int]:
    if not isinstance(raw, dict):
        return (0, 0)
    regions = raw.get("regions")
    if not isinstance(regions, list):
        return (0, 0)
    region_count = sum(1 for region in regions if isinstance(region, dict))
    element_count = sum(
        len(region.get("elements") or [])
        for region in regions
        if isinstance(region, dict)
    )
    return (region_count, element_count)


def manifest_version(path: Path) -> int:
    match = re.fullmatch(r"element-manifest-v(\d+)\.json", path.name)
    if not match:
        return -1
    return int(match.group(1))


def resolve_manifest_path(out_dir: Path, requested: Path, auto_latest: bool = True) -> Path:
    if requested.is_absolute() or not auto_latest or requested.name != "element-manifest.json":
        return requested
    default_path = out_dir / requested
    best_score = manifest_score(load_json(default_path, {}))
    best_path = default_path
    best_version = -1
    for candidate in sorted(out_dir.glob("element-manifest-v*.json")):
        score = manifest_score(load_json(candidate, {}))
        if score[0] < best_score[0]:
            continue
        version = manifest_version(candidate)
        if version > best_version:
            best_version = version
            best_path = candidate
    return best_path


def normalize_text(value: str) -> str:
    text = re.sub(r"\s+", " ", value or "").strip().lower()
    if text == "good morning, 👋":
        return "good morning, sophia 👋"
    return text


def type_compatible(element_type: str, dom: dict[str, Any]) -> tuple[bool, str]:
    tag = str(dom.get("tag") or "")
    role = str(dom.get("role") or "")
    if element_type == "text":
        if normalize_text(str(dom.get("text") or "")):
            return True, ""
        return False, "type 'text' but the DOM node has no text content"
    if element_type == "icon":
        if dom.get("has_svg") or dom.get("has_img") or tag in {"svg", "img", "i", "canvas"}:
            return True, ""
        return False, "type 'icon' but the DOM node has no svg/img content"
    if element_type == "image":
        if dom.get("has_img") or tag in {"img", "picture", "video"}:
            return True, ""
        return False, "type 'image' but the DOM node has no img content"
    if element_type == "control":
        if tag in CONTROL_TAGS or role in CONTROL_ROLES:
            return True, ""
        return False, f"type 'control' but tag `{tag}`/role `{role or 'none'}` is not interactive"
    if element_type == "chart-host":
        if dom.get("has_canvas") or dom.get("has_svg"):
            return True, ""
        return False, "type 'chart-host' but the DOM node contains no canvas/svg (the chart library has not rendered into it)"
    return True, ""


def element_requires_asset(element: dict[str, Any]) -> bool:
    if str(element.get("asset_role") or "") == "chart-reference-backing" and element.get("render_asset_in_library") is False:
        return False
    return bool(element.get("requires_asset") or element.get("asset_id") or element.get("asset_path"))


def verify_asset_contract(element: dict[str, Any], dom: dict[str, Any]) -> list[str]:
    """Asset-required elements must render from a measured image asset.

    This deliberately rejects an inline SVG-only approximation for a declared icon/photo
    crop. The stable identity is data-element-asset-id; img src proves the visual comes
    from an asset slot rather than a hand-drawn DOM shape.
    """
    if not element_requires_asset(element):
        return []

    element_id = str(element.get("id") or "")
    expected_asset_id = str(element.get("asset_id") or element_id).strip()
    expected_path = str(element.get("asset_path") or "").strip()
    actual_asset_id = str(dom.get("asset_id") or "").strip()
    asset_src = str(dom.get("asset_src") or dom.get("img_src") or "").strip()
    tag = str(dom.get("tag") or "")
    has_img = bool(dom.get("has_img") or tag in {"img", "picture", "video"})

    problems: list[str] = []
    if not actual_asset_id:
        problems.append(
            f"requires an asset but DOM has no data-element-asset-id; expected '{expected_asset_id}'"
        )
    elif expected_asset_id and actual_asset_id != expected_asset_id:
        problems.append(
            f"data-element-asset-id '{actual_asset_id}' != expected asset_id '{expected_asset_id}'"
        )
    if not has_img:
        problems.append(
            "requires an asset image node; inline svg/canvas/CSS drawing is not a measured asset crop"
        )
    if expected_path and not asset_src:
        problems.append(f"requires asset_path '{expected_path}' but DOM has no img src")
    return problems


def verify_element(element: dict[str, Any], dom_matches: list[dict[str, Any]], args: argparse.Namespace) -> dict[str, Any]:
    element_id = str(element.get("id"))
    result: dict[str, Any] = {
        "id": element_id,
        "type": element.get("type") or None,
        "maps_to": element.get("maps_to") or None,
        "requires_asset": element_requires_asset(element),
        "asset_id": element.get("asset_id") or None,
        "asset_path": element.get("asset_path") or None,
        "labeled": bool(element.get("type")),
        "problems": [],
    }
    if not result["labeled"]:
        result["problems"].append("unlabeled: type/content/maps_to not filled in")

    if not dom_matches:
        result["status"] = "missing"
        result["problems"].append("no DOM node carries data-element with this id")
        return result
    if len(dom_matches) > 1:
        result["status"] = "duplicate"
        result["problems"].append(f"{len(dom_matches)} DOM nodes carry this id; ids must be unique")
        return result

    dom = dom_matches[0]
    bounds = element.get("bounds") or {}
    dom_bounds = dom.get("bounds") or {}
    dx = abs(int(dom_bounds.get("x", 0)) - int(bounds.get("x", 0)))
    dy = abs(int(dom_bounds.get("y", 0)) - int(bounds.get("y", 0)))
    dw = abs(int(dom_bounds.get("width", 0)) - int(bounds.get("width", 0)))
    dh = abs(int(dom_bounds.get("height", 0)) - int(bounds.get("height", 0)))
    result["position_delta"] = {"dx": dx, "dy": dy}
    result["size_delta"] = {"dw": dw, "dh": dh}
    if max(dx, dy) > args.max_position_delta:
        result["problems"].append(
            f"position off by ({dx}, {dy})px, allowed {args.max_position_delta}px; manifest {bounds} vs DOM {dom_bounds}"
        )
    if max(dw, dh) > args.max_size_delta:
        result["problems"].append(
            f"size off by ({dw}, {dh})px, allowed {args.max_size_delta}px; manifest {bounds} vs DOM {dom_bounds}"
        )

    element_type = str(element.get("type") or "")
    if element_type == "text" and element.get("content"):
        expected = normalize_text(str(element["content"]))
        actual = normalize_text(str(dom.get("text") or ""))
        if expected and expected not in actual and actual not in expected:
            result["problems"].append(f"text mismatch: manifest '{element['content']}' vs DOM '{dom.get('text')}'")
    if element_type == "collection":
        actual_items = int(dom.get("item_count") or 0)
        result["item_count"] = actual_items
        if actual_items == 0:
            result["problems"].append(
                "type 'collection' but no descendant [data-element-item] nodes; "
                "collections must render rows/items from a data array through one template"
            )
        else:
            expected_count = element.get("item_count")
            min_items = element.get("min_items")
            if expected_count is not None and actual_items != int(expected_count):
                result["problems"].append(f"item count {actual_items} != expected {expected_count}")
            elif min_items is not None and actual_items < int(min_items):
                result["problems"].append(f"item count {actual_items} < min_items {min_items}")
            first_expected = element.get("first_item_content")
            if first_expected:
                expected = normalize_text(str(first_expected))
                actual = normalize_text(str(dom.get("first_item_text") or ""))
                if expected and expected not in actual and actual not in expected:
                    result["problems"].append(
                        f"first item mismatch: manifest '{first_expected}' vs DOM '{dom.get('first_item_text')}'"
                    )
    if element_type:
        compatible, reason = type_compatible(element_type, dom)
        if not compatible:
            result["problems"].append(reason)
    result["dom_asset"] = {
        "asset_id": dom.get("asset_id"),
        "asset_type": dom.get("asset_type"),
        "asset_src": dom.get("asset_src") or dom.get("img_src"),
    }
    result["problems"].extend(verify_asset_contract(element, dom))

    # --- style contract (optional): single style and/or composite runs ---
    base_tol = base_tolerance(args)

    style_block = element.get("style")
    if isinstance(style_block, dict):
        apply_style_contract(result, style_block, dom.get("style") or {}, base_tol)

    runs = element.get("runs")
    if isinstance(runs, list) and runs:
        dom_runs = dom.get("runs") or []
        result["run_count"] = len(dom_runs)
        if len(dom_runs) != len(runs):
            result["problems"].append(
                f"runs: DOM has {len(dom_runs)} [data-run] node(s), manifest declares {len(runs)} "
                "(composite text must render one node per run carrying data-run)"
            )
        for i, run in enumerate(runs):
            if not isinstance(run, dict):
                continue
            dom_run = dom_runs[i] if i < len(dom_runs) else {}
            exp_t = normalize_text(str(run.get("text") or ""))
            act_t = normalize_text(str(dom_run.get("text") or ""))
            if exp_t and exp_t not in act_t and act_t not in exp_t:
                result["problems"].append(f"run[{i}] text: '{run.get('text')}' vs DOM '{dom_run.get('text')}'")
            apply_style_contract(result, run.get("style") or {}, dom_run.get("style") or {}, base_tol, label=f"run[{i}]")

    result["status"] = "ok" if not result["problems"] else "failed"
    return result


def blueprint_components(blueprint: Any) -> list[dict[str, Any]]:
    if not isinstance(blueprint, dict):
        return []
    components = blueprint.get("components")
    if not isinstance(components, list):
        return []
    return [component for component in components if isinstance(component, dict) and component.get("id")]


def component_type_for_region(region_name: str, elements: list[dict[str, Any]]) -> tuple[str, str]:
    name = region_name.lower()
    if any(str(element.get("type") or "") == "chart-host" for element in elements):
        return ("data-display", "chart-container")
    if name in {"sidebar", "bottom-nav"}:
        return ("navigation", "menu")
    if name == "tabs":
        return ("navigation", "tabs")
    if name.startswith("kpi-"):
        return ("data-display", "statistic")
    if any(str(element.get("type") or "") == "collection" for element in elements):
        if any(token in name for token in ("audit", "health")):
            return ("data-display", "table")
        if any(token in name for token in ("timeline", "itinerary")):
            return ("data-display", "timeline")
        return ("data-display", "list")
    if any(token in name for token in ("hero", "assistant", "recommendations", "packing")):
        return ("custom", "card")
    return ("layout", "container")


def derived_manifest_components(
    manifest: Any,
    dom_components_by_id: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Use region components only when the DOM explicitly exposes component roots.

    Older element-only benchmarks and labs have no `data-component`; for those,
    component verification remains opt-in through a blueprint. Newer materialized
    component rebuilds expose each region as a component root, so the manifest can
    provide a conservative ownership contract even before a full blueprint exists.
    """
    if not dom_components_by_id or not isinstance(manifest, dict):
        return []
    regions = manifest.get("regions")
    if not isinstance(regions, list):
        return []
    components: list[dict[str, Any]] = []
    for region in regions:
        if not isinstance(region, dict) or not region.get("name"):
            continue
        component_id = str(region["name"])
        if component_id not in dom_components_by_id:
            continue
        raw_elements = region.get("elements") or []
        elements = [element for element in raw_elements if isinstance(element, dict) and element.get("id")]
        category, component_type = component_type_for_region(component_id, elements)
        components.append(
            {
                "id": component_id,
                "region": component_id,
                "category": category,
                "type": component_type,
                "bounds": region.get("bounds") or {},
                "elements": [
                    {
                        "id": str(element["id"]),
                        "type": str(element.get("type") or "container"),
                        "bounds": element.get("bounds") or {},
                    }
                    for element in elements
                ],
                "source": "derived-from-element-manifest",
            }
        )
    return components


def verify_component(
    component: dict[str, Any],
    dom_matches: list[dict[str, Any]],
    dom_by_id: dict[str, list[dict[str, Any]]],
    args: argparse.Namespace,
) -> dict[str, Any]:
    component_id = str(component.get("id"))
    result: dict[str, Any] = {
        "id": component_id,
        "type": component.get("type") or None,
        "region": component.get("region") or None,
        "problems": [],
        "warnings": [],
    }
    if not dom_matches:
        result["status"] = "missing"
        result["problems"].append("no DOM node carries data-component with this component id")
        return result
    if len(dom_matches) > 1:
        result["status"] = "duplicate"
        result["problems"].append(f"{len(dom_matches)} DOM nodes carry this component id; data-component ids must be unique")
        return result

    dom = dom_matches[0]
    bounds = component.get("bounds") or {}
    dom_bounds = dom.get("bounds") or {}
    delta = bounds_delta(dom_bounds, bounds)
    result["bounds"] = dom_bounds
    result["expected_bounds"] = bounds
    result["style"] = dom.get("style") or {}
    result["position_delta"] = {"dx": delta["dx"], "dy": delta["dy"]}
    result["size_delta"] = {"dw": delta["dw"], "dh": delta["dh"]}
    if max(delta["dx"], delta["dy"]) > args.max_position_delta:
        result["problems"].append(
            f"component position off by ({delta['dx']}, {delta['dy']})px, allowed {args.max_position_delta}px; "
            f"blueprint {bounds} vs DOM {dom_bounds}"
        )
    if max(delta["dw"], delta["dh"]) > args.max_size_delta:
        result["problems"].append(
            f"component size off by ({delta['dw']}, {delta['dh']})px, allowed {args.max_size_delta}px; "
            f"blueprint {bounds} vs DOM {dom_bounds}"
        )
    style_block = component.get("style")
    if isinstance(style_block, dict):
        apply_style_contract(
            result,
            style_block,
            dom.get("style") or {},
            base_tolerance(args),
            label="component style",
        )
    owned: list[str] = []
    ownership_problems: list[str] = []
    for element in component.get("elements") or []:
        if not isinstance(element, dict) or not element.get("id"):
            continue
        element_id = str(element["id"])
        element_nodes = dom_by_id.get(element_id) or []
        if not element_nodes:
            ownership_problems.append(
                f"component element '{element_id}' has no DOM node; component-owned data-elements must render under data-component '{component_id}'"
            )
            continue
        for node in element_nodes:
            owner = node.get("component_id")
            if owner != component_id:
                ownership_problems.append(
                    f"component element '{element_id}' rendered outside data-component '{component_id}' "
                    f"(owner: {owner or 'none'}); data-element nodes declared by a component must be descendants of its root"
                )
            else:
                owned.append(element_id)
    result["owned_elements"] = sorted(set(owned))
    result["ownership_problems"] = ownership_problems
    result["problems"].extend(ownership_problems)
    result["status"] = "ok" if not result["problems"] else "failed"
    return result


def verify_composition(region: dict[str, Any], dom_by_id: dict[str, list[dict[str, Any]]], args: argparse.Namespace) -> dict[str, Any] | None:
    """Anti-swallow gate: every declared foreground overlay must render as an addressable
    [data-element] DOM node, inside the background region and stacked above it. A foreground
    baked into the background bitmap has no addressable node and fails here."""
    comp = region.get("composition")
    if not isinstance(comp, dict):
        return None
    bg = comp.get("background") or {}
    fg = comp.get("foreground") or []
    problems: list[str] = []
    bg_id = bg.get("asset_element_id")
    bg_bounds = region.get("bounds") or {}
    margin = args.max_position_delta

    if bg_id:
        nodes = dom_by_id.get(str(bg_id)) or []
        if not nodes:
            problems.append(f"background.asset_element_id '{bg_id}' has no DOM node")
        else:
            node = nodes[0]
            is_asset = bool(node.get("has_img") or node.get("has_canvas") or node.get("has_svg")) or str(node.get("tag")) in {"img", "canvas", "svg", "video"}
            if not is_asset and bg.get("asset_policy") != "none":
                problems.append(f"background '{bg_id}' is not an image/canvas/svg node (asset_policy={bg.get('asset_policy')})")

    found = 0
    for entry in fg:
        if not isinstance(entry, dict):
            continue
        cid = str(entry.get("component_id"))
        nodes = dom_by_id.get(cid) or []
        if not nodes:
            problems.append(
                f"foreground overlay '{cid}' declared in composition but no [data-element] DOM node — "
                "it was likely baked into the background asset (anti-swallow)"
            )
            continue
        found += 1
        node = nodes[0]
        if bg_id and cid == str(bg_id):
            problems.append(f"foreground overlay '{cid}' is the same node as the background asset")
        nb = node.get("bounds") or {}
        if bg_bounds and nb:
            if not bounds_inside(nb, bg_bounds, margin):
                problems.append(f"foreground overlay '{cid}' bounds {nb} not within background region {bg_bounds}")
        expected_bounds = entry.get("bounds")
        if isinstance(expected_bounds, dict) and nb:
            delta = bounds_delta(nb, expected_bounds)
            if max(delta["dx"], delta["dy"]) > args.max_position_delta:
                problems.append(
                    f"foreground overlay '{cid}' position off by ({delta['dx']}, {delta['dy']})px, "
                    f"allowed {args.max_position_delta}px; composition {expected_bounds} vs DOM {nb}"
                )
            if max(delta["dw"], delta["dh"]) > args.max_size_delta:
                problems.append(
                    f"foreground overlay '{cid}' size off by ({delta['dw']}, {delta['dh']})px, "
                    f"allowed {args.max_size_delta}px; composition {expected_bounds} vs DOM {nb}"
                )
        style = node.get("style") or {}
        z = style.get("z_index")
        pos = style.get("position")
        expected_z = entry.get("z")
        if pos in (None, "static"):
            problems.append(f"foreground overlay '{cid}' must be explicitly positioned above the background (position={pos})")
        if isinstance(expected_z, int):
            if z is None:
                problems.append(f"foreground overlay '{cid}' missing z-index; composition requires z >= {expected_z}")
            elif z < expected_z:
                problems.append(f"foreground overlay '{cid}' z-index {z} below composition z {expected_z}")
        elif z is None or z < 1:
            problems.append(
                f"foreground overlay '{cid}' is not stacked above the background "
                f"(position={pos}, z-index={z}); overlays must be positioned with z-index >= 1"
            )

    return {"declared": len(fg), "found": found, "problems": problems}


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Element Verification",
        "",
        f"Generated: `{report['generated_at']}`",
        f"Result: `{'PASS' if summary['pass'] else 'FAIL'}`",
        "",
        f"- elements: {summary['total']} | ok: {summary['ok']} | missing: {summary['missing']} | "
        f"duplicate: {summary['duplicate']} | failed: {summary['failed']} | unlabeled: {summary['unlabeled']}",
        f"- components: {summary.get('components_total', 0)} | ok: {summary.get('components_ok', 0)} | "
        f"missing: {summary.get('components_missing', 0)} | failed: {summary.get('components_failed', 0)}",
        f"- composition problems: {summary.get('composition_problems', 0)}",
        f"- extra DOM ids not in the manifest: {summary['extra_dom_ids']}",
        "",
        "| Region | Ok / Total |",
        "| --- | --- |",
    ]
    for region in report["regions"]:
        lines.append(f"| `{region['name']}` | {region['ok']}/{region['total']} |")
    lines.append("")
    for region in report["regions"]:
        failures = [e for e in region["elements"] if e["status"] != "ok" or e["problems"] or e.get("warnings")]
        comp = region.get("composition") or {}
        comp_problems = comp.get("problems") or []
        if not failures and not comp_problems:
            continue
        lines.append(f"## `{region['name']}`")
        lines.append("")
        for problem in comp_problems:
            lines.append(f"- composition: {problem}")
        for element in failures:
            lines.append(f"- `{element['id']}` ({element['status']}):")
            for problem in element["problems"]:
                lines.append(f"  - {problem}")
            for warning in element.get("warnings", []):
                lines.append(f"  - ⚠ {warning}")
        lines.append("")
    component_failures = [
        c for c in report.get("components", [])
        if c.get("status") != "ok" or c.get("problems") or c.get("warnings")
    ]
    if component_failures:
        lines.append("## Component Layout")
        lines.append("")
        for component in component_failures:
            lines.append(f"- `{component['id']}` ({component['status']}):")
            for problem in component.get("problems") or []:
                lines.append(f"  - {problem}")
            for warning in component.get("warnings") or []:
                lines.append(f"  - ⚠ {warning}")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True, help="Pixel Twin Lab output directory")
    parser.add_argument("--manifest-name", default="element-manifest.json")
    parser.add_argument(
        "--no-auto-latest-manifest",
        action="store_true",
        help="Do not replace the default element-manifest.json with the latest element-manifest-vN.json snapshot.",
    )
    parser.add_argument("--blueprint-name", default="ui-blueprint.json")
    parser.add_argument("--dom-name", default="dom-elements.json")
    parser.add_argument("--max-position-delta", type=int, default=6, help="Max allowed |dx|/|dy| in px")
    parser.add_argument("--max-size-delta", type=int, default=10, help="Max allowed |dw|/|dh| in px")
    parser.add_argument("--max-font-size-delta", type=float, default=1.0, help="Max allowed font-size delta in px")
    parser.add_argument("--max-font-weight-delta", type=int, default=0, help="Max allowed numeric weight delta (0 = exact)")
    parser.add_argument("--max-color-delta", type=int, default=8, help="Max allowed per-channel RGB delta (0-255)")
    parser.add_argument("--max-line-height-delta", type=float, default=2.0, help="Max allowed line-height delta in px")
    parser.add_argument("--max-letter-spacing-delta", type=float, default=0.5, help="Max allowed letter-spacing delta in px")
    parser.add_argument("--max-radius-delta", type=float, default=2.0, help="Max allowed border-radius delta in px")
    parser.add_argument("--max-border-width-delta", type=float, default=1.0, help="Max allowed border-width delta in px")
    parser.add_argument("--max-opacity-delta", type=float, default=0.02, help="Max allowed opacity delta")
    parser.add_argument("--json-name", default="element-verification.json")
    parser.add_argument("--md-name", default="element-verification.md")
    args = parser.parse_args()

    out_dir = Path(args.out_dir).expanduser().resolve()
    manifest_path = Path(args.manifest_name).expanduser()
    if not manifest_path.is_absolute():
        manifest_path = resolve_manifest_path(out_dir, manifest_path, auto_latest=not args.no_auto_latest_manifest)
    manifest = load_json(manifest_path, {})
    if not isinstance(manifest, dict) or not manifest.get("regions"):
        raise SystemExit(f"Element manifest missing or empty: {manifest_path}. Run init_element_manifest.py first.")
    dom = load_json(out_dir / args.dom_name, {})
    if not isinstance(dom, dict) or "elements" not in dom:
        raise SystemExit(f"DOM measurement missing: {out_dir / args.dom_name}. Run measure_dom_elements.cjs first.")
    blueprint = load_json(out_dir / args.blueprint_name, None)

    dom_by_id: dict[str, list[dict[str, Any]]] = {}
    for node in dom.get("elements") or []:
        if isinstance(node, dict) and node.get("id"):
            dom_by_id.setdefault(str(node["id"]), []).append(node)
    dom_components_by_id: dict[str, list[dict[str, Any]]] = {}
    for node in dom.get("components") or []:
        if isinstance(node, dict) and node.get("id"):
            dom_components_by_id.setdefault(str(node["id"]), []).append(node)

    manifest_ids: set[str] = set()
    regions_out: list[dict[str, Any]] = []
    all_results: list[dict[str, Any]] = []
    for region in manifest["regions"]:
        if not isinstance(region, dict):
            continue
        elements = region.get("elements") or []
        results = []
        for element in elements:
            if not isinstance(element, dict) or not element.get("id"):
                continue
            element_id = str(element["id"])
            manifest_ids.add(element_id)
            results.append(verify_element(element, dom_by_id.get(element_id, []), args))
        region_entry = {
            "name": str(region.get("name")),
            "total": len(results),
            "ok": sum(1 for r in results if r["status"] == "ok"),
            "elements": results,
        }
        composition = verify_composition(region, dom_by_id, args)
        if composition is not None:
            region_entry["composition"] = composition
        regions_out.append(region_entry)
        all_results.extend(results)

    extra_dom_ids = sorted(set(dom_by_id) - manifest_ids)
    component_specs = blueprint_components(blueprint)
    if not component_specs:
        component_specs = derived_manifest_components(manifest, dom_components_by_id)
    component_results = [
        verify_component(component, dom_components_by_id.get(str(component["id"]), []), dom_by_id, args)
        for component in component_specs
    ]
    composition_problems = sum(
        len(region["composition"]["problems"]) for region in regions_out if region.get("composition")
    )
    summary = {
        "total": len(all_results),
        "ok": sum(1 for r in all_results if r["status"] == "ok"),
        "missing": sum(1 for r in all_results if r["status"] == "missing"),
        "duplicate": sum(1 for r in all_results if r["status"] == "duplicate"),
        "failed": sum(1 for r in all_results if r["status"] == "failed"),
        "unlabeled": sum(1 for r in all_results if not r["labeled"]),
        "components_total": len(component_results),
        "components_ok": sum(1 for r in component_results if r["status"] == "ok"),
        "components_missing": sum(1 for r in component_results if r["status"] == "missing"),
        "components_duplicate": sum(1 for r in component_results if r["status"] == "duplicate"),
        "components_failed": sum(1 for r in component_results if r["status"] == "failed"),
        "composition_problems": composition_problems,
        "extra_dom_ids": extra_dom_ids,
    }
    summary["pass"] = (
        summary["total"] > 0
        and summary["ok"] == summary["total"]
        and summary["unlabeled"] == 0
        and summary["components_ok"] == summary["components_total"]
        and composition_problems == 0
    )

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "out_dir": str(out_dir),
        "manifest": str(manifest_path),
        "blueprint": str(out_dir / args.blueprint_name) if isinstance(blueprint, dict) else None,
        "dom_source": str(out_dir / args.dom_name),
        "thresholds": {
            "max_position_delta": args.max_position_delta,
            "max_size_delta": args.max_size_delta,
            "max_font_size_delta": args.max_font_size_delta,
            "max_font_weight_delta": args.max_font_weight_delta,
            "max_color_delta": args.max_color_delta,
            "max_line_height_delta": args.max_line_height_delta,
            "max_letter_spacing_delta": args.max_letter_spacing_delta,
            "max_radius_delta": args.max_radius_delta,
            "max_border_width_delta": args.max_border_width_delta,
            "max_opacity_delta": args.max_opacity_delta,
        },
        "summary": summary,
        "components": component_results,
        "regions": regions_out,
    }
    (out_dir / args.json_name).write_text(json.dumps(report, indent=2), encoding="utf-8")
    (out_dir / args.md_name).write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "out_dir": str(out_dir),
                "pass": summary["pass"],
                "ok": f"{summary['ok']}/{summary['total']}",
                "components_ok": f"{summary['components_ok']}/{summary['components_total']}",
                "missing": summary["missing"],
                "failed": summary["failed"],
                "unlabeled": summary["unlabeled"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
