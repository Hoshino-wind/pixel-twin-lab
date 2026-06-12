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
   written into the blueprint. If information you need is missing from the packet, that is a
   blueprint defect - report it back to the orchestrator instead of looking at images.
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True, help="Pixel Twin Lab output directory")
    parser.add_argument("--blueprint-name", default="ui-blueprint.json", help="Blueprint filename inside out-dir")
    parser.add_argument("--validation-name", default="blueprint-validation.json", help="Validation report filename inside out-dir")
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
        packet = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "component": component,
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
