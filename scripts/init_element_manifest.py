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


def merge_region(
    region_name: str,
    measured_boxes: list[dict[str, Any]],
    existing_elements: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    elements = [dict(element) for element in existing_elements]
    added = 0
    next_index = len(elements) + 1
    for box in measured_boxes:
        bounds = box.get("full_bounds") or box
        box_center = center(bounds)
        if any(contains(element.get("bounds", {}), box_center) for element in elements):
            continue
        elements.append(new_element(region_name, next_index, box))
        next_index += 1
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
            "| Id | Bounds | Kind hint | Type | Content | Maps to |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for element in region["elements"]:
            lines.append(
                f"| `{element['id']}` | `{element['bounds']}` | `{element.get('kind_hint', '')}` | "
                f"{element.get('type') or '**unlabeled**'} | {element.get('content', '')} | {element.get('maps_to', '')} |"
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

    regions_out: list[dict[str, Any]] = []
    total_added = 0
    for region in measured_regions:
        if not isinstance(region, dict) or not region.get("name"):
            continue
        name = str(region["name"])
        if wanted is not None and name not in wanted:
            continue
        elements, added = merge_region(name, region.get("primitives") or [], existing_by_name.pop(name, []))
        total_added += added
        regions_out.append({"name": name, "bounds": region.get("bounds"), "elements": elements})
    # Regions present in the existing manifest but not re-measured this round are preserved untouched.
    for name, elements in existing_by_name.items():
        if wanted is not None and name not in wanted:
            pass
        regions_out.append({"name": name, "bounds": None, "elements": elements})

    if not regions_out:
        raise SystemExit("No regions selected for the element manifest.")

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "out_dir": str(out_dir),
        "measured_source": str(measured_path),
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
