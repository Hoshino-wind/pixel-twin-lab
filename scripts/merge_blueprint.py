#!/usr/bin/env python3
"""Deterministically merge region blueprint fragments into a complete ui-blueprint.json.

Multi-agent orchestration: the main agent writes blueprint-skeleton.json (page-level
regions + implementation stack), each region sub-agent writes
packets/regions/<name>/fragment.json (components, interactions, token proposals).
This script concatenates components/interactions, folds token proposals into the
visual-tokens base (rewriting elements[].token_refs when a proposal merges into an
existing token), and emits a blueprint that must pass validate_blueprint.py.
Errors block the merge (exit code 1, no ui-blueprint.json written); warnings do not.
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REGION_FIELDS = ("name", "bounds", "role", "track", "parent", "notes")
RELATION_FIELDS = ("scope", "type", "items", "gap_px", "columns", "align", "confidence", "notes")
COMPONENT_FIELDS = ("id", "region", "category", "type", "bounds", "content", "maps_to", "elements", "notes")
ELEMENT_FIELDS = ("id", "type", "bounds", "content", "token_refs")
INTERACTION_FIELDS = ("target", "trigger", "behavior", "states", "source", "notes")
DATA_FIELDS = ("component_id", "shape", "fields", "mock_data", "binding", "source", "library", "notes")
COLOR_FIELDS = ("name", "value", "usage", "sampled_at", "maps_to")
TYPOGRAPHY_FIELDS = ("name", "size_px", "weight", "line_height_px", "measured_from", "maps_to")
SPACING_FIELDS = ("name", "px", "maps_to")


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

    def error(self, path: str, message: str) -> None:
        self.issues.append({"level": "error", "path": path, "message": message})

    def warning(self, path: str, message: str) -> None:
        self.issues.append({"level": "warning", "path": path, "message": message})

    @property
    def errors(self) -> int:
        return sum(1 for issue in self.issues if issue["level"] == "error")

    @property
    def warnings(self) -> int:
        return sum(1 for issue in self.issues if issue["level"] == "warning")


def pick(obj: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    return {key: obj[key] for key in fields if key in obj}


def is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def is_hex_color(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 7 or value[0] != "#":
        return False
    return all(ch in "0123456789abcdefABCDEF" for ch in value[1:])


def parse_hex(value: str) -> tuple[int, int, int]:
    return (int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16))


def rgb_distance(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------

def load_skeleton(out_dir: Path, log: IssueLog) -> dict[str, Any]:
    path = out_dir / "blueprint-skeleton.json"
    if not path.exists():
        raise SystemExit(f"Skeleton not found: {path}. The main agent must author blueprint-skeleton.json first.")
    skeleton = load_json(path, None)
    if not isinstance(skeleton, dict):
        raise SystemExit(f"Skeleton is not a JSON object: {path}")
    for key in ("source", "layout", "implementation"):
        if not isinstance(skeleton.get(key), dict):
            log.error("skeleton", f"blueprint-skeleton.json missing or invalid '{key}' object")
    regions = (skeleton.get("layout") or {}).get("regions")
    if not isinstance(regions, list) or not regions:
        log.error("skeleton", "blueprint-skeleton.json layout.regions must be a non-empty array")
    return skeleton


def skeleton_regions(skeleton: dict[str, Any]) -> list[dict[str, Any]]:
    regions = (skeleton.get("layout") or {}).get("regions")
    if not isinstance(regions, list):
        return []
    return [region for region in regions if isinstance(region, dict) and isinstance(region.get("name"), str)]


def load_fragments(out_dir: Path, region_order: dict[str, int], log: IssueLog) -> list[dict[str, Any]]:
    packet_dir = out_dir / "packets" / "regions"
    paths = sorted(packet_dir.glob("*/fragment.json"))
    if not paths:
        raise SystemExit(
            f"No fragments found under {packet_dir}/*/fragment.json. Region sub-agents must write them first."
        )
    fragments: list[dict[str, Any]] = []
    for path in paths:
        data = load_json(path, None)
        label = f"packets/regions/{path.parent.name}/fragment.json"
        if not isinstance(data, dict):
            log.error(label, "fragment is not a JSON object")
            continue
        region = data.get("region")
        if not isinstance(region, str) or not region:
            log.error(label, f"fragment must declare a 'region' string, got {region!r}")
            continue
        fragments.append({"path": label, "dir": path.parent.name, "region": region, "data": data})
    fragments.sort(key=lambda f: (region_order.get(f["region"], len(region_order)), f["dir"]))
    return fragments


# ---------------------------------------------------------------------------
# Components and interactions
# ---------------------------------------------------------------------------

def merge_components(fragments: list[dict[str, Any]], region_names: set[str], log: IssueLog) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen_ids: dict[str, str] = {}
    for fragment in fragments:
        raw = fragment["data"].get("components")
        if not isinstance(raw, list):
            log.error(fragment["path"], "fragment 'components' must be an array")
            continue
        if fragment["region"] not in region_names:
            log.error(fragment["path"], f"fragment region '{fragment['region']}' is not declared in skeleton layout.regions")
        for index, component in enumerate(raw):
            path = f"{fragment['path']}:components[{index}]"
            if not isinstance(component, dict):
                log.error(path, "component must be an object")
                continue
            component_id = component.get("id")
            if not isinstance(component_id, str) or not component_id:
                log.error(path, f"component id must be a non-empty string, got {component_id!r}")
                continue
            if component_id in seen_ids:
                log.error(path, f"component id '{component_id}' already declared by {seen_ids[component_id]}")
                continue
            seen_ids[component_id] = path
            region = component.get("region")
            if region != fragment["region"]:
                log.error(path, f"component '{component_id}' region {region!r} does not match fragment region '{fragment['region']}'")
            elif region not in region_names:
                log.error(path, f"component '{component_id}' region '{region}' is not declared in skeleton layout.regions")
            if not component_id.startswith(f"{fragment['region']}-"):
                log.warning(path, f"component id '{component_id}' does not start with '{fragment['region']}-'; "
                                  "region-prefixed ids avoid cross-fragment collisions")
            clean = pick(component, COMPONENT_FIELDS)
            if isinstance(clean.get("elements"), list):
                clean["elements"] = [pick(element, ELEMENT_FIELDS) for element in clean["elements"] if isinstance(element, dict)]
            fragment.setdefault("component_refs", []).append(clean)
            merged.append(clean)
    return merged


def merge_interactions(fragments: list[dict[str, Any]], component_ids: set[str], log: IssueLog) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    for fragment in fragments:
        raw = fragment["data"].get("interactions")
        if raw is None:
            continue
        if not isinstance(raw, list):
            log.error(fragment["path"], "fragment 'interactions' must be an array")
            continue
        for index, interaction in enumerate(raw):
            path = f"{fragment['path']}:interactions[{index}]"
            if not isinstance(interaction, dict):
                log.error(path, "interaction must be an object")
                continue
            target = interaction.get("target")
            if target not in component_ids:
                log.error(path, f"interaction target {target!r} is not a merged component id")
            merged.append(pick(interaction, INTERACTION_FIELDS))
    return merged


def merge_data(fragments: list[dict[str, Any]], component_ids: set[str], log: IssueLog) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: dict[str, str] = {}
    for fragment in fragments:
        raw = fragment["data"].get("data")
        if raw is None:
            continue
        if not isinstance(raw, list):
            log.error(fragment["path"], "fragment 'data' must be an array")
            continue
        for index, entry in enumerate(raw):
            path = f"{fragment['path']}:data[{index}]"
            if not isinstance(entry, dict):
                log.error(path, "data entry must be an object")
                continue
            component_id = entry.get("component_id")
            if component_id not in component_ids:
                log.error(path, f"data entry component_id {component_id!r} is not a merged component id")
                continue
            if component_id in seen:
                log.error(path, f"component '{component_id}' already has a data entry from {seen[component_id]}")
                continue
            seen[component_id] = path
            merged.append(pick(entry, DATA_FIELDS))
    return merged


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------

def base_tokens(out_dir: Path, log: IssueLog) -> dict[str, list[dict[str, Any]]]:
    tokens: dict[str, list[dict[str, Any]]] = {
        "colors": [], "typography": [], "spacing": [], "radius": [], "shadows": [], "borders": [],
    }
    visual = load_json(out_dir / "visual-tokens.json", None)
    if visual is None:
        return tokens
    if not isinstance(visual, dict):
        log.warning("visual-tokens.json", "not a JSON object; starting from empty tokens")
        return tokens
    for group, fields in (("colors", COLOR_FIELDS), ("typography", TYPOGRAPHY_FIELDS), ("spacing", SPACING_FIELDS)):
        entries = visual.get(group)
        if not isinstance(entries, list):
            continue
        tokens[group] = [pick(entry, fields) for entry in entries
                         if isinstance(entry, dict) and isinstance(entry.get("name"), str)]
    return tokens


def all_token_names(tokens: dict[str, list[dict[str, Any]]]) -> set[str]:
    return {entry["name"] for group in tokens.values() for entry in group if isinstance(entry.get("name"), str)}


def unique_name(name: str, taken: set[str]) -> str:
    if name not in taken:
        return name
    suffix = 2
    while f"{name}-{suffix}" in taken:
        suffix += 1
    return f"{name}-{suffix}"


def proposal_to_color(proposal: dict[str, Any], name: str) -> dict[str, Any]:
    entry = pick(proposal, COLOR_FIELDS)
    entry["name"] = name
    return entry


def proposal_to_typography(proposal: dict[str, Any], name: str) -> dict[str, Any]:
    entry = pick(proposal, TYPOGRAPHY_FIELDS)
    entry["name"] = name
    return entry


def proposal_to_spacing(proposal: dict[str, Any], name: str) -> dict[str, Any]:
    entry = pick(proposal, SPACING_FIELDS)
    entry["name"] = name
    return entry


def rewrite_token_refs(components: list[dict[str, Any]], old: str, new: str) -> int:
    rewritten = 0
    for component in components:
        for element in component.get("elements") or []:
            refs = element.get("token_refs")
            if not isinstance(refs, list):
                continue
            for index, ref in enumerate(refs):
                if ref == old:
                    refs[index] = new
                    rewritten += 1
    return rewritten


def merge_token_proposals(
    fragments: list[dict[str, Any]],
    tokens: dict[str, list[dict[str, Any]]],
    color_merge_distance: float,
    log: IssueLog,
) -> list[dict[str, Any]]:
    merges: list[dict[str, Any]] = []
    for fragment in fragments:
        raw = fragment["data"].get("token_proposals")
        if raw is None:
            continue
        if not isinstance(raw, list):
            log.error(fragment["path"], "fragment 'token_proposals' must be an array")
            continue
        components = fragment.get("component_refs") or []
        for index, proposal in enumerate(raw):
            path = f"{fragment['path']}:token_proposals[{index}]"
            if not isinstance(proposal, dict):
                log.error(path, "token proposal must be an object")
                continue
            kind = proposal.get("kind")
            name = proposal.get("name")
            if not isinstance(name, str) or not name:
                log.error(path, f"token proposal name must be a non-empty string, got {name!r}")
                continue
            if kind == "colors":
                merge = merge_color_proposal(proposal, path, tokens, color_merge_distance, log)
            elif kind == "typography":
                merge = merge_scalar_proposal(proposal, path, tokens, "typography", "size_px", 2.0, proposal_to_typography, log)
            elif kind == "spacing":
                merge = merge_scalar_proposal(proposal, path, tokens, "spacing", "px", 2.0, proposal_to_spacing, log)
            else:
                log.error(path, f"token proposal kind must be colors/typography/spacing, got {kind!r}")
                continue
            if merge is None:
                continue
            merge["fragment"] = fragment["path"]
            if merge["from"] != merge["to"]:
                merge["refs_rewritten"] = rewrite_token_refs(components, merge["from"], merge["to"])
            merges.append(merge)
    return merges


def merge_color_proposal(
    proposal: dict[str, Any],
    path: str,
    tokens: dict[str, list[dict[str, Any]]],
    color_merge_distance: float,
    log: IssueLog,
) -> dict[str, Any] | None:
    name = proposal["name"]
    value = proposal.get("value")
    if not is_hex_color(value):
        log.error(path, f"color proposal '{name}' value must match #rrggbb, got {value!r}")
        return None
    rgb = parse_hex(value)
    best: tuple[float, dict[str, Any]] | None = None
    for existing in tokens["colors"]:
        if not is_hex_color(existing.get("value")):
            continue
        distance = rgb_distance(rgb, parse_hex(existing["value"]))
        if distance <= color_merge_distance and (best is None or distance < best[0]):
            best = (distance, existing)
    if best is not None:
        distance, existing = best
        return {"kind": "colors", "from": name, "to": existing["name"], "action": "merged",
                "detail": f"{value} ~ {existing['value']} (rgb distance {distance:.1f} <= {color_merge_distance:g})"}
    final_name = unique_name(name, all_token_names(tokens))
    tokens["colors"].append(proposal_to_color(proposal, final_name))
    detail = f"{value} appended as new color token"
    if final_name != name:
        detail += f" (name '{name}' taken, renamed)"
    return {"kind": "colors", "from": name, "to": final_name, "action": "appended", "detail": detail}


def merge_scalar_proposal(
    proposal: dict[str, Any],
    path: str,
    tokens: dict[str, list[dict[str, Any]]],
    group: str,
    field: str,
    tolerance: float,
    to_entry: Any,
    log: IssueLog,
) -> dict[str, Any] | None:
    name = proposal["name"]
    value = proposal.get(field)
    if not is_number(value):
        log.error(path, f"{group} proposal '{name}' {field} must be a number, got {value!r}")
        return None
    best: tuple[float, dict[str, Any]] | None = None
    for existing in tokens[group]:
        if not is_number(existing.get(field)):
            continue
        delta = abs(float(value) - float(existing[field]))
        if delta <= tolerance and (best is None or delta < best[0]):
            best = (delta, existing)
    if best is not None:
        delta, existing = best
        return {"kind": group, "from": name, "to": existing["name"], "action": "merged",
                "detail": f"{field} {value} ~ {existing[field]} (delta {delta:g} <= {tolerance:g})"}
    final_name = unique_name(name, all_token_names(tokens))
    tokens[group].append(to_entry(proposal, final_name))
    detail = f"{field} {value} appended as new {group} token"
    if final_name != name:
        detail += f" (name '{name}' taken, renamed)"
    return {"kind": group, "from": name, "to": final_name, "action": "appended", "detail": detail}


# ---------------------------------------------------------------------------
# Layout relations and implementation plan
# ---------------------------------------------------------------------------

def merge_relations(
    out_dir: Path,
    skeleton: dict[str, Any],
    fragments: list[dict[str, Any]],
    region_names: set[str],
    component_ids: set[str],
    log: IssueLog,
) -> list[dict[str, Any]]:
    candidates: list[tuple[str, dict[str, Any]]] = []
    skeleton_relations = (skeleton.get("layout") or {}).get("relations")
    if isinstance(skeleton_relations, list) and skeleton_relations:
        for relation in skeleton_relations:
            candidates.append(("blueprint-skeleton.json", relation))
    else:
        inferred = load_json(out_dir / "layout-relations.json", None)
        raw = inferred.get("relations") if isinstance(inferred, dict) else inferred
        if isinstance(raw, list):
            for relation in raw:
                if isinstance(relation, dict) and relation.get("scope") in region_names:
                    candidates.append(("layout-relations.json", relation))
                else:
                    scope = relation.get("scope") if isinstance(relation, dict) else None
                    log.warning("layout-relations.json", f"dropped relation with scope {scope!r}: not a declared region")
    for fragment in fragments:
        raw = fragment["data"].get("relations")
        if raw is None:
            continue
        if not isinstance(raw, list):
            log.warning(fragment["path"], "fragment 'relations' must be an array; ignored")
            continue
        for relation in raw:
            candidates.append((fragment["path"], relation))

    known_ids = region_names | component_ids
    relations: list[dict[str, Any]] = []
    for origin, relation in candidates:
        if not isinstance(relation, dict):
            log.warning(origin, "dropped relation: not an object")
            continue
        items = relation.get("items")
        unknown = [item for item in items if item not in known_ids] if isinstance(items, list) else []
        if unknown:
            log.warning(origin, f"dropped relation scope {relation.get('scope')!r}: items reference unknown ids {unknown}")
            continue
        relations.append(pick(relation, RELATION_FIELDS))
    return relations


def component_sort_key(component: dict[str, Any], region_order: dict[str, int]) -> tuple[int, int, int]:
    bounds = component.get("bounds") if isinstance(component.get("bounds"), dict) else {}
    y = bounds.get("y") if is_number(bounds.get("y")) else 0
    x = bounds.get("x") if is_number(bounds.get("x")) else 0
    return (region_order.get(component.get("region"), len(region_order)), int(y), int(x))


def build_implementation(
    skeleton: dict[str, Any],
    components: list[dict[str, Any]],
    region_order: dict[str, int],
) -> tuple[dict[str, Any], bool]:
    raw = skeleton.get("implementation") if isinstance(skeleton.get("implementation"), dict) else {}
    implementation = pick(raw, ("framework", "styling", "ui_library", "final_dir", "plan"))
    if isinstance(implementation.get("plan"), list) and implementation["plan"]:
        return implementation, False
    implementation.pop("plan", None)
    ordered = sorted(components, key=lambda c: component_sort_key(c, region_order))
    implementation["plan"] = [
        {"component_id": component["id"], "action": "create", "order": order}
        for order, component in enumerate(ordered, start=1)
    ]
    return implementation, True


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Blueprint Merge Report",
        "",
        f"Generated: `{report['generated_at']}`",
        f"Out dir: `{report['out_dir']}`",
        f"Result: `{'PASS' if summary['pass'] else 'FAIL'}`",
        "",
        f"- fragments: {summary['fragments']} ({', '.join(f'`{name}`' for name in report['fragment_regions']) or 'none'})",
        f"- components: {summary['components']} | interactions: {summary['interactions']} | data entries: {summary['data_entries']}",
        f"- tokens: colors {summary['tokens']['colors']}, typography {summary['tokens']['typography']}, "
        f"spacing {summary['tokens']['spacing']}",
        f"- token proposals processed: {summary['token_merges']} | relations kept: {report['relations']}",
        f"- errors: {summary['errors']} | warnings: {summary['warnings']}",
        f"- color merge distance: `{report['color_merge_distance']}` (RGB euclidean)",
    ]
    if report["default_plan"]:
        lines.append("- implementation.plan: default plan generated (every component `create`, ordered by region then y,x); "
                     "the main agent must review which steps should be `reuse`/`extend`")
    lines += ["", "## Token merge map", ""]
    if report["token_merges"]:
        lines += ["| Kind | Proposal | Final token | Action | Detail | Fragment |",
                  "| --- | --- | --- | --- | --- | --- |"]
        for merge in report["token_merges"]:
            refs = f" ({merge['refs_rewritten']} refs rewritten)" if merge.get("refs_rewritten") else ""
            lines.append(f"| `{merge['kind']}` | `{merge['from']}` | `{merge['to']}` | {merge['action']}{refs} "
                         f"| {merge['detail']} | `{merge['fragment']}` |")
    else:
        lines.append("No token proposals.")
    for level in ("error", "warning"):
        issues = [issue for issue in report["issues"] if issue["level"] == level]
        lines += ["", f"## {level.capitalize()}s", ""]
        if issues:
            lines += [f"- `{issue['path']}`: {issue['message']}" for issue in issues]
        else:
            lines.append("None.")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True, help="Pixel Twin Lab output directory")
    parser.add_argument("--color-merge-distance", type=float, default=24.0,
                        help="Max RGB euclidean distance to merge a color proposal into an existing token")
    parser.add_argument("--blueprint-name", default="ui-blueprint.json")
    parser.add_argument("--report-name", default="merge-report.md")
    args = parser.parse_args()

    out_dir = Path(args.out_dir).expanduser().resolve()
    log = IssueLog()
    skeleton = load_skeleton(out_dir, log)
    regions = skeleton_regions(skeleton)
    region_names = {region["name"] for region in regions}
    region_order = {region["name"]: index for index, region in enumerate(regions)}
    fragments = load_fragments(out_dir, region_order, log)

    components = merge_components(fragments, region_names, log)
    component_ids = {component["id"] for component in components}
    interactions = merge_interactions(fragments, component_ids, log)
    data_entries = merge_data(fragments, component_ids, log)

    tokens = base_tokens(out_dir, log)
    token_merges = merge_token_proposals(fragments, tokens, args.color_merge_distance, log)

    relations = merge_relations(out_dir, skeleton, fragments, region_names, component_ids, log)
    implementation, default_plan = build_implementation(skeleton, components, region_order)

    layout: dict[str, Any] = {"regions": [pick(region, REGION_FIELDS) for region in regions]}
    if relations:
        layout["relations"] = relations
    blueprint = {
        "version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": skeleton.get("source") or {},
        "layout": layout,
        "components": components,
        "tokens": tokens,
        "data": data_entries,
        "interactions": interactions,
        "implementation": implementation,
    }

    summary = {
        "fragments": len(fragments),
        "components": len(components),
        "interactions": len(interactions),
        "data_entries": len(data_entries),
        "tokens": {group: len(tokens[group]) for group in ("colors", "typography", "spacing")},
        "token_merges": len(token_merges),
        "errors": log.errors,
        "warnings": log.warnings,
        "pass": log.errors == 0,
    }
    report = {
        "generated_at": blueprint["generated_at"],
        "out_dir": str(out_dir),
        "fragment_regions": [fragment["region"] for fragment in fragments],
        "color_merge_distance": args.color_merge_distance,
        "token_merges": token_merges,
        "relations": len(relations),
        "default_plan": default_plan,
        "summary": summary,
        "issues": log.issues,
    }
    report_path = out_dir / args.report_name
    report_path.write_text(render_markdown(report), encoding="utf-8")

    blueprint_path = out_dir / args.blueprint_name
    if summary["pass"]:
        blueprint_path.write_text(json.dumps(blueprint, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({**summary, "blueprint": str(blueprint_path) if summary["pass"] else None,
                      "report": str(report_path)}, indent=2))
    if not summary["pass"]:
        print(f"FAIL: {summary['errors']} error(s); ui-blueprint.json not written. See {report_path}")
        raise SystemExit(1)
    if default_plan:
        print("Note: implementation.plan was auto-generated (all 'create'); main agent must review reuse/extend.")
    print(f"Next: run validate_blueprint.py --out-dir {out_dir}")


if __name__ == "__main__":
    main()
