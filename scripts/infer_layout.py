#!/usr/bin/env python3
"""Infer flow layout relations (row/column/grid/stack) from absolute element boxes."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

ROW_OVERLAP_RATIO = 0.5
COLUMN_OVERLAP_RATIO = 0.5
GAP_TOLERANCE = 4.0
COLUMN_X_TOLERANCE = 4.0
ALIGN_TOLERANCE = 3.0
STACK_CONFIDENCE = 0.5


def load_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return fallback


def load_elements(out_dir: Path, blueprint_name: str, measured_name: str) -> tuple[str, dict[str, list[dict[str, Any]]], bool]:
    """Return (source path, region -> elements with id/bounds, ids_are_placeholders)."""
    blueprint_path = out_dir / blueprint_name
    blueprint = load_json(blueprint_path, None)
    if isinstance(blueprint, dict) and isinstance(blueprint.get("components"), list):
        regions: dict[str, list[dict[str, Any]]] = {}
        for component in blueprint["components"]:
            if not (isinstance(component, dict) and component.get("id") and component.get("bounds")):
                continue
            region = str(component.get("region", "unknown"))
            regions.setdefault(region, []).append({"id": component["id"], "bounds": component["bounds"]})
        return str(blueprint_path), regions, False

    measured_path = out_dir / measured_name
    measured = load_json(measured_path, None)
    if isinstance(measured, dict) and isinstance(measured.get("regions"), list):
        regions = {}
        for region in measured["regions"]:
            name = str(region.get("name", "unknown"))
            elements = []
            for index, box in enumerate(region.get("primitives", []), start=1):
                if box.get("full_bounds"):
                    elements.append({"id": f"{name}-p{index:02d}", "bounds": box["full_bounds"]})
            regions[name] = elements
        return str(measured_path), regions, True

    raise SystemExit(f"Neither {blueprint_path} nor {measured_path} provided usable elements.")


def overlap_ratio(a0: float, a1: float, b0: float, b1: float) -> float:
    overlap = min(a1, b1) - max(a0, b0)
    shortest = min(a1 - a0, b1 - b0)
    if shortest <= 0:
        return 0.0
    return overlap / shortest


def vertical_overlap(a: dict[str, Any], b: dict[str, Any]) -> float:
    return overlap_ratio(a["y"], a["y"] + a["height"], b["y"], b["y"] + b["height"])


def horizontal_overlap(a: dict[str, Any], b: dict[str, Any]) -> float:
    return overlap_ratio(a["x"], a["x"] + a["width"], b["x"], b["x"] + b["width"])


def group_rows(elements: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Transitively cluster elements whose vertical overlap is >= ROW_OVERLAP_RATIO."""
    rows: list[list[dict[str, Any]]] = []
    for element in sorted(elements, key=lambda e: e["bounds"]["y"]):
        placed = False
        for row in rows:
            if any(vertical_overlap(element["bounds"], member["bounds"]) >= ROW_OVERLAP_RATIO for member in row):
                row.append(element)
                placed = True
                break
        if not placed:
            rows.append([element])
    for row in rows:
        row.sort(key=lambda e: e["bounds"]["x"])
    rows.sort(key=lambda r: min(e["bounds"]["y"] for e in r))
    return rows


def horizontal_gaps(row: list[dict[str, Any]]) -> list[float]:
    gaps = []
    for left, right in zip(row, row[1:]):
        gaps.append(float(right["bounds"]["x"] - (left["bounds"]["x"] + left["bounds"]["width"])))
    return gaps


def vertical_gaps(rows: list[list[dict[str, Any]]]) -> list[float]:
    gaps = []
    for above, below in zip(rows, rows[1:]):
        bottom = max(e["bounds"]["y"] + e["bounds"]["height"] for e in above)
        top = min(e["bounds"]["y"] for e in below)
        gaps.append(float(top - bottom))
    return gaps


def confidence_from_error(error: float, tolerance: float) -> float:
    """error 0 -> 1.0; error == tolerance -> 0.5; beyond -> lower, floored at 0."""
    if tolerance <= 0:
        return 1.0 if error <= 0 else 0.0
    return round(max(0.0, 1.0 - 0.5 * (error / tolerance)), 2)


def detect_align(row: list[dict[str, Any]]) -> str:
    tops = [e["bounds"]["y"] for e in row]
    bottoms = [e["bounds"]["y"] + e["bounds"]["height"] for e in row]
    centers = [e["bounds"]["y"] + e["bounds"]["height"] / 2 for e in row]
    if max(centers) - min(centers) <= ALIGN_TOLERANCE:
        return "center"
    if max(tops) - min(tops) <= ALIGN_TOLERANCE:
        return "start"
    if max(bottoms) - min(bottoms) <= ALIGN_TOLERANCE:
        return "end"
    return "stretch"


def detect_column_align(column: list[dict[str, Any]]) -> str:
    lefts = [e["bounds"]["x"] for e in column]
    rights = [e["bounds"]["x"] + e["bounds"]["width"] for e in column]
    centers = [e["bounds"]["x"] + e["bounds"]["width"] / 2 for e in column]
    if max(centers) - min(centers) <= ALIGN_TOLERANCE:
        return "center"
    if max(lefts) - min(lefts) <= ALIGN_TOLERANCE:
        return "start"
    if max(rights) - min(rights) <= ALIGN_TOLERANCE:
        return "end"
    return "stretch"


def row_relation(scope: str, row: list[dict[str, Any]], min_confidence: float) -> dict[str, Any]:
    gaps = horizontal_gaps(row)
    spread = max(gaps) - min(gaps)
    align = detect_align(row)
    centers = [e["bounds"]["y"] + e["bounds"]["height"] / 2 for e in row]
    align_error = max(centers) - min(centers) if align == "stretch" else 0.0
    error = max(spread / GAP_TOLERANCE, align_error / ALIGN_TOLERANCE)
    confidence = confidence_from_error(error * GAP_TOLERANCE, GAP_TOLERANCE)
    items = [e["id"] for e in row]
    if confidence < min_confidence:
        return {
            "scope": scope,
            "type": "absolute-fallback",
            "items": items,
            "confidence": confidence,
            "notes": f"irregular gaps: {[round(g) for g in gaps]}",
        }
    return {
        "scope": scope,
        "type": "row",
        "items": items,
        "gap_px": float(np.median(gaps)),
        "align": align,
        "confidence": confidence,
    }


def column_relation(scope: str, column: list[dict[str, Any]], min_confidence: float) -> dict[str, Any]:
    gaps = []
    ordered = sorted(column, key=lambda e: e["bounds"]["y"])
    for above, below in zip(ordered, ordered[1:]):
        gaps.append(float(below["bounds"]["y"] - (above["bounds"]["y"] + above["bounds"]["height"])))
    spread = max(gaps) - min(gaps)
    confidence = confidence_from_error(spread, GAP_TOLERANCE)
    items = [e["id"] for e in ordered]
    if confidence < min_confidence:
        return {
            "scope": scope,
            "type": "absolute-fallback",
            "items": items,
            "confidence": confidence,
            "notes": f"irregular vertical gaps: {[round(g) for g in gaps]}",
        }
    return {
        "scope": scope,
        "type": "column",
        "items": items,
        "gap_px": float(np.median(gaps)),
        "align": detect_column_align(ordered),
        "confidence": confidence,
    }


def grid_relation(scope: str, rows: list[list[dict[str, Any]]], min_confidence: float) -> dict[str, Any] | None:
    columns = len(rows[0])
    if columns < 2 or any(len(row) != columns for row in rows):
        return None
    column_spreads = []
    for col in range(columns):
        xs = [row[col]["bounds"]["x"] for row in rows]
        column_spreads.append(max(xs) - min(xs))
    if max(column_spreads) > COLUMN_X_TOLERANCE:
        return None
    row_gaps = vertical_gaps(rows)
    gap_spread = max(row_gaps) - min(row_gaps) if len(row_gaps) > 1 else 0.0
    error = max(max(column_spreads), gap_spread)
    confidence = confidence_from_error(error, GAP_TOLERANCE)
    items = [e["id"] for row in rows for e in row]
    if confidence < min_confidence:
        return {
            "scope": scope,
            "type": "absolute-fallback",
            "items": items,
            "confidence": confidence,
            "notes": f"grid column x spread {max(column_spreads):.0f}px / row gap spread {gap_spread:.0f}px exceed tolerance",
        }
    return {
        "scope": scope,
        "type": "grid",
        "items": items,
        "gap_px": float(np.median(row_gaps)),
        "columns": columns,
        "confidence": confidence,
    }


def stack_relation(scope: str, elements: list[dict[str, Any]], reason: str, min_confidence: float) -> dict[str, Any]:
    ordered = sorted(elements, key=lambda e: (e["bounds"]["y"], e["bounds"]["x"]))
    relation_type = "stack" if STACK_CONFIDENCE >= min_confidence else "absolute-fallback"
    return {
        "scope": scope,
        "type": relation_type,
        "items": [e["id"] for e in ordered],
        "confidence": STACK_CONFIDENCE,
        "notes": reason,
    }


def infer_region(scope: str, elements: list[dict[str, Any]], min_confidence: float) -> list[dict[str, Any]]:
    if not elements:
        return []
    if len(elements) == 1:
        return [stack_relation(scope, elements, "single element in region", min_confidence)]
    rows = group_rows(elements)

    if len(rows) == 1:
        return [row_relation(scope, rows[0], min_confidence)]

    if len(rows) >= 2:
        grid = grid_relation(scope, rows, min_confidence)
        if grid is not None:
            return [grid]

    if all(len(row) == 1 for row in rows):
        singles = [row[0] for row in rows]
        chained = all(
            horizontal_overlap(above["bounds"], below["bounds"]) >= COLUMN_OVERLAP_RATIO
            for above, below in zip(singles, singles[1:])
        )
        if chained:
            return [column_relation(scope, singles, min_confidence)]

    relations: list[dict[str, Any]] = []
    leftovers: list[dict[str, Any]] = []
    for row in rows:
        if len(row) >= 2:
            relations.append(row_relation(scope, row, min_confidence))
        else:
            leftovers.append(row[0])
    if len(leftovers) >= 2 and all(
        horizontal_overlap(above["bounds"], below["bounds"]) >= COLUMN_OVERLAP_RATIO
        for above, below in zip(leftovers, leftovers[1:])
    ):
        relations.append(column_relation(scope, leftovers, min_confidence))
    elif leftovers:
        relations.append(stack_relation(scope, leftovers, "elements not captured by row/column/grid grouping", min_confidence))
    return relations


def render_markdown(report: dict[str, Any], placeholders: bool) -> str:
    lines = [
        "# Layout Relations",
        "",
        f"Generated: `{report['generated_at']}`",
        f"Out dir: `{report['out_dir']}`",
        f"Source: `{report['source']}`",
        "",
        "v1 limitation: relations are inferred per region from flat element boxes; no nested recursion "
        "(rows inside cards inside grids must be reviewed manually).",
        "",
    ]
    if placeholders:
        lines += [
            "Item ids are placeholders derived from measured-primitives (`<region>-pNN`); "
            "replace them with blueprint component ids before merging into ui-blueprint.json.",
            "",
        ]
    lines += [
        "| Scope | Type | Items | gap_px | Columns | Align | Confidence | Notes |",
        "| --- | --- | --- | ---: | ---: | --- | ---: | --- |",
    ]
    for relation in report["relations"]:
        lines.append(
            "| `{scope}` | `{type}` | {items} | {gap} | {columns} | {align} | {confidence} | {notes} |".format(
                scope=relation["scope"],
                type=relation["type"],
                items=", ".join(f"`{item}`" for item in relation["items"]),
                gap=relation.get("gap_px", ""),
                columns=relation.get("columns", ""),
                align=relation.get("align", ""),
                confidence=relation["confidence"],
                notes=relation.get("notes", ""),
            )
        )
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True, help="Pixel Twin Lab output directory")
    parser.add_argument("--blueprint", default="ui-blueprint.json", help="Blueprint filename inside out-dir")
    parser.add_argument("--measured-primitives", default="measured-primitives.json", help="Fallback measured-primitives filename inside out-dir")
    parser.add_argument("--regions", help="Comma-separated region names to infer")
    parser.add_argument("--min-confidence", type=float, default=0.6, help="Below this, groups become absolute-fallback")
    parser.add_argument("--json-name", default="layout-relations.json")
    parser.add_argument("--md-name", default="layout-relations.md")
    args = parser.parse_args()

    out_dir = Path(args.out_dir).expanduser().resolve()
    source, regions, placeholders = load_elements(out_dir, args.blueprint, args.measured_primitives)
    if args.regions:
        wanted = {name.strip() for name in args.regions.split(",") if name.strip()}
        regions = {name: elements for name, elements in regions.items() if name in wanted}
    if not regions:
        raise SystemExit("No regions to infer. Check --regions or the source files.")

    relations: list[dict[str, Any]] = []
    for name in sorted(regions):
        relations.extend(infer_region(name, regions[name], args.min_confidence))

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "out_dir": str(out_dir),
        "source": source,
        "relations": relations,
    }
    (out_dir / args.json_name).write_text(json.dumps(report, indent=2), encoding="utf-8")
    (out_dir / args.md_name).write_text(render_markdown(report, placeholders), encoding="utf-8")
    by_type: dict[str, int] = {}
    for relation in relations:
        by_type[relation["type"]] = by_type.get(relation["type"], 0) + 1
    print(
        json.dumps(
            {
                "out_dir": str(out_dir),
                "source": source,
                "regions": len(regions),
                "relations": len(relations),
                "by_type": by_type,
                "placeholder_ids": placeholders,
                "json": str(out_dir / args.json_name),
                "markdown": str(out_dir / args.md_name),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
