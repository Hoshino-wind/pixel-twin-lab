#!/usr/bin/env python3
"""Create concrete recovery artifacts after triage/planner output."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from PIL import Image
except ImportError:  # pragma: no cover - script remains useful without crops.
    Image = None  # type: ignore[assignment]


ISLAND_NAME_PATTERNS = (
    "avatar",
    "chart",
    "cohort",
    "conversion",
    "graph",
    "heatmap",
    "image",
    "map",
    "media",
    "photo",
    "picture",
    "recommendation",
    "thumbnail",
    "trend",
    "viz",
)

TRACKS_WITH_ASSET_DEFAULTS = {"island"}

COMPONENT_NAME_PATTERNS = (
    "app",
    "bar",
    "button",
    "card",
    "filter",
    "header",
    "kpi",
    "nav",
    "panel",
    "search",
    "shell",
    "sidebar",
    "tab",
    "topbar",
)

APPROX_NAME_PATTERNS = (
    "feed",
    "list",
    "stream",
    "table",
    "timeline",
)


def load_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return fallback


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "region"


def clamp_region(region: dict[str, Any], width: int, height: int) -> dict[str, Any] | None:
    try:
        x = int(round(float(region.get("x", 0))))
        y = int(round(float(region.get("y", 0))))
        w = int(round(float(region.get("width", 0))))
        h = int(round(float(region.get("height", 0))))
    except (TypeError, ValueError):
        return None
    x = max(0, min(x, width))
    y = max(0, min(y, height))
    w = max(0, min(w, width - x))
    h = max(0, min(h, height - y))
    if w <= 0 or h <= 0:
        return None
    name = str(region.get("name") or f"region-{x}-{y}-{w}-{h}")
    return {"name": slugify(name), "x": x, "y": y, "width": w, "height": h}


def normalize_regions(raw: Any, width: int, height: int) -> list[dict[str, Any]]:
    if isinstance(raw, dict):
        candidates = raw.get("regions") or raw.get("slices") or []
    else:
        candidates = raw
    regions: list[dict[str, Any]] = []
    seen: dict[str, int] = {}
    if not isinstance(candidates, list):
        return regions
    for item in candidates:
        if not isinstance(item, dict):
            continue
        region = clamp_region(item, width, height)
        if not region:
            continue
        base = region["name"]
        seen[base] = seen.get(base, 0) + 1
        if seen[base] > 1:
            region["name"] = f"{base}-{seen[base]}"
        regions.append(region)
    return regions


def regions_from_lab(lab: dict[str, Any], width: int, height: int) -> list[dict[str, Any]]:
    return normalize_regions(lab.get("slices", []), width, height)


def metric_regions(summary: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    metrics: dict[str, dict[str, Any]] = {}
    for item in summary:
        if item.get("file") != "rebuilt-capture.png":
            continue
        for region in item.get("regions") or []:
            name = slugify(str(region.get("name") or "region"))
            metrics[name] = {
                "mismatch_pixels": region.get("mismatch_pixels"),
                "mismatch_pct": region.get("mismatch_pct"),
                "mismatch_pixels_tolerant": region.get("mismatch_pixels_tolerant"),
                "mismatch_pct_tolerant": region.get("mismatch_pct_tolerant"),
                "mae": region.get("mae"),
                "max_delta": region.get("max_delta"),
            }
    return metrics


def capture_metric(summary: list[dict[str, Any]], file_name: str) -> dict[str, Any]:
    for item in summary:
        if item.get("file") == file_name:
            return item
    return {}


def planner_classes(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    classes: dict[str, dict[str, Any]] = {}
    passes = plan.get("passes") or plan.get("groups") or {}
    if not isinstance(passes, dict):
        return classes
    for pass_name, items in passes.items():
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            name = slugify(str(item.get("name") or "region"))
            classes[name] = {
                "planner_pass": pass_name,
                "action": item.get("action", ""),
                "evidence": item.get("evidence", {}),
            }
    return classes


def name_contains(name: str, patterns: tuple[str, ...]) -> bool:
    lowered = name.lower()
    return any(pattern in lowered for pattern in patterns)


def classify_region(region: dict[str, Any], planner: dict[str, Any], table_track: str) -> tuple[str, str]:
    name = str(region["name"])
    planner_pass = planner.get("planner_pass")
    action = str(planner.get("action") or "")
    if planner_pass == "slice-island":
        return "island", action or "Planner classified this region as raster-like."
    if planner_pass == "not-built":
        return "component", action or "Bootstrap this missing region as a component container first."
    if name_contains(name, ISLAND_NAME_PATTERNS):
        return "island", "Name suggests raster-like or visualization content; keep as an island until layout converges."
    if "table" in name.lower():
        return table_track, f"Dense table defaults to {table_track} for the first recovery pass."
    if name_contains(name, APPROX_NAME_PATTERNS):
        return "approximation", "Dense text/list content should be componentized only after geometry converges."
    if name_contains(name, COMPONENT_NAME_PATTERNS):
        return "component", "Structural UI chrome is suitable for component reconstruction."
    if planner_pass == "rebuild":
        return "component", action or "Planner marked this as a structural rebuild candidate."
    return "component", "Default to component; override in the ledger if this region proves raster-like."


def uncovered_rects(rects: list[dict[str, Any]], width: int, height: int) -> list[dict[str, int]]:
    """Return simple non-overlapping gap rectangles not covered by named regions."""
    breaks = {0, height}
    for rect in rects:
        breaks.add(int(rect["y"]))
        breaks.add(int(rect["y"]) + int(rect["height"]))
    ys = sorted(y for y in breaks if 0 <= y <= height)

    bands: list[tuple[int, int, tuple[tuple[int, int], ...]]] = []
    for y0, y1 in zip(ys, ys[1:]):
        covered = sorted(
            (int(r["x"]), int(r["x"]) + int(r["width"]))
            for r in rects
            if int(r["y"]) <= y0 and int(r["y"]) + int(r["height"]) >= y1
        )
        intervals: list[tuple[int, int]] = []
        cursor = 0
        for x0, x1 in covered:
            if x0 > cursor:
                intervals.append((cursor, min(x0, width)))
            cursor = max(cursor, x1)
            if cursor >= width:
                break
        if cursor < width:
            intervals.append((cursor, width))
        bands.append((y0, y1, tuple(intervals)))

    merged: list[dict[str, int]] = []
    current: tuple[int, int, tuple[tuple[int, int], ...]] | None = None
    for y0, y1, intervals in bands:
        if current and current[1] == y0 and current[2] == intervals:
            current = (current[0], y1, intervals)
            continue
        if current:
            merged.extend(
                {"x": x0, "y": current[0], "width": x1 - x0, "height": current[1] - current[0]}
                for x0, x1 in current[2]
                if x1 > x0
            )
        current = (y0, y1, intervals)
    if current:
        merged.extend(
            {"x": x0, "y": current[0], "width": x1 - x0, "height": current[1] - current[0]}
            for x0, x1 in current[2]
            if x1 > x0
        )
    return merged


def mismatch_pixels(region: dict[str, Any]) -> float:
    metrics = region.get("metrics") or {}
    value = metrics.get("mismatch_pixels")
    if value is not None:
        try:
            return float(value)
        except (TypeError, ValueError):
            pass
    pct = metrics.get("mismatch_pct")
    try:
        return float(region["width"]) * float(region["height"]) * float(pct) / 100.0
    except (TypeError, ValueError, KeyError):
        return float(region.get("width", 0)) * float(region.get("height", 0))


def has_mismatch_metric(region: dict[str, Any]) -> bool:
    metrics = region.get("metrics") or {}
    return metrics.get("mismatch_pixels") is not None or metrics.get("mismatch_pct") is not None


def region_area(region: dict[str, Any]) -> float:
    return float(region.get("width", 0)) * float(region.get("height", 0))


def select_asset_regions(
    regions: list[dict[str, Any]],
    provider: str,
    policy: str,
    target_match: float,
    summary: list[dict[str, Any]],
    width: int,
    height: int,
) -> tuple[set[str], dict[str, Any]]:
    if provider == "none" or policy == "none":
        return set(), {"estimated_match_pct": None, "selected_mismatch_pixels": 0}

    selected: set[str] = set()
    if policy == "ledger-islands":
        selected = {region["name"] for region in regions if region.get("track") in TRACKS_WITH_ASSET_DEFAULTS}
    elif policy == "non-component":
        selected = {region["name"] for region in regions if region.get("track") != "component"}
    elif policy == "all-regions":
        selected = {region["name"] for region in regions}
    elif policy == "target":
        rebuilt = capture_metric(summary, "rebuilt-capture.png")
        total_pixels = max(1, width * height)
        current_mismatch = float(rebuilt.get("mismatch_pixels") or total_pixels)
        target_mismatch = total_pixels * max(0.0, 100.0 - target_match) / 100.0

        priority = {"island": 0, "approximation": 1, "component": 2, "coverage": 3}
        measured_candidates = [region for region in regions if has_mismatch_metric(region)]
        coverage_candidates = [region for region in regions if not has_mismatch_metric(region)]
        candidates = sorted(
            measured_candidates,
            key=lambda region: (-mismatch_pixels(region), priority.get(str(region.get("track")), 4)),
        ) + sorted(
            coverage_candidates,
            key=lambda region: (-region_area(region), priority.get(str(region.get("track")), 4)),
        )
        remaining = current_mismatch
        for region in candidates:
            if remaining <= target_mismatch:
                break
            selected.add(region["name"])
            remaining = max(0.0, remaining - mismatch_pixels(region))
    else:
        raise SystemExit(f"Unknown asset policy: {policy}")

    selected_mismatch = sum(mismatch_pixels(region) for region in regions if region["name"] in selected)
    rebuilt = capture_metric(summary, "rebuilt-capture.png")
    total_pixels = max(1, width * height)
    rebuilt_mismatch = rebuilt.get("mismatch_pixels")
    if rebuilt_mismatch is None:
        return selected, {
            "estimated_match_pct": None,
            "selected_mismatch_pixels": round(selected_mismatch),
            "current_mismatch_pixels": None,
            "target_match_pct": target_match,
            "note": "no pixel-diff data; estimate unavailable",
        }
    current_mismatch = float(rebuilt_mismatch)
    estimated_remaining = max(0.0, current_mismatch - selected_mismatch)
    return selected, {
        "estimated_match_pct": 100.0 - (estimated_remaining / total_pixels * 100.0),
        "selected_mismatch_pixels": round(selected_mismatch),
        "current_mismatch_pixels": round(current_mismatch),
        "target_match_pct": target_match,
    }


def sample_fill(reference: Any, region: dict[str, Any]) -> str:
    if reference is None:
        return "#f3f4f6"
    crop = reference.crop(
        (
            region["x"],
            region["y"],
            region["x"] + region["width"],
            region["y"] + region["height"],
        )
    ).convert("RGB")
    tiny = crop.resize((1, 1))
    r, g, b = tiny.getpixel((0, 0))
    return f"#{r:02x}{g:02x}{b:02x}"


def write_manifest(path: Path, regions: list[dict[str, Any]]) -> None:
    path.write_text(json.dumps({"regions": regions}, indent=2), encoding="utf-8")


def crop_assets(reference_path: Path, asset_dir: Path, regions: list[dict[str, Any]]) -> dict[str, str]:
    assets: dict[str, str] = {}
    if Image is None or not reference_path.exists():
        return assets
    asset_dir.mkdir(parents=True, exist_ok=True)
    reference = Image.open(reference_path).convert("RGBA")
    for region in regions:
        crop = reference.crop(
            (
                region["x"],
                region["y"],
                region["x"] + region["width"],
                region["y"] + region["height"],
            )
        )
        filename = f"{region['name']}.png"
        crop.save(asset_dir / filename)
        assets[region["name"]] = f"{asset_dir.name}/{filename}"
    return assets


def write_placeholder_assets(asset_dir: Path, regions: list[dict[str, Any]]) -> dict[str, str]:
    assets: dict[str, str] = {}
    if Image is None:
        return assets
    asset_dir.mkdir(parents=True, exist_ok=True)
    for region in regions:
        width = int(region["width"])
        height = int(region["height"])
        fill = str(region.get("fill") or "#f3f4f6")
        image = Image.new("RGBA", (width, height), fill)
        filename = f"{region['name']}.placeholder.png"
        image.save(asset_dir / filename)
        assets[region["name"]] = f"{asset_dir.name}/{filename}"
    return assets


def render_css(width: int, height: int, background: str, regions: list[dict[str, Any]]) -> str:
    lines = [
        "/* Generated by pixel-twin-lab bootstrap_recovery.py. Keep in work/ until adapted to the target project. */",
        ".pt-recovery {",
        "  position: relative;",
        f"  width: {width}px;",
        f"  height: {height}px;",
        f"  background: {background};",
        "  overflow: hidden;",
        "}",
        "",
        ".pt-region {",
        "  position: absolute;",
        "  box-sizing: border-box;",
        "  overflow: hidden;",
        "  background: var(--pt-fill, rgba(248, 250, 252, 0.9));",
        "}",
        "",
        ".pt-region[data-has-asset=\"true\"] > img {",
        "  width: 100%;",
        "  height: 100%;",
        "  display: block;",
        "  object-fit: fill;",
        "}",
        "",
    ]
    for region in regions:
        lines.extend(
            [
                f".pt-region--{region['name']} {{",
                f"  left: {region['x']}px;",
                f"  top: {region['y']}px;",
                f"  width: {region['width']}px;",
                f"  height: {region['height']}px;",
                f"  --pt-fill: {region['fill']};",
                "}",
                "",
            ]
        )
    return "\n".join(lines)


def render_react(width: int, height: int, background: str, regions: list[dict[str, Any]]) -> str:
    payload = [
        {
            "name": region["name"],
            "track": region["track"],
            "assetProvider": region.get("asset_provider", "none"),
            "assetStrategy": region.get("asset_strategy", "none"),
            "className": f"pt-region--{region['name']}",
            "asset": region.get("asset"),
        }
        for region in regions
    ]
    return (
        "import './recovery-skeleton.css';\n\n"
        f"const viewport = {{ width: {width}, height: {height}, background: '{background}' }};\n"
        f"const regions = {json.dumps(payload, indent=2)};\n\n"
        "export default function RecoveryScaffold() {\n"
        "  return (\n"
        "    <div className=\"pt-recovery\" style={{ width: viewport.width, height: viewport.height, background: viewport.background }}>\n"
        "      {regions.map((region) => (\n"
        "        <section key={region.name} className={`pt-region ${region.className}`} data-track={region.track} data-asset-provider={region.assetProvider} data-asset-strategy={region.assetStrategy} data-has-asset={region.asset ? 'true' : 'false'} aria-label={region.name}>\n"
        "          {region.asset ? <img src={region.asset} alt=\"\" draggable={false} /> : null}\n"
        "        </section>\n"
        "      ))}\n"
        "    </div>\n"
        "  );\n"
        "}\n"
    )


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Recovery Bootstrap",
        "",
        f"Generated: `{report['generated_at']}`",
        f"Source lab: `{report['source_lab']}`",
        f"Triage decision: `{report['triage_decision']}`",
        f"Asset provider: `{report['asset_provider']}`",
        f"Asset policy: `{report['asset_policy']}`",
        "",
        "## Files",
        "",
    ]
    for key, value in report["files"].items():
        lines.append(f"- `{key}`: `{value}`")
    lines += [
        "",
        "## Region Ledger",
        "",
        "| Region | Track | Asset | Bounds | Tolerant mismatch | Reason |",
        "| --- | --- | --- | --- | ---: | --- |",
    ]
    for region in report["regions"]:
        bounds = f"{region['x']},{region['y']} {region['width']}x{region['height']}"
        tolerant = region.get("metrics", {}).get("mismatch_pct_tolerant")
        tolerant_text = "n/a" if tolerant is None else f"{float(tolerant):.4f}%"
        asset = region.get("asset_strategy") or "none"
        reason = str(region.get("reason") or "").replace("|", "/")
        lines.append(f"| `{region['name']}` | `{region['track']}` | `{asset}` | `{bounds}` | {tolerant_text} | {reason} |")
    lines += [
        "",
        "## Asset Strategy",
        "",
        "- `image2-extract`: crop-based stand-in for an Image2 element extraction pass; replace with real extracted/generated assets when available.",
        "- `placeholder`: same-size neutral placeholder for models that cannot extract or recreate the element.",
        "- `none`: keep the region as project-native component work.",
        "",
        "## Next Pass",
        "",
        "1. Rerun `prepare_lab.py --manifest recovery/slice-manifest.starter.json` to prove named exact coverage.",
        "2. Use `recovery-skeleton.css` or `RecoveryScaffold.jsx` only as an intermediate scaffold, not as final production code.",
        "3. Embed `image2-extract` assets for the high-fidelity pass; use same-size placeholders when the current model cannot extract the element.",
        "4. Rebuild `component` regions against named region metrics before tuning small text or colors.",
        "5. Revisit `approximation` regions after shell geometry and islands converge.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True, help="Pixel Twin Lab output directory")
    parser.add_argument("--regions", help="Optional regions.json or manifest path; defaults to out-dir/regions.json")
    parser.add_argument("--recovery-dir", default="recovery", help="Output directory name inside out-dir")
    parser.add_argument(
        "--asset-provider",
        choices=["image2", "placeholder", "none"],
        default="image2",
        help="Use Image2-style extracted assets, same-size placeholders, or no generated assets",
    )
    parser.add_argument(
        "--asset-policy",
        choices=["none", "ledger-islands", "non-component", "target", "all-regions"],
        default="ledger-islands",
        help="Which regions receive generated assets",
    )
    parser.add_argument("--target-match", type=float, default=98.0, help="Target match percentage for --asset-policy target")
    parser.add_argument("--cover-gaps", action="store_true", help="Add gap rectangles so the scaffold can cover the whole canvas")
    parser.add_argument(
        "--table-track",
        choices=["component", "island", "approximation"],
        default="island",
        help="Default track for regions whose name contains table",
    )
    args = parser.parse_args()

    out_dir = Path(args.out_dir).expanduser().resolve()
    if not out_dir.exists():
        raise SystemExit(f"Output directory does not exist: {out_dir}")

    lab = load_json(out_dir / "lab-config.json", {})
    width = int(lab.get("width") or 0)
    height = int(lab.get("height") or 0)
    if width <= 0 or height <= 0:
        raise SystemExit("lab-config.json must include positive width and height")

    regions_path = Path(args.regions).expanduser().resolve() if args.regions else out_dir / "regions.json"
    if regions_path.exists():
        regions = normalize_regions(load_json(regions_path, {}), width, height)
    else:
        regions = regions_from_lab(lab, width, height)
    if not regions:
        raise SystemExit("No usable regions found. Provide --regions or create regions.json.")

    if args.cover_gaps:
        gaps = uncovered_rects(regions, width, height)
        for index, gap in enumerate(gaps, start=1):
            gap.update({"name": f"gap-{index:03d}"})
            regions.append(gap)

    recovery_dir = out_dir / args.recovery_dir
    recovery_dir.mkdir(parents=True, exist_ok=True)

    reference_path = out_dir / "assets" / "reference.png"
    reference_img = Image.open(reference_path).convert("RGB") if Image is not None and reference_path.exists() else None
    summary = load_json(out_dir / "pixel-diff-summary.json", [])
    plan = load_json(out_dir / "calibration-plan.json", {})
    triage = load_json(out_dir / "triage-report.json", {})
    metrics = metric_regions(summary if isinstance(summary, list) else [])
    planner = planner_classes(plan if isinstance(plan, dict) else {})

    enriched: list[dict[str, Any]] = []
    for region in regions:
        classes = planner.get(region["name"], {})
        if str(region["name"]).startswith("gap-"):
            track, reason = "coverage", "Generated gap filler so the high-fidelity scaffold can cover the full canvas."
        else:
            track, reason = classify_region(region, classes, args.table_track)
        enriched_region = dict(region)
        enriched_region["track"] = track
        enriched_region["reason"] = reason
        enriched_region["fill"] = sample_fill(reference_img, region)
        enriched_region["metrics"] = metrics.get(region["name"], {})
        enriched.append(enriched_region)

    asset_names, asset_estimate = select_asset_regions(
        enriched,
        args.asset_provider,
        args.asset_policy,
        args.target_match,
        summary if isinstance(summary, list) else [],
        width,
        height,
    )
    asset_regions = [region for region in enriched if region["name"] in asset_names]
    if args.asset_provider == "image2":
        assets = crop_assets(reference_path, recovery_dir / "assets", asset_regions)
        asset_strategy = "image2-extract"
    elif args.asset_provider == "placeholder":
        assets = write_placeholder_assets(recovery_dir / "assets", asset_regions)
        asset_strategy = "placeholder"
    else:
        assets = {}
        asset_strategy = "none"

    for region in enriched:
        if region["name"] in assets:
            region["asset"] = assets[region["name"]]
            region["asset_provider"] = args.asset_provider
            region["asset_strategy"] = asset_strategy
        else:
            region["asset_provider"] = "none"
            region["asset_strategy"] = "none"

    write_manifest(recovery_dir / "slice-manifest.starter.json", [{k: r[k] for k in ("name", "x", "y", "width", "height")} for r in enriched])
    island_regions = [region for region in enriched if region["track"] == "island"]
    write_manifest(recovery_dir / "island-manifest.starter.json", [{k: r[k] for k in ("name", "x", "y", "width", "height")} for r in island_regions])
    write_manifest(recovery_dir / "asset-manifest.starter.json", [{k: r[k] for k in ("name", "x", "y", "width", "height")} for r in asset_regions])
    (recovery_dir / "recovery-skeleton.css").write_text(
        render_css(width, height, str(lab.get("background") or "#ffffff"), enriched),
        encoding="utf-8",
    )
    (recovery_dir / "RecoveryScaffold.jsx").write_text(
        render_react(width, height, str(lab.get("background") or "#ffffff"), enriched),
        encoding="utf-8",
    )

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_lab": str(out_dir),
        "triage_decision": triage.get("decision"),
        "asset_provider": args.asset_provider,
        "asset_policy": args.asset_policy,
        "asset_estimate": asset_estimate,
        "viewport": {"width": width, "height": height},
        "region_source": str(regions_path) if regions_path.exists() else "lab-config.json:slices",
        "files": {
            "slice_manifest": str(recovery_dir / "slice-manifest.starter.json"),
            "island_manifest": str(recovery_dir / "island-manifest.starter.json"),
            "asset_manifest": str(recovery_dir / "asset-manifest.starter.json"),
            "ledger_json": str(recovery_dir / "component-ledger.json"),
            "ledger_md": str(recovery_dir / "component-ledger.md"),
            "css": str(recovery_dir / "recovery-skeleton.css"),
            "react": str(recovery_dir / "RecoveryScaffold.jsx"),
            "assets": str(recovery_dir / "assets"),
        },
        "regions": enriched,
    }
    (recovery_dir / "component-ledger.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (recovery_dir / "component-ledger.md").write_text(render_markdown(report), encoding="utf-8")

    counts: dict[str, int] = {}
    asset_count = 0
    for region in enriched:
        counts[region["track"]] = counts.get(region["track"], 0) + 1
        if region.get("asset") is not None:
            asset_count += 1
    print(json.dumps({"out_dir": str(out_dir), "recovery_dir": str(recovery_dir), "tracks": counts, "assets": asset_count, "asset_estimate": asset_estimate}, indent=2))


if __name__ == "__main__":
    main()
