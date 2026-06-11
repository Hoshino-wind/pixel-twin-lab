#!/usr/bin/env python3
"""Compare region metrics between a baseline lab and a candidate lab."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"Missing file: {path}") from None
    except json.JSONDecodeError as error:
        raise SystemExit(f"Invalid JSON: {path}: {error}") from None


def find_capture(summary: list[dict[str, Any]], file_name: str) -> dict[str, Any]:
    for item in summary:
        if item.get("file") == file_name:
            return item
    raise SystemExit(f"Missing capture metrics for {file_name}")


def by_region(capture: dict[str, Any]) -> dict[str, dict[str, Any]]:
    regions: dict[str, dict[str, Any]] = {}
    for region in capture.get("regions") or []:
        if isinstance(region, dict) and region.get("name"):
            regions[str(region["name"])] = region
    return regions


def as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def opt_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fmt_pct(value: Any) -> str:
    return "n/a" if value is None else f"{float(value):.4f}%"


def fmt_delta(value: Any) -> str:
    return "n/a" if value is None else f"{float(value):+.4f}%"


def metric_row(name: str, baseline: dict[str, Any] | None, candidate: dict[str, Any] | None, epsilon: float) -> dict[str, Any]:
    if baseline is None or candidate is None:
        status = "baseline-missing" if baseline is None else "candidate-missing"
        return {
            "name": name,
            "status": status,
            "baseline_strict_mismatch_pct": opt_float((baseline or {}).get("mismatch_pct")),
            "candidate_strict_mismatch_pct": opt_float((candidate or {}).get("mismatch_pct")),
            "strict_delta_pct": None,
            "baseline_tolerant_mismatch_pct": opt_float((baseline or {}).get("mismatch_pct_tolerant")),
            "candidate_tolerant_mismatch_pct": opt_float((candidate or {}).get("mismatch_pct_tolerant")),
            "tolerant_delta_pct": None,
            "strict_improved": False,
            "tolerant_improved": False,
            "regressed": False,
        }
    base_strict = as_float(baseline.get("mismatch_pct"))
    cand_strict = as_float(candidate.get("mismatch_pct"))
    base_tol = opt_float(baseline.get("mismatch_pct_tolerant"))
    cand_tol = opt_float(candidate.get("mismatch_pct_tolerant"))
    strict_delta = cand_strict - base_strict
    tolerant_delta = cand_tol - base_tol if base_tol is not None and cand_tol is not None else None
    return {
        "name": name,
        "status": "compared",
        "baseline_strict_mismatch_pct": base_strict,
        "candidate_strict_mismatch_pct": cand_strict,
        "strict_delta_pct": strict_delta,
        "baseline_tolerant_mismatch_pct": base_tol,
        "candidate_tolerant_mismatch_pct": cand_tol,
        "tolerant_delta_pct": tolerant_delta,
        "strict_improved": strict_delta < -epsilon,
        "tolerant_improved": tolerant_delta is not None and tolerant_delta < -epsilon,
        "regressed": strict_delta > epsilon or (tolerant_delta is not None and tolerant_delta > epsilon),
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Region Metric Comparison",
        "",
        f"Generated: `{report['generated_at']}`",
        f"Baseline: `{report['baseline_dir']}`",
        f"Candidate: `{report['candidate_dir']}`",
        f"Capture: `{report['capture']}`",
        "",
        "## Overall",
        "",
        "| Metric | Baseline | Candidate | Delta |",
        "| --- | ---: | ---: | ---: |",
    ]
    overall = report["overall"]
    lines += [
        f"| Strict mismatch | {fmt_pct(overall['baseline_strict_mismatch_pct'])} | {fmt_pct(overall['candidate_strict_mismatch_pct'])} | {fmt_delta(overall['strict_delta_pct'])} |",
        f"| Tolerant mismatch | {fmt_pct(overall['baseline_tolerant_mismatch_pct'])} | {fmt_pct(overall['candidate_tolerant_mismatch_pct'])} | {fmt_delta(overall['tolerant_delta_pct'])} |",
        "",
        "## Regions",
        "",
        "| Region | Strict delta | Tolerant delta | Baseline strict | Candidate strict | Baseline tolerant | Candidate tolerant |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in report["regions"]:
        lines.append(
            f"| `{item['name']}` | {fmt_delta(item['strict_delta_pct'])} | {fmt_delta(item['tolerant_delta_pct'])} | "
            f"{fmt_pct(item['baseline_strict_mismatch_pct'])} | {fmt_pct(item['candidate_strict_mismatch_pct'])} | "
            f"{fmt_pct(item['baseline_tolerant_mismatch_pct'])} | {fmt_pct(item['candidate_tolerant_mismatch_pct'])} |"
        )
    missing = report.get("missing_regions") or []
    if missing:
        lines += [
            "",
            "## Missing Regions",
            "",
            "These regions exist on only one side; they are excluded from regressed/improved stats and fail gates.",
            "",
            "| Region | Status | Baseline strict | Candidate strict | Baseline tolerant | Candidate tolerant |",
            "| --- | --- | ---: | ---: | ---: | ---: |",
        ]
        for item in missing:
            lines.append(
                f"| `{item['name']}` | `{item['status']}` | "
                f"{fmt_pct(item['baseline_strict_mismatch_pct'])} | {fmt_pct(item['candidate_strict_mismatch_pct'])} | "
                f"{fmt_pct(item['baseline_tolerant_mismatch_pct'])} | {fmt_pct(item['candidate_tolerant_mismatch_pct'])} |"
            )
    lines += [
        "",
        "## Interpretation",
        "",
        f"- strict_improved_regions: `{report['summary']['strict_improved_regions']}`",
        f"- tolerant_improved_regions: `{report['summary']['tolerant_improved_regions']}`",
        f"- regressed_regions: `{report['summary']['regressed_regions']}`",
        f"- missing_regions: `{report['summary']['missing_regions']}`",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, help="Baseline Pixel Twin lab directory")
    parser.add_argument("--candidate", required=True, help="Candidate Pixel Twin lab directory")
    parser.add_argument("--capture", default="rebuilt-capture.png")
    parser.add_argument("--regions", help="Comma-separated region names to include in pass/fail checks")
    parser.add_argument(
        "--epsilon",
        type=float,
        default=0.01,
        help="Delta in percentage points beyond which a change counts as regressed/improved; deltas within ±epsilon are unchanged",
    )
    parser.add_argument("--fail-on-regression", action="store_true", help="Exit nonzero if overall or any selected region regresses in strict or tolerant mismatch")
    parser.add_argument("--fail-on-strict-regression", action="store_true", help="Exit nonzero if overall or any selected region regresses in strict mismatch")
    parser.add_argument("--json-name", default="region-metric-comparison.json")
    parser.add_argument("--md-name", default="region-metric-comparison.md")
    args = parser.parse_args()

    baseline_dir = Path(args.baseline).expanduser().resolve()
    candidate_dir = Path(args.candidate).expanduser().resolve()
    baseline_summary = load_json(baseline_dir / "pixel-diff-summary.json")
    candidate_summary = load_json(candidate_dir / "pixel-diff-summary.json")
    if not isinstance(baseline_summary, list) or not isinstance(candidate_summary, list):
        raise SystemExit("pixel-diff-summary.json must be a list in both labs")

    baseline_capture = find_capture(baseline_summary, args.capture)
    candidate_capture = find_capture(candidate_summary, args.capture)
    baseline_regions = by_region(baseline_capture)
    candidate_regions = by_region(candidate_capture)
    names = sorted(set(baseline_regions) | set(candidate_regions))
    selected_names = {name.strip() for name in args.regions.split(",") if name.strip()} if args.regions else set()
    if selected_names:
        if not selected_names & set(names):
            raise SystemExit(
                f"--regions matched no region in either lab. Available regions: {', '.join(names) or '(none)'}"
            )
        names = [name for name in names if name in selected_names]
    all_rows = [metric_row(name, baseline_regions.get(name), candidate_regions.get(name), args.epsilon) for name in names]
    rows = [item for item in all_rows if item["status"] == "compared"]
    missing_rows = [item for item in all_rows if item["status"] != "compared"]
    rows.sort(key=lambda item: (item["strict_delta_pct"], as_float(item["tolerant_delta_pct"])))

    overall = metric_row("__overall__", baseline_capture, candidate_capture, args.epsilon)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "baseline_dir": str(baseline_dir),
        "candidate_dir": str(candidate_dir),
        "capture": args.capture,
        "selected_regions": sorted(selected_names),
        "epsilon_pct": args.epsilon,
        "overall": overall,
        "regions": rows,
        "missing_regions": missing_rows,
        "summary": {
            "strict_improved_regions": [item["name"] for item in rows if item["strict_improved"]],
            "tolerant_improved_regions": [item["name"] for item in rows if item["tolerant_improved"]],
            "regressed_regions": [item["name"] for item in rows if item["regressed"]],
            "missing_regions": [item["name"] for item in missing_rows],
        },
    }
    (candidate_dir / args.json_name).write_text(json.dumps(report, indent=2), encoding="utf-8")
    (candidate_dir / args.md_name).write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "candidate": str(candidate_dir),
                "overall_strict_delta_pct": round(overall["strict_delta_pct"], 4),
                "overall_tolerant_delta_pct": round(overall["tolerant_delta_pct"], 4),
                "strict_improved_regions": report["summary"]["strict_improved_regions"],
                "tolerant_improved_regions": report["summary"]["tolerant_improved_regions"],
                "regressed_regions": report["summary"]["regressed_regions"],
                "missing_regions": report["summary"]["missing_regions"],
            },
            indent=2,
        )
    )
    if args.fail_on_regression and (overall["regressed"] or any(item["regressed"] for item in rows)):
        raise SystemExit(2)
    if args.fail_on_strict_regression and (
        overall["strict_delta_pct"] > args.epsilon or any(item["strict_delta_pct"] > args.epsilon for item in rows)
    ):
        raise SystemExit(3)


if __name__ == "__main__":
    main()
