#!/usr/bin/env python3
"""Create a component primitive rebuild worklist from a Pixel Twin lab.

This script is intentionally a planning tool, not a fidelity pass. It joins
regions, latest pixel diff metrics, recovery ledger tracks, and lightweight DOM
evidence into a region-by-region list of DOM/SVG primitives that must be rebuilt
before a componentized fidelity gate can honestly pass.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def load_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return fallback


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def opt_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fmt_pct(value: Any, missing: str = "n/a") -> str:
    return missing if value is None else f"{float(value):.4f}%"


def metric_capture(summary: list[dict[str, Any]], file_name: str) -> dict[str, Any]:
    for item in summary:
        if item.get("file") == file_name:
            return item
    return {}


def normalize_bounds(region: dict[str, Any]) -> dict[str, int] | None:
    if all(k in region for k in ("x", "y", "width", "height")):
        return {
            "x": int(region["x"]),
            "y": int(region["y"]),
            "width": int(region["width"]),
            "height": int(region["height"]),
        }
    bounds = region.get("bounds")
    if isinstance(bounds, dict) and all(k in bounds for k in ("x", "y", "width", "height")):
        return {
            "x": int(bounds["x"]),
            "y": int(bounds["y"]),
            "width": int(bounds["width"]),
            "height": int(bounds["height"]),
        }
    return None


def regions_by_name(out_dir: Path) -> dict[str, dict[str, Any]]:
    regions_file = load_json(out_dir / "regions.json", {})
    raw = regions_file.get("regions") if isinstance(regions_file, dict) else None
    regions: dict[str, dict[str, Any]] = {}
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict) and item.get("name"):
                regions[str(item["name"])] = item
    return regions


def ledger_by_name(out_dir: Path, recovery_dir: str) -> dict[str, dict[str, Any]]:
    ledger = load_json(out_dir / recovery_dir / "component-ledger.json", {})
    raw = ledger.get("regions") if isinstance(ledger, dict) else None
    regions: dict[str, dict[str, Any]] = {}
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict) and item.get("name"):
                regions[str(item["name"])] = item
    return regions


def metric_regions(capture: dict[str, Any]) -> dict[str, dict[str, Any]]:
    regions: dict[str, dict[str, Any]] = {}
    for item in capture.get("regions") or []:
        if isinstance(item, dict) and item.get("name"):
            regions[str(item["name"])] = item
    return regions


def strip_tags(html: str) -> str:
    text = re.sub(r"<script\b.*?</script>", " ", html, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<style\b.*?</style>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def dom_summary(out_dir: Path) -> dict[str, Any]:
    path = out_dir / "index.html"
    if not path.exists():
        return {"source": None, "text_tokens": [], "section_count": 0}
    html = path.read_text(encoding="utf-8", errors="ignore")
    text = strip_tags(html)
    tokens = [token for token in re.split(r"\s+", text) if token]
    labels = re.findall(r'aria-label="([^"]+)"', html)
    return {
        "source": str(path),
        "text_tokens": tokens[:160],
        "section_count": len(re.findall(r"<section\b", html, flags=re.IGNORECASE)),
        "aria_labels": labels,
        "tag_counts": {
            "button": len(re.findall(r"<button\b", html, flags=re.IGNORECASE)),
            "a": len(re.findall(r"<a\b", html, flags=re.IGNORECASE)),
            "table": len(re.findall(r"<table\b", html, flags=re.IGNORECASE)),
            "svg": len(re.findall(r"<svg\b", html, flags=re.IGNORECASE)),
            "img": len(re.findall(r"<img\b", html, flags=re.IGNORECASE)),
        },
    }


def load_routing(out_dir: Path) -> dict[str, dict[str, Any]]:
    routing: dict[str, dict[str, Any]] = {}
    manifest = load_json(out_dir / "routing-manifest.json", None)
    if isinstance(manifest, dict):
        for region in manifest.get("regions") or []:
            if isinstance(region, dict) and region.get("name") and region.get("track"):
                routing[str(region["name"])] = region
    return routing


def infer_track(name: str, ledger_region: dict[str, Any] | None, routing: dict[str, dict[str, Any]] | None = None) -> str:
    routed = (routing or {}).get(name)
    if routed:
        return str(routed["track"])
    if ledger_region and ledger_region.get("track"):
        return str(ledger_region["track"])
    lowered = name.lower()
    # Fallback name heuristics only — classify_slices.py routing is the authoritative source.
    if any(key in lowered for key in ("chart", "trend", "funnel", "heatmap", "map")):
        return "approximation"
    if any(key in lowered for key in ("photo", "avatar", "image")):
        return "island"
    if lowered.startswith(("slice-", "gap-")):
        return "diagnostic"
    return "component"


def has_asset(ledger_region: dict[str, Any] | None) -> bool:
    if not ledger_region:
        return False
    strategy = str(ledger_region.get("asset_strategy") or "none")
    provider = str(ledger_region.get("asset_provider") or "none")
    return bool(ledger_region.get("asset")) or strategy != "none" or provider != "none"


def primitive_hints(name: str, track: str) -> list[str]:
    lowered = name.lower()
    hints: list[str] = []
    if track == "approximation":
        # Never hand-draw data shapes: approximation regions are a library + mock data, full stop.
        return [
            "third-party library container (chart: echarts; map: leaflet; 3D: three)",
            "mock data from the blueprint data layer fed via the library option/props",
            "library restyle to tokens (colors, fonts, grid)",
            "container shell geometry (position, size, radius, border) as strict DOM",
        ]
    if track == "island":
        hints.extend(["component shell", "measured island bounds", "asset clipping/masking"])
    if any(k in lowered for k in ("header", "topbar", "app-bar")):
        hints.extend(["app bar container", "heading/text nodes", "button controls", "icon assets (crop or transparent regen)", "badge/avatar primitives"])
    if any(k in lowered for k in ("sidebar", "nav", "bottom-nav")):
        hints.extend(["navigation item rows from a data array", "section labels", "active state", "icon assets", "usage/progress indicators"])
    if any(k in lowered for k in ("kpi", "card", "weather")):
        hints.extend(["card primitives", "metric labels", "numeric typography", "status badges"])
    if any(k in lowered for k in ("right", "insight", "automation", "alert")):
        hints.extend(["panel cards", "text hierarchy", "callout blocks", "toggle/status controls", "small badge primitives"])
    if any(k in lowered for k in ("stream", "timeline", "list")):
        hints.extend(["one row template + mock data array (collection contract)", "time/status labels", "divider lines", "row icon assets"])
    if any(k in lowered for k in ("table", "pipeline")):
        hints.extend(["semantic table from column defs + row records (collection contract)", "column headers", "cell typography", "status tags", "row separators"])
    if any(k in lowered for k in ("tabs", "filter", "segment")):
        hints.extend(["segmented control", "active indicator", "button text", "control spacing"])
    if any(k in lowered for k in ("assistant", "search", "input")):
        hints.extend(["input/control container", "placeholder text", "command buttons", "icon assets"])
    if any(k in lowered for k in ("recommendation", "trip", "photo")):
        hints.extend(["content card shell", "thumbnail/media island if approved", "title/meta text", "action controls"])
    if not hints:
        hints.extend(["layout container", "text nodes", "spacing tokens", "border/radius/shadow tokens"])

    deduped: list[str] = []
    for hint in hints:
        if hint not in deduped:
            deduped.append(hint)
    return deduped


def region_role(track: str, asset: bool, strategy: str, allowed_tracks: set[str]) -> str:
    if asset and ((track not in allowed_tracks and track != "approximation") or strategy == "placeholder"):
        return "asset-disallowed"
    if asset and track in allowed_tracks:
        return "approved-island"
    if track == "approximation":
        return "approximation-library"
    if track in allowed_tracks:
        return "island-missing-asset"
    if track == "diagnostic":
        return "diagnostic-slice"
    return "component-required"


def next_actions(role: str, name: str, strict: float, tolerant: float) -> list[str]:
    if role == "approved-island":
        return ["Keep as measured island; verify shell alignment and clipping only."]
    if role == "approximation-library":
        return [
            "Render with the routed third-party library fed the blueprint's mock data; restyle to tokens.",
            "Evaluate per-region (tolerant mismatch + compare_structure.py), not whole-page strict.",
        ]
    if role == "asset-disallowed":
        return ["Remove generated asset from component track.", "Rebuild visible content as DOM/SVG primitives."]
    if role == "island-missing-asset":
        return ["Either approve this as an island and add a measured asset, or reclassify to component."]
    if role == "diagnostic-slice":
        return ["Do not treat this as product UI; use it only for exact-mode diagnostics."]
    actions = ["Rebuild as semantic DOM/SVG primitives, not a raster crop."]
    if tolerant > 20:
        actions.append("Fix structural layout, spacing, and missing content before token tuning.")
    elif strict > 40:
        actions.append("Geometry and structure are still far off; continue primitive rebuild before token tuning.")
    elif strict > 10:
        actions.append("Tighten typography, icon geometry, borders, shadows, and antialias-sensitive edges.")
    else:
        actions.append("Tune final token deltas and subpixel alignment.")
    if any(k in name.lower() for k in ("kpi", "card", "sidebar", "topbar", "header", "nav")):
        actions.append("Extract repeated primitives into reusable project-native components.")
    return actions


def build_report(out_dir: Path, recovery_dir: str, allowed_tracks: set[str], target_match: float) -> dict[str, Any]:
    summary = load_json(out_dir / "pixel-diff-summary.json", [])
    if not isinstance(summary, list):
        raise SystemExit("pixel-diff-summary.json is missing or invalid; run pixel_diff.py first.")
    rebuilt = metric_capture(summary, "rebuilt-capture.png")
    reference = metric_capture(summary, "reference-capture.png")
    exact = metric_capture(summary, "exact-capture.png")
    if not rebuilt:
        raise SystemExit("rebuilt-capture.png metrics are missing; capture and diff rebuilt mode first.")

    named_regions = regions_by_name(out_dir)
    ledger = ledger_by_name(out_dir, recovery_dir)
    routing = load_routing(out_dir)
    metrics = metric_regions(rebuilt)
    all_names = sorted(set(named_regions) | set(ledger) | set(metrics))
    dom = dom_summary(out_dir)
    gate = load_json(out_dir / "fidelity-gate.json", {})
    semantic = gate.get("component_semantic_evidence") if isinstance(gate, dict) else {}
    semantic_failures = {
        str(item.get("name")): item
        for item in (semantic.get("failures") if isinstance(semantic, dict) else []) or []
        if isinstance(item, dict) and item.get("name")
    }

    items: list[dict[str, Any]] = []
    for name in all_names:
        metric = metrics.get(name, {})
        ledger_region = ledger.get(name)
        region = named_regions.get(name, {})
        bounds = normalize_bounds(region) or normalize_bounds(metric) or normalize_bounds(ledger_region or {})
        track = infer_track(name, ledger_region, routing)
        asset = has_asset(ledger_region)
        strategy = str((ledger_region or {}).get("asset_strategy") or "none")
        strict = opt_float(metric.get("mismatch_pct"))
        tolerant = opt_float(metric.get("mismatch_pct_tolerant"))
        measured = strict is not None or tolerant is not None
        role = region_role(track, asset, strategy, allowed_tracks)
        item = {
            "name": name,
            "role": role,
            "track": track,
            "bounds": bounds,
            "area": (bounds["width"] * bounds["height"]) if bounds else None,
            "measured": measured,
            "strict_mismatch_pct": strict,
            "tolerant_mismatch_pct": tolerant,
            "asset": asset,
            "asset_provider": str((ledger_region or {}).get("asset_provider") or "none"),
            "asset_strategy": strategy,
            "ledger_reason": (ledger_region or {}).get("reason"),
            "primitive_targets": primitive_hints(name, track),
            "next_actions": next_actions(role, name, as_float(strict), as_float(tolerant)),
            "semantic_failure": semantic_failures.get(name),
        }
        if role == "component-required" and name in semantic_failures:
            collection_hint = "collection/list template with data-element-item rows"
            if collection_hint not in item["primitive_targets"]:
                item["primitive_targets"].insert(0, collection_hint)
            item["next_actions"].insert(
                1,
                "Promote loose measured primitives into a data-driven collection/table/list component contract.",
            )
        items.append(item)

    def sort_key(item: dict[str, Any]) -> tuple[int, int, float, float]:
        role_rank = {
            "asset-disallowed": 0,
            "component-required": 1,
            "approximation-library": 2,
            "island-missing-asset": 3,
            "approved-island": 4,
            "diagnostic-slice": 5,
        }.get(str(item["role"]), 6)
        # Unmeasured regions sort first within a role: no data means most in need of attention.
        measured_rank = 1 if item.get("measured") else 0
        return (role_rank, measured_rank, -as_float(item.get("tolerant_mismatch_pct")), -as_float(item.get("strict_mismatch_pct")))

    items.sort(key=sort_key)
    component_items = [item for item in items if item["role"] == "component-required"]
    approved_islands = [item for item in items if item["role"] == "approved-island"]
    disallowed_assets = [item for item in items if item["role"] == "asset-disallowed"]
    strict_match = 100.0 - as_float(rebuilt.get("mismatch_pct"), 100.0)
    tolerant_match = 100.0 - as_float(rebuilt.get("mismatch_pct_tolerant"), 100.0)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "out_dir": str(out_dir),
        "target_match_pct": target_match,
        "allowed_asset_tracks": sorted(allowed_tracks),
        "summary": {
            "strict_match_pct": strict_match,
            "tolerant_match_pct": tolerant_match,
            "reference_mismatch_pct": opt_float(reference.get("mismatch_pct")),
            "exact_mismatch_pct": opt_float(exact.get("mismatch_pct")),
            "component_required_count": len(component_items),
            "approved_island_count": len(approved_islands),
            "disallowed_asset_count": len(disallowed_assets),
            "component_semantic_failure_count": len(semantic_failures),
            "component_semantic_failure_regions": sorted(semantic_failures),
            "worst_component_regions": [item["name"] for item in component_items[:8]],
        },
        "dom_evidence": dom,
        "regions": items,
    }


def markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Component Primitive Worklist",
        "",
        f"Generated: `{report['generated_at']}`",
        f"Out dir: `{report['out_dir']}`",
        f"Target strict match: `{report['target_match_pct']:.4f}%`",
        "",
        "## Summary",
        "",
        f"- strict match: `{summary['strict_match_pct']:.4f}%`",
        f"- tolerant match: `{summary['tolerant_match_pct']:.4f}%`",
        f"- reference mismatch: `{fmt_pct(summary['reference_mismatch_pct'])}`",
        f"- exact mismatch: `{fmt_pct(summary['exact_mismatch_pct'])}`",
        f"- component-required regions: `{summary['component_required_count']}`",
        f"- approved islands: `{summary['approved_island_count']}`",
        f"- disallowed asset regions: `{summary['disallowed_asset_count']}`",
        f"- component semantic failures: `{summary.get('component_semantic_failure_count', 0)}`",
        "",
        "## Worklist",
        "",
        "| Priority | Region | Role | Track | Strict mismatch | Tolerant mismatch | Primitive targets |",
        "| ---: | --- | --- | --- | ---: | ---: | --- |",
    ]

    priority = 1
    for item in report["regions"]:
        if item["role"] in {"approved-island", "diagnostic-slice"}:
            continue
        targets = ", ".join(item["primitive_targets"][:6])
        lines.append(
            f"| {priority} | `{item['name']}` | `{item['role']}` | `{item['track']}` | "
            f"{fmt_pct(item['strict_mismatch_pct'], 'n/a (unmeasured)')} | "
            f"{fmt_pct(item['tolerant_mismatch_pct'], 'n/a (unmeasured)')} | {targets} |"
        )
        priority += 1

    lines += ["", "## Region Details", ""]
    for item in report["regions"]:
        if item["role"] in {"approved-island", "diagnostic-slice"}:
            continue
        lines += [
            f"### `{item['name']}`",
            "",
            f"- role: `{item['role']}`; track: `{item['track']}`; bounds: `{item['bounds']}`",
            f"- mismatch: strict `{fmt_pct(item['strict_mismatch_pct'], 'n/a (unmeasured)')}`, tolerant `{fmt_pct(item['tolerant_mismatch_pct'], 'n/a (unmeasured)')}`",
            f"- asset: `{item['asset']}`; provider: `{item['asset_provider']}`; strategy: `{item['asset_strategy']}`",
            f"- primitives: {', '.join(item['primitive_targets'])}",
            f"- next: {'; '.join(item['next_actions'])}",
        "",
        ]
        if item.get("semantic_failure"):
            failure = item["semantic_failure"]
            lines.insert(
                -1,
                f"- semantic: missing `{failure.get('required')}` evidence "
                f"(data-element-item={failure.get('data_element_item_count', 0)}, "
                f"table={failure.get('table_count', 0)}, list_role={failure.get('list_role_count', 0)})",
            )

    island_lines = [item for item in report["regions"] if item["role"] == "approved-island"]
    if island_lines:
        lines += ["## Approved Islands", ""]
        for item in island_lines:
            lines.append(
                f"- `{item['name']}`: bounds `{item['bounds']}`, provider `{item['asset_provider']}`, strategy `{item['asset_strategy']}`"
            )
        lines.append("")

    dom = report.get("dom_evidence") or {}
    lines += [
        "## DOM Evidence",
        "",
        f"- source: `{dom.get('source') or 'none'}`",
        f"- tag_counts: `{dom.get('tag_counts') or {}}`",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True, help="Pixel Twin Lab output directory")
    parser.add_argument("--recovery-dir", default="recovery", help="Recovery directory containing component-ledger.json")
    parser.add_argument("--target-match", type=float, default=98.0, help="Target strict match percentage")
    parser.add_argument(
        "--allowed-asset-tracks",
        default="island",
        help="Comma-separated tracks that may use generated assets",
    )
    parser.add_argument("--json-name", default="component-primitives.json", help="JSON output filename")
    parser.add_argument("--md-name", default="component-primitives.md", help="Markdown output filename")
    args = parser.parse_args()

    out_dir = Path(args.out_dir).expanduser().resolve()
    allowed_tracks = {track.strip() for track in args.allowed_asset_tracks.split(",") if track.strip()}
    report = build_report(out_dir, args.recovery_dir, allowed_tracks, args.target_match)
    (out_dir / args.json_name).write_text(json.dumps(report, indent=2), encoding="utf-8")
    (out_dir / args.md_name).write_text(markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "out_dir": str(out_dir),
                "strict_match_pct": round(report["summary"]["strict_match_pct"], 4),
                "component_required_count": report["summary"]["component_required_count"],
                "approved_island_count": report["summary"]["approved_island_count"],
                "disallowed_asset_count": report["summary"]["disallowed_asset_count"],
                "component_semantic_failure_count": report["summary"].get("component_semantic_failure_count", 0),
                "worst_component_regions": report["summary"]["worst_component_regions"][:5],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
