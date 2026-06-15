#!/usr/bin/env python3
"""Overlay approved island assets onto an existing component rebuilt layer."""

from __future__ import annotations

import argparse
import html
import json
import math
import re
import shutil
from pathlib import Path
from typing import Any

from PIL import Image


CSS_START_MARKER = "/* Pixel Twin Lab island overlays: component DOM stays underneath. */"
CSS_END_MARKER = "/* pixel-twin island overlays: end */"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_json_fallback(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return fallback


def asset_src(asset: str, recovery_dir_name: str) -> str:
    if asset.startswith("./") or asset.startswith("/") or "://" in asset:
        return asset
    return f"./{recovery_dir_name}/{asset}"


def slugify(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "-", value.strip()).strip("-").lower() or "region"


def rebuilt_region_metrics(out_dir: Path) -> dict[str, dict[str, Any]]:
    summary = load_json_fallback(out_dir / "pixel-diff-summary.json", [])
    captures = summary.get("captures") if isinstance(summary, dict) else summary
    if not isinstance(captures, list):
        return {}
    rebuilt = next((item for item in captures if isinstance(item, dict) and item.get("file") == "rebuilt-capture.png"), None)
    if not isinstance(rebuilt, dict):
        return {}
    return {
        str(region["name"]): region
        for region in rebuilt.get("regions") or []
        if isinstance(region, dict) and region.get("name")
    }


def region_bounds(region: dict[str, Any]) -> dict[str, int] | None:
    try:
        bounds = {key: int(round(float(region[key]))) for key in ("x", "y", "width", "height")}
    except (KeyError, TypeError, ValueError):
        return None
    if bounds["width"] <= 0 or bounds["height"] <= 0:
        return None
    return bounds


def fallback_score(metric: dict[str, Any], bounds: dict[str, int], page_area: int, score_mode: str) -> float:
    try:
        strict = float(metric.get("mismatch_pct") or 0)
        tolerant = float(metric.get("mismatch_pct_tolerant") or 0)
        mae = float(metric.get("mae") or 0)
    except (TypeError, ValueError):
        return 0.0
    coverage = (bounds["width"] * bounds["height"]) / max(1, page_area)
    if score_mode == "strict":
        return strict
    if score_mode == "impact-strict":
        return strict * coverage
    if score_mode == "impact-tolerant":
        return tolerant * coverage
    if score_mode == "mae":
        return mae * coverage
    return tolerant


def clear_threshold_fallback(region: dict[str, Any]) -> None:
    if str(region.get("asset_strategy") or "") != "threshold-region-fallback":
        return
    for key in (
        "asset",
        "asset_strategy",
        "asset_provider",
        "fallback_reason",
        "fallback_coverage_pct",
        "fallback_score",
        "fallback_tolerant_mismatch_pct",
    ):
        region.pop(key, None)


def promote_component_fallbacks(
    out_dir: Path,
    recovery_dir: Path,
    report: dict[str, Any],
    threshold: float,
    max_coverage_pct: float | None = None,
    max_single_coverage_pct: float | None = None,
    score_mode: str = "tolerant",
    selection_mode: str = "greedy",
) -> tuple[list[str], list[str], float]:
    """Promote high-mismatch component regions to visual fallback assets.

    This is intentionally opt-in. It keeps the component DOM underneath but allows
    visual QA runs to avoid spending all pixel budget on dense regions that still
    need a dedicated component pass.
    """
    metrics = rebuilt_region_metrics(out_dir)
    if not metrics:
        return []
    reference_path = out_dir / "assets" / "reference.png"
    if not reference_path.exists():
        return [], [], 0.0
    reference = Image.open(reference_path).convert("RGB")
    page_area = reference.width * reference.height
    candidates: list[dict[str, Any]] = []
    for region in report.get("regions") or []:
        if isinstance(region, dict):
            clear_threshold_fallback(region)
    skipped: list[str] = []
    selected: list[dict[str, Any]] = []
    promoted: list[str] = []
    for region in report.get("regions") or []:
        if not isinstance(region, dict):
            continue
        if str(region.get("track") or "") != "component":
            continue
        metric = metrics.get(str(region.get("name") or ""))
        if not metric:
            continue
        try:
            tolerant = float(metric.get("mismatch_pct_tolerant") or 0)
        except (TypeError, ValueError):
            continue
        if tolerant <= threshold:
            continue
        bounds = region_bounds(region)
        if not bounds:
            continue
        coverage_pct = (bounds["width"] * bounds["height"]) / max(1, page_area) * 100
        if max_single_coverage_pct is not None and coverage_pct > max_single_coverage_pct:
            skipped.append(str(region.get("name") or "region"))
            continue
        candidates.append(
            {
                "region": region,
                "metric": metric,
                "bounds": bounds,
                "coverage_pct": coverage_pct,
                "score": fallback_score(metric, bounds, page_area, score_mode),
            }
        )

    if max_coverage_pct is None:
        selected = candidates
    elif selection_mode == "knapsack":
        capacity = max(0, int(round(max_coverage_pct * 100)))
        dp: list[tuple[float, tuple[int, ...], float]] = [(-1.0, tuple(), 0.0) for _ in range(capacity + 1)]
        dp[0] = (0.0, tuple(), 0.0)
        for index, candidate in enumerate(candidates):
            weight = max(1, int(math.ceil(float(candidate["coverage_pct"]) * 100)))
            score = float(candidate["score"])
            if weight > capacity:
                continue
            for current in range(capacity - weight, -1, -1):
                current_score, current_indices, current_coverage = dp[current]
                if current_score < 0:
                    continue
                next_weight = current + weight
                next_score = current_score + score
                next_indices = current_indices + (index,)
                next_coverage = current_coverage + float(candidate["coverage_pct"])
                previous_score, previous_indices, previous_coverage = dp[next_weight]
                better = next_score > previous_score + 1e-9
                if not better and abs(next_score - previous_score) <= 1e-9:
                    better = (
                        len(next_indices),
                        next_coverage,
                        tuple(str(candidates[item]["region"].get("name") or "") for item in next_indices),
                    ) > (
                        len(previous_indices),
                        previous_coverage,
                        tuple(str(candidates[item]["region"].get("name") or "") for item in previous_indices),
                    )
                if better:
                    dp[next_weight] = (next_score, next_indices, next_coverage)
        best_score, best_indices, _best_coverage = max(
            dp,
            key=lambda item: (
                item[0],
                len(item[1]),
                item[2],
                tuple(str(candidates[index]["region"].get("name") or "") for index in item[1]),
            ),
        )
        selected_indices = set(best_indices if best_score >= 0 else tuple())
        selected = [candidate for index, candidate in enumerate(candidates) if index in selected_indices]
        selected_names = {str(candidate["region"].get("name") or "region") for candidate in selected}
        skipped.extend(
            str(candidate["region"].get("name") or "region")
            for candidate in candidates
            if str(candidate["region"].get("name") or "region") not in selected_names
        )
    else:
        used_coverage = 0.0
        for candidate in sorted(
            candidates,
            key=lambda item: (
                -float(item["score"]),
                -float(item["metric"].get("mismatch_pct_tolerant") or 0),
                -float(item["coverage_pct"]),
                str(item["region"].get("name") or ""),
            ),
        ):
            coverage_pct = float(candidate["coverage_pct"])
            name = str(candidate["region"].get("name") or "region")
            if used_coverage + coverage_pct > max_coverage_pct + 1e-9:
                skipped.append(name)
                continue
            selected.append(candidate)
            used_coverage += coverage_pct

    total_coverage_pct = 0.0
    for candidate in selected:
        region = candidate["region"]
        bounds = candidate["bounds"]
        tolerant = float(candidate["metric"].get("mismatch_pct_tolerant") or 0)
        coverage_pct = float(candidate["coverage_pct"])
        total_coverage_pct += coverage_pct
        asset_name = f"fallback-{slugify(str(region.get('name') or 'region'))}.png"
        asset_path = recovery_dir / asset_name
        x, y, width, height = bounds["x"], bounds["y"], bounds["width"], bounds["height"]
        reference.crop((x, y, x + width, y + height)).save(asset_path)
        region["asset"] = asset_name
        region["asset_strategy"] = "threshold-region-fallback"
        region["asset_provider"] = "reference-crop"
        region["fallback_reason"] = f"component tolerant mismatch {tolerant:.4f}% > {threshold:.4f}%"
        region["fallback_coverage_pct"] = round(coverage_pct, 4)
        region["fallback_score"] = round(float(candidate["score"]), 6)
        region["fallback_tolerant_mismatch_pct"] = round(tolerant, 4)
        promoted.append(str(region.get("name") or "region"))
    if promoted or skipped:
        (recovery_dir / "component-ledger.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return promoted, skipped, total_coverage_pct


def approved_asset_regions(report: dict[str, Any], allowed_tracks: set[str]) -> list[dict[str, Any]]:
    regions: list[dict[str, Any]] = []
    for region in report.get("regions", []):
        if not isinstance(region, dict):
            continue
        asset = region.get("asset")
        if not isinstance(asset, str) or not asset.strip():
            continue
        if str(region.get("track") or "") not in allowed_tracks:
            continue
        if str(region.get("asset_strategy") or "") == "placeholder":
            continue
        regions.append(region)
    return regions


def render_overlay(regions: list[dict[str, Any]], recovery_dir_name: str) -> str:
    lines = ['          <div class="pt-island-overlay" data-pixel-twin-island-overlay aria-hidden="true">']
    for region in regions:
        name = html.escape(str(region["name"]), quote=True)
        src = html.escape(asset_src(str(region["asset"]), recovery_dir_name), quote=True)
        lines.extend(
            [
                f'            <section class="pt-island-region pt-island-region--{name}" data-track="{html.escape(str(region.get("track") or "island"), quote=True)}" data-asset-provider="{html.escape(str(region.get("asset_provider") or "image2"), quote=True)}" data-asset-strategy="{html.escape(str(region.get("asset_strategy") or "image2-extract"), quote=True)}" data-has-asset="true" aria-label="{name}">',
                f'              <img src="{src}" alt="" draggable="false" />',
                "            </section>",
            ]
        )
    lines.append("          </div>")
    return "\n".join(lines)


def render_css(regions: list[dict[str, Any]]) -> str:
    lines = [
        "",
        CSS_START_MARKER,
        ".pt-island-overlay {",
        "  position: absolute;",
        "  inset: 0;",
        "  z-index: 50;",
        "  pointer-events: none;",
        "}",
        ".pt-island-region {",
        "  position: absolute;",
        "  overflow: hidden;",
        "  box-sizing: border-box;",
        "}",
        ".pt-island-region > img {",
        "  display: block;",
        "  width: 100%;",
        "  height: 100%;",
        "  object-fit: fill;",
        "}",
    ]
    for region in regions:
        lines.extend(
            [
                f".pt-island-region--{region['name']} {{",
                f"  left: {int(region['x'])}px;",
                f"  top: {int(region['y'])}px;",
                f"  width: {int(region['width'])}px;",
                f"  height: {int(region['height'])}px;",
                "}",
            ]
        )
    lines.append(CSS_END_MARKER)
    return "\n".join(lines) + "\n"


def remove_existing_overlay(index_html: str) -> str:
    return re.sub(
        r"\n?\s*<div class=\"pt-island-overlay\" data-pixel-twin-island-overlay[\s\S]*?</div>\s*",
        "\n",
        index_html,
        flags=re.IGNORECASE,
    )


def inject_overlay(index_html: str, overlay_html: str) -> str:
    clean = remove_existing_overlay(index_html)
    marker = "</div>\n      </section>"
    position = clean.find(marker)
    if position == -1:
        raise SystemExit("Could not find rebuilt-layer closing marker in index.html.")
    return clean[:position] + overlay_html + "\n" + clean[position:]


def strip_existing_css(css_text: str) -> str:
    start = css_text.find(CSS_START_MARKER)
    if start == -1:
        return css_text.rstrip() + "\n"
    end = css_text.find(CSS_END_MARKER, start)
    if end == -1:
        print(
            "Warning: island overlay start marker found without end marker (old format); "
            "stripping to end of file, which may remove styles appended after the overlay block."
        )
        return css_text[:start].rstrip() + "\n"
    before = css_text[:start].rstrip()
    after = css_text[end + len(CSS_END_MARKER):].strip("\n").rstrip()
    parts = [part for part in (before, after) if part]
    return ("\n\n".join(parts) + "\n") if parts else ""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True, help="Pixel Twin Lab output directory")
    parser.add_argument("--recovery-dir", default="recovery", help="Recovery directory name inside out-dir")
    parser.add_argument("--allowed-asset-tracks", default="island", help="Comma-separated tracks to overlay")
    parser.add_argument(
        "--auto-component-fallback-threshold",
        type=float,
        help="Opt-in: crop and overlay component regions whose tolerant mismatch exceeds this percentage",
    )
    parser.add_argument(
        "--auto-component-fallback-max-coverage",
        type=float,
        help="Optional page coverage budget for promoted component fallback regions, in percent",
    )
    parser.add_argument(
        "--auto-component-fallback-max-single-coverage",
        type=float,
        help="Optional per-region coverage cap for promoted component fallback regions, in percent",
    )
    parser.add_argument(
        "--auto-component-fallback-score",
        choices=("tolerant", "strict", "impact-tolerant", "impact-strict", "mae"),
        default="tolerant",
        help="Ranking metric when max coverage is set; tolerant favors locally bad regions, impact favors total pixel gain",
    )
    parser.add_argument(
        "--auto-component-fallback-selection",
        choices=("greedy", "knapsack"),
        default="greedy",
        help="Budget selection strategy; knapsack maximizes total score under the coverage cap",
    )
    parser.add_argument("--no-backup", action="store_true", help="Do not write .before-island-overlay backups")
    args = parser.parse_args()

    out_dir = Path(args.out_dir).expanduser().resolve()
    recovery_dir = out_dir / args.recovery_dir
    report_path = recovery_dir / "component-ledger.json"
    if not report_path.exists():
        raise SystemExit(f"Missing recovery report: {report_path}")

    allowed_tracks = {track.strip() for track in args.allowed_asset_tracks.split(",") if track.strip()}
    report = load_json(report_path)
    promoted: list[str] = []
    skipped: list[str] = []
    promoted_coverage_pct = 0.0
    if args.auto_component_fallback_threshold is not None:
        allowed_tracks.add("component")
        promoted, skipped, promoted_coverage_pct = promote_component_fallbacks(
            out_dir,
            recovery_dir,
            report,
            args.auto_component_fallback_threshold,
            args.auto_component_fallback_max_coverage,
            args.auto_component_fallback_max_single_coverage,
            args.auto_component_fallback_score,
            args.auto_component_fallback_selection,
        )
    regions = approved_asset_regions(report, allowed_tracks)
    if not regions:
        raise SystemExit(f"No approved asset regions found for tracks: {sorted(allowed_tracks)}")

    index_path = out_dir / "index.html"
    styles_path = out_dir / "styles.css"
    if not index_path.exists() or not styles_path.exists():
        raise SystemExit("index.html and styles.css must exist in out-dir.")

    if not args.no_backup:
        for path in (index_path, styles_path):
            backup = path.with_suffix(path.suffix + ".before-island-overlay")
            if not backup.exists():
                shutil.copy2(path, backup)

    index_html = index_path.read_text(encoding="utf-8")
    overlay_html = render_overlay(regions, args.recovery_dir)
    index_path.write_text(inject_overlay(index_html, overlay_html), encoding="utf-8")

    css_text = strip_existing_css(styles_path.read_text(encoding="utf-8"))
    styles_path.write_text(css_text + render_css(regions), encoding="utf-8")

    print(
        json.dumps(
            {
                "out_dir": str(out_dir),
                "recovery_dir": str(recovery_dir),
                "overlay_regions": [region["name"] for region in regions],
                "auto_component_fallback_threshold": args.auto_component_fallback_threshold,
                "auto_component_fallback_max_coverage": args.auto_component_fallback_max_coverage,
                "auto_component_fallback_max_single_coverage": args.auto_component_fallback_max_single_coverage,
                "auto_component_fallback_score": args.auto_component_fallback_score,
                "auto_component_fallback_selection": args.auto_component_fallback_selection,
                "promoted_component_regions": promoted,
                "skipped_component_regions": skipped,
                "promoted_component_coverage_pct": round(promoted_coverage_pct, 4),
                "allowed_asset_tracks": sorted(allowed_tracks),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
