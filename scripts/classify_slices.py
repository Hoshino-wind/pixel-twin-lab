#!/usr/bin/env python3
"""Classify slice content BEFORE blueprint/code work, and route each slice to its track.

This is the Phase 1.5 routing gate: after prepare_lab.py slices the reference, every named
slice gets a content_type, and the content_type decides the handling up front — instead of
hand-building everything as DOM and discovering charts/maps/icons through diff failures:

  plain-dom -> component track (DOM/CSS rebuild, strict pixel loop)
  icon      -> island asset (crop, or regenerate with transparent background)
  image     -> island asset (crop)
  chart     -> approximation track, third-party chart library (default echarts) fed mock data
  map       -> approximation track, third-party map library (default leaflet)
  scene-3d  -> approximation track, third-party 3D library (default three), structural-only eval
  mixed     -> split it: re-slice into separate regions, then classify those

Usage:
  --mode init   writes slice-classification.json (scaffold) and classification-sheet.png
                (all crops on one labeled sheet, so classification costs ONE image read).
                Re-running merges new slices without clobbering existing labels.
  --mode apply  validates that every entry is labeled, then writes routing-manifest.json
                and merges track/handling into regions.json. Unlabeled entries fail (exit 1).

Downstream: bootstrap_recovery.py and the orchestrator read routing-manifest.json as the
authoritative track source; name-pattern guessing is only the fallback when no manifest exists.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

CONTENT_TYPES = ("plain-dom", "icon", "image", "chart", "map", "scene-3d", "mixed")
ICON_HANDLINGS = ("crop-asset", "regenerate-transparent")

ROUTING_RULES: dict[str, dict[str, Any]] = {
    "plain-dom": {"track": "component", "handling": "dom-rebuild"},
    "icon": {"track": "island", "handling": "crop-asset"},
    "image": {"track": "island", "handling": "crop-asset"},
    "chart": {"track": "approximation", "handling": "third-party-library", "library": "echarts",
              "eval": "tolerant+structural", "data": "mock-data-fed"},
    "map": {"track": "approximation", "handling": "third-party-library", "library": "leaflet",
            "eval": "tolerant+structural", "data": "mock-data-fed"},
    "scene-3d": {"track": "approximation", "handling": "third-party-library", "library": "three",
                 "eval": "structural-only"},
}


def load_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return fallback


def named_slices(out_dir: Path) -> list[dict[str, Any]]:
    """Named slices from lab-config.json plus named rectangles from regions.json, deduped by name."""
    found: dict[str, dict[str, Any]] = {}
    lab = load_json(out_dir / "lab-config.json", {})
    for slice_entry in lab.get("slices", []) if isinstance(lab, dict) else []:
        if not isinstance(slice_entry, dict):
            continue
        name = slice_entry.get("name")
        if not name or str(name).startswith("gap-"):
            continue
        if all(key in slice_entry for key in ("x", "y", "width", "height")):
            found[str(name)] = {key: int(slice_entry[key]) for key in ("x", "y", "width", "height")}
    regions_json = load_json(out_dir / "regions.json", {})
    raw = regions_json.get("regions") if isinstance(regions_json, dict) else regions_json
    for region in raw if isinstance(raw, list) else []:
        if not isinstance(region, dict) or not region.get("name"):
            continue
        if str(region["name"]).startswith("gap-"):
            continue
        if all(key in region for key in ("x", "y", "width", "height")):
            found.setdefault(str(region["name"]), {key: int(region[key]) for key in ("x", "y", "width", "height")})
    entries = [{"name": name, "bounds": bounds} for name, bounds in found.items()]
    entries.sort(key=lambda entry: (entry["bounds"]["y"], entry["bounds"]["x"]))
    return entries


def build_sheet(reference: Image.Image, entries: list[dict[str, Any]], column_width: int, columns: int) -> Image.Image:
    font = ImageFont.load_default()
    title_h, pad = 16, 10
    cells: list[Image.Image] = []
    for entry in entries:
        b = entry["bounds"]
        crop = reference.crop((b["x"], b["y"], b["x"] + b["width"], b["y"] + b["height"]))
        scale = min(1.0, column_width / max(1, crop.width))
        crop = crop.resize((max(1, int(crop.width * scale)), max(1, int(crop.height * scale))))
        cell = Image.new("RGB", (column_width, crop.height + title_h + pad), "#eef2f6")
        draw = ImageDraw.Draw(cell)
        draw.text((0, 0), f"{entry['name']} ({b['width']}x{b['height']})", fill="#111827", font=font)
        cell.paste(crop, (0, title_h))
        cells.append(cell)
    rows: list[list[Image.Image]] = [cells[i:i + columns] for i in range(0, len(cells), columns)]
    row_heights = [max(cell.height for cell in row) for row in rows]
    sheet = Image.new("RGB", (columns * (column_width + pad) - pad, sum(row_heights) + pad * (len(rows) - 1)), "#eef2f6")
    y = 0
    for row, row_height in zip(rows, row_heights):
        for index, cell in enumerate(row):
            sheet.paste(cell, (index * (column_width + pad), y))
        y += row_height + pad
    return sheet


def routing_for(entry: dict[str, Any]) -> dict[str, Any]:
    content_type = entry["content_type"]
    routing = dict(ROUTING_RULES[content_type])
    if content_type == "icon" and entry.get("handling") in ICON_HANDLINGS:
        routing["handling"] = entry["handling"]
    if "library" in routing and isinstance(entry.get("library"), str) and entry["library"].strip():
        routing["library"] = entry["library"].strip()
    return routing


def mode_init(out_dir: Path, args: argparse.Namespace) -> None:
    entries = named_slices(out_dir)
    if not entries:
        raise SystemExit(
            f"No named slices found in {out_dir}/lab-config.json or regions.json. "
            "Run prepare_lab.py first (use --manifest for named slices on low-contrast UIs)."
        )
    classification_path = out_dir / args.classification_name
    existing = load_json(classification_path, {})
    existing_entries = {
        str(entry.get("name")): entry
        for entry in (existing.get("slices") if isinstance(existing, dict) else []) or []
        if isinstance(entry, dict) and entry.get("name")
    }
    merged = []
    for entry in entries:
        previous = existing_entries.get(entry["name"], {})
        merged.append({
            "name": entry["name"],
            "bounds": entry["bounds"],
            "content_type": previous.get("content_type", ""),
            "handling": previous.get("handling", ""),
            "library": previous.get("library", ""),
            "notes": previous.get("notes", ""),
        })
    scaffold = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "content_types": list(CONTENT_TYPES),
        "instructions": (
            "Look at classification-sheet.png ONCE and set content_type for every slice. "
            "plain-dom: text/controls/cards/lists/tables the browser lays out. icon: a glyph "
            "(set handling to crop-asset or regenerate-transparent). image: photo/illustration/logo. "
            "chart: any data visualization (optionally override library; default echarts). "
            "map: map tiles. scene-3d: WebGL/3D. mixed: re-slice this region into smaller ones "
            "instead of labeling it. Then run --mode apply."
        ),
        "slices": merged,
    }
    classification_path.write_text(json.dumps(scaffold, indent=2, ensure_ascii=False), encoding="utf-8")

    reference_path = out_dir / "assets" / "reference.png"
    if not reference_path.exists():
        raise SystemExit(f"Reference image not found: {reference_path}")
    sheet = build_sheet(Image.open(reference_path).convert("RGB"), entries, args.column_width, args.columns)
    sheet_path = out_dir / args.sheet_name
    sheet.save(sheet_path)

    unlabeled = sum(1 for entry in merged if not entry["content_type"])
    print(json.dumps({
        "mode": "init",
        "slices": len(merged),
        "unlabeled": unlabeled,
        "classification": str(classification_path),
        "sheet": str(sheet_path),
        "next": f"label content_type in {classification_path.name} by reading {sheet_path.name}, then rerun with --mode apply",
    }, indent=2))


def mode_apply(out_dir: Path, args: argparse.Namespace) -> None:
    classification_path = out_dir / args.classification_name
    classification = load_json(classification_path, None)
    if not isinstance(classification, dict) or not isinstance(classification.get("slices"), list):
        raise SystemExit(f"Classification not found or invalid: {classification_path}. Run --mode init first.")

    errors: list[str] = []
    labeled: list[dict[str, Any]] = []
    for index, entry in enumerate(classification["slices"]):
        label = f"slices[{index}] '{entry.get('name', '?')}'" if isinstance(entry, dict) else f"slices[{index}]"
        if not isinstance(entry, dict) or not entry.get("name") or not isinstance(entry.get("bounds"), dict):
            errors.append(f"{label}: must be an object with name and bounds")
            continue
        content_type = entry.get("content_type")
        if content_type not in CONTENT_TYPES:
            errors.append(f"{label}: content_type must be one of {list(CONTENT_TYPES)}, got {content_type!r}")
            continue
        if content_type == "mixed":
            errors.append(f"{label}: 'mixed' is not routable - re-slice this region into separate "
                          "plain-dom/visualization regions and re-run --mode init")
            continue
        if content_type == "icon" and entry.get("handling") not in ("", None) + ICON_HANDLINGS:
            errors.append(f"{label}: icon handling must be one of {list(ICON_HANDLINGS)}, got {entry.get('handling')!r}")
            continue
        labeled.append(entry)
    if errors:
        for message in errors:
            print(f"ERROR: {message}")
        raise SystemExit(1)

    routed = []
    track_counts: dict[str, int] = {}
    for entry in labeled:
        routing = routing_for(entry)
        routed.append({
            "name": entry["name"],
            "bounds": entry["bounds"],
            "content_type": entry["content_type"],
            **routing,
            **({"notes": entry["notes"]} if entry.get("notes") else {}),
        })
        track_counts[routing["track"]] = track_counts.get(routing["track"], 0) + 1

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": str(classification_path),
        "regions": routed,
    }
    manifest_path = out_dir / args.manifest_name
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    regions_path = out_dir / "regions.json"
    regions_json = load_json(regions_path, {})
    existing_regions = {
        str(region.get("name")): region
        for region in (regions_json.get("regions") if isinstance(regions_json, dict) else regions_json) or []
        if isinstance(region, dict) and region.get("name")
    }
    for entry in routed:
        region = existing_regions.setdefault(entry["name"], {"name": entry["name"], **entry["bounds"]})
        region["track"] = entry["track"]
    regions_path.write_text(
        json.dumps({"regions": list(existing_regions.values())}, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print(json.dumps({
        "mode": "apply",
        "routed": len(routed),
        "tracks": track_counts,
        "manifest": str(manifest_path),
        "regions": str(regions_path),
    }, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out-dir", required=True, help="Pixel Twin Lab output directory")
    parser.add_argument("--mode", choices=["init", "apply"], required=True)
    parser.add_argument("--classification-name", default="slice-classification.json")
    parser.add_argument("--manifest-name", default="routing-manifest.json")
    parser.add_argument("--sheet-name", default="classification-sheet.png")
    parser.add_argument("--column-width", type=int, default=320)
    parser.add_argument("--columns", type=int, default=3)
    args = parser.parse_args()

    out_dir = Path(args.out_dir).expanduser().resolve()
    if args.mode == "init":
        mode_init(out_dir, args)
    else:
        mode_apply(out_dir, args)


if __name__ == "__main__":
    main()
