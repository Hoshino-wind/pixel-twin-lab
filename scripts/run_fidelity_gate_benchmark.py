#!/usr/bin/env python3
"""Regression checks for fidelity gate asset/component boundaries."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent


def write_common(out_dir: Path, asset_type: str, asset_source: str = "asset-plan") -> None:
    (out_dir / "element-assets").mkdir(parents=True, exist_ok=True)
    (out_dir / "element-assets" / "asset-1.png").write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc````\x00\x00"
        b"\x00\x05\x00\x01\xa5\xf6E@\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    (out_dir / "pixel-diff-summary.json").write_text(
        json.dumps(
            [
                {"file": "reference-capture.png", "mismatch_pct": 0.0},
                {"file": "rebuilt-capture.png", "mismatch_pct": 0.0},
                {"file": "exact-capture.png", "mismatch_pct": 0.0},
            ],
            indent=2,
        ),
        encoding="utf-8",
    )
    (out_dir / "lab-config.json").write_text(json.dumps({"width": 120, "height": 80}, indent=2), encoding="utf-8")
    asset = {
        "id": "asset-1",
        "region": "panel-a",
        "asset_type": asset_type,
        "source": asset_source,
        "bounds": {"x": 24, "y": 28, "width": 17, "height": 17},
        "path": "element-assets/asset-1.png",
    }
    (out_dir / "element-assets.json").write_text(json.dumps({"assets": [asset]}, indent=2), encoding="utf-8")
    (out_dir / "element-manifest.json").write_text(
        json.dumps(
            {
                "regions": [
                    {
                        "name": "panel-a",
                        "bounds": {"x": 12, "y": 12, "width": 96, "height": 56},
                        "elements": [
                            {
                                "id": "asset-1",
                                "type": "image" if asset_type == "component" else asset_type,
                                "bounds": {"x": 24, "y": 28, "width": 17, "height": 17},
                                "asset_id": "asset-1",
                                "asset_type": asset_type,
                                "asset_source": asset_source,
                                "asset_path": "element-assets/asset-1.png",
                                "requires_asset": True,
                            }
                        ],
                    }
                ]
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (out_dir / "element-verification.json").write_text(
        json.dumps({"summary": {"pass": True, "ok": 1, "total": 1, "missing": 0, "failed": 0, "unlabeled": 0}}, indent=2),
        encoding="utf-8",
    )
    (out_dir / "index.html").write_text(
        """
<!doctype html>
<html>
  <body>
    <section class="pt-region" aria-label="panel-a">
      <span data-element="asset-1" data-element-asset-id="asset-1">
        <img src="./element-assets/asset-1.png" alt="" />
      </span>
    </section>
  </body>
</html>
""".strip(),
        encoding="utf-8",
    )


def run_gate(out_dir: Path) -> dict[str, Any]:
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "fidelity_gate.py"), "--out-dir", str(out_dir), "--target-match", "98"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(f"fidelity_gate.py failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    return json.loads((out_dir / "fidelity-gate.json").read_text(encoding="utf-8"))


def make_case(name: str, asset_type: str, asset_source: str = "asset-plan") -> Path:
    out_dir = Path(tempfile.mkdtemp(prefix=f"ptl-fidelity-{name}.", dir="/tmp"))
    write_common(out_dir, asset_type, asset_source)
    return out_dir


def main() -> None:
    results: list[tuple[str, list[str]]] = []

    icon_report = run_gate(make_case("icon-asset", "icon"))
    icon_failures: list[str] = []
    if not icon_report["gates"]["componentized_islands_98"]["pass"]:
        icon_failures.append(icon_report["gates"]["componentized_islands_98"]["reason"])
    if not icon_report["element_asset_evidence"]["policy_pass"]:
        icon_failures.append("icon asset should satisfy element asset policy")
    results.append(("icon-asset-allowed", icon_failures))

    component_report = run_gate(make_case("component-asset", "component", "component-fragment"))
    component_failures: list[str] = []
    if component_report["gates"]["componentized_islands_98"]["pass"]:
        component_failures.append("componentized_islands_98 passed with a component asset")
    if component_report["element_asset_evidence"]["policy_pass"]:
        component_failures.append("component asset should fail element asset policy")
    results.append(("component-asset-rejected", component_failures))

    print("\nfidelity gate benchmark")
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
