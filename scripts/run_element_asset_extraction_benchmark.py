#!/usr/bin/env python3
"""Regression checks for element-level asset extraction heuristics."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parent.parent


def write_reference(out_dir: Path, extra_icon: bool = False) -> None:
    assets_dir = out_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (120, 80), "white")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((12, 12, 108, 68), radius=8, fill=(248, 250, 252), outline=(220, 224, 230))
    draw.ellipse((24, 28, 40, 44), fill=(20, 20, 20))
    if extra_icon:
        draw.rectangle((72, 28, 88, 44), fill=(20, 20, 20))
    image.save(assets_dir / "reference.png")


def write_measured(out_dir: Path, extra_icon: bool = False) -> None:
    primitives = [
        {
            "kind": "icon-or-badge",
            "full_bounds": {"x": 24, "y": 28, "width": 17, "height": 17},
        }
    ]
    if extra_icon:
        primitives.append(
            {
                "kind": "icon-or-badge",
                "full_bounds": {"x": 72, "y": 28, "width": 17, "height": 17},
            }
        )
    measured = {
        "regions": [
            {
                "name": "panel-a",
                "bounds": {"x": 12, "y": 12, "width": 96, "height": 56},
                "primitives": primitives,
            }
        ]
    }
    (out_dir / "measured-primitives.json").write_text(json.dumps(measured, indent=2), encoding="utf-8")


def write_asset_plan(out_dir: Path) -> None:
    plan = {
        "assets": [
            {
                "id": "planned-left-icon",
                "region": "panel-a",
                "asset_type": "icon",
                "bounds": {"x": 24, "y": 28, "width": 17, "height": 17},
                "reason": "explicitly selected icon asset",
            }
        ]
    }
    (out_dir / "asset-plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")


def write_text_overlap_manifest(out_dir: Path) -> None:
    manifest = {
        "regions": [
            {
                "name": "panel-a",
                "bounds": {"x": 12, "y": 12, "width": 96, "height": 56},
                "elements": [
                    {
                        "id": "panel-a-ocr-01",
                        "type": "text",
                        "source": "ocr",
                        "bounds": {"x": 20, "y": 25, "width": 30, "height": 24},
                        "content": "O",
                    }
                ],
            }
        ]
    }
    (out_dir / "element-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def run_extract_report(out_dir: Path) -> dict[str, Any]:
    proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "extract_element_assets.py"),
            "--out-dir",
            str(out_dir),
            "--no-text-assets",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(f"extract_element_assets.py failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    return json.loads((out_dir / "element-assets.json").read_text(encoding="utf-8"))


def run_extract(out_dir: Path) -> list[dict[str, Any]]:
    report = run_extract_report(out_dir)
    return [asset for asset in report.get("assets") or [] if isinstance(asset, dict)]


def run_extract_expect_failure(out_dir: Path) -> str:
    proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "extract_element_assets.py"),
            "--out-dir",
            str(out_dir),
            "--no-text-assets",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode == 0:
        raise AssertionError(f"extract_element_assets.py unexpectedly passed\nSTDOUT:\n{proc.stdout}")
    return proc.stdout + proc.stderr


def make_case(name: str, with_text_overlap: bool) -> Path:
    out_dir = Path(tempfile.mkdtemp(prefix=f"ptl-element-assets-{name}.", dir="/tmp"))
    write_reference(out_dir)
    write_measured(out_dir)
    if with_text_overlap:
        write_text_overlap_manifest(out_dir)
    return out_dir


def make_plan_case() -> Path:
    out_dir = Path(tempfile.mkdtemp(prefix="ptl-element-assets-plan-only.", dir="/tmp"))
    write_reference(out_dir, extra_icon=True)
    write_measured(out_dir, extra_icon=True)
    write_asset_plan(out_dir)
    return out_dir


def make_component_plan_case() -> Path:
    out_dir = Path(tempfile.mkdtemp(prefix="ptl-element-assets-component-plan.", dir="/tmp"))
    write_reference(out_dir)
    write_measured(out_dir)
    plan = {
        "assets": [
            {
                "id": "bad-component-crop",
                "region": "panel-a",
                "asset_type": "component",
                "bounds": {"x": 12, "y": 12, "width": 96, "height": 56},
            }
        ]
    }
    (out_dir / "asset-plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
    return out_dir


def make_text_dominant_control_case() -> Path:
    out_dir = Path(tempfile.mkdtemp(prefix="ptl-element-assets-text-control.", dir="/tmp"))
    assets_dir = out_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (140, 82), "white")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((18, 24, 76, 46), radius=5, fill=(255, 255, 255), outline=(220, 224, 230))
    draw.rectangle((25, 31, 68, 39), fill=(20, 20, 20))
    draw.ellipse((98, 28, 114, 44), fill=(20, 20, 20))
    image.save(assets_dir / "reference.png")
    measured = {
        "regions": [
            {
                "name": "panel-a",
                "bounds": {"x": 12, "y": 12, "width": 116, "height": 58},
                "primitives": [
                    {
                        "kind": "control-or-card-fragment",
                        "full_bounds": {"x": 18, "y": 24, "width": 58, "height": 22},
                    },
                    {
                        "kind": "icon-or-badge",
                        "full_bounds": {"x": 98, "y": 28, "width": 17, "height": 17},
                    },
                ],
            }
        ]
    }
    (out_dir / "measured-primitives.json").write_text(json.dumps(measured, indent=2), encoding="utf-8")
    manifest = {
        "regions": [
            {
                "name": "panel-a",
                "bounds": {"x": 12, "y": 12, "width": 116, "height": 58},
                "elements": [
                    {
                        "id": "panel-a-ocr-01",
                        "type": "text",
                        "source": "ocr",
                        "bounds": {"x": 23, "y": 29, "width": 48, "height": 12},
                        "content": "Label",
                    }
                ],
            }
        ]
    }
    (out_dir / "element-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return out_dir


def main() -> None:
    results: list[tuple[str, list[str]]] = []

    icon_dir = make_case("generic-icon", with_text_overlap=False)
    icon_assets = run_extract(icon_dir)
    icon_failures: list[str] = []
    if len(icon_assets) != 1:
        icon_failures.append(f"expected 1 asset, got {len(icon_assets)}")
    elif icon_assets[0].get("asset_type") != "icon":
        icon_failures.append(f"expected icon asset_type, got {icon_assets[0].get('asset_type')!r}")
    elif "generic monochrome icon geometry" not in str(icon_assets[0].get("reason") or ""):
        icon_failures.append(f"expected generic monochrome reason, got {icon_assets[0].get('reason')!r}")
    results.append(("generic-monochrome-icon", icon_failures))

    text_dir = make_case("text-overlap", with_text_overlap=True)
    text_assets = run_extract(text_dir)
    text_failures: list[str] = []
    if text_assets:
        text_failures.append(f"expected text-overlap primitive to be rejected, got {len(text_assets)} assets")
    results.append(("text-overlap-rejected", text_failures))

    text_control_dir = make_text_dominant_control_case()
    text_control_report = run_extract_report(text_control_dir)
    text_control_assets = [asset for asset in text_control_report.get("assets") or [] if isinstance(asset, dict)]
    text_control_surfaces = [
        surface for surface in text_control_report.get("component_surfaces") or [] if isinstance(surface, dict)
    ]
    text_control_failures: list[str] = []
    if any(asset.get("asset_type") == "control" for asset in text_control_assets):
        text_control_failures.append("expected text-dominant control crop to be rejected")
    if not text_control_surfaces:
        text_control_failures.append("expected text-dominant control crop to become a component surface")
    if not any(asset.get("asset_type") == "icon" for asset in text_control_assets):
        text_control_failures.append("expected non-overlapping icon crop to be preserved")
    results.append(("text-dominant-control-rejected", text_control_failures))

    plan_dir = make_plan_case()
    plan_assets = run_extract(plan_dir)
    plan_failures: list[str] = []
    if len(plan_assets) != 1:
        plan_failures.append(f"expected plan-only extraction to keep exactly 1 asset, got {len(plan_assets)}")
    elif plan_assets[0].get("id") != "planned-left-icon":
        plan_failures.append(f"expected planned-left-icon, got {plan_assets[0].get('id')!r}")
    elif plan_assets[0].get("source") != "asset-plan":
        plan_failures.append(f"expected source asset-plan, got {plan_assets[0].get('source')!r}")
    results.append(("asset-plan-disables-heuristics", plan_failures))

    component_plan_dir = make_component_plan_case()
    component_plan_failures: list[str] = []
    try:
        failure_text = run_extract_expect_failure(component_plan_dir)
    except AssertionError as exc:
        component_plan_failures.append(str(exc))
    else:
        if "asset_type 'component'" not in failure_text:
            component_plan_failures.append(f"expected component asset rejection, got output: {failure_text!r}")
    results.append(("asset-plan-rejects-component-crops", component_plan_failures))

    print("\nelement asset extraction benchmark")
    failed = 0
    for name, failures in results:
        if failures:
            failed += 1
            print(f"  x {name}")
            for failure in failures:
                print(f"    - {failure}")
        else:
            print(f"  ✓ {name}")

    if failed:
        print(f"\nFAIL - {len(results) - failed}/{len(results)} cases passed")
        raise SystemExit(1)
    print(f"\nPASS - {len(results)}/{len(results)} cases")


if __name__ == "__main__":
    main()
