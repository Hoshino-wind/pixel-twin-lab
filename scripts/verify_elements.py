#!/usr/bin/env python3
"""Verify the rendered DOM against the element manifest: presence, geometry, content, and type compatibility.

This is the anti-collage check at element granularity: a bitmap patch cannot contain the
individually addressable [data-element] nodes the manifest demands.

Repeated content (tables, lists, queues, feeds) is verified through ONE `collection` entry:
the container carries data-element, every row/item carries data-element-item, and the entry
declares item_count (or min_items) plus first_item_content. This replaces per-cell entries —
a collection that renders from a data array passes; hand-written sibling blocks still pass
geometry but are caught in review, while a bitmap patch fails outright. Approximation-track
chart containers use type `chart-host`: the node must contain the library's canvas/svg output.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CONTROL_TAGS = {"button", "input", "select", "textarea", "a", "label", "summary"}
CONTROL_ROLES = {"button", "link", "checkbox", "radio", "switch", "tab", "menuitem", "slider", "combobox"}

WEIGHT_NAMES = {"normal": 400, "bold": 700, "lighter": 300, "bolder": 700}
STYLE_DEFAULTS = {
    "font_size_px": 1.0,
    "font_weight": 0,
    "color_rgb_max": 8,
    "line_height_px": 2.0,
    "letter_spacing_px": 0.5,
    "border_radius_px": 2.0,
}
STYLE_NUMERIC = (
    ("font_size_px", "font_size_px"),
    ("line_height_px", "line_height_px"),
    ("letter_spacing_px", "letter_spacing_px"),
    ("border_radius_px", "border_radius_px"),
)
STYLE_COLORS = ("color", "background_color", "border_color")
STYLE_ENUMS = ("text_align", "vertical_align", "text_transform")


def norm_weight(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value).strip().lower()
    if s.isdigit():
        return int(s)
    return WEIGHT_NAMES.get(s)


def hex_to_rgb(value: Any) -> tuple[int, int, int] | None:
    if not isinstance(value, str):
        return None
    h = value.strip().lstrip("#")
    if len(h) != 6:
        return None
    try:
        return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError:
        return None


def compare_style(expected: dict[str, Any], dom_style: dict[str, Any], tol: dict[str, float], label: str = "") -> list[str]:
    """Per-property style assertion. Returns one problem string per property outside tolerance,
    each carrying the actual-vs-expected delta — the repair worklist line."""
    problems: list[str] = []
    if not isinstance(expected, dict) or not isinstance(dom_style, dict):
        return problems

    def limit(key: str) -> float:
        return float(tol.get(key, STYLE_DEFAULTS.get(key, 0)))

    pfx = f"{label} " if label else ""
    for prop, tolkey in STYLE_NUMERIC:
        if expected.get(prop) is None:
            continue
        exp = float(expected[prop])
        act = dom_style.get(prop)
        if act is None:
            problems.append(f"{pfx}{prop}: expected {exp}, DOM missing")
            continue
        delta = abs(float(act) - exp)
        if delta > limit(tolkey):
            problems.append(f"{pfx}{prop}: {act} vs expected {exp} (Δ{round(delta, 2)} > {limit(tolkey)})")

    if expected.get("font_weight") is not None:
        exp_w = norm_weight(expected["font_weight"])
        act_w = norm_weight(dom_style.get("font_weight"))
        if act_w is None:
            problems.append(f"{pfx}font_weight: expected {exp_w}, DOM missing")
        elif exp_w is not None and abs(act_w - exp_w) > limit("font_weight"):
            problems.append(f"{pfx}font_weight: {act_w} vs expected {exp_w} (Δ{abs(act_w - exp_w)})")

    for prop in STYLE_COLORS:
        if not expected.get(prop):
            continue
        exp_c = hex_to_rgb(expected[prop])
        if exp_c is None:
            continue
        act_c = hex_to_rgb(dom_style.get(prop))
        if act_c is None:
            problems.append(f"{pfx}{prop}: expected {expected[prop]}, DOM {dom_style.get(prop)}")
            continue
        delta = max(abs(act_c[i] - exp_c[i]) for i in range(3))
        if delta > limit("color_rgb_max"):
            problems.append(f"{pfx}{prop}: {dom_style.get(prop)} vs expected {expected[prop]} (Δch{delta} > {int(limit('color_rgb_max'))})")

    for prop in STYLE_ENUMS:
        if expected.get(prop) is None:
            continue
        act = dom_style.get(prop)
        if act is not None and str(act) != str(expected[prop]):
            problems.append(f"{pfx}{prop}: '{act}' vs expected '{expected[prop]}'")

    if expected.get("font_family"):
        act = str(dom_style.get("font_family") or "")
        exp_f = str(expected["font_family"])
        if act and exp_f.lower() not in act.lower() and act.lower() not in exp_f.lower():
            problems.append(f"{pfx}font_family: '{act}' vs expected '{exp_f}'")

    return problems


def base_tolerance(args: argparse.Namespace) -> dict[str, float]:
    return {
        "font_size_px": args.max_font_size_delta,
        "font_weight": args.max_font_weight_delta,
        "color_rgb_max": args.max_color_delta,
        "line_height_px": args.max_line_height_delta,
        "letter_spacing_px": args.max_letter_spacing_delta,
        "border_radius_px": args.max_radius_delta,
    }


def load_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return fallback


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip().lower()


def type_compatible(element_type: str, dom: dict[str, Any]) -> tuple[bool, str]:
    tag = str(dom.get("tag") or "")
    role = str(dom.get("role") or "")
    if element_type == "text":
        if normalize_text(str(dom.get("text") or "")):
            return True, ""
        return False, "type 'text' but the DOM node has no text content"
    if element_type == "icon":
        if dom.get("has_svg") or dom.get("has_img") or tag in {"svg", "img", "i", "canvas"}:
            return True, ""
        return False, "type 'icon' but the DOM node has no svg/img content"
    if element_type == "image":
        if dom.get("has_img") or tag in {"img", "picture", "video"}:
            return True, ""
        return False, "type 'image' but the DOM node has no img content"
    if element_type == "control":
        if tag in CONTROL_TAGS or role in CONTROL_ROLES:
            return True, ""
        return False, f"type 'control' but tag `{tag}`/role `{role or 'none'}` is not interactive"
    if element_type == "chart-host":
        if dom.get("has_canvas") or dom.get("has_svg"):
            return True, ""
        return False, "type 'chart-host' but the DOM node contains no canvas/svg (the chart library has not rendered into it)"
    return True, ""


def verify_element(element: dict[str, Any], dom_matches: list[dict[str, Any]], args: argparse.Namespace) -> dict[str, Any]:
    element_id = str(element.get("id"))
    result: dict[str, Any] = {
        "id": element_id,
        "type": element.get("type") or None,
        "maps_to": element.get("maps_to") or None,
        "labeled": bool(element.get("type")),
        "problems": [],
    }
    if not result["labeled"]:
        result["problems"].append("unlabeled: type/content/maps_to not filled in")

    if not dom_matches:
        result["status"] = "missing"
        result["problems"].append("no DOM node carries data-element with this id")
        return result
    if len(dom_matches) > 1:
        result["status"] = "duplicate"
        result["problems"].append(f"{len(dom_matches)} DOM nodes carry this id; ids must be unique")
        return result

    dom = dom_matches[0]
    bounds = element.get("bounds") or {}
    dom_bounds = dom.get("bounds") or {}
    dx = abs(int(dom_bounds.get("x", 0)) - int(bounds.get("x", 0)))
    dy = abs(int(dom_bounds.get("y", 0)) - int(bounds.get("y", 0)))
    dw = abs(int(dom_bounds.get("width", 0)) - int(bounds.get("width", 0)))
    dh = abs(int(dom_bounds.get("height", 0)) - int(bounds.get("height", 0)))
    result["position_delta"] = {"dx": dx, "dy": dy}
    result["size_delta"] = {"dw": dw, "dh": dh}
    if max(dx, dy) > args.max_position_delta:
        result["problems"].append(
            f"position off by ({dx}, {dy})px, allowed {args.max_position_delta}px; manifest {bounds} vs DOM {dom_bounds}"
        )
    if max(dw, dh) > args.max_size_delta:
        result["problems"].append(
            f"size off by ({dw}, {dh})px, allowed {args.max_size_delta}px; manifest {bounds} vs DOM {dom_bounds}"
        )

    element_type = str(element.get("type") or "")
    if element_type == "text" and element.get("content"):
        expected = normalize_text(str(element["content"]))
        actual = normalize_text(str(dom.get("text") or ""))
        if expected and expected not in actual and actual not in expected:
            result["problems"].append(f"text mismatch: manifest '{element['content']}' vs DOM '{dom.get('text')}'")
    if element_type == "collection":
        actual_items = int(dom.get("item_count") or 0)
        result["item_count"] = actual_items
        if actual_items == 0:
            result["problems"].append(
                "type 'collection' but no descendant [data-element-item] nodes; "
                "collections must render rows/items from a data array through one template"
            )
        else:
            expected_count = element.get("item_count")
            min_items = element.get("min_items")
            if expected_count is not None and actual_items != int(expected_count):
                result["problems"].append(f"item count {actual_items} != expected {expected_count}")
            elif min_items is not None and actual_items < int(min_items):
                result["problems"].append(f"item count {actual_items} < min_items {min_items}")
            first_expected = element.get("first_item_content")
            if first_expected:
                expected = normalize_text(str(first_expected))
                actual = normalize_text(str(dom.get("first_item_text") or ""))
                if expected and expected not in actual and actual not in expected:
                    result["problems"].append(
                        f"first item mismatch: manifest '{first_expected}' vs DOM '{dom.get('first_item_text')}'"
                    )
    if element_type:
        compatible, reason = type_compatible(element_type, dom)
        if not compatible:
            result["problems"].append(reason)

    # --- style contract (optional): single style and/or composite runs ---
    base_tol = base_tolerance(args)

    def apply_style(block: dict[str, Any], dom_style: dict[str, Any], label: str = "") -> None:
        if not isinstance(block, dict) or not isinstance(block.get("expected"), dict):
            return
        tol = {**base_tol, **(block.get("tolerance") or {})}
        problems = compare_style(block["expected"], dom_style or {}, tol, label=label)
        if not problems:
            return
        if block.get("severity") == "warn":
            result.setdefault("warnings", []).extend(problems)
        else:
            result["problems"].extend(problems)

    style_block = element.get("style")
    if isinstance(style_block, dict):
        apply_style(style_block, dom.get("style") or {})

    runs = element.get("runs")
    if isinstance(runs, list) and runs:
        dom_runs = dom.get("runs") or []
        result["run_count"] = len(dom_runs)
        if len(dom_runs) != len(runs):
            result["problems"].append(
                f"runs: DOM has {len(dom_runs)} [data-run] node(s), manifest declares {len(runs)} "
                "(composite text must render one node per run carrying data-run)"
            )
        for i, run in enumerate(runs):
            if not isinstance(run, dict):
                continue
            dom_run = dom_runs[i] if i < len(dom_runs) else {}
            exp_t = normalize_text(str(run.get("text") or ""))
            act_t = normalize_text(str(dom_run.get("text") or ""))
            if exp_t and exp_t not in act_t and act_t not in exp_t:
                result["problems"].append(f"run[{i}] text: '{run.get('text')}' vs DOM '{dom_run.get('text')}'")
            apply_style(run.get("style") or {}, dom_run.get("style") or {}, label=f"run[{i}]")

    result["status"] = "ok" if not result["problems"] else "failed"
    return result


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Element Verification",
        "",
        f"Generated: `{report['generated_at']}`",
        f"Result: `{'PASS' if summary['pass'] else 'FAIL'}`",
        "",
        f"- elements: {summary['total']} | ok: {summary['ok']} | missing: {summary['missing']} | "
        f"duplicate: {summary['duplicate']} | failed: {summary['failed']} | unlabeled: {summary['unlabeled']}",
        f"- extra DOM ids not in the manifest: {summary['extra_dom_ids']}",
        "",
        "| Region | Ok / Total |",
        "| --- | --- |",
    ]
    for region in report["regions"]:
        lines.append(f"| `{region['name']}` | {region['ok']}/{region['total']} |")
    lines.append("")
    for region in report["regions"]:
        failures = [e for e in region["elements"] if e["status"] != "ok" or e["problems"] or e.get("warnings")]
        if not failures:
            continue
        lines.append(f"## `{region['name']}`")
        lines.append("")
        for element in failures:
            lines.append(f"- `{element['id']}` ({element['status']}):")
            for problem in element["problems"]:
                lines.append(f"  - {problem}")
            for warning in element.get("warnings", []):
                lines.append(f"  - ⚠ {warning}")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True, help="Pixel Twin Lab output directory")
    parser.add_argument("--manifest-name", default="element-manifest.json")
    parser.add_argument("--dom-name", default="dom-elements.json")
    parser.add_argument("--max-position-delta", type=int, default=6, help="Max allowed |dx|/|dy| in px")
    parser.add_argument("--max-size-delta", type=int, default=10, help="Max allowed |dw|/|dh| in px")
    parser.add_argument("--max-font-size-delta", type=float, default=1.0, help="Max allowed font-size delta in px")
    parser.add_argument("--max-font-weight-delta", type=int, default=0, help="Max allowed numeric weight delta (0 = exact)")
    parser.add_argument("--max-color-delta", type=int, default=8, help="Max allowed per-channel RGB delta (0-255)")
    parser.add_argument("--max-line-height-delta", type=float, default=2.0, help="Max allowed line-height delta in px")
    parser.add_argument("--max-letter-spacing-delta", type=float, default=0.5, help="Max allowed letter-spacing delta in px")
    parser.add_argument("--max-radius-delta", type=float, default=2.0, help="Max allowed border-radius delta in px")
    parser.add_argument("--json-name", default="element-verification.json")
    parser.add_argument("--md-name", default="element-verification.md")
    args = parser.parse_args()

    out_dir = Path(args.out_dir).expanduser().resolve()
    manifest = load_json(out_dir / args.manifest_name, {})
    if not isinstance(manifest, dict) or not manifest.get("regions"):
        raise SystemExit(f"Element manifest missing or empty: {out_dir / args.manifest_name}. Run init_element_manifest.py first.")
    dom = load_json(out_dir / args.dom_name, {})
    if not isinstance(dom, dict) or "elements" not in dom:
        raise SystemExit(f"DOM measurement missing: {out_dir / args.dom_name}. Run measure_dom_elements.cjs first.")

    dom_by_id: dict[str, list[dict[str, Any]]] = {}
    for node in dom.get("elements") or []:
        if isinstance(node, dict) and node.get("id"):
            dom_by_id.setdefault(str(node["id"]), []).append(node)

    manifest_ids: set[str] = set()
    regions_out: list[dict[str, Any]] = []
    all_results: list[dict[str, Any]] = []
    for region in manifest["regions"]:
        if not isinstance(region, dict):
            continue
        elements = region.get("elements") or []
        results = []
        for element in elements:
            if not isinstance(element, dict) or not element.get("id"):
                continue
            element_id = str(element["id"])
            manifest_ids.add(element_id)
            results.append(verify_element(element, dom_by_id.get(element_id, []), args))
        regions_out.append(
            {
                "name": str(region.get("name")),
                "total": len(results),
                "ok": sum(1 for r in results if r["status"] == "ok"),
                "elements": results,
            }
        )
        all_results.extend(results)

    extra_dom_ids = sorted(set(dom_by_id) - manifest_ids)
    summary = {
        "total": len(all_results),
        "ok": sum(1 for r in all_results if r["status"] == "ok"),
        "missing": sum(1 for r in all_results if r["status"] == "missing"),
        "duplicate": sum(1 for r in all_results if r["status"] == "duplicate"),
        "failed": sum(1 for r in all_results if r["status"] == "failed"),
        "unlabeled": sum(1 for r in all_results if not r["labeled"]),
        "extra_dom_ids": extra_dom_ids,
    }
    summary["pass"] = (
        summary["total"] > 0
        and summary["ok"] == summary["total"]
        and summary["unlabeled"] == 0
    )

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "out_dir": str(out_dir),
        "manifest": str(out_dir / args.manifest_name),
        "dom_source": str(out_dir / args.dom_name),
        "thresholds": {"max_position_delta": args.max_position_delta, "max_size_delta": args.max_size_delta},
        "summary": summary,
        "regions": regions_out,
    }
    (out_dir / args.json_name).write_text(json.dumps(report, indent=2), encoding="utf-8")
    (out_dir / args.md_name).write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "out_dir": str(out_dir),
                "pass": summary["pass"],
                "ok": f"{summary['ok']}/{summary['total']}",
                "missing": summary["missing"],
                "failed": summary["failed"],
                "unlabeled": summary["unlabeled"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
