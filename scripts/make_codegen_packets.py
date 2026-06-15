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
3. The rendered DOM must carry a `data-element` attribute for every element id listed in
   `packet.json` -> `component.elements`.
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
10. **Composite typography & per-element style (element style contract).** When an element
   carries `runs`, render one node per run in order, each carrying `data-run="<element-id>.<index>"`,
   and apply that run's `style.expected` via tokens — a value like "128 kg CO2e" is NOT one
   flat string at one size; it is number + unit + suffix, each with its own size/weight, and
   the suffix uses `vertical_align: sub`/`super` so it does not overflow-clip. When an element
   carries `style`, apply `style.expected` via tokens. Set font-size/weight/color EXPLICITLY
   from the contract — never rely on a tag's browser default (e.g. `<strong>` = 700), which is
   exactly the leak that bolds and clips composite values. verify_elements.py asserts each
   run/element style against the rendered DOM, so an unstyled flat string will fail the gate.
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
            assets[str(asset["id"])] = asset
    return assets


def elements_for_component(component: dict[str, Any], by_region: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    region = component.get("region")
    if region is not None and str(region) in by_region:
        return by_region[str(region)]
    component_id = component.get("id")
    if component_id is not None and str(component_id) in by_region:
        return by_region[str(component_id)]
    return []


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
        component_elements = elements_for_component(component, elements_for_region)
        component_assets = []
        for element in component_elements:
            asset_id = element.get("asset_id")
            if asset_id is not None and str(asset_id) in asset_lookup:
                component_assets.append(asset_lookup[str(asset_id)])
        component_packet = dict(component)
        component_packet["elements"] = component_elements
        packet = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "component": component_packet,
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
