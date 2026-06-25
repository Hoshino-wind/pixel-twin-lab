#!/usr/bin/env python3
"""Build per-component codegen packets for sub-agents that never see the reference image."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

INSTRUCTIONS_TEMPLATE = """# Codegen packet: `{component_id}`

You are a code-generation sub-agent. Generate the project-native implementation of this
component using **only** `packet.json` in this directory.

## Hard rules

1. **Do not read the reference image or any lab screenshot.** This isolation is by design,
   not an oversight: every visual fact you are allowed to use has already been measured and
   written into the blueprint and element manifest. If information you need is missing from
   the packet, that is a blueprint/element-contract defect - report it back to the
   orchestrator instead of looking at images.
2. Generate a component native to the project stack declared in `packet.json` -> `project`
   (framework / styling / ui_library / final_dir). Never paste lab HTML.
3. The component's root DOM node must carry `data-component="<component.id>"`, and the
   rendered DOM inside that root must carry a `data-element` attribute for every element id
   listed in `packet.json` -> `component.elements`. Do not render component-owned
   `data-element` nodes as loose siblings elsewhere on the page.
4. Style values (colors, font sizes, spacing, radii, shadows, borders) may only come from
   `packet.json` -> `tokens`. When a token declares `maps_to`, prefer the existing project
   token it points to over a hard-coded value.
5. Implement every entry in `packet.json` -> `interactions` targeting this component, and
   cover each state it declares (hover/active/focus/disabled/selected/loading/empty/error).
6. Use `packet.json` -> `relations` and `component.bounds` (within `source_dims`) to derive
   layout; prefer flow layout per the relation type, falling back to absolute positioning
   only where a relation says `absolute-fallback`.
7. When the packet has `data` entries, render **from the data**: map a single row/item
   template over `mock_data`, or feed it to the declared third-party library (`library`,
   e.g. echarts) via `binding`. Do not inline the values as hard-coded sibling nodes, and
   do not draw the data's appearance with bespoke SVG/CSS shapes. Keep `mock_data` in a
   separate fixture file (e.g. `<component>.mock.ts`/`.json`) so it is swappable for real
   data sources later.
8. When the plan action is `create`, the component's `type` names its archetype (table,
   list, kpi-card, tabs, badge, panel, chart container). Write it as a reusable,
   props-driven generic component in the project's stack — content arrives only through
   props/data, styles only through tokens. Do not bake this region's content into the
   component body; the fixture file is what makes this instance render this region.
9. When an element has `requires_asset: true`, render that visual as an image/asset slot
   using its `asset_path`; do not replace it with a hand-drawn approximation. These paths
   point to measured element crops, not to the original reference screenshot.
   Icon/image elements should be treated as asset-backed by default; only an icon explicitly
   declaring `asset_policy: "trivial-vector"` may be hand-drawn as a simple geometric glyph.
10. **Composite typography & per-element style (element style contract).** When an element
   carries `runs`, render one node per run in order, each carrying `data-run="<element-id>.<index>"`,
   and apply that run's `style.expected` via tokens — a value like "128 kg CO2e" is NOT one
   flat string at one size; it is number + unit + suffix, each with its own size/weight, and
   the suffix uses `vertical_align: sub`/`super` so it does not overflow-clip. When an element
   carries `style`, apply `style.expected` via tokens. Set font-size/weight/color EXPLICITLY
   from the contract — never rely on a tag's browser default (e.g. `<strong>` = 700), which is
   exactly the leak that bolds and clips composite values. verify_elements.py asserts each
   run/element style against the rendered DOM, so an unstyled flat string will fail the gate.
11. **Component root surface style.** When `packet.json` -> `component.style` exists, apply
    `style.expected` to the `data-component` root itself: background, border width/color/style,
    radius, shadow, opacity, and any declared typography. Do not move these onto a child wrapper
    unless that wrapper is the `data-component` root; verify_elements.py measures the root node.
    For card/list/table/statistic/container-like surfaces, the blueprint must include a verifiable
    root fill (`background_color`) and at least one root boundary/shape property (`border_radius_px`,
    `border_width_px`, `border_color`, or `box_shadow`). Treat a weaker style as a blueprint defect.
12. **Collections.** When an element has `type: "collection"`, render ONE collection container
    carrying that element's `data-element`, then render rows/items by mapping over the matching
    `packet.json` -> `data` entry. Every rendered row/item must carry `data-element-item`.
    `item_count`, `min_items`, and `first_item_content` are verification contracts, not copy
    suggestions. A table/list/timeline implemented as loose hard-coded siblings is a failure.
13. **Region composition.** When `packet.json` -> `region.composition` exists, keep the
    background plane and foreground overlays separate. Background assets/maps/charts render as
    the declared asset/library element. Foreground entries render as independent DOM components
    with their own `data-element`, explicit positioning, and z-index. Do not bake foreground
    text/cards/statistics into the background asset.
"""


def load_required_json(path: Path, hint: str) -> Any:
    if not path.exists():
        raise SystemExit(f"{hint} not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{hint} is not valid JSON: {path} ({exc})")


def load_optional_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return fallback


def relations_for(component: dict[str, Any], relations: list[Any]) -> list[dict[str, Any]]:
    component_id = component.get("id")
    region = component.get("region")
    matched: list[dict[str, Any]] = []
    for relation in relations:
        if not isinstance(relation, dict):
            continue
        items = relation.get("items") or []
        if relation.get("scope") == region or (isinstance(items, list) and component_id in items):
            matched.append(relation)
    return matched


def plan_for(component_id: str, implementation: dict[str, Any]) -> dict[str, Any] | None:
    for entry in implementation.get("plan") or []:
        if isinstance(entry, dict) and entry.get("component_id") == component_id:
            return entry
    return None


def elements_by_region(manifest: dict[str, Any] | None) -> dict[str, list[dict[str, Any]]]:
    if not isinstance(manifest, dict):
        return {}
    by_region: dict[str, list[dict[str, Any]]] = {}
    for region in manifest.get("regions") or []:
        if not isinstance(region, dict) or not region.get("name"):
            continue
        elements = [element for element in region.get("elements") or [] if isinstance(element, dict)]
        by_region[str(region["name"])] = elements
    return by_region


def assets_by_id(report: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not isinstance(report, dict):
        return {}
    assets: dict[str, dict[str, Any]] = {}
    for asset in report.get("assets") or []:
        if isinstance(asset, dict) and asset.get("id"):
            asset_path = asset.get("asset_path") or asset.get("path") or asset.get("file")
            normalized = {
                "id": asset.get("id"),
                "element_id": asset.get("element_id"),
                "asset_path": asset_path,
                "asset_type": asset.get("asset_type"),
                "asset_role": asset.get("asset_role") or "element-asset",
                "asset_policy": asset.get("asset_policy"),
                "asset_source": asset.get("asset_source") or asset.get("source"),
                "bounds": asset.get("bounds"),
            }
            assets[str(asset["id"])] = {k: v for k, v in normalized.items() if v is not None}
    return assets


def elements_for_component(component: dict[str, Any], by_region: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    region = component.get("region")
    if region is not None and str(region) in by_region:
        return by_region[str(region)]
    component_id = component.get("id")
    if component_id is not None and str(component_id) in by_region:
        return by_region[str(component_id)]
    return []


def merge_element_contracts(blueprint_elements: list[Any], manifest_elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []

    def add(element: Any, *, prefer_existing: bool) -> None:
        if not isinstance(element, dict) or not element.get("id"):
            return
        element_id = str(element["id"])
        if element_id not in merged:
            merged[element_id] = dict(element)
            order.append(element_id)
            return
        if prefer_existing:
            current = merged[element_id]
            merged[element_id] = {**dict(element), **current}
        else:
            merged[element_id] = {**merged[element_id], **dict(element)}

    for element in blueprint_elements or []:
        add(element, prefer_existing=True)
    for element in manifest_elements or []:
        add(element, prefer_existing=False)
    return [merged[element_id] for element_id in order]


def enrich_asset_elements(elements: list[dict[str, Any]], asset_lookup: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for element in elements:
        if not isinstance(element, dict):
            continue
        current = dict(element)
        asset_id = current.get("asset_id")
        if asset_id is not None and str(asset_id) in asset_lookup:
            asset = asset_lookup[str(asset_id)]
            if not current.get("asset_path") and asset.get("asset_path"):
                current["asset_path"] = asset["asset_path"]
            for field in ("asset_type", "asset_role", "asset_policy", "asset_source"):
                if not current.get(field) and asset.get(field):
                    current[field] = asset[field]
        enriched.append(current)
    return enriched


def assert_asset_contract(component_id: str, elements: list[dict[str, Any]]) -> None:
    missing: list[str] = []
    for element in elements:
        if not isinstance(element, dict):
            continue
        if not (element.get("requires_asset") or element.get("asset_id") or element.get("asset_path")):
            continue
        if not element.get("asset_id") or not element.get("asset_path"):
            missing.append(str(element.get("id") or "<unknown>"))
    if missing:
        raise SystemExit(
            f"Component '{component_id}' has asset-required element(s) without asset_id+asset_path: "
            f"{missing}. Run extract_element_assets.py/init_element_manifest.py or provide element-assets.json "
            "before codegen."
        )


def regions_by_name(layout: dict[str, Any]) -> dict[str, dict[str, Any]]:
    regions: dict[str, dict[str, Any]] = {}
    raw = layout.get("regions") if isinstance(layout, dict) else []
    for region in raw or []:
        if isinstance(region, dict) and region.get("name"):
            regions[str(region["name"])] = region
    return regions


def inline_assets_from_elements(elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    assets: list[dict[str, Any]] = []
    for element in elements:
        if not isinstance(element, dict):
            continue
        asset_path = element.get("asset_path")
        if not isinstance(asset_path, str) or not asset_path:
            continue
        assets.append(
            {
                "id": element.get("asset_id") or element.get("id"),
                "element_id": element.get("id"),
                "asset_path": asset_path,
                "asset_role": element.get("asset_role") or "element-asset",
                "asset_policy": element.get("asset_policy"),
            }
        )
    return assets


def composition_assets(region: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(region, dict):
        return []
    composition = region.get("composition")
    if not isinstance(composition, dict):
        return []
    background = composition.get("background")
    if not isinstance(background, dict):
        return []
    asset_path = background.get("generated_asset_path")
    if not isinstance(asset_path, str) or not asset_path:
        return []
    return [
        {
            "id": background.get("asset_element_id"),
            "element_id": background.get("asset_element_id"),
            "asset_path": asset_path,
            "asset_role": "composition-background",
            "asset_policy": background.get("asset_policy"),
            "kind": background.get("kind"),
            "library": background.get("library"),
        }
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True, help="Pixel Twin Lab output directory")
    parser.add_argument("--blueprint-name", default="ui-blueprint.json", help="Blueprint filename inside out-dir")
    parser.add_argument("--validation-name", default="blueprint-validation.json", help="Validation report filename inside out-dir")
    parser.add_argument("--element-manifest-name", default="element-manifest.json", help="Element manifest filename inside out-dir")
    parser.add_argument("--element-assets-name", default="element-assets.json", help="Element assets filename inside out-dir")
    parser.add_argument("--components", help="Comma-separated component ids to pack")
    args = parser.parse_args()

    out_dir = Path(args.out_dir).expanduser().resolve()
    blueprint = load_required_json(out_dir / args.blueprint_name, "Blueprint")
    validation = load_required_json(out_dir / args.validation_name, "Validation report")

    summary = validation.get("summary") if isinstance(validation, dict) else None
    if not (isinstance(summary, dict) and summary.get("pass") is True):
        raise SystemExit(
            f"Blueprint has not passed validation ({out_dir / args.validation_name}: "
            f"summary.pass != true). Run validate_blueprint.py until summary.pass is true "
            "before dispatching codegen sub-agents - this gate is the orchestrated form of the "
            "Phase 2 -> Phase 4 gate."
        )

    components = blueprint.get("components")
    if not isinstance(components, list) or not components:
        raise SystemExit("Blueprint has no components to pack.")
    if args.components:
        wanted = {component_id.strip() for component_id in args.components.split(",") if component_id.strip()}
        components = [c for c in components if isinstance(c, dict) and c.get("id") in wanted]
        if not components:
            raise SystemExit(f"No components matched --components filter: {args.components}")

    layout = blueprint.get("layout") if isinstance(blueprint.get("layout"), dict) else {}
    relations = layout.get("relations") if isinstance(layout.get("relations"), list) else []
    region_lookup = regions_by_name(layout)
    tokens = blueprint.get("tokens") if isinstance(blueprint.get("tokens"), dict) else {}
    interactions = blueprint.get("interactions") if isinstance(blueprint.get("interactions"), list) else []
    data_entries = blueprint.get("data") if isinstance(blueprint.get("data"), list) else []
    implementation = blueprint.get("implementation") if isinstance(blueprint.get("implementation"), dict) else {}
    source = blueprint.get("source") if isinstance(blueprint.get("source"), dict) else {}
    element_manifest = load_optional_json(out_dir / args.element_manifest_name, None)
    element_assets = load_optional_json(out_dir / args.element_assets_name, None)
    elements_for_region = elements_by_region(element_manifest)
    asset_lookup = assets_by_id(element_assets)

    contract = load_optional_json(out_dir / "component-contract.json", None)
    project_profile = contract.get("project_profile") if isinstance(contract, dict) else None
    project: dict[str, Any] = {
        key: implementation.get(key) for key in ("framework", "styling", "ui_library", "final_dir")
    }
    if project_profile is not None:
        project["project_profile"] = project_profile

    packets_dir = out_dir / "packets" / "codegen"
    packets_dir.mkdir(parents=True, exist_ok=True)

    packed: list[str] = []
    for component in components:
        if not (isinstance(component, dict) and component.get("id")):
            continue
        component_id = str(component["id"])
        packet_dir = packets_dir / component_id
        packet_dir.mkdir(parents=True, exist_ok=True)
        manifest_elements = elements_for_component(component, elements_for_region)
        blueprint_elements = component.get("elements") if isinstance(component.get("elements"), list) else []
        component_elements = enrich_asset_elements(
            merge_element_contracts(blueprint_elements, manifest_elements),
            asset_lookup,
        )
        assert_asset_contract(component_id, component_elements)
        region_packet = region_lookup.get(str(component.get("region") or "")) or {}
        component_assets = []
        for element in component_elements:
            asset_id = element.get("asset_id")
            if asset_id is not None and str(asset_id) in asset_lookup:
                component_assets.append(asset_lookup[str(asset_id)])
        component_assets.extend(inline_assets_from_elements(component_elements))
        component_assets.extend(composition_assets(region_packet))
        component_packet = dict(component)
        component_packet["elements"] = component_elements
        packet = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "component": component_packet,
            "region": region_packet,
            "assets": component_assets,
            "relations": relations_for(component, relations),
            "tokens": tokens,
            "interactions": [
                entry for entry in interactions if isinstance(entry, dict) and entry.get("target") == component_id
            ],
            "data": [
                entry for entry in data_entries if isinstance(entry, dict) and entry.get("component_id") == component_id
            ],
            "plan": plan_for(component_id, implementation),
            "project": project,
            "source_dims": {"width": source.get("width"), "height": source.get("height")},
        }
        (packet_dir / "packet.json").write_text(json.dumps(packet, indent=2), encoding="utf-8")
        (packet_dir / "INSTRUCTIONS.md").write_text(
            INSTRUCTIONS_TEMPLATE.format(component_id=component_id), encoding="utf-8"
        )
        packed.append(component_id)

    if not packed:
        raise SystemExit("No codegen packets were produced.")

    print(json.dumps({"components": packed, "packets_dir": str(packets_dir)}, indent=2))


if __name__ == "__main__":
    main()
