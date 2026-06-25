#!/usr/bin/env python3
"""Run a dependency-light smoke test for the Pixel Twin Lab core workflow."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]


def run(*args: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(args, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        print(result.stdout)
        print(result.stderr, file=sys.stderr)
        raise SystemExit(f"Command failed: {' '.join(args)}")
    return result


def write_reference(path: Path) -> None:
    image = Image.new("RGB", (200, 100), "#f5f5f5")
    draw = ImageDraw.Draw(image)
    draw.rectangle((10, 10, 90, 80), fill="#ffffff", outline="#111111")
    draw.text((20, 35), "Hello", fill="#111111")
    draw.ellipse((62, 20, 82, 40), fill="#ef4444")
    draw.rectangle((110, 20, 185, 70), fill="#2563eb")
    draw.text((125, 42), "Go", fill="#ffffff")
    image.save(path)


def label_all_plain_dom(path: Path) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    for entry in data["slices"]:
        entry["content_type"] = "plain-dom"
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def assert_gate(path: Path, gate: str, expected: bool) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    actual = bool(data["gates"][gate]["pass"])
    if actual != expected:
        raise AssertionError(f"{gate} expected {expected}, got {actual}: {data['gates'][gate]['reason']}")


def assert_asset_manifest(path: Path) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    assets = data.get("assets") or []
    if not assets:
        raise AssertionError("Expected extract_element_assets.py to find at least one element asset")


def assert_element_assets_merged(path: Path) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    elements = [element for region in data.get("regions", []) for element in region.get("elements", [])]
    if not any(element.get("requires_asset") and element.get("asset_path") for element in elements):
        raise AssertionError("Expected init_element_manifest.py to merge element-assets.json into asset elements")


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="pixel-twin-lab-smoke.") as tmp_name:
        tmp = Path(tmp_name)
        reference = tmp / "reference.png"
        manifest = tmp / "manifest.json"
        lab = tmp / "lab"
        write_reference(reference)
        manifest.write_text(
            json.dumps(
                {
                    "regions": [
                        {"name": "card", "x": 10, "y": 10, "width": 80, "height": 70},
                        {"name": "cta", "x": 110, "y": 20, "width": 75, "height": 50},
                    ]
                }
            ),
            encoding="utf-8",
        )

        run(sys.executable, "scripts/prepare_lab.py", "--reference", str(reference), "--out-dir", str(lab), "--manifest", str(manifest))
        run(sys.executable, "scripts/classify_slices.py", "--out-dir", str(lab), "--mode", "init")
        label_all_plain_dom(lab / "slice-classification.json")
        run(sys.executable, "scripts/classify_slices.py", "--out-dir", str(lab), "--mode", "apply")

        for name in ("reference-capture.png", "rebuilt-capture.png", "exact-capture.png"):
            shutil.copy2(lab / "assets" / "reference.png", lab / name)
        run(sys.executable, "scripts/pixel_diff.py", "--reference", str(lab / "assets" / "reference.png"), "--out-dir", str(lab), "--tolerance", "8")
        run(sys.executable, "scripts/fidelity_gate.py", "--out-dir", str(lab), "--target-match", "98")
        assert_gate(lab / "fidelity-gate.json", "component_only_98", True)
        assert_gate(lab / "fidelity-gate.json", "componentized_islands_98", True)
        run(sys.executable, "scripts/measure_primitives.py", "--out-dir", str(lab), "--regions", "card,cta")
        run(sys.executable, "scripts/extract_element_assets.py", "--out-dir", str(lab))
        assert_asset_manifest(lab / "element-assets.json")
        run(sys.executable, "scripts/init_element_manifest.py", "--out-dir", str(lab))
        assert_element_assets_merged(lab / "element-manifest.json")
        run(sys.executable, "scripts/materialize_element_manifest_lab.py", "--out-dir", str(lab))
        run(sys.executable, "scripts/fidelity_gate.py", "--out-dir", str(lab), "--target-match", "98")
        assert_gate(lab / "fidelity-gate.json", "element_asset_contract", True)
        assert_gate(lab / "fidelity-gate.json", "componentized_islands_98", False)

        blueprint = {
            "version": 1,
            "source": {"reference": "assets/reference.png", "width": 200, "height": 100, "background": "#f5f5f5"},
            "layout": {"regions": [{"name": "card", "bounds": {"x": 10, "y": 10, "width": 80, "height": 70}, "role": "card", "track": "component"}]},
            "components": [
                {
                    "id": "card-shell",
                    "region": "card",
                    "category": "data-display",
                    "type": "card",
                    "bounds": {"x": 10, "y": 10, "width": 80, "height": 70},
                    "content": "Hello",
                    "style": {
                        "source": "measured",
                        "expected": {
                            "background_color": "#ffffff",
                            "border_radius_px": 0,
                        },
                    },
                    "elements": [
                        {
                            "id": "card-shell-title",
                            "type": "text",
                            "bounds": {"x": 18, "y": 20, "width": 48, "height": 16},
                            "content": "Hello",
                            "maps_to": "Card:title",
                        }
                    ],
                }
            ],
            "tokens": {"colors": [{"name": "surface", "value": "#ffffff", "sampled_at": [20, 20]}], "typography": [], "spacing": []},
            "data": [],
            "interactions": [],
            "implementation": {"framework": "react", "styling": "css", "final_dir": "src/pixel-test", "plan": [{"component_id": "card-shell", "action": "create", "order": 1}]},
        }
        (lab / "ui-blueprint.json").write_text(json.dumps(blueprint, indent=2), encoding="utf-8")
        run(sys.executable, "scripts/validate_blueprint.py", "--out-dir", str(lab))

        (lab / "index.html").write_text(
            """<!doctype html><html><body><section class="stage"><img class="reference-layer" src="./assets/reference.png" alt="Reference UI" /><div class="rebuilt-layer"><img src="./assets/reference.png" alt="" /></div></section></body></html>""",
            encoding="utf-8",
        )
        run(sys.executable, "scripts/fidelity_gate.py", "--out-dir", str(lab), "--target-match", "98")
        assert_gate(lab / "fidelity-gate.json", "component_only_98", False)
        assert_gate(lab / "fidelity-gate.json", "componentized_islands_98", False)

        print(json.dumps({"status": "ok", "lab": str(lab)}, indent=2))


if __name__ == "__main__":
    main()
