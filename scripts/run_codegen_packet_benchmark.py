#!/usr/bin/env python3
"""Regression benchmark for make_codegen_packets.py.

Browser e2e benchmarks prove the rendered DOM contract. This benchmark proves the
generation handoff contract: fields that validation accepts in ui-blueprint.json
must survive merge/packet creation and reach codegen sub-agents without requiring
the raw reference image.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"


def run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def element_by_id(packet: dict[str, Any], element_id: str) -> dict[str, Any] | None:
    component = packet.get("component") if isinstance(packet.get("component"), dict) else {}
    for element in component.get("elements") or []:
        if isinstance(element, dict) and element.get("id") == element_id:
            return element
    return None


def assert_codegen_packet(work: Path) -> list[str]:
    fails: list[str] = []
    audit_packet_path = work / "packets" / "codegen" / "audit-log" / "packet.json"
    overlay_packet_path = work / "packets" / "codegen" / "trip-booking-card" / "packet.json"
    if not audit_packet_path.exists():
        fails.append(f"missing packet: {audit_packet_path}")
        return fails
    if not overlay_packet_path.exists():
        fails.append(f"missing packet: {overlay_packet_path}")
        return fails

    audit = load_json(audit_packet_path)
    collection = element_by_id(audit, "audit-log-list")
    if not collection:
        fails.append("audit-log packet lost blueprint collection element 'audit-log-list'")
    else:
        if collection.get("type") != "collection":
            fails.append(f"audit-log-list type = {collection.get('type')!r}, expected 'collection'")
        if collection.get("item_count") != 3:
            fails.append(f"audit-log-list item_count = {collection.get('item_count')!r}, expected 3")
        item_bounds = collection.get("item_bounds")
        if not isinstance(item_bounds, list) or len(item_bounds) != 3:
            fails.append(f"audit-log-list item_bounds length = {len(item_bounds) if isinstance(item_bounds, list) else None!r}, expected 3")
        elif item_bounds[0].get("id") != "row-1" or item_bounds[0].get("height") != 52:
            fails.append(f"audit-log-list first item_bounds not preserved: {item_bounds[0]!r}")
    if not audit.get("data"):
        fails.append("audit-log packet lost data entry")
    elif audit["data"][0].get("shape") != "items":
        fails.append(f"audit-log data shape = {audit['data'][0].get('shape')!r}, expected 'items'")
    icon = element_by_id(audit, "audit-log-icon")
    if not icon:
        fails.append("audit-log packet lost asset-required element 'audit-log-icon'")
    else:
        if icon.get("requires_asset") is not True:
            fails.append("audit-log-icon lost requires_asset=true")
        if icon.get("asset_path") != "element-assets/audit-log-icon.png":
            fails.append(f"audit-log-icon asset_path = {icon.get('asset_path')!r}, expected element-assets/audit-log-icon.png")
    audit_asset_paths = {
        asset.get("asset_path")
        for asset in audit.get("assets") or []
        if isinstance(asset, dict)
    }
    if "element-assets/audit-log-icon.png" not in audit_asset_paths:
        fails.append("audit-log packet did not expose audit-log-icon asset path to codegen")

    overlay = load_json(overlay_packet_path)
    component_style = ((overlay.get("component") or {}).get("style") or {}).get("expected") or {}
    if component_style.get("background_color") != "#ffffff":
        fails.append("trip-booking-card packet lost component.style.expected.background_color")
    if component_style.get("border_radius_px") != 8:
        fails.append("trip-booking-card packet lost component.style.expected.border_radius_px")
    if component_style.get("border_color") != "#dbe3ec":
        fails.append("trip-booking-card packet lost component.style.expected.border_color")
    region = overlay.get("region") if isinstance(overlay.get("region"), dict) else {}
    composition = region.get("composition") if isinstance(region.get("composition"), dict) else None
    if not composition:
        fails.append("trip-booking-card packet lost region.composition")
    else:
        background = composition.get("background") or {}
        if background.get("generated_asset_path") != "composition-assets/trip-map-background-clean.png":
            fails.append(
                "trip-booking-card composition background asset path not preserved: "
                f"{background.get('generated_asset_path')!r}"
            )
    asset_paths = {
        asset.get("asset_path")
        for asset in overlay.get("assets") or []
        if isinstance(asset, dict)
    }
    if "composition-assets/trip-map-background-clean.png" not in asset_paths:
        fails.append("trip-booking-card packet did not expose composition background as an asset")
    return fails


def assert_bad_composition_fails(work: Path) -> list[str]:
    fails: list[str] = []
    bad = work / "ui-blueprint-bad-composition.json"
    if not bad.exists():
        return ["missing bad composition fixture"]
    shutil.copy(bad, work / "ui-blueprint.json")
    validation = run(["python3", str(SCRIPTS / "validate_blueprint.py"), "--out-dir", str(work), "--allow-unmeasured"], ROOT)
    if validation.returncode == 0:
        return ["bad composition blueprint unexpectedly passed validation"]
    report = load_json(work / "blueprint-validation.json")
    messages = "\n".join(str(issue.get("message") or "") for issue in report.get("issues") or [])
    if "foreground component_id" not in messages:
        fails.append("bad composition failure did not mention foreground component_id")
    if "not a declared component id or element id" not in messages:
        fails.append("bad composition failure did not mention missing declared component/element id")
    return fails


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", default="codegen-packet-contract", help="benchmark/<suite> directory name")
    parser.add_argument("--keep", action="store_true", help="keep the temp work dir")
    args = parser.parse_args()

    suite = ROOT / "benchmark" / args.suite
    if not suite.exists():
        print(f"environment error: missing suite {suite}", file=sys.stderr)
        return 2

    work = Path(tempfile.mkdtemp(prefix="codegen-packet-bench-"))
    try:
        for path in suite.iterdir():
            if path.is_file():
                shutil.copy(path, work / path.name)

        validation = run(["python3", str(SCRIPTS / "validate_blueprint.py"), "--out-dir", str(work), "--allow-unmeasured"], ROOT)
        if validation.returncode != 0:
            print("FAIL: validate_blueprint.py failed", file=sys.stderr)
            print(validation.stdout)
            print(validation.stderr, file=sys.stderr)
            return 1

        packets = run(["python3", str(SCRIPTS / "make_codegen_packets.py"), "--out-dir", str(work)], ROOT)
        if packets.returncode != 0:
            print("FAIL: make_codegen_packets.py failed", file=sys.stderr)
            print(packets.stdout)
            print(packets.stderr, file=sys.stderr)
            return 1

        fails = assert_codegen_packet(work)
        bad_fails = assert_bad_composition_fails(work)
        print(f"\ncodegen packet benchmark: {args.suite}")
        if fails or bad_fails:
            print("  ✗ packet contract")
            for fail in fails + bad_fails:
                print(f"      - {fail}")
            if args.keep:
                print(f"  (work dir kept: {work})")
            return 1
        print("  ✓ packet contract")
        print("  ✓ bad composition rejected")
        print("\nPASS — 2/2 cases")
        if args.keep:
            print(f"  (work dir kept: {work})")
        return 0
    finally:
        if not args.keep:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
