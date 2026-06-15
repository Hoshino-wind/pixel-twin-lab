#!/usr/bin/env python3
"""Gate Pixel Twin fidelity without confusing component-only and hybrid asset results."""

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


def find_capture(summary: list[dict[str, Any]], file_name: str) -> dict[str, Any]:
    for item in summary:
        if item.get("file") == file_name:
            return item
    return {}


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def pct(value: Any) -> str:
    return f"{as_float(value):.4f}%"


def parse_attrs(tag: str) -> dict[str, str]:
    return {key: value for key, value in re.findall(r'([a-zA-Z0-9_-]+)="([^"]*)"', tag)}


def strategy_from_dom(index_html: str) -> list[dict[str, str]]:
    regions: list[dict[str, str]] = []
    for match in re.finditer(r"<section\b[^>]*>", index_html, re.IGNORECASE):
        attrs = parse_attrs(match.group(0))
        if "data-asset-provider" not in attrs and "data-has-asset" not in attrs:
            continue
        regions.append(
            {
                "name": attrs.get("aria-label", "unknown"),
                "provider": attrs.get("data-asset-provider", "none"),
                "strategy": attrs.get("data-asset-strategy", "none"),
                "has_asset": attrs.get("data-has-asset", "false"),
                "track": attrs.get("data-track", "unknown"),
            }
        )
    return regions


def declared_asset_context(index_html: str, position: int, attrs: dict[str, str]) -> bool:
    if attrs.get("data-has-asset") == "true" or attrs.get("data-asset-provider") not in (None, "", "none"):
        return True
    if "data-element-asset-id" in attrs or "data-pixel-twin-element-asset-overlay" in attrs:
        return True
    for tag in ("span", "button", "a", "div", "section"):
        last_open = index_html.rfind(f"<{tag}", 0, position)
        last_close = index_html.rfind(f"</{tag}>", 0, position)
        if last_open <= last_close:
            continue
        tag_end = index_html.find(">", last_open)
        if tag_end == -1 or tag_end > position:
            continue
        ancestor_attrs = parse_attrs(index_html[last_open : tag_end + 1])
        if "data-element-asset-id" in ancestor_attrs or "data-pixel-twin-element-asset-overlay" in ancestor_attrs:
            return True
    last_section = index_html.rfind("<section", 0, position)
    last_section_close = index_html.rfind("</section>", 0, position)
    if last_section <= last_section_close:
        return False
    section_end = index_html.find(">", last_section)
    if section_end == -1 or section_end > position:
        return False
    section_attrs = parse_attrs(index_html[last_section : section_end + 1])
    return (
        section_attrs.get("data-has-asset") == "true"
        or section_attrs.get("data-asset-provider") not in (None, "", "none")
        or "data-asset-strategy" in section_attrs
    )


def collect_element_asset_evidence(out_dir: Path, index_html: str) -> dict[str, Any]:
    assets_path = out_dir / "element-assets.json"
    assets_report = load_json(assets_path, {})
    assets = assets_report.get("assets") if isinstance(assets_report, dict) else None
    if not isinstance(assets, list):
        assets = []

    type_counts: dict[str, int] = {}
    source_counts: dict[str, int] = {}
    asset_ids: set[str] = set()
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        asset_id = str(asset.get("id") or "")
        if asset_id:
            asset_ids.add(asset_id)
        asset_type = str(asset.get("asset_type") or "asset")
        source = str(asset.get("source") or "unknown")
        type_counts[asset_type] = type_counts.get(asset_type, 0) + 1
        source_counts[source] = source_counts.get(source, 0) + 1

    manifest_path = out_dir / "element-manifest.json"
    manifest = load_json(manifest_path, {})
    required_asset_ids: set[str] = set()
    required_asset_elements = 0
    manifest_elements = 0
    if isinstance(manifest, dict):
        for region in manifest.get("regions") or []:
            if not isinstance(region, dict):
                continue
            for element in region.get("elements") or []:
                if not isinstance(element, dict):
                    continue
                manifest_elements += 1
                if element.get("requires_asset") or element.get("asset_path") or element.get("asset_id"):
                    required_asset_elements += 1
                    asset_id = str(element.get("asset_id") or "")
                    if asset_id:
                        required_asset_ids.add(asset_id)

    dom_ids = set(re.findall(r'data-element-asset-id="([^"]+)"', index_html))
    overlay_blocks = re.findall(
        r'data-pixel-twin-element-asset-overlay[^>]*>[\s\S]*?</div>',
        index_html,
        flags=re.IGNORECASE,
    )
    overlay_ids: set[str] = set()
    for block in overlay_blocks:
        overlay_ids.update(re.findall(r'data-element-asset-id="([^"]+)"', block))
    rendered_ids = dom_ids - overlay_ids

    missing_manifest_assets = sorted(required_asset_ids - asset_ids)
    missing_rendered_assets = sorted(required_asset_ids - rendered_ids)
    return {
        "source": str(assets_path) if assets_path.exists() else None,
        "manifest_source": str(manifest_path) if manifest_path.exists() else None,
        "asset_count": len(assets),
        "asset_type_counts": type_counts,
        "asset_source_counts": source_counts,
        "manifest_element_count": manifest_elements,
        "required_asset_element_count": required_asset_elements,
        "required_asset_ids": sorted(required_asset_ids),
        "missing_manifest_asset_ids": missing_manifest_assets,
        "dom_asset_count": len(dom_ids),
        "overlay_dom_asset_count": len(overlay_ids),
        "rendered_dom_asset_count": len(rendered_ids),
        "rendered_dom_asset_ids": sorted(rendered_ids),
        "missing_rendered_asset_ids": missing_rendered_assets,
        "overlay_present": bool(overlay_blocks),
        "pass": bool(required_asset_ids) and not missing_manifest_assets and not missing_rendered_assets,
        "reason": (
            "required element assets are present in the manifest and rendered as non-overlay DOM assets"
            if required_asset_ids and not missing_manifest_assets and not missing_rendered_assets
            else (
                "no element manifest asset requirements declared"
                if not required_asset_ids
                else "some required element assets are missing from the asset ledger or rendered DOM"
            )
        ),
    }


def region_section_blocks(index_html: str) -> dict[str, str]:
    blocks: dict[str, str] = {}
    pattern = re.compile(r"<section\b(?=[^>]*\bpt-region\b)([^>]*)>([\s\S]*?)</section>", re.IGNORECASE)
    for match in pattern.finditer(index_html):
        attrs = parse_attrs(match.group(1))
        name = attrs.get("aria-label")
        if name:
            blocks[name] = match.group(2)
    return blocks


def semantic_track_lookup(ledger: dict[str, Any]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    if isinstance(ledger, dict):
        for region in ledger.get("regions") or []:
            if isinstance(region, dict) and region.get("name") and region.get("track"):
                lookup[str(region["name"])] = str(region["track"])
    return lookup


def collection_semantic_required(name: str) -> bool:
    lowered = name.lower()
    if lowered.startswith("kpi-"):
        return False
    return any(
        token in lowered
        for token in (
            "audit",
            "bottom-nav",
            "checklist",
            "deployment",
            "incident",
            "itinerary",
            "packing",
            "queue",
            "recommendation",
            "regional-health",
            "runbook",
            "sidebar",
            "table",
            "timeline",
        )
    )


def collect_component_semantic_evidence(out_dir: Path, index_html: str, ledger: dict[str, Any]) -> dict[str, Any]:
    manifest = load_json(out_dir / "element-manifest.json", {})
    regions = manifest.get("regions") if isinstance(manifest, dict) else []
    if not isinstance(regions, list):
        regions = []
    sections = region_section_blocks(index_html)
    tracks = semantic_track_lookup(ledger)
    checked: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for region in regions:
        if not isinstance(region, dict) or not region.get("name"):
            continue
        name = str(region["name"])
        track = tracks.get(name, str(region.get("track") or "component"))
        if track != "component" or not collection_semantic_required(name):
            continue
        block = sections.get(name, "")
        evidence = {
            "data_element_item_count": len(re.findall(r"\bdata-element-item=", block)),
            "table_count": len(re.findall(r"<table\b", block, flags=re.IGNORECASE)),
            "list_role_count": len(re.findall(r'role="(?:list|listitem|row|grid)"', block, flags=re.IGNORECASE)),
            "collection_kind_count": len(re.findall(r'data-kind="[^"]*collection[^"]*"', block, flags=re.IGNORECASE)),
        }
        ok = any(value > 0 for value in evidence.values())
        item = {"name": name, "track": track, "required": "collection", "pass": ok, **evidence}
        checked.append(item)
        if not ok:
            failures.append(item)
    return {
        "source": str(out_dir / "element-manifest.json") if (out_dir / "element-manifest.json").exists() else None,
        "checked_region_count": len(checked),
        "failure_count": len(failures),
        "checked_regions": checked,
        "failures": failures,
        "pass": len(failures) == 0,
        "reason": (
            "collection-like component regions have collection/table/list DOM evidence"
            if not failures
            else "collection-like component regions are rendered as loose primitives instead of data-driven collections"
        ),
    }


def collect_undeclared_bitmap_evidence(out_dir: Path, index_html: str) -> dict[str, Any]:
    """Find bitmap-like DOM/CSS evidence that is not tied to the recovery asset ledger.

    The component gates are only meaningful when images are declared as region-scoped
    assets or represented as addressable DOM elements. A plain full-page <img>, or a
    CSS background image, otherwise looks like a no-asset component pass to metrics.
    """
    items: list[dict[str, str]] = []
    for match in re.finditer(r"<img\b[^>]*>", index_html, re.IGNORECASE):
        tag = match.group(0)
        attrs = parse_attrs(tag)
        class_name = attrs.get("class", "")
        src = attrs.get("src", "")
        if "reference-layer" in class_name:
            continue
        if declared_asset_context(index_html, match.start(), attrs):
            continue
        items.append({"kind": "img", "source": "index.html", "src": src or "unknown"})

    background_pattern = re.compile(r"background(?:-image)?\s*:[^;{}]*url\(([^)]*)\)", re.IGNORECASE)
    sources = [("index.html", index_html)]
    styles_path = out_dir / "styles.css"
    if styles_path.exists():
        sources.append(("styles.css", styles_path.read_text(encoding="utf-8", errors="ignore")))
    for source_name, text in sources:
        for match in background_pattern.finditer(text):
            url = match.group(1).strip("\"' ")
            if not url:
                continue
            if source_name == "styles.css":
                selector_start = text.rfind("}", 0, match.start()) + 1
                selector_end = text.rfind("{", 0, match.start())
                selector = text[selector_start:selector_end] if selector_end >= selector_start else ""
                if ".pt-background-patch--" in selector:
                    continue
            items.append({"kind": "css-background", "source": source_name, "src": url})
    return {
        "count": len(items),
        "items": items,
        "pass": len(items) == 0,
        "reason": (
            "no undeclared bitmap DOM/CSS evidence found"
            if not items
            else "bitmap images must be declared as region-scoped assets or rebuilt as DOM elements"
        ),
    }


def collect_asset_evidence(out_dir: Path, ledger: dict[str, Any], ledger_path: Path) -> dict[str, Any]:
    index_path = out_dir / "index.html"
    if index_path.exists():
        dom_regions = strategy_from_dom(index_path.read_text(encoding="utf-8", errors="ignore"))
        if dom_regions:
            regions: list[dict[str, Any]] = []
            for region in dom_regions:
                regions.append(
                    {
                        "name": region.get("name"),
                        "track": region.get("track", "unknown"),
                        "asset": region.get("has_asset") == "true",
                        "asset_provider": region.get("provider", "none"),
                        "asset_strategy": region.get("strategy", "none"),
                    }
                )
            return summarize_asset_regions(regions, str(index_path))

    regions: list[dict[str, Any]] = []
    evidence_source = None
    if isinstance(ledger, dict) and isinstance(ledger.get("regions"), list):
        evidence_source = str(ledger_path)
        for region in ledger["regions"]:
            if not isinstance(region, dict):
                continue
            regions.append(
                {
                    "name": region.get("name"),
                    "track": region.get("track"),
                    "asset": region.get("asset"),
                    "asset_provider": region.get("asset_provider", "none"),
                    "asset_strategy": region.get("asset_strategy", "none"),
                }
            )

    if not regions:
        return summarize_asset_regions([], None)
    return summarize_asset_regions(regions, evidence_source)


def region_bounds_lookup(out_dir: Path, ledger: dict[str, Any]) -> dict[str, dict[str, int]]:
    lookup: dict[str, dict[str, int]] = {}
    sources: list[Any] = []
    regions_data = load_json(out_dir / "regions.json", [])
    if isinstance(regions_data, dict):
        regions_data = regions_data.get("regions", [])
    if isinstance(regions_data, list):
        sources.append(regions_data)
    if isinstance(ledger, dict) and isinstance(ledger.get("regions"), list):
        sources.append(ledger["regions"])
    for source in sources:
        for region in source:
            if not isinstance(region, dict):
                continue
            name = str(region.get("name") or "")
            try:
                bounds = {
                    "x": int(region["x"]),
                    "y": int(region["y"]),
                    "width": int(region["width"]),
                    "height": int(region["height"]),
                }
            except (KeyError, TypeError, ValueError):
                continue
            if name and bounds["width"] > 0 and bounds["height"] > 0:
                lookup[name] = bounds
    return lookup


def page_dimensions(out_dir: Path, ledger: dict[str, Any]) -> tuple[int, int] | None:
    viewport = ledger.get("viewport") if isinstance(ledger, dict) else None
    if isinstance(viewport, dict):
        width, height = int(as_float(viewport.get("width"))), int(as_float(viewport.get("height")))
        if width > 0 and height > 0:
            return width, height
    lab = load_json(out_dir / "lab-config.json", {})
    if isinstance(lab, dict):
        width, height = int(as_float(lab.get("width"))), int(as_float(lab.get("height")))
        if width > 0 and height > 0:
            return width, height
    return None


def measure_asset_coverage(
    asset_evidence: dict[str, Any],
    bounds_lookup: dict[str, dict[str, int]],
    page: tuple[int, int] | None,
    max_single_pct: float,
) -> dict[str, Any]:
    page_area = page[0] * page[1] if page else 0
    measured: list[dict[str, Any]] = []
    unknown: list[str] = []
    for region in asset_evidence.get("generated_regions") or []:
        name = str(region.get("name") or "unknown")
        bounds = bounds_lookup.get(name)
        if bounds is None or page_area <= 0:
            unknown.append(name)
            continue
        width = max(0, min(bounds["width"], page[0] - max(0, bounds["x"])))
        height = max(0, min(bounds["height"], page[1] - max(0, bounds["y"])))
        area_pct = width * height / page_area * 100.0
        measured.append({"name": name, "track": region.get("track"), "area_pct": round(area_pct, 4)})
    total_pct = round(sum(item["area_pct"] for item in measured), 4)
    surface_patches = [item for item in measured if item["area_pct"] > max_single_pct]
    return {
        "page": {"width": page[0], "height": page[1]} if page else None,
        "total_asset_coverage_pct": total_pct,
        "measured_regions": sorted(measured, key=lambda item: item["area_pct"], reverse=True),
        "unmeasurable_regions": unknown,
        "surface_patch_regions": surface_patches,
    }


def summarize_asset_regions(regions: list[dict[str, Any]], evidence_source: str | None) -> dict[str, Any]:
    counts: dict[str, int] = {}
    track_counts: dict[str, int] = {}
    generated: list[dict[str, Any]] = []
    for region in regions:
        strategy = str(region.get("asset_strategy") or "none")
        provider = str(region.get("asset_provider") or "none")
        has_asset = bool(region.get("asset")) or strategy != "none" or provider != "none"
        if not has_asset:
            continue
        counts[strategy] = counts.get(strategy, 0) + 1
        track = str(region.get("track") or "unknown")
        track_counts[track] = track_counts.get(track, 0) + 1
        generated.append(region)

    return {
        "source": evidence_source,
        "generated_asset_count": len(generated),
        "asset_strategy_counts": counts,
        "asset_track_counts": track_counts,
        "generated_regions": generated,
    }


def rebuilt_region_metrics(rebuilt: dict[str, Any]) -> dict[str, dict[str, Any]]:
    metrics: dict[str, dict[str, Any]] = {}
    for region in rebuilt.get("regions") or []:
        if isinstance(region, dict) and region.get("name"):
            metrics[str(region["name"])] = region
    return metrics


def evaluate_approximation_regions(
    ledger: dict[str, Any],
    region_metrics: dict[str, dict[str, Any]],
    structural: dict[str, Any],
    approximation_tracks: set[str],
    tolerant_max: float,
    require_structural: bool,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    regions = ledger.get("regions") if isinstance(ledger, dict) else None
    for region in regions or []:
        if not isinstance(region, dict):
            continue
        if str(region.get("track") or "") not in approximation_tracks:
            continue
        name = str(region.get("name") or "unknown")
        eval_mode = str(region.get("eval") or "tolerant+structural")
        metric_entry = region_metrics.get(name)
        notes: list[str] = []

        if eval_mode == "structural-only":
            pixel_ok = True
            tolerant = as_float((metric_entry or {}).get("mismatch_pct_tolerant"), None)
            notes.append("pixel metrics exempt (independent rendering pipeline, e.g. WebGL)")
        else:
            tolerant = as_float((metric_entry or {}).get("mismatch_pct_tolerant"), None) if metric_entry else None
            if tolerant is None:
                pixel_ok = False
                notes.append("tolerant mismatch missing; run pixel_diff.py with --tolerance and a regions entry for this name")
            else:
                pixel_ok = tolerant <= tolerant_max
                if not pixel_ok:
                    notes.append(f"tolerant mismatch {tolerant:.2f}% exceeds {tolerant_max:.2f}%")

        structural_entry = structural.get(name) if isinstance(structural, dict) else None
        structural_ok: bool | None
        if isinstance(structural_entry, dict) and "pass" in structural_entry:
            structural_ok = bool(structural_entry["pass"])
            if not structural_ok:
                notes.append("structural comparison failed; see structural-comparison.md")
        else:
            structural_ok = None
            notes.append("structural comparison not run (compare_structure.py)")

        region_pass = pixel_ok and structural_ok is not False
        if require_structural and structural_ok is not True:
            region_pass = False
        results.append(
            {
                "name": name,
                "track": region.get("track"),
                "eval": eval_mode,
                "strategy": region.get("strategy"),
                "tolerant_mismatch_pct": tolerant,
                "pixel_pass": pixel_ok,
                "structural_pass": structural_ok,
                "pass": region_pass,
                "notes": notes,
            }
        )
    return results


def component_track_strict(
    rebuilt: dict[str, Any],
    region_metrics: dict[str, dict[str, Any]],
    approximation_names: list[str],
    page: tuple[int, int] | None,
) -> dict[str, Any]:
    whole_mismatch_pct = as_float(rebuilt.get("mismatch_pct"), 100.0)
    total_mismatch = as_float(rebuilt.get("mismatch_pixels"), None)
    if page:
        total_pixels = page[0] * page[1]
    elif total_mismatch is not None and whole_mismatch_pct > 0:
        total_pixels = int(round(total_mismatch / whole_mismatch_pct * 100.0))
    else:
        total_pixels = 0
    if total_mismatch is None and total_pixels:
        total_mismatch = total_pixels * whole_mismatch_pct / 100.0

    unmeasured: list[str] = []
    excluded_mismatch = 0.0
    excluded_area = 0
    for name in approximation_names:
        entry = region_metrics.get(name)
        mismatch = as_float((entry or {}).get("mismatch_pixels"), None) if entry else None
        if entry is None or mismatch is None:
            unmeasured.append(name)
            continue
        excluded_mismatch += mismatch
        excluded_area += int(as_float(entry.get("width"))) * int(as_float(entry.get("height")))

    if not total_pixels or total_mismatch is None or unmeasured:
        return {
            "strict_mismatch_pct": None,
            "strict_match_pct": None,
            "excluded_area_px": excluded_area,
            "unmeasured_regions": unmeasured,
            "note": "component-track strict mismatch could not be computed"
            + (f"; approximation regions without per-region metrics: {unmeasured}" if unmeasured else "; page dimensions or capture metrics missing"),
        }
    denominator = max(1, total_pixels - excluded_area)
    mismatch_pct = max(0.0, total_mismatch - excluded_mismatch) / denominator * 100.0
    return {
        "strict_mismatch_pct": round(mismatch_pct, 4),
        "strict_match_pct": round(100.0 - mismatch_pct, 4),
        "excluded_area_px": excluded_area,
        "unmeasured_regions": [],
        "note": "whole-page strict metrics minus approximation-track regions (overlapping regions are subtracted once each)",
    }


def bad_asset_regions(asset_evidence: dict[str, Any], allowed_tracks: set[str]) -> list[dict[str, Any]]:
    bad: list[dict[str, Any]] = []
    for region in asset_evidence.get("generated_regions") or []:
        track = str(region.get("track") or "unknown")
        strategy = str(region.get("asset_strategy") or "none")
        if track not in allowed_tracks or strategy == "placeholder":
            bad.append(region)
    return bad


def status(pass_condition: bool, reason: str) -> dict[str, Any]:
    return {"pass": pass_condition, "reason": reason}


def render_markdown(report: dict[str, Any]) -> str:
    metrics = report["metrics"]
    assets = report["asset_evidence"]
    gates = report["gates"]
    lines = [
        "# Fidelity Gate",
        "",
        f"Generated: `{report['generated_at']}`",
        f"Out dir: `{report['out_dir']}`",
        f"Target match: `{report['target_match_pct']:.4f}%`",
        "",
        "## Metrics",
        "",
        "| Capture | Strict mismatch | Strict match | Tolerant mismatch | Tolerant match | MAE | Max delta |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for key in ("reference", "rebuilt", "exact"):
        item = metrics.get(key) or {}
        mismatch = as_float(item.get("mismatch_pct"))
        tolerant = as_float(item.get("mismatch_pct_tolerant"))
        lines.append(
            f"| `{key}` | {mismatch:.4f}% | {100 - mismatch:.4f}% | "
            f"{tolerant:.4f}% | {100 - tolerant:.4f}% | "
            f"{as_float(item.get('mae')):.4f} | {as_float(item.get('max_delta')):.0f} |"
        )

    lines += [
        "",
        "## Gates",
        "",
        "| Gate | Result | Reason |",
        "| --- | --- | --- |",
    ]
    for name, gate in gates.items():
        result = "PASS" if gate["pass"] else "FAIL"
        lines.append(f"| `{name}` | `{result}` | {gate['reason']} |")

    baseline = report.get("baseline") or {}
    coverage = report.get("asset_coverage") or {}
    lines += [
        "",
        "## Baseline",
        "",
        f"- result: `{'PASS' if baseline.get('pass') else 'FAIL'}`",
        f"- {baseline.get('reason', 'unknown')}",
        "",
        "## Asset Coverage",
        "",
        f"- result: `{'PASS' if coverage.get('pass') else 'FAIL'}`",
        f"- total_asset_coverage_pct: `{coverage.get('total_asset_coverage_pct', 0)}%` (max `{coverage.get('max_asset_coverage_pct')}%`)",
        f"- max_single_asset_coverage_pct: `{coverage.get('max_single_asset_coverage_pct')}%`",
    ]
    for problem in coverage.get("problems") or []:
        lines.append(f"- problem: {problem}")
    for item in (coverage.get("measured_regions") or [])[:10]:
        lines.append(f"- `{item.get('name')}` ({item.get('track')}): {item.get('area_pct')}% of page")
    bitmap = report.get("undeclared_bitmap_evidence") or {}
    lines += [
        "",
        "## Undeclared Bitmap Evidence",
        "",
        f"- result: `{'PASS' if bitmap.get('pass') else 'FAIL'}`",
        f"- {bitmap.get('reason', 'not checked')}",
    ]
    for item in bitmap.get("items") or []:
        lines.append(f"- `{item.get('kind')}` in `{item.get('source')}`: `{item.get('src')}`")
    element_assets = report.get("element_asset_evidence") or {}
    lines += [
        "",
        "## Element Asset Evidence",
        "",
        f"- result: `{'PASS' if element_assets.get('pass') else 'FAIL'}`",
        f"- {element_assets.get('reason', 'not checked')}",
        f"- source: `{element_assets.get('source') or 'none'}`",
        f"- manifest_source: `{element_assets.get('manifest_source') or 'none'}`",
        f"- asset_count: `{element_assets.get('asset_count', 0)}`",
        f"- asset_type_counts: `{element_assets.get('asset_type_counts', {})}`",
        f"- required_asset_element_count: `{element_assets.get('required_asset_element_count', 0)}`",
        f"- rendered_dom_asset_count: `{element_assets.get('rendered_dom_asset_count', 0)}`",
        f"- overlay_dom_asset_count: `{element_assets.get('overlay_dom_asset_count', 0)}`",
    ]
    if element_assets.get("overlay_present"):
        lines.append("- overlay diagnostic layer detected; this is evidence only, not componentized success")
    for asset_id in element_assets.get("missing_manifest_asset_ids") or []:
        lines.append(f"- missing asset ledger entry: `{asset_id}`")
    for asset_id in element_assets.get("missing_rendered_asset_ids") or []:
        lines.append(f"- missing rendered DOM asset: `{asset_id}`")
    semantic = report.get("component_semantic_evidence") or {}
    lines += [
        "",
        "## Component Semantic Evidence",
        "",
        f"- result: `{'PASS' if semantic.get('pass') else 'FAIL'}`",
        f"- {semantic.get('reason', 'not checked')}",
        f"- checked_region_count: `{semantic.get('checked_region_count', 0)}`",
        f"- failure_count: `{semantic.get('failure_count', 0)}`",
    ]
    for item in semantic.get("failures") or []:
        lines.append(
            f"- `{item.get('name')}` missing `{item.get('required')}` evidence "
            f"(data-element-item={item.get('data_element_item_count', 0)}, "
            f"table={item.get('table_count', 0)}, list_role={item.get('list_role_count', 0)})"
        )
    component_track = report.get("component_track") or {}
    approximations = report.get("approximation_regions") or []
    if approximations:
        match_pct = component_track.get("strict_match_pct")
        lines += [
            "",
            "## Component Track (approximation regions excluded)",
            "",
            f"- strict match: `{match_pct if match_pct is not None else 'n/a'}%`",
            f"- excluded area: `{component_track.get('excluded_area_px', 0)}px`",
            f"- {component_track.get('note', '')}",
            "",
            "## Approximation Regions",
            "",
            "| Region | Eval | Tolerant mismatch | Pixel | Structural | Result |",
            "| --- | --- | ---: | --- | --- | --- |",
        ]
        for item in approximations:
            tolerant = item.get("tolerant_mismatch_pct")
            structural = item.get("structural_pass")
            lines.append(
                f"| `{item['name']}` | `{item['eval']}` | "
                f"{f'{tolerant:.2f}%' if tolerant is not None else 'n/a'} | "
                f"{'PASS' if item['pixel_pass'] else 'FAIL'} | "
                f"{'PASS' if structural is True else 'FAIL' if structural is False else 'unverified'} | "
                f"{'PASS' if item['pass'] else 'FAIL'} |"
            )
        for item in approximations:
            for note in item.get("notes") or []:
                lines.append(f"- `{item['name']}`: {note}")
    lines += [
        "",
        "## Asset Evidence",
        "",
        f"- source: `{assets.get('source') or 'none'}`",
        f"- generated_asset_count: `{assets['generated_asset_count']}`",
        f"- asset_strategy_counts: `{assets['asset_strategy_counts']}`",
        f"- asset_track_counts: `{assets['asset_track_counts']}`",
        f"- allowed_asset_tracks: `{report['allowed_asset_tracks']}`",
        "",
    ]
    if report.get("bad_asset_regions"):
        lines += ["## Disallowed Asset Regions", ""]
        for region in report["bad_asset_regions"]:
            lines.append(
                f"- `{region.get('name')}`: provider `{region.get('asset_provider')}`, "
                f"strategy `{region.get('asset_strategy')}`, track `{region.get('track')}`"
            )
        lines.append("")
    if assets["generated_regions"]:
        lines += ["## Generated Asset Regions", ""]
        for region in assets["generated_regions"]:
            lines.append(
                f"- `{region.get('name')}`: provider `{region.get('asset_provider')}`, "
                f"strategy `{region.get('asset_strategy')}`, track `{region.get('track')}`"
            )
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True, help="Pixel Twin Lab output directory")
    parser.add_argument("--target-match", type=float, default=98.0, help="Required strict match percentage")
    parser.add_argument("--reference-target", type=float, default=0.01, help="Max allowed reference baseline mismatch; above this all fidelity gates fail")
    parser.add_argument(
        "--max-asset-coverage",
        type=float,
        default=40.0,
        help="Max total page area (pct) that generated island assets may cover for componentized_islands fidelity",
    )
    parser.add_argument(
        "--max-single-asset-coverage",
        type=float,
        default=30.0,
        help="Any single generated asset covering more page area (pct) than this is flagged as a surface patch and fails componentized fidelity",
    )
    parser.add_argument("--recovery-dir", default="recovery", help="Recovery directory to inspect for generated assets")
    parser.add_argument(
        "--approximation-tracks",
        default="approximation",
        help="Comma-separated ledger tracks evaluated per-region (third-party charts/maps/3D) instead of whole-page strict",
    )
    parser.add_argument(
        "--approximation-tolerant-max",
        type=float,
        default=25.0,
        help="Max tolerant mismatch (pct) for an approximation region with eval 'tolerant+structural'",
    )
    parser.add_argument(
        "--require-structural",
        action="store_true",
        help="Approximation regions must have a passing structural-comparison entry, not just an absent one",
    )
    parser.add_argument("--structural-name", default="structural-comparison.json", help="Structural comparison report filename")
    parser.add_argument(
        "--allowed-asset-tracks",
        default="island",
        help="Comma-separated ledger tracks where generated assets may be used for componentized-islands fidelity",
    )
    parser.add_argument("--json-name", default="fidelity-gate.json", help="JSON output filename")
    parser.add_argument("--md-name", default="fidelity-gate.md", help="Markdown output filename")
    args = parser.parse_args()

    out_dir = Path(args.out_dir).expanduser().resolve()
    summary = load_json(out_dir / "pixel-diff-summary.json", [])
    if not isinstance(summary, list):
        raise SystemExit("pixel-diff-summary.json is missing or invalid; run pixel_diff.py first.")

    reference = find_capture(summary, "reference-capture.png")
    rebuilt = find_capture(summary, "rebuilt-capture.png")
    exact = find_capture(summary, "exact-capture.png")
    if not rebuilt:
        raise SystemExit("rebuilt-capture.png metrics are missing; capture rebuilt mode and run pixel_diff.py first.")

    strict_mismatch = as_float(rebuilt.get("mismatch_pct"), 100.0)
    strict_match = 100.0 - strict_mismatch
    target_mismatch = max(0.0, 100.0 - args.target_match)

    reference_mismatch = as_float(reference.get("mismatch_pct"), None) if reference else None
    baseline_ok = reference_mismatch is not None and reference_mismatch <= args.reference_target
    baseline_reason = (
        f"reference baseline {reference_mismatch:.4f}% <= {args.reference_target:.4f}%"
        if baseline_ok
        else (
            "reference-capture.png metrics missing; the rendering environment is unproven"
            if reference_mismatch is None
            else f"reference baseline {reference_mismatch:.4f}% exceeds {args.reference_target:.4f}%; screenshot math is untrusted"
        )
    )

    ledger_path = out_dir / args.recovery_dir / "component-ledger.json"
    ledger = load_json(ledger_path, {})
    # classify_slices.py routing is an upstream track source: approximation regions routed
    # there must be evaluated per-region even when no recovery ledger exists yet.
    routing_manifest = load_json(out_dir / "routing-manifest.json", None)
    if isinstance(routing_manifest, dict) and isinstance(routing_manifest.get("regions"), list):
        ledger_names = {
            str(region.get("name"))
            for region in (ledger.get("regions") if isinstance(ledger, dict) else None) or []
            if isinstance(region, dict)
        }
        extra = [
            {**region.get("bounds", {}), "name": region["name"], "track": region["track"],
             **({"eval": region["eval"]} if region.get("eval") else {})}
            for region in routing_manifest["regions"]
            if isinstance(region, dict) and region.get("name") and region.get("track")
            and str(region["name"]) not in ledger_names
        ]
        if extra:
            if not isinstance(ledger, dict):
                ledger = {}
            ledger.setdefault("regions", [])
            if isinstance(ledger["regions"], list):
                ledger["regions"].extend(extra)
    index_path = out_dir / "index.html"
    index_html = index_path.read_text(encoding="utf-8", errors="ignore") if index_path.exists() else ""
    asset_evidence = collect_asset_evidence(out_dir, ledger, ledger_path)
    element_asset_evidence = collect_element_asset_evidence(out_dir, index_html)
    component_semantic_evidence = collect_component_semantic_evidence(out_dir, index_html, ledger)
    undeclared_bitmap_evidence = collect_undeclared_bitmap_evidence(out_dir, index_html)
    generated_assets = int(asset_evidence["generated_asset_count"])
    placeholder_assets = int(asset_evidence["asset_strategy_counts"].get("placeholder", 0))
    image2_assets = int(asset_evidence["asset_strategy_counts"].get("image2-extract", 0))
    element_asset_count = int(element_asset_evidence.get("asset_count") or 0)
    required_element_asset_count = int(element_asset_evidence.get("required_asset_element_count") or 0)
    rendered_element_asset_count = int(element_asset_evidence.get("rendered_dom_asset_count") or 0)
    overlay_element_asset_count = int(element_asset_evidence.get("overlay_dom_asset_count") or 0)
    element_assets_absent = (
        element_asset_count == 0
        and required_element_asset_count == 0
        and rendered_element_asset_count == 0
        and overlay_element_asset_count == 0
    )
    allowed_tracks = {track.strip() for track in args.allowed_asset_tracks.split(",") if track.strip()}
    disallowed_assets = bad_asset_regions(asset_evidence, allowed_tracks)

    coverage = measure_asset_coverage(
        asset_evidence,
        region_bounds_lookup(out_dir, ledger),
        page_dimensions(out_dir, ledger),
        args.max_single_asset_coverage,
    )
    coverage_problems: list[str] = []
    if generated_assets:
        if coverage["unmeasurable_regions"]:
            coverage_problems.append(
                f"asset regions without verifiable bounds: {coverage['unmeasurable_regions']}"
            )
        if coverage["surface_patch_regions"]:
            names = [item["name"] for item in coverage["surface_patch_regions"]]
            coverage_problems.append(
                f"surface-patch assets covering > {args.max_single_asset_coverage:.1f}% of the page each: {names}"
            )
        if coverage["total_asset_coverage_pct"] > args.max_asset_coverage:
            coverage_problems.append(
                f"total asset coverage {coverage['total_asset_coverage_pct']:.2f}% exceeds {args.max_asset_coverage:.1f}%"
            )
    coverage_ok = not coverage_problems

    approximation_tracks = {track.strip() for track in args.approximation_tracks.split(",") if track.strip()}
    structural_report = load_json(out_dir / args.structural_name, {})
    structural_regions = structural_report.get("regions") if isinstance(structural_report, dict) else {}
    if not isinstance(structural_regions, dict):
        structural_regions = {}
    region_metrics = rebuilt_region_metrics(rebuilt)
    approximation_results = evaluate_approximation_regions(
        ledger,
        region_metrics,
        structural_regions,
        approximation_tracks,
        args.approximation_tolerant_max,
        args.require_structural,
    )
    approximation_names = [item["name"] for item in approximation_results]
    component_track = component_track_strict(rebuilt, region_metrics, approximation_names, page_dimensions(out_dir, ledger))
    approximation_failures = [item for item in approximation_results if not item["pass"]]

    element_manifest_path = out_dir / "element-manifest.json"
    element_verification = load_json(out_dir / "element-verification.json", {})
    element_summary = element_verification.get("summary") if isinstance(element_verification, dict) else None
    if not element_manifest_path.exists():
        element_ok: bool | None = None
        element_reason = (
            "no element manifest declared; element-level contract unchecked "
            "(measure_primitives.py -> init_element_manifest.py -> label -> verify_elements.py)"
        )
    elif not isinstance(element_summary, dict):
        element_ok = False
        element_reason = (
            "element-manifest.json exists but element-verification.json is missing; "
            "run measure_dom_elements.cjs and verify_elements.py"
        )
    else:
        element_ok = bool(element_summary.get("pass"))
        element_reason = (
            f"{element_summary.get('ok', 0)}/{element_summary.get('total', 0)} elements verified, "
            f"missing {element_summary.get('missing', 0)}, failed {element_summary.get('failed', 0)}, "
            f"unlabeled {element_summary.get('unlabeled', 0)}"
        )

    metric_pass = baseline_ok and strict_mismatch <= target_mismatch
    bitmap_ok = bool(undeclared_bitmap_evidence["pass"])
    semantic_ok = bool(component_semantic_evidence["pass"])
    component_only_pass = (
        metric_pass
        and generated_assets == 0
        and element_assets_absent
        and bitmap_ok
        and element_ok is not False
        and semantic_ok
    )
    componentized_islands_pass = (
        metric_pass
        and not disallowed_assets
        and coverage_ok
        and bitmap_ok
        and element_ok is not False
        and semantic_ok
    )
    hybrid_pass = metric_pass and image2_assets > 0
    placeholder_contract = placeholder_assets > 0

    component_track_match = component_track.get("strict_match_pct")
    if not approximation_results:
        approximation_pass = False
        approximation_reason = "no approximation-track regions declared; use componentized_islands_98 instead"
    elif not baseline_ok:
        approximation_pass = False
        approximation_reason = baseline_reason
    elif component_track_match is None:
        approximation_pass = False
        approximation_reason = component_track["note"]
    elif component_track_match < args.target_match:
        approximation_pass = False
        approximation_reason = (
            f"component-track strict match {component_track_match:.4f}% is below {args.target_match:.4f}% "
            "(whole page minus approximation regions)"
        )
    elif disallowed_assets or not coverage_ok or not bitmap_ok:
        approximation_pass = False
        approximation_reason = "island asset rules failed" + ("" if coverage_ok else f"; {'; '.join(coverage_problems)}")
        if not bitmap_ok:
            approximation_reason += "; undeclared bitmap DOM/CSS evidence found"
    elif approximation_failures:
        approximation_pass = False
        names = [item["name"] for item in approximation_failures]
        approximation_reason = f"approximation regions failed their own evaluation: {names}"
    elif element_ok is False:
        approximation_pass = False
        approximation_reason = f"element contract failed: {element_reason}"
    elif not semantic_ok:
        approximation_pass = False
        approximation_reason = f"component semantic contract failed: {component_semantic_evidence['reason']}"
    else:
        approximation_pass = True
        approximation_reason = (
            f"component-track strict match {component_track_match:.4f}% >= {args.target_match:.4f}%, island assets compliant, "
            f"and all {len(approximation_results)} approximation regions passed their per-region evaluation"
        )

    baseline_suffix = "" if baseline_ok else f"; {baseline_reason}"
    coverage_suffix = "" if coverage_ok else f"; {'; '.join(coverage_problems)}"
    element_suffix = "" if element_ok is not False else f"; element contract failed: {element_reason}"
    element_asset_suffix = (
        ""
        if element_assets_absent
        else (
            "; element asset evidence present "
            f"(ledger {element_asset_count}, required {required_element_asset_count}, "
            f"rendered {rendered_element_asset_count}, overlay {overlay_element_asset_count})"
        )
    )
    semantic_suffix = "" if semantic_ok else f"; component semantic contract failed: {component_semantic_evidence['failure_count']} collection-like regions lack collection/table/list DOM evidence"
    bitmap_suffix = "" if bitmap_ok else "; undeclared bitmap DOM/CSS evidence found"
    gates = {
        "component_only_98": status(
            component_only_pass,
            (
                f"strict match {strict_match:.4f}% >= {args.target_match:.4f}% with no generated or element bitmap assets"
                if component_only_pass
                else f"requires a proven baseline, strict match >= {args.target_match:.4f}%, generated_asset_count = 0, element asset counts = 0, no undeclared bitmap patches, and component semantic coverage; got strict match {strict_match:.4f}%, generated_asset_count {generated_assets}, element_asset_count {element_asset_count}{baseline_suffix}{element_asset_suffix}{bitmap_suffix}{element_suffix}{semantic_suffix}"
            ),
        ),
        "hybrid_asset_98": status(
            hybrid_pass,
            (
                f"strict match {strict_match:.4f}% >= {args.target_match:.4f}% using {image2_assets} image2-extract assets"
                if hybrid_pass
                else f"requires a proven baseline and strict match >= {args.target_match:.4f}% with image2-extract assets; got strict match {strict_match:.4f}% and image2 assets {image2_assets}{baseline_suffix}"
            ),
        ),
        "componentized_islands_98": status(
            componentized_islands_pass,
            (
                f"strict match {strict_match:.4f}% >= {args.target_match:.4f}%, all generated assets limited to tracks {sorted(allowed_tracks)}, and asset coverage {coverage['total_asset_coverage_pct']:.2f}% within {args.max_asset_coverage:.1f}%"
                if componentized_islands_pass
                else f"requires a proven baseline, strict match >= {args.target_match:.4f}%, generated assets only on tracks {sorted(allowed_tracks)}, region-scoped asset coverage, no undeclared bitmap patches, and component semantic coverage; got strict match {strict_match:.4f}% and {len(disallowed_assets)} disallowed asset regions{baseline_suffix}{coverage_suffix}{bitmap_suffix}{element_suffix}{semantic_suffix}"
            ),
        ),
        "componentized_approximation_98": status(approximation_pass, approximation_reason),
        "element_contract": status(
            element_ok is True,
            element_reason
            if element_ok is True
            else f"requires a fully labeled element manifest verified against the rendered DOM; {element_reason}",
        ),
        "placeholder_contract": status(
            placeholder_contract,
            (
                f"same-size placeholder path used for {placeholder_assets} assets; this is a contract gate, not a fidelity pass"
                if placeholder_contract
                else "no placeholder assets detected"
            ),
        ),
        "element_asset_contract": status(
            bool(element_asset_evidence["pass"]),
            str(element_asset_evidence["reason"]),
        ),
        "component_semantics_contract": status(
            semantic_ok,
            str(component_semantic_evidence["reason"]),
        ),
    }

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "out_dir": str(out_dir),
        "target_match_pct": args.target_match,
        "target_mismatch_pct": target_mismatch,
        "allowed_asset_tracks": sorted(allowed_tracks),
        "baseline": {"pass": baseline_ok, "reason": baseline_reason, "reference_target_pct": args.reference_target},
        "asset_coverage": {
            **coverage,
            "max_asset_coverage_pct": args.max_asset_coverage,
            "max_single_asset_coverage_pct": args.max_single_asset_coverage,
            "pass": coverage_ok,
            "problems": coverage_problems,
        },
        "undeclared_bitmap_evidence": undeclared_bitmap_evidence,
        "element_asset_evidence": element_asset_evidence,
        "component_semantic_evidence": component_semantic_evidence,
        "element_contract": {
            "declared": element_manifest_path.exists(),
            "pass": element_ok,
            "reason": element_reason,
            "summary": element_summary,
        },
        "component_track": component_track,
        "approximation_regions": approximation_results,
        "approximation_settings": {
            "tracks": sorted(approximation_tracks),
            "tolerant_max_pct": args.approximation_tolerant_max,
            "require_structural": args.require_structural,
            "structural_report": str(out_dir / args.structural_name),
        },
        "metrics": {"reference": reference, "rebuilt": rebuilt, "exact": exact},
        "asset_evidence": asset_evidence,
        "bad_asset_regions": disallowed_assets,
        "gates": gates,
    }

    (out_dir / args.json_name).write_text(json.dumps(report, indent=2), encoding="utf-8")
    (out_dir / args.md_name).write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "out_dir": str(out_dir),
                "strict_match_pct": round(strict_match, 4),
                "component_only_98": gates["component_only_98"]["pass"],
                "componentized_islands_98": gates["componentized_islands_98"]["pass"],
                "componentized_approximation_98": gates["componentized_approximation_98"]["pass"],
                "element_contract": gates["element_contract"]["pass"],
                "hybrid_asset_98": gates["hybrid_asset_98"]["pass"],
                "placeholder_contract": gates["placeholder_contract"]["pass"],
                "element_asset_contract": gates["element_asset_contract"]["pass"],
                "component_semantics_contract": gates["component_semantics_contract"]["pass"],
                "generated_asset_count": generated_assets,
                "element_asset_count": element_asset_evidence["asset_count"],
                "rendered_element_asset_count": element_asset_evidence["rendered_dom_asset_count"],
                "overlay_element_asset_count": element_asset_evidence["overlay_dom_asset_count"],
                "undeclared_bitmap_count": undeclared_bitmap_evidence["count"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
