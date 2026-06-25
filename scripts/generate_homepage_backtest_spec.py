#!/usr/bin/env python3
"""Write full-homepage region and asset specs for the current screenshot backtests.

The generated specs are still component-faithful: large content sections become named
regions for measurement/component rebuild, while only resource islands such as photos,
maps, avatars, logos, and chart backings are listed in asset-plan.json.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def region(name: str, x: int, y: int, width: int, height: int) -> dict[str, Any]:
    return {"name": name, "x": x, "y": y, "width": width, "height": height}


def asset(asset_id: str, region_name: str, asset_type: str, x: int, y: int, width: int, height: int, reason: str) -> dict[str, Any]:
    return {
        "id": asset_id,
        "region": region_name,
        "asset_type": asset_type,
        "bounds": {"x": x, "y": y, "width": width, "height": height},
        "reason": reason,
    }


NEBULA_REGIONS = [
    region("sidebar", 0, 0, 232, 989),
    region("topbar", 252, 14, 1322, 47),
    region("kpi-uptime", 252, 72, 252, 119),
    region("kpi-incidents", 514, 72, 251, 119),
    region("kpi-latency", 776, 72, 250, 119),
    region("kpi-spend", 1037, 72, 266, 119),
    region("kpi-budget", 1313, 72, 261, 119),
    region("system-performance-chart", 252, 203, 513, 278),
    region("incident-timeline", 775, 203, 313, 278),
    region("dependency-map", 1098, 203, 476, 278),
    region("regional-health", 252, 492, 467, 252),
    region("deployment-queue", 728, 492, 395, 252),
    region("ai-remediation", 1135, 492, 439, 252),
    region("audit-log", 252, 755, 1322, 223),
]

NEBULA_ASSETS = [
    asset("nebula-logo", "sidebar", "logo", 19, 18, 27, 27, "brand logo resource"),
    asset("system-performance-chart-backing", "system-performance-chart", "chart", 252, 203, 513, 278, "chart panel routed to library rendering"),
]

NEBULA_ROUTING = [
    {
        "name": "system-performance-chart",
        "content_type": "chart",
        "track": "approximation",
        "handling": "library",
        "library": "echarts",
        "eval": "tolerant+structural",
    }
]

LUMA_REGIONS = [
    region("header", 28, 17, 794, 53),
    region("intro-actions", 36, 102, 786, 101),
    region("trip-hero", 28, 223, 795, 325),
    region("tabs", 27, 572, 795, 49),
    region("itinerary", 27, 621, 795, 390),
    region("recommendations", 28, 1038, 795, 251),
    region("insights-row", 29, 1304, 794, 191),
    region("packing-checklist", 30, 1514, 327, 220),
    region("ai-assistant", 380, 1514, 443, 220),
    region("bottom-nav", 0, 1733, 864, 88),
]

LUMA_ASSETS = [
    asset("luma-logo-mark", "header", "logo", 28, 25, 31, 39, "brand mark resource"),
    asset("header-avatar", "header", "avatar", 769, 18, 49, 49, "profile avatar resource"),
    asset("trip-hero-photo", "trip-hero", "photo", 28, 223, 532, 325, "Rome photo resource inside hero card"),
    asset("trip-hero-map", "trip-hero", "map", 559, 223, 264, 325, "map tile resource inside hero card"),
    asset("trip-avatar-1", "trip-hero", "avatar", 55, 434, 44, 44, "collaborator avatar resource"),
    asset("trip-avatar-2", "trip-hero", "avatar", 100, 434, 44, 44, "collaborator avatar resource"),
    asset("trip-avatar-3", "trip-hero", "avatar", 145, 434, 44, 44, "collaborator avatar resource"),
    asset("recommendation-photo-1", "recommendations", "photo", 28, 1073, 199, 121, "recommendation card photo resource"),
    asset("recommendation-photo-2", "recommendations", "photo", 241, 1073, 200, 121, "recommendation card photo resource"),
    asset("recommendation-photo-3", "recommendations", "photo", 455, 1073, 199, 121, "recommendation card photo resource"),
    asset("recommendation-photo-4", "recommendations", "photo", 668, 1073, 155, 121, "recommendation card photo resource"),
    asset("packing-suitcase", "packing-checklist", "image", 270, 1595, 70, 92, "packing illustration resource"),
    asset("assistant-robot", "ai-assistant", "image", 718, 1548, 82, 72, "assistant illustration resource"),
]

LUMA_ROUTING: list[dict[str, Any]] = []


SPECS = {
    "nebula": {"regions": NEBULA_REGIONS, "assets": NEBULA_ASSETS, "routing": NEBULA_ROUTING},
    "luma": {"regions": LUMA_REGIONS, "assets": LUMA_ASSETS, "routing": LUMA_ROUTING},
}


def infer_target(out_dir: Path) -> str:
    config_path = out_dir / "lab-config.json"
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            config = {}
        source = str(config.get("source") or "").lower()
        if "nebula" in source:
            return "nebula"
        if "luma" in source:
            return "luma"
        width = int(config.get("width") or 0)
        height = int(config.get("height") or 0)
        if (width, height) == (1589, 989):
            return "nebula"
        if (width, height) == (864, 1821):
            return "luma"
    name = out_dir.name.lower()
    if "nebula" in name:
        return "nebula"
    if "luma" in name:
        return "luma"
    raise SystemExit("Could not infer target. Pass --target nebula or --target luma.")


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True, help="Pixel Twin Lab output directory")
    parser.add_argument("--target", choices=sorted(SPECS), help="Screenshot family; inferred from lab-config/source when omitted")
    parser.add_argument("--regions-name", default="regions-manifest.json")
    parser.add_argument("--asset-plan-name", default="asset-plan.json")
    parser.add_argument("--routing-name", default="routing-manifest.json")
    args = parser.parse_args()

    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    target = args.target or infer_target(out_dir)
    spec = SPECS[target]

    regions_doc = {"regions": spec["regions"]}
    write_json(out_dir / args.regions_name, regions_doc)
    write_json(out_dir / "regions.json", regions_doc)
    write_json(out_dir / args.asset_plan_name, {"assets": spec["assets"]})
    write_json(out_dir / args.routing_name, {"regions": spec["routing"]})

    page_area = 1589 * 989 if target == "nebula" else 864 * 1821
    region_area = sum(item["width"] * item["height"] for item in spec["regions"])
    asset_area = sum(item["bounds"]["width"] * item["bounds"]["height"] for item in spec["assets"])
    print(
        json.dumps(
            {
                "out_dir": str(out_dir),
                "target": target,
                "regions": len(spec["regions"]),
                "region_area_pct_raw": round(region_area / page_area * 100, 2),
                "assets": len(spec["assets"]),
                "asset_area_pct_raw": round(asset_area / page_area * 100, 2),
                "regions_manifest": str(out_dir / args.regions_name),
                "asset_plan": str(out_dir / args.asset_plan_name),
                "routing_manifest": str(out_dir / args.routing_name),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
