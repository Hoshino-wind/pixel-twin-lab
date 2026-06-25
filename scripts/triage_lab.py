#!/usr/bin/env python3
"""Triage a Pixel Twin Lab run and recommend the next fidelity pass."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PASS_ORDER = ["not-built", "layout", "token", "slice-island", "rebuild"]


def load_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return fallback


def as_float(value: Any, default: float | None = None) -> float | None:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def fmt_pct(value: Any) -> str:
    number = as_float(value)
    if number is None:
        return "n/a"
    return f"{number:.4f}%"


def find_capture(summary: list[dict[str, Any]], file_name: str) -> dict[str, Any] | None:
    for item in summary:
        if item.get("file") == file_name:
            return item
    return None


def metric(capture: dict[str, Any] | None, key: str) -> float | None:
    if not capture:
        return None
    return as_float(capture.get(key))


def plan_passes(plan: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    raw = plan.get("passes") or plan.get("groups") or {}
    return {name: list(raw.get(name, [])) for name in PASS_ORDER}


def top_plan_regions(passes: dict[str, list[dict[str, Any]]], limit: int = 8) -> list[dict[str, Any]]:
    regions: list[dict[str, Any]] = []
    for pass_name in PASS_ORDER:
        for region in passes.get(pass_name, []):
            regions.append(
                {
                    "name": region.get("name", "unnamed"),
                    "pass": pass_name,
                    "mismatch_pct": region.get("mismatch_pct"),
                    "mismatch_pct_tolerant": region.get("mismatch_pct_tolerant"),
                    "action": region.get("action", ""),
                }
            )
    regions.sort(
        key=lambda item: as_float(item.get("mismatch_pct_tolerant"), as_float(item.get("mismatch_pct"), 0.0)) or 0.0,
        reverse=True,
    )
    return regions[:limit]


def choose_decision(
    lab: dict[str, Any],
    summary: list[dict[str, Any]],
    plan: dict[str, Any],
    args: argparse.Namespace,
) -> tuple[str, list[str], list[str]]:
    reasons: list[str] = []
    actions: list[str] = []

    reference = find_capture(summary, "reference-capture.png")
    rebuilt = find_capture(summary, "rebuilt-capture.png")
    exact = find_capture(summary, "exact-capture.png")

    if not summary:
        return (
            "run-diff",
            ["pixel-diff-summary.json is missing or unreadable."],
            [
                "Capture reference/rebuilt/exact modes at native size.",
                "Run pixel_diff.py before planning implementation changes.",
            ],
        )

    reference_mismatch = metric(reference, "mismatch_pct")
    if reference_mismatch is None:
        return (
            "fix-environment",
            ["reference-capture.png is missing from pixel-diff-summary.json."],
            ["Capture reference mode and prove a 0% baseline before touching reconstruction code."],
        )
    if reference_mismatch > args.reference_target:
        return (
            "fix-environment",
            [f"Reference baseline is {fmt_pct(reference_mismatch)}, expected <= {fmt_pct(args.reference_target)}."],
            ["Fix viewport, deviceScaleFactor, color profile, browser path, or wrong server/port first."],
        )

    slice_source = str(lab.get("slice_source") or "unknown")
    coverage = as_float(lab.get("coverage_pct"), 0.0) or 0.0
    exact_mismatch = metric(exact, "mismatch_pct")
    if slice_source == "manifest" and coverage < args.coverage_min:
        reasons.append(f"Manifest slice coverage is {coverage:.2f}%, below {args.coverage_min:.2f}%.")
        if exact_mismatch is None:
            reasons.append("exact-capture.png is missing, so the manifest ceiling is unproven.")
        elif exact_mismatch > args.exact_target:
            reasons.append(f"Manifest exact mismatch is {fmt_pct(exact_mismatch)}, above {fmt_pct(args.exact_target)}.")
        actions.extend(
            [
                "Expand the named region manifest before rebuilding individual components.",
                "Keep only media/icon/map/photo/chart islands as assets; component regions must stay DOM/SVG.",
                "Rerun prepare_lab.py --manifest after the region list covers the actual page content.",
            ]
        )
        return "manual-manifest", reasons, actions
    if slice_source == "auto":
        if coverage < args.coverage_min:
            reasons.append(f"Auto slice coverage is {coverage:.2f}%, below {args.coverage_min:.2f}%.")
        if exact_mismatch is None:
            reasons.append("exact-capture.png is missing, so the auto-slice ceiling is unproven.")
        elif exact_mismatch > args.exact_target:
            reasons.append(f"Auto exact mismatch is {fmt_pct(exact_mismatch)}, above {fmt_pct(args.exact_target)}.")
        if reasons:
            actions.extend(
                [
                    "Run a full-bleed exact proof to confirm the screenshot pipeline can reach 0%.",
                    "Measure named component bounds and create slice-manifest.json plus matching regions.json.",
                    "If regions.json already exists, run bootstrap_recovery.py to generate starter manifests, island assets, a ledger, and a scaffold.",
                    "Rerun prepare_lab.py --manifest with gap coverage enabled before judging component fidelity.",
                ]
            )
            return "manual-manifest", reasons, actions

    if slice_source in {"full-bleed", "manifest"} and exact_mismatch is not None and exact_mismatch <= args.exact_target and rebuilt is None:
        source_label = "Full-bleed" if slice_source == "full-bleed" else "Manifest"
        return (
            "bitmap-ceiling-ok",
            [f"{source_label} exact mismatch is {fmt_pct(exact_mismatch)}, so the screenshot pipeline can reach the bitmap ceiling."],
            [
                "Use this result as proof of the exact ceiling, not as a component implementation.",
                "Return to the component lab and create a named manual manifest for reusable islands and component regions.",
            ],
        )

    if rebuilt is None:
        return (
            "implement-first",
            ["rebuilt-capture.png is missing from pixel-diff-summary.json."],
            ["Implement or capture rebuilt mode before measuring component fidelity."],
        )

    if not plan:
        return (
            "run-planner",
            ["calibration-plan.json is missing or unreadable."],
            ["Run plan_calibration.py after pixel_diff.py, then rerun triage_lab.py."],
        )

    passes = plan_passes(plan)
    counts = {name: len(passes.get(name, [])) for name in PASS_ORDER}
    if counts["not-built"]:
        return (
            "skeleton-first",
            [f"{counts['not-built']} regions are classified as not built."],
            [
                "Apply skeleton.suggested.css or recreate equivalent project-native containers first.",
                "Recapture and rerun diff before tuning typography or colors.",
            ],
        )
    if counts["slice-island"]:
        return (
            "merge-islands",
            [f"{counts['slice-island']} raster-like regions are classified as slice islands."],
            [
                "Merge slice-manifest.suggested.json into slice-manifest.json.",
                "Keep charts/maps/photos/avatars/dense media as island assets unless the user explicitly wants hand-coded approximations.",
            ],
        )
    if counts["layout"] or counts["token"]:
        return (
            "layout-token-pass",
            [f"{counts['layout']} layout regions and {counts['token']} token regions need repair."],
            [
                "Fix integer layout shifts, canvas sizes, margins, and row/column geometry before colors.",
                "Sample colors and text styles from the reference after geometry stabilizes.",
            ],
        )
    if counts["rebuild"]:
        return (
            "region-rebuild",
            [f"{counts['rebuild']} regions remain structural rebuild candidates."],
            ["Rebuild worst regions one at a time against reference crops; avoid broad page rewrites."],
        )

    strict_score = metric(rebuilt, "mismatch_pct")
    tolerant_score = metric(rebuilt, "mismatch_pct_tolerant")
    if strict_score is None:
        return (
            "run-diff",
            ["rebuilt-capture.png has no strict mismatch metric."],
            ["Rerun pixel_diff.py before judging convergence."],
        )
    if strict_score > args.component_target:
        return (
            "region-rebuild",
            [
                f"Strict component mismatch is {fmt_pct(strict_score)} (tolerant {fmt_pct(tolerant_score)}), "
                f"above target {fmt_pct(args.component_target)} (the fidelity_gate 98% criterion uses strict mismatch)."
            ],
            ["Create named regions and rebuild the worst remaining component first."],
        )

    return (
        "converged",
        [
            f"Strict component mismatch is {fmt_pct(strict_score)} (tolerant {fmt_pct(tolerant_score)}), "
            f"within target {fmt_pct(args.component_target)}."
        ],
        [
            "Run fidelity_gate.py to confirm which gate this result satisfies.",
            "Record final metrics and hand off both component-faithful and bitmap-exact tracks.",
        ],
    )


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Lab Triage Report",
        "",
        f"Generated: `{report['generated_at']}`",
        f"Out dir: `{report['out_dir']}`",
        f"Decision: `{report['decision']}`",
        "",
        "## Why",
        "",
    ]
    for reason in report["reasons"]:
        lines.append(f"- {reason}")
    lines += ["", "## Next Actions", ""]
    for index, action in enumerate(report["actions"], 1):
        lines.append(f"{index}. {action}")

    metrics = report["metrics"]
    lines += [
        "",
        "## Metrics",
        "",
        "| Item | Strict mismatch | Tolerant mismatch | MAE |",
        "| --- | ---: | ---: | ---: |",
    ]
    for key in ("reference", "rebuilt", "exact"):
        item = metrics.get(key) or {}
        lines.append(
            f"| `{key}` | {fmt_pct(item.get('mismatch_pct'))} | "
            f"{fmt_pct(item.get('mismatch_pct_tolerant'))} | {item.get('mae', 'n/a')} |"
        )

    lab = report["lab"]
    lines += [
        "",
        "## Lab",
        "",
        f"- slice_source: `{lab.get('slice_source', 'unknown')}`",
        f"- coverage_pct: `{lab.get('coverage_pct', 'unknown')}`",
        f"- background_uniform: `{lab.get('background_uniform', 'unknown')}`",
    ]

    if report.get("plan_counts"):
        lines += ["", "## Plan Counts", ""]
        for name in PASS_ORDER:
            lines.append(f"- `{name}`: {report['plan_counts'].get(name, 0)}")

    if report.get("top_regions"):
        lines += ["", "## Top Regions", ""]
        for region in report["top_regions"]:
            lines.append(
                f"- `{region['name']}` ({region['pass']}): "
                f"{fmt_pct(region.get('mismatch_pct_tolerant'))} tolerant, "
                f"{fmt_pct(region.get('mismatch_pct'))} strict - {region.get('action', '')}"
            )

    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True, help="Pixel Twin Lab output directory")
    parser.add_argument("--coverage-min", type=float, default=80.0, help="Minimum trusted auto-slice coverage")
    parser.add_argument("--exact-target", type=float, default=1.0, help="Max trusted exact-mode mismatch percentage")
    parser.add_argument("--reference-target", type=float, default=0.01, help="Max allowed reference baseline mismatch")
    parser.add_argument(
        "--component-target",
        type=float,
        default=2.0,
        help="Target strict component mismatch percentage (2.0 matches the fidelity_gate 98%% strict-match criterion)",
    )
    parser.add_argument("--json-name", default="triage-report.json", help="JSON report filename")
    parser.add_argument("--md-name", default="triage-report.md", help="Markdown report filename")
    args = parser.parse_args()

    out_dir = Path(args.out_dir).expanduser().resolve()
    if not out_dir.exists():
        raise SystemExit(f"Output directory does not exist: {out_dir}")

    lab = load_json(out_dir / "lab-config.json", {})
    capture_meta = load_json(out_dir / "capture-meta.json", {})
    summary = load_json(out_dir / "pixel-diff-summary.json", [])
    if not isinstance(summary, list):
        summary = []
    plan = load_json(out_dir / "calibration-plan.json", {})
    if not isinstance(plan, dict):
        plan = {}

    decision, reasons, actions = choose_decision(lab, summary, plan, args)
    if not lab:
        reasons.append("lab-config.json is missing or unreadable; slice provenance and coverage were not checked.")
    passes = plan_passes(plan) if plan else {}
    plan_counts = {name: len(passes.get(name, [])) for name in PASS_ORDER} if plan else {}

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "out_dir": str(out_dir),
        "decision": decision,
        "reasons": reasons,
        "actions": actions,
        "thresholds": {
            "coverage_min": args.coverage_min,
            "exact_target": args.exact_target,
            "reference_target": args.reference_target,
            "component_target": args.component_target,
        },
        "lab": lab,
        "capture_meta": capture_meta,
        "metrics": {
            "reference": find_capture(summary, "reference-capture.png"),
            "rebuilt": find_capture(summary, "rebuilt-capture.png"),
            "exact": find_capture(summary, "exact-capture.png"),
        },
        "plan_counts": plan_counts,
        "top_regions": top_plan_regions(passes),
    }

    (out_dir / args.json_name).write_text(json.dumps(report, indent=2), encoding="utf-8")
    (out_dir / args.md_name).write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"out_dir": str(out_dir), "decision": decision, "reasons": reasons}, indent=2))


if __name__ == "__main__":
    main()
