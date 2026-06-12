#!/usr/bin/env python3
"""Gate the six-layer ui-blueprint.json: schema-shape validation plus reconciliation against measurements.

The blueprint is only trusted when every layer can be proven against the lab artifacts:
bounds against measured-primitives.json, colors against the reference pixels, references
against declared ids, datasets against the components they feed. Errors block code
generation (exit code 1); warnings do not.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image

KEBAB_HEAD = "abcdefghijklmnopqrstuvwxyz0123456789"
KEBAB_BODY = KEBAB_HEAD + "-"

REGION_ROLES = {
    "app-shell", "navigation", "topbar", "sidebar", "content", "card", "table",
    "list", "chart", "media", "footer", "modal", "other",
}
REGION_TRACKS = {"component", "island", "approximation"}
RELATION_TYPES = {"row", "column", "grid", "stack", "absolute-fallback"}
RELATION_ALIGNS = {"start", "center", "end", "stretch", "space-between"}
COMPONENT_TYPES = {
    "button", "input", "select", "checkbox", "switch", "tabs", "tab-item", "menu",
    "menu-item", "card", "table", "table-row", "list", "list-item", "badge", "avatar",
    "icon", "image", "text", "heading", "divider", "progress", "chart-container",
    "map-container", "media", "nav-item", "breadcrumb", "pagination", "tooltip-anchor",
    "container", "other",
}
ELEMENT_TYPES = {
    "text", "icon", "image", "control", "list-item", "card", "divider", "badge",
    "chart-mark", "container", "decoration",
}
TYPOGRAPHY_WEIGHTS = {"regular", "medium", "semibold", "bold"}
INTERACTION_TRIGGERS = {
    "click", "hover", "focus", "input", "submit", "tab-switch", "filter-change",
    "select", "toggle", "scroll", "load",
}
INTERACTION_STATES = {"hover", "active", "focus", "disabled", "selected", "loading", "empty", "error"}
INTERACTION_SOURCES = {"project-convention", "project-token", "type-default"}
PLAN_ACTIONS = {"reuse", "extend", "create"}
INTERACTIVE_COMPONENT_TYPES = {"button", "input", "select", "tabs", "checkbox", "switch"}
DATA_SHAPES = {"rows", "items", "series", "keyvalue", "tree", "geo"}
DATA_SOURCES = {"extracted", "approximated"}
# Component types that must be data-driven: a missing data entry means codegen would
# hard-code content nodes (lists/tables) or draw data shapes by hand (charts).
DATA_REQUIRED_COMPONENT_TYPES = {"table", "list", "chart-container"}
DATA_RECOMMENDED_COMPONENT_TYPES = {"map-container"}
ARRAY_DATA_SHAPES = {"rows", "items", "series"}


def load_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return fallback


class IssueLog:
    def __init__(self) -> None:
        self.issues: list[dict[str, str]] = []

    def error(self, layer: str, path: str, message: str) -> None:
        self.issues.append({"level": "error", "layer": layer, "path": path, "message": message})

    def warning(self, layer: str, path: str, message: str) -> None:
        self.issues.append({"level": "warning", "layer": layer, "path": path, "message": message})

    @property
    def errors(self) -> int:
        return sum(1 for issue in self.issues if issue["level"] == "error")

    @property
    def warnings(self) -> int:
        return sum(1 for issue in self.issues if issue["level"] == "warning")


def is_kebab(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) > 0
        and value[0] in KEBAB_HEAD
        and all(ch in KEBAB_BODY for ch in value)
    )


def is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def is_number(value: Any) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool))


def is_hex_color(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 7 or value[0] != "#":
        return False
    return all(ch in "0123456789abcdefABCDEF" for ch in value[1:])


def parse_hex(value: str) -> tuple[int, int, int]:
    return (int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16))


def check_object(log: IssueLog, layer: str, path: str, obj: Any, required: list[str], allowed: set[str] | None) -> bool:
    if not isinstance(obj, dict):
        log.error(layer, path, f"expected an object, got {type(obj).__name__}")
        return False
    for key in required:
        if key not in obj:
            log.error(layer, path, f"missing required field '{key}'")
    if allowed is not None:
        for key in sorted(set(obj) - allowed):
            log.error(layer, path, f"unknown field '{key}' (additionalProperties is false)")
    return True


def check_bounds(log: IssueLog, layer: str, path: str, bounds: Any) -> dict[str, int] | None:
    if not check_object(log, layer, path, bounds, ["x", "y", "width", "height"], {"x", "y", "width", "height"}):
        return None
    ok = True
    for key, minimum in (("x", 0), ("y", 0), ("width", 1), ("height", 1)):
        value = bounds.get(key)
        if key not in bounds:
            ok = False
        elif not is_int(value):
            log.error(layer, path, f"bounds.{key} must be an integer, got {value!r}")
            ok = False
        elif value < minimum:
            log.error(layer, path, f"bounds.{key} must be >= {minimum}, got {value}")
            ok = False
    if not ok:
        return None
    return {"x": bounds["x"], "y": bounds["y"], "width": bounds["width"], "height": bounds["height"]}


def check_enum(log: IssueLog, layer: str, path: str, field: str, value: Any, allowed: set[str], required: bool) -> bool:
    if value is None and not required:
        return True
    if not isinstance(value, str) or value not in allowed:
        log.error(layer, path, f"{field} must be one of {sorted(allowed)}, got {value!r}")
        return False
    return True


def check_kebab(log: IssueLog, layer: str, path: str, field: str, value: Any) -> bool:
    if not is_kebab(value):
        log.error(layer, path, f"{field} must match ^[a-z0-9][a-z0-9-]*$ (lowercase kebab), got {value!r}")
        return False
    return True


def bounds_center(bounds: dict[str, int]) -> tuple[float, float]:
    return (bounds["x"] + bounds["width"] / 2.0, bounds["y"] + bounds["height"] / 2.0)


def point_in_bounds(x: float, y: float, bounds: dict[str, int], tolerance: int = 0) -> bool:
    return (
        bounds["x"] - tolerance <= x <= bounds["x"] + bounds["width"] + tolerance
        and bounds["y"] - tolerance <= y <= bounds["y"] + bounds["height"] + tolerance
    )


def iou(a: dict[str, int], b: dict[str, int]) -> float:
    ax0, ay0, ax1, ay1 = a["x"], a["y"], a["x"] + a["width"], a["y"] + a["height"]
    bx0, by0, bx1, by1 = b["x"], b["y"], b["x"] + b["width"], b["y"] + b["height"]
    inter_w = max(0, min(ax1, bx1) - max(ax0, bx0))
    inter_h = max(0, min(ay1, by1) - max(ay0, by0))
    inter = inter_w * inter_h
    if inter <= 0:
        return 0.0
    union = a["width"] * a["height"] + b["width"] * b["height"] - inter
    return inter / union if union > 0 else 0.0


def boxes_match(blueprint_box: dict[str, int], measured_box: dict[str, int], tolerance: int) -> bool:
    cx, cy = bounds_center(blueprint_box)
    if point_in_bounds(cx, cy, measured_box, tolerance):
        return True
    return iou(blueprint_box, measured_box) >= 0.3


# ---------------------------------------------------------------------------
# Layer A: structural validation (handwritten against ui-blueprint.schema.json)
# ---------------------------------------------------------------------------

def validate_source(log: IssueLog, blueprint: dict[str, Any]) -> tuple[int, int] | None:
    source = blueprint.get("source")
    if not check_object(log, "source", "source", source, ["reference", "width", "height"], None):
        return None
    if "reference" in source and (not isinstance(source["reference"], str) or not source["reference"]):
        log.error("source", "source.reference", f"reference must be a non-empty string, got {source['reference']!r}")
    canvas = []
    for key in ("width", "height"):
        value = source.get(key)
        if key not in source:
            return None
        if not is_int(value) or value < 1:
            log.error("source", f"source.{key}", f"must be an integer >= 1, got {value!r}")
            return None
        canvas.append(value)
    if "background" in source and not isinstance(source["background"], str):
        log.error("source", "source.background", f"background must be a string, got {source['background']!r}")
    return (canvas[0], canvas[1])


def validate_layout(log: IssueLog, blueprint: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    regions: dict[str, dict[str, Any]] = {}
    relations: list[dict[str, Any]] = []
    layout = blueprint.get("layout")
    if not check_object(log, "layout", "layout", layout, ["regions"], {"regions", "relations"}):
        return regions, relations

    raw_regions = layout.get("regions")
    if not isinstance(raw_regions, list) or not raw_regions:
        log.error("layout", "layout.regions", "regions must be a non-empty array")
        raw_regions = []
    for index, region in enumerate(raw_regions):
        path = f"layout.regions[{index}]"
        if not check_object(log, "layout", path, region, ["name", "bounds", "role", "track"],
                            {"name", "bounds", "role", "track", "parent", "notes"}):
            continue
        name = region.get("name")
        if "name" in region and check_kebab(log, "layout", path, "name", name):
            if name in regions:
                log.error("layout", path, f"duplicate region name '{name}'")
        bounds = check_bounds(log, "layout", f"{path}.bounds", region.get("bounds")) if "bounds" in region else None
        if "role" in region:
            check_enum(log, "layout", path, "role", region.get("role"), REGION_ROLES, True)
        if "track" in region:
            check_enum(log, "layout", path, "track", region.get("track"), REGION_TRACKS, True)
        if "parent" in region and not isinstance(region["parent"], str):
            log.error("layout", path, f"parent must be a string, got {region['parent']!r}")
        if "notes" in region and not isinstance(region["notes"], str):
            log.error("layout", path, f"notes must be a string, got {region['notes']!r}")
        if is_kebab(name) and name not in regions:
            regions[name] = {"bounds": bounds, "track": region.get("track"), "path": path}

    for index, region in enumerate(raw_regions):
        if isinstance(region, dict) and isinstance(region.get("parent"), str) and region["parent"] not in regions:
            log.error("layout", f"layout.regions[{index}]", f"parent '{region['parent']}' is not a declared region")

    raw_relations = layout.get("relations")
    if raw_relations is not None and not isinstance(raw_relations, list):
        log.error("layout", "layout.relations", "relations must be an array")
        raw_relations = None
    for index, relation in enumerate(raw_relations or []):
        path = f"layout.relations[{index}]"
        if not check_object(log, "layout", path, relation, ["scope", "type", "items", "confidence"],
                            {"scope", "type", "items", "gap_px", "columns", "align", "confidence", "notes"}):
            continue
        relations.append(relation)
        if "type" in relation:
            check_enum(log, "layout", path, "type", relation.get("type"), RELATION_TYPES, True)
        if "align" in relation:
            check_enum(log, "layout", path, "align", relation.get("align"), RELATION_ALIGNS, False)
        if "gap_px" in relation and (not is_number(relation["gap_px"]) or relation["gap_px"] < 0):
            log.error("layout", path, f"gap_px must be a number >= 0, got {relation['gap_px']!r}")
        if "columns" in relation and (not is_int(relation["columns"]) or relation["columns"] < 1):
            log.error("layout", path, f"columns must be an integer >= 1, got {relation['columns']!r}")
        confidence = relation.get("confidence")
        if "confidence" in relation and (not is_number(confidence) or not 0 <= confidence <= 1):
            log.error("layout", path, f"confidence must be a number in [0, 1], got {confidence!r}")
        items = relation.get("items")
        if "items" in relation and (not isinstance(items, list) or not items
                                    or any(not isinstance(item, str) for item in items)):
            log.error("layout", path, "items must be a non-empty array of strings")
        if relation.get("type") == "absolute-fallback" and not (isinstance(relation.get("notes"), str) and relation["notes"].strip()):
            log.error("layout", path, "type 'absolute-fallback' requires notes explaining why inference was not trusted")
        if (
            is_number(confidence)
            and confidence < 0.6
            and relation.get("type") in RELATION_TYPES
            and relation.get("type") != "absolute-fallback"
        ):
            log.warning("layout", path, f"confidence {confidence} < 0.6 but type is '{relation.get('type')}'; consider absolute-fallback with notes")
    return regions, relations


def validate_components(log: IssueLog, blueprint: dict[str, Any], regions: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    components: dict[str, dict[str, Any]] = {}
    raw = blueprint.get("components")
    if not isinstance(raw, list) or not raw:
        log.error("components", "components", "components must be a non-empty array")
        return components
    for index, component in enumerate(raw):
        path = f"components[{index}]"
        if not check_object(log, "components", path, component, ["id", "region", "type", "bounds"],
                            {"id", "region", "type", "bounds", "content", "maps_to", "elements", "notes"}):
            continue
        component_id = component.get("id")
        if "id" in component and check_kebab(log, "components", path, "id", component_id):
            if component_id in components:
                log.error("components", path, f"duplicate component id '{component_id}'")
        if "type" in component:
            check_enum(log, "components", path, "type", component.get("type"), COMPONENT_TYPES, True)
        bounds = check_bounds(log, "components", f"{path}.bounds", component.get("bounds")) if "bounds" in component else None
        region_name = component.get("region")
        if "region" in component:
            if not isinstance(region_name, str):
                log.error("components", path, f"region must be a string, got {region_name!r}")
            elif region_name not in regions:
                log.error("components", path, f"region '{region_name}' is not declared in layout.regions")
        for field in ("content", "maps_to", "notes"):
            if field in component and not isinstance(component[field], str):
                log.error("components", path, f"{field} must be a string, got {component[field]!r}")

        elements: list[dict[str, Any]] = []
        raw_elements = component.get("elements")
        if raw_elements is not None and not isinstance(raw_elements, list):
            log.error("components", f"{path}.elements", "elements must be an array")
            raw_elements = None
        for element_index, element in enumerate(raw_elements or []):
            element_path = f"{path}.elements[{element_index}]"
            if not check_object(log, "components", element_path, element, ["id", "type", "bounds"],
                                {"id", "type", "bounds", "content", "token_refs"}):
                continue
            if "id" in element and not isinstance(element["id"], str):
                log.error("components", element_path, f"id must be a string, got {element['id']!r}")
            if "type" in element:
                check_enum(log, "components", element_path, "type", element.get("type"), ELEMENT_TYPES, True)
            element_bounds = check_bounds(log, "components", f"{element_path}.bounds", element.get("bounds")) if "bounds" in element else None
            if "content" in element and not isinstance(element["content"], str):
                log.error("components", element_path, f"content must be a string, got {element['content']!r}")
            token_refs = element.get("token_refs")
            if "token_refs" in element and (not isinstance(token_refs, list) or any(not isinstance(t, str) for t in token_refs)):
                log.error("components", element_path, "token_refs must be an array of strings")
            elements.append({"id": element.get("id"), "bounds": element_bounds, "path": element_path, "token_refs": token_refs or []})

        if isinstance(component_id, str) and component_id not in components:
            components[component_id] = {
                "type": component.get("type"),
                "region": region_name if isinstance(region_name, str) else None,
                "bounds": bounds,
                "elements": elements,
                "path": path,
            }
    return components


def validate_tokens(log: IssueLog, blueprint: dict[str, Any]) -> dict[str, Any]:
    tokens = blueprint.get("tokens")
    result: dict[str, Any] = {"colors": [], "typography": [], "names": set()}
    if not check_object(log, "tokens", "tokens", tokens, ["colors", "typography", "spacing"],
                        {"colors", "typography", "spacing", "radius", "shadows", "borders"}):
        return result

    colors = tokens.get("colors")
    if colors is not None and not isinstance(colors, list):
        log.error("tokens", "tokens.colors", "colors must be an array")
        colors = None
    for index, color in enumerate(colors or []):
        path = f"tokens.colors[{index}]"
        if not check_object(log, "tokens", path, color, ["name", "value"],
                            {"name", "value", "usage", "sampled_at", "maps_to"}):
            continue
        if "name" in color and check_kebab(log, "tokens", path, "name", color.get("name")):
            result["names"].add(color["name"])
        if "value" in color and not is_hex_color(color.get("value")):
            log.error("tokens", path, f"value must match ^#[0-9a-fA-F]{{6}}$, got {color.get('value')!r}")
        sampled_at = color.get("sampled_at")
        if "sampled_at" in color and (
            not isinstance(sampled_at, list) or len(sampled_at) != 2 or any(not is_int(v) for v in sampled_at)
        ):
            log.error("tokens", path, f"sampled_at must be [x, y] integers, got {sampled_at!r}")
            sampled_at = None
        for field in ("usage", "maps_to"):
            if field in color and not isinstance(color[field], str):
                log.error("tokens", path, f"{field} must be a string, got {color[field]!r}")
        result["colors"].append({"name": color.get("name"), "value": color.get("value"),
                                 "sampled_at": sampled_at if isinstance(sampled_at, list) else None, "path": path})

    typography = tokens.get("typography")
    if typography is not None and not isinstance(typography, list):
        log.error("tokens", "tokens.typography", "typography must be an array")
        typography = None
    for index, typo in enumerate(typography or []):
        path = f"tokens.typography[{index}]"
        if not check_object(log, "tokens", path, typo, ["name", "size_px"],
                            {"name", "size_px", "weight", "line_height_px", "measured_from", "maps_to"}):
            continue
        if "name" in typo and check_kebab(log, "tokens", path, "name", typo.get("name")):
            result["names"].add(typo["name"])
        size_px = typo.get("size_px")
        if "size_px" in typo and (not is_number(size_px) or size_px < 4):
            log.error("tokens", path, f"size_px must be a number >= 4, got {size_px!r}")
        if "weight" in typo:
            check_enum(log, "tokens", path, "weight", typo.get("weight"), TYPOGRAPHY_WEIGHTS, False)
        if "line_height_px" in typo and not is_number(typo["line_height_px"]):
            log.error("tokens", path, f"line_height_px must be a number, got {typo['line_height_px']!r}")
        for field in ("measured_from", "maps_to"):
            if field in typo and not isinstance(typo[field], str):
                log.error("tokens", path, f"{field} must be a string, got {typo[field]!r}")
        result["typography"].append({"name": typo.get("name"), "size_px": size_px,
                                     "measured_from": typo.get("measured_from"), "path": path})

    simple_groups = {
        "spacing": (["name", "px"], {"name", "px", "maps_to"}, ("px", 0)),
        "radius": (["name", "px"], {"name", "px", "maps_to"}, ("px", None)),
        "shadows": (["name", "value"], {"name", "value", "maps_to"}, None),
        "borders": (["name", "value"], {"name", "value", "maps_to"}, None),
    }
    for group, (required, allowed, numeric) in simple_groups.items():
        entries = tokens.get(group)
        if entries is None:
            continue
        if not isinstance(entries, list):
            log.error("tokens", f"tokens.{group}", f"{group} must be an array")
            continue
        for index, entry in enumerate(entries):
            path = f"tokens.{group}[{index}]"
            if not check_object(log, "tokens", path, entry, required, allowed):
                continue
            if "name" in entry:
                if not isinstance(entry["name"], str):
                    log.error("tokens", path, f"name must be a string, got {entry['name']!r}")
                else:
                    result["names"].add(entry["name"])
            if numeric is not None and numeric[0] in entry:
                value = entry[numeric[0]]
                if not is_number(value) or (numeric[1] is not None and value < numeric[1]):
                    log.error("tokens", path, f"{numeric[0]} must be a number{f' >= {numeric[1]}' if numeric[1] is not None else ''}, got {value!r}")
            if "value" in required and "value" in entry and not isinstance(entry["value"], str):
                log.error("tokens", path, f"value must be a string, got {entry['value']!r}")
            if "maps_to" in entry and not isinstance(entry["maps_to"], str):
                log.error("tokens", path, f"maps_to must be a string, got {entry['maps_to']!r}")
    return result


def validate_interactions(log: IssueLog, blueprint: dict[str, Any], components: dict[str, dict[str, Any]]) -> None:
    interactions = blueprint.get("interactions")
    if not isinstance(interactions, list):
        log.error("interactions", "interactions", "interactions must be an array")
        interactions = []
    covered_targets: set[str] = set()
    for index, interaction in enumerate(interactions):
        path = f"interactions[{index}]"
        if not check_object(log, "interactions", path, interaction, ["target", "trigger", "behavior", "source"],
                            {"target", "trigger", "behavior", "states", "source", "notes"}):
            continue
        target = interaction.get("target")
        if "target" in interaction:
            if not isinstance(target, str):
                log.error("interactions", path, f"target must be a string, got {target!r}")
            elif target not in components:
                log.error("interactions", path, f"target '{target}' is not a declared component id")
            else:
                covered_targets.add(target)
        if "trigger" in interaction:
            check_enum(log, "interactions", path, "trigger", interaction.get("trigger"), INTERACTION_TRIGGERS, True)
        if "source" in interaction:
            check_enum(log, "interactions", path, "source", interaction.get("source"), INTERACTION_SOURCES, True)
        if "behavior" in interaction and (not isinstance(interaction["behavior"], str) or not interaction["behavior"].strip()):
            log.error("interactions", path, f"behavior must be a non-empty string, got {interaction['behavior']!r}")
        states = interaction.get("states")
        if "states" in interaction:
            if not isinstance(states, list):
                log.error("interactions", path, f"states must be an array, got {states!r}")
            else:
                for state in states:
                    if state not in INTERACTION_STATES:
                        log.error("interactions", path, f"state must be one of {sorted(INTERACTION_STATES)}, got {state!r}")
        if "notes" in interaction and not isinstance(interaction["notes"], str):
            log.error("interactions", path, f"notes must be a string, got {interaction['notes']!r}")

    missing = sorted(
        component_id
        for component_id, component in components.items()
        if component.get("type") in INTERACTIVE_COMPONENT_TYPES and component_id not in covered_targets
    )
    if missing:
        log.error(
            "interactions",
            "interactions",
            f"interactive components ({'/'.join(sorted(INTERACTIVE_COMPONENT_TYPES))}) without any interaction: {missing}",
        )


def validate_data(log: IssueLog, blueprint: dict[str, Any], components: dict[str, dict[str, Any]]) -> None:
    entries = blueprint.get("data")
    if entries is None:
        entries = []
    elif not isinstance(entries, list):
        log.error("data", "data", "data must be an array")
        entries = []
    covered: set[str] = set()
    for index, entry in enumerate(entries):
        path = f"data[{index}]"
        if not check_object(log, "data", path, entry, ["component_id", "shape", "mock_data", "binding", "source"],
                            {"component_id", "shape", "fields", "mock_data", "binding", "source", "library", "notes"}):
            continue
        component_id = entry.get("component_id")
        if "component_id" in entry:
            if not isinstance(component_id, str):
                log.error("data", path, f"component_id must be a string, got {component_id!r}")
            elif component_id not in components:
                log.error("data", path, f"component_id '{component_id}' is not a declared component")
            else:
                covered.add(component_id)
        shape = entry.get("shape")
        if "shape" in entry:
            check_enum(log, "data", path, "shape", shape, DATA_SHAPES, True)
        if "source" in entry:
            check_enum(log, "data", path, "source", entry.get("source"), DATA_SOURCES, True)
        if "binding" in entry and (not isinstance(entry["binding"], str) or not entry["binding"].strip()):
            log.error("data", path, f"binding must be a non-empty string, got {entry['binding']!r}")
        fields = entry.get("fields")
        if "fields" in entry and (not isinstance(fields, list) or any(not isinstance(f, str) for f in fields)):
            log.error("data", path, "fields must be an array of strings")
        for field in ("library", "notes"):
            if field in entry and not isinstance(entry[field], str):
                log.error("data", path, f"{field} must be a string, got {entry[field]!r}")
        mock_data = entry.get("mock_data")
        if "mock_data" in entry:
            if shape in ARRAY_DATA_SHAPES:
                if not isinstance(mock_data, list) or not mock_data:
                    log.error("data", path, f"mock_data for shape '{shape}' must be a non-empty array")
                elif isinstance(fields, list) and fields:
                    for row_index, row in enumerate(mock_data):
                        if isinstance(row, dict) and not set(fields) & set(row):
                            log.error("data", path,
                                      f"mock_data[{row_index}] shares no keys with declared fields {fields}")
                            break
            elif shape in DATA_SHAPES and not isinstance(mock_data, (list, dict)):
                log.error("data", path, f"mock_data must be an array or object, got {type(mock_data).__name__}")
        component = components.get(component_id) if isinstance(component_id, str) else None
        if component and component.get("type") == "chart-container" and not (isinstance(entry.get("library"), str) and entry["library"].strip()):
            log.error("data", path,
                      f"chart data for '{component_id}' must declare its rendering library (e.g. 'echarts'); "
                      "charts are rendered by a third-party library fed mock data, never drawn as bespoke SVG/CSS")

    for component_id, component in sorted(components.items()):
        ctype = component.get("type")
        if ctype in DATA_REQUIRED_COMPONENT_TYPES and component_id not in covered:
            log.error("data", component["path"],
                      f"component '{component_id}' (type '{ctype}') has no data entry: data-driven components must "
                      "render from mock_data (template + array, or library option), not hard-coded content nodes")
        elif ctype in DATA_RECOMMENDED_COMPONENT_TYPES and component_id not in covered:
            log.warning("data", component["path"],
                        f"component '{component_id}' (type '{ctype}') has no data entry; declare markers/center as "
                        "mock data if the map shows data, or note why it is static")


def validate_implementation(log: IssueLog, blueprint: dict[str, Any], components: dict[str, dict[str, Any]]) -> None:
    implementation = blueprint.get("implementation")
    if not check_object(log, "implementation", "implementation", implementation, ["framework", "styling", "plan"],
                        {"framework", "styling", "ui_library", "final_dir", "plan"}):
        return
    for field in ("framework", "styling"):
        value = implementation.get(field)
        if field in implementation and (not isinstance(value, str) or not value.strip()):
            log.error("implementation", f"implementation.{field}", f"{field} must be a non-empty string, got {value!r}")
    for field in ("ui_library", "final_dir"):
        if field in implementation and not isinstance(implementation[field], str):
            log.error("implementation", f"implementation.{field}", f"{field} must be a string, got {implementation[field]!r}")
    final_dir = implementation.get("final_dir")
    if not (isinstance(final_dir, str) and final_dir.strip()):
        log.warning("implementation", "implementation.final_dir",
                    "final_dir is missing or empty; final components have no declared destination")

    plan = implementation.get("plan")
    if plan is not None and (not isinstance(plan, list) or not plan):
        log.error("implementation", "implementation.plan", "plan must be a non-empty array")
        plan = None
    orders: dict[int, list[str]] = {}
    for index, step in enumerate(plan or []):
        path = f"implementation.plan[{index}]"
        if not check_object(log, "implementation", path, step, ["component_id", "action", "order"],
                            {"component_id", "action", "target", "order", "acceptance"}):
            continue
        component_id = step.get("component_id")
        if "component_id" in step:
            if not isinstance(component_id, str):
                log.error("implementation", path, f"component_id must be a string, got {component_id!r}")
            elif component_id not in components:
                log.error("implementation", path, f"component_id '{component_id}' is not a declared component")
        if "action" in step:
            check_enum(log, "implementation", path, "action", step.get("action"), PLAN_ACTIONS, True)
        order = step.get("order")
        if "order" in step:
            if not is_int(order) or order < 1:
                log.error("implementation", path, f"order must be an integer >= 1, got {order!r}")
            else:
                orders.setdefault(order, []).append(path)
        for field in ("target", "acceptance"):
            if field in step and not isinstance(step[field], str):
                log.error("implementation", path, f"{field} must be a string, got {step[field]!r}")
    for order, paths in sorted(orders.items()):
        if len(paths) > 1:
            log.error("implementation", "implementation.plan", f"order {order} is used by {len(paths)} steps ({', '.join(paths)}); orders must be unique")


def validate_relation_references(
    log: IssueLog,
    relations: list[dict[str, Any]],
    regions: dict[str, dict[str, Any]],
    components: dict[str, dict[str, Any]],
) -> None:
    known_ids = set(regions) | set(components)
    for index, relation in enumerate(relations):
        path = f"layout.relations[{index}]"
        scope = relation.get("scope")
        if isinstance(scope, str) and scope not in regions:
            log.error("layout", path, f"scope '{scope}' is not a declared region")
        elif not isinstance(scope, str):
            log.error("layout", path, f"scope must be a string, got {scope!r}")
        items = relation.get("items")
        if isinstance(items, list):
            unknown = [item for item in items if isinstance(item, str) and item not in known_ids]
            if unknown:
                log.error("layout", path, f"items reference unknown component ids / region names: {unknown}")


def check_component_region_coverage(log: IssueLog, regions: dict[str, dict[str, Any]], components: dict[str, dict[str, Any]]) -> None:
    covered = {component["region"] for component in components.values() if component.get("region")}
    for name, region in regions.items():
        if region.get("track") == "component" and name not in covered:
            log.warning("layout", region["path"], f"track 'component' region '{name}' has no component declared in it")


# ---------------------------------------------------------------------------
# Layer B: reconciliation against measurements
# ---------------------------------------------------------------------------

def reconcile_geometry(
    log: IssueLog,
    canvas: tuple[int, int] | None,
    regions: dict[str, dict[str, Any]],
    components: dict[str, dict[str, Any]],
    tolerance: int,
) -> None:
    if canvas:
        width, height = canvas
        for name, region in regions.items():
            bounds = region.get("bounds")
            if not bounds:
                continue
            if bounds["x"] + bounds["width"] > width or bounds["y"] + bounds["height"] > height:
                log.error("layout", region["path"],
                          f"region '{name}' bounds {bounds} exceed the {width}x{height} canvas")
    for component_id, component in components.items():
        bounds = component.get("bounds")
        region = regions.get(component.get("region") or "")
        if not bounds or not region or not region.get("bounds"):
            continue
        cx, cy = bounds_center(bounds)
        if not point_in_bounds(cx, cy, region["bounds"], tolerance):
            log.error("components", component["path"],
                      f"component '{component_id}' center ({cx:.0f}, {cy:.0f}) is outside its region "
                      f"'{component['region']}' bounds {region['bounds']} (tolerance {tolerance}px)")


def load_measured_boxes(measured: Any) -> dict[str, list[dict[str, Any]]]:
    boxes: dict[str, list[dict[str, Any]]] = {}
    if not isinstance(measured, dict):
        return boxes
    for region in measured.get("regions") or []:
        if not isinstance(region, dict) or not region.get("name"):
            continue
        entries = []
        for primitive in region.get("primitives") or []:
            if not isinstance(primitive, dict):
                continue
            full = primitive.get("full_bounds")
            if not isinstance(full, dict) or any(key not in full for key in ("x", "y", "width", "height")):
                continue
            entries.append({"bounds": {k: int(full[k]) for k in ("x", "y", "width", "height")},
                            "kind": str(primitive.get("kind") or "primitive")})
        boxes[str(region["name"])] = entries
    return boxes


def reconcile_elements(
    log: IssueLog,
    components: dict[str, dict[str, Any]],
    measured_boxes: dict[str, list[dict[str, Any]]],
    tolerance: int,
) -> dict[str, Any]:
    all_boxes = [entry for entries in measured_boxes.values() for entry in entries]
    matched_by_region: dict[str, set[int]] = {name: set() for name in measured_boxes}
    checked = 0
    unbacked = 0
    for component_id, component in components.items():
        for element in component["elements"]:
            bounds = element.get("bounds")
            if not bounds:
                continue
            checked += 1
            backed = False
            for region_name, entries in measured_boxes.items():
                for box_index, entry in enumerate(entries):
                    if boxes_match(bounds, entry["bounds"], tolerance):
                        matched_by_region[region_name].add(box_index)
                        backed = True
            if not backed:
                unbacked += 1
                log.error("components", element["path"],
                          f"element '{element.get('id')}' bounds {bounds} not backed by measurement: "
                          f"no measured box contains its center (tolerance {tolerance}px) or reaches IoU >= 0.3 "
                          f"({len(all_boxes)} measured boxes checked)")
    coverage: dict[str, Any] = {}
    for region_name, entries in measured_boxes.items():
        total = len(entries)
        if total == 0:
            continue
        matched = len(matched_by_region[region_name])
        ratio = matched / total
        coverage[region_name] = {"measured": total, "covered": matched, "ratio": round(ratio, 4)}
        if ratio < 0.6:
            log.warning("components", f"measured-primitives:{region_name}",
                        f"measured boxes uncovered by blueprint in region '{region_name}': "
                        f"{total - matched} of {total} measured boxes have no matching blueprint element "
                        f"(coverage {ratio * 100:.1f}% < 60%)")
    return {"elements_checked": checked, "elements_unbacked": unbacked, "region_coverage": coverage}


def reconcile_colors(
    log: IssueLog,
    colors: list[dict[str, Any]],
    reference: Image.Image | None,
    tolerance: int,
) -> None:
    for color in colors:
        if not is_hex_color(color.get("value")):
            continue
        sampled_at = color.get("sampled_at")
        if not sampled_at:
            log.warning("tokens", color["path"],
                        f"unverifiable color '{color.get('name')}': no sampled_at coordinate to check against the reference")
            continue
        if reference is None:
            log.error("tokens", color["path"],
                      f"color '{color.get('name')}' declares sampled_at {sampled_at} but the reference image is missing; cannot verify")
            continue
        x, y = sampled_at
        width, height = reference.size
        if not (0 <= x < width and 0 <= y < height):
            log.error("tokens", color["path"],
                      f"color '{color.get('name')}' sampled_at {sampled_at} is outside the {width}x{height} reference image")
            continue
        expected = parse_hex(color["value"])
        actual = reference.getpixel((x, y))[:3]
        deltas = [abs(a - e) for a, e in zip(actual, expected)]
        if max(deltas) > tolerance:
            actual_hex = "#{:02x}{:02x}{:02x}".format(*actual)
            log.error("tokens", color["path"],
                      f"color '{color.get('name')}' {color['value']} does not match the reference at {sampled_at}: "
                      f"sampled {actual_hex}, per-channel delta {deltas} exceeds {tolerance}")


def reconcile_typography(
    log: IssueLog,
    typography: list[dict[str, Any]],
    measured_boxes: dict[str, list[dict[str, Any]]],
) -> None:
    if not measured_boxes:
        return
    text_heights = sorted(
        entry["bounds"]["height"]
        for entries in measured_boxes.values()
        for entry in entries
        if entry["kind"] == "text-line"
    )
    for typo in typography:
        if not typo.get("measured_from") or not is_number(typo.get("size_px")):
            continue
        size = float(typo["size_px"])
        if not any(abs(height - size) <= 4 for height in text_heights):
            log.warning("tokens", typo["path"],
                        f"typography '{typo.get('name')}' size_px {size} (measured_from '{typo['measured_from']}') has no "
                        f"text-line measured box with height within +-4px ({len(text_heights)} text-line boxes available)")


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

LAYER_ORDER = ["root", "source", "layout", "components", "tokens", "data", "interactions", "implementation"]


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Blueprint Validation",
        "",
        f"Generated: `{report['generated_at']}`",
        f"Out dir: `{report['out_dir']}`",
        f"Blueprint: `{report['blueprint']}`",
        f"Schema: `{report['schema']}`",
        f"Result: `{'PASS' if summary['pass'] else 'FAIL'}`",
        "",
        f"- errors: {summary['errors']} | warnings: {summary['warnings']}",
        f"- thresholds: bounds tolerance `{report['thresholds']['bounds_tolerance_px']}px`, "
        f"color tolerance `{report['thresholds']['color_tolerance']}` per RGB channel",
        f"- measured primitives: `{report['measured_primitives'] or 'not found (bounds reconciliation skipped)'}`",
        "",
        "| Layer | Errors | Warnings |",
        "| --- | ---: | ---: |",
    ]
    by_layer: dict[str, list[dict[str, str]]] = {}
    for issue in report["issues"]:
        by_layer.setdefault(issue["layer"], []).append(issue)
    layers = [layer for layer in LAYER_ORDER if layer in by_layer]
    layers += sorted(set(by_layer) - set(LAYER_ORDER))
    for layer in layers:
        issues = by_layer[layer]
        errors = sum(1 for issue in issues if issue["level"] == "error")
        lines.append(f"| `{layer}` | {errors} | {len(issues) - errors} |")
    lines.append("")
    for layer in layers:
        lines.append(f"## `{layer}`")
        lines.append("")
        for issue in by_layer[layer]:
            lines.append(f"- **{issue['level']}** `{issue['path']}`: {issue['message']}")
        lines.append("")
    if not layers:
        lines += ["No issues found: the blueprint is structurally valid and backed by measurements.", ""]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True, help="Pixel Twin Lab output directory")
    parser.add_argument("--blueprint-name", default="ui-blueprint.json", help="Blueprint filename inside out-dir")
    parser.add_argument(
        "--schema",
        default=str(Path(__file__).resolve().parent.parent / "schemas" / "ui-blueprint.schema.json"),
        help="Schema path, recorded in the report for reference",
    )
    parser.add_argument("--bounds-tolerance", type=int, default=8, help="Px tolerance for bounds reconciliation")
    parser.add_argument("--color-tolerance", type=int, default=12, help="Max per-channel RGB delta for sampled colors")
    parser.add_argument("--json-name", default="blueprint-validation.json")
    parser.add_argument("--md-name", default="blueprint-validation.md")
    args = parser.parse_args()

    out_dir = Path(args.out_dir).expanduser().resolve()
    blueprint_path = out_dir / args.blueprint_name
    if not blueprint_path.exists():
        raise SystemExit(f"Blueprint not found: {blueprint_path}. Author ui-blueprint.json first.")
    try:
        blueprint = json.loads(blueprint_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Blueprint is not valid JSON: {blueprint_path} ({exc})")

    log = IssueLog()
    if not isinstance(blueprint, dict):
        log.error("root", "$", f"blueprint must be a JSON object, got {type(blueprint).__name__}")
        blueprint = {}

    # --- Layer A: structure ---
    required_top = ["version", "source", "layout", "components", "tokens", "interactions", "implementation"]
    allowed_top = set(required_top) | {"generated_at", "data"}
    for key in required_top:
        if key not in blueprint:
            log.error("root", "$", f"missing required field '{key}'")
    for key in sorted(set(blueprint) - allowed_top):
        log.error("root", "$", f"unknown field '{key}' (additionalProperties is false)")
    if "version" in blueprint and blueprint["version"] != 1:
        log.error("root", "version", f"version must be the constant 1, got {blueprint['version']!r}")
    if "generated_at" in blueprint and not isinstance(blueprint["generated_at"], str):
        log.error("root", "generated_at", f"generated_at must be a string, got {blueprint['generated_at']!r}")

    canvas = validate_source(log, blueprint) if "source" in blueprint else None
    regions, relations = validate_layout(log, blueprint) if "layout" in blueprint else ({}, [])
    components = validate_components(log, blueprint, regions) if "components" in blueprint else {}
    tokens = validate_tokens(log, blueprint) if "tokens" in blueprint else {"colors": [], "typography": [], "names": set()}
    validate_data(log, blueprint, components)
    if "interactions" in blueprint:
        validate_interactions(log, blueprint, components)
    if "implementation" in blueprint:
        validate_implementation(log, blueprint, components)
    validate_relation_references(log, relations, regions, components)
    check_component_region_coverage(log, regions, components)

    # --- Layer B: reconciliation against measurements ---
    reconcile_geometry(log, canvas, regions, components, args.bounds_tolerance)

    reference_path = out_dir / "assets" / "reference.png"
    if not reference_path.exists():
        source = blueprint.get("source")
        declared = source.get("reference") if isinstance(source, dict) else None
        if isinstance(declared, str) and declared:
            candidate = Path(declared)
            candidate = candidate if candidate.is_absolute() else out_dir / candidate
            if candidate.exists():
                reference_path = candidate
    reference: Image.Image | None = None
    if reference_path.exists():
        reference = Image.open(reference_path).convert("RGB")
        if canvas and reference.size != canvas:
            log.warning("source", "source", f"declared canvas {canvas[0]}x{canvas[1]} does not match the "
                                            f"reference image {reference.size[0]}x{reference.size[1]}")
    else:
        log.warning("source", "source.reference", f"reference image not found ({reference_path}); color sampling limited")

    measured_path = out_dir / "measured-primitives.json"
    measured_boxes = load_measured_boxes(load_json(measured_path, None)) if measured_path.exists() else {}
    reconciliation: dict[str, Any] = {}
    if measured_boxes:
        reconciliation = reconcile_elements(log, components, measured_boxes, args.bounds_tolerance)
    elif any(component["elements"] for component in components.values()):
        log.warning("components", "measured-primitives",
                    f"measured-primitives.json not found in {out_dir}; element bounds are unbacked by measurement "
                    "(run measure_primitives.py)")
    reconcile_colors(log, tokens["colors"], reference, args.color_tolerance)
    reconcile_typography(log, tokens["typography"], measured_boxes)

    summary = {"errors": log.errors, "warnings": log.warnings, "pass": log.errors == 0}
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "out_dir": str(out_dir),
        "blueprint": str(blueprint_path),
        "schema": str(Path(args.schema).expanduser()),
        "reference": str(reference_path) if reference is not None else None,
        "measured_primitives": str(measured_path) if measured_boxes else None,
        "thresholds": {"bounds_tolerance_px": args.bounds_tolerance, "color_tolerance": args.color_tolerance},
        "reconciliation": reconciliation,
        "summary": summary,
        "issues": log.issues,
    }
    (out_dir / args.json_name).write_text(json.dumps(report, indent=2), encoding="utf-8")
    (out_dir / args.md_name).write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "out_dir": str(out_dir),
                "blueprint": str(blueprint_path),
                "pass": summary["pass"],
                "errors": summary["errors"],
                "warnings": summary["warnings"],
                "json": str(out_dir / args.json_name),
                "markdown": str(out_dir / args.md_name),
            },
            indent=2,
        )
    )
    if summary["errors"] > 0:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
