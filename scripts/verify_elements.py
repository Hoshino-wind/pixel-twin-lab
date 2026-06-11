#!/usr/bin/env python3
"""Verify the rendered DOM against the element manifest: presence, geometry, content, and type compatibility.

This is the anti-collage check at element granularity: a bitmap patch cannot contain the
individually addressable [data-element] nodes the manifest demands.
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
    if element_type:
        compatible, reason = type_compatible(element_type, dom)
        if not compatible:
            result["problems"].append(reason)

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
        failures = [e for e in region["elements"] if e["status"] != "ok" or e["problems"]]
        if not failures:
            continue
        lines.append(f"## `{region['name']}`")
        lines.append("")
        for element in failures:
            lines.append(f"- `{element['id']}` ({element['status']}):")
            for problem in element["problems"]:
                lines.append(f"  - {problem}")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True, help="Pixel Twin Lab output directory")
    parser.add_argument("--manifest-name", default="element-manifest.json")
    parser.add_argument("--dom-name", default="dom-elements.json")
    parser.add_argument("--max-position-delta", type=int, default=6, help="Max allowed |dx|/|dy| in px")
    parser.add_argument("--max-size-delta", type=int, default=10, help="Max allowed |dw|/|dh| in px")
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
