from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "visual_compare.py"
SPEC = importlib.util.spec_from_file_location("visual_compare", MODULE_PATH)
assert SPEC and SPEC.loader
VISUAL_COMPARE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VISUAL_COMPARE)


class VisualCompareTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="visual-compare-test-")
        self.directory = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def save(self, name: str, image: Image.Image) -> Path:
        path = self.directory / name
        image.save(path)
        return path

    def analyze(self, reference: Image.Image, actual: Image.Image, **options):
        reference_path = self.save("reference.png", reference)
        actual_path = self.save("actual.png", actual)
        return VISUAL_COMPARE.analyze_images(reference_path, actual_path, **options)

    def analyze_dynamic(
        self,
        reference: Image.Image,
        actual: Image.Image,
        *,
        temporal: Image.Image | None = None,
    ):
        reference_path = self.save("dynamic-reference.png", reference)
        actual_path = self.save("dynamic-actual.png", actual)
        temporal_path = self.save("dynamic-temporal.png", temporal) if temporal else None
        return VISUAL_COMPARE.analyze_images(
            reference_path,
            actual_path,
            dynamic_regions=[
                {
                    "id": "canvas#trend",
                    "source": "auto",
                    "kind": "canvas",
                    "selector": "canvas#trend",
                    "bounds": [20, 20, 80, 40],
                }
            ],
            temporal_path=temporal_path,
        )

    @staticmethod
    def canvas() -> Image.Image:
        return Image.new("RGB", (120, 80), "white")

    def test_identical_images_preserve_legacy_metrics(self) -> None:
        reference = self.canvas()
        ImageDraw.Draw(reference).rectangle((20, 15, 70, 55), fill="#c52323")

        report, diff = self.analyze(reference, reference.copy())

        self.assertEqual(report["strict_match_pct"], 100.0)
        self.assertEqual(report["tolerant_match_pct"], 100.0)
        self.assertEqual(report["mae"], 0.0)
        self.assertEqual(report["max_delta"], 0)
        self.assertEqual(report["hotspots"], [])
        self.assertEqual(diff.getbbox(), None)

    def test_color_change_preserves_layout_and_edges(self) -> None:
        reference = self.canvas()
        actual = self.canvas()
        ImageDraw.Draw(reference).rectangle((20, 15, 70, 55), fill="#c52323")
        ImageDraw.Draw(actual).rectangle((20, 15, 70, 55), fill="#234fc5")

        report, diff = self.analyze(reference, actual)

        self.assertTrue(report["pass"])
        self.assertEqual(report["size"], [120, 80])
        self.assertEqual(report["tolerance"], 8)
        self.assertLess(report["color_similarity_pct"], 95)
        self.assertGreater(report["layout_match_pct"], 99)
        self.assertGreater(report["edge_match_pct"], 99)
        self.assertLess(report["strict_match_pct"], 100)
        self.assertIsNotNone(diff)

    def test_color_similarity_is_continuous_across_old_bin_boundary(self) -> None:
        reference = Image.new("RGB", (40, 40), "#0f0f0f")
        adjacent = Image.new("RGB", (40, 40), "#101010")
        baseline = Image.new("RGB", (40, 40), "#000000")

        report, _ = self.analyze(reference, adjacent)
        baseline_report, _ = self.analyze(reference, baseline)
        comparison = VISUAL_COMPARE.compare_reports(baseline_report, report)

        self.assertEqual(report["tolerant_match_pct"], 100.0)
        self.assertEqual(report["mae"], 1.0)
        self.assertGreater(report["color_similarity_pct"], 99)
        self.assertEqual(comparison["status"], "improved")
        self.assertGreater(comparison["overall"]["color_gain_pct"], 0)

    def test_shifted_geometry_reduces_layout_and_structure_scores(self) -> None:
        reference = self.canvas()
        actual = self.canvas()
        ImageDraw.Draw(reference).rectangle((10, 15, 45, 55), fill="black")
        ImageDraw.Draw(actual).rectangle((55, 15, 90, 55), fill="black")

        report, _ = self.analyze(reference, actual)

        self.assertLess(report["layout_match_pct"], 95)
        self.assertLess(report["structure_similarity_pct"], 95)
        self.assertLess(report["edge_match_pct"], 95)
        self.assertGreaterEqual(len(report["hotspots"]), 1)
        self.assertEqual(report["_grid"]["rows"], 4)
        self.assertEqual(report["_grid"]["columns"], 4)

    def test_hotspots_are_merged_ranked_and_limited(self) -> None:
        reference = self.canvas()
        actual = self.canvas()
        draw = ImageDraw.Draw(actual)
        draw.rectangle((5, 5, 24, 24), fill="black")
        draw.rectangle((50, 5, 64, 19), fill="black")
        draw.rectangle((95, 55, 104, 64), fill="black")

        report, _ = self.analyze(reference, actual, max_hotspots=2)

        self.assertEqual(len(report["hotspots"]), 2)
        self.assertEqual([item["rank"] for item in report["hotspots"]], [1, 2])
        self.assertGreaterEqual(report["hotspots"][0]["score"], report["hotspots"][1]["score"])
        self.assertGreater(report["hotspots"][0]["changed_pixels"], 0)

    def test_actionable_hotspot_is_not_starved_by_larger_unowned_residuals(self) -> None:
        reference = Image.new("RGB", (300, 200), "white")
        actual = reference.copy()
        draw = ImageDraw.Draw(actual)
        draw.rectangle((10, 10, 59, 49), fill="black")
        draw.rectangle((100, 10, 149, 49), fill="black")
        draw.rectangle((190, 10, 239, 49), fill="black")
        ImageDraw.Draw(reference).rectangle((120, 130, 151, 153), fill="#2458d3")
        draw.rectangle((120, 130, 151, 153), fill="#d52b2b")
        dom_index = {
            "space": "capture-css-px",
            "truncated": False,
            "nodes": [
                {
                    "selector": "div.card",
                    "tag": "div",
                    "role": "surface",
                    "bounds": [120, 130, 32, 24],
                    "visible_bounds": [120, 130, 32, 24],
                    "depth": 3,
                    "unique": True,
                    "visual": True,
                    "computed": {
                        "display": "block",
                        "background-color": "rgb(213, 43, 43)",
                    },
                }
            ],
        }

        report, _ = self.analyze(
            reference,
            actual,
            max_hotspots=3,
            dom_index=dom_index,
        )

        actionable = [
            hotspot
            for hotspot in report["hotspots"]
            if (hotspot.get("repair_hint") or {}).get("kind") == "color"
        ]
        self.assertEqual(len(report["hotspots"]), 3)
        self.assertEqual(len(actionable), 1)
        self.assertEqual(actionable[0]["dom"]["selector"], "div.card")
        self.assertEqual(actionable[0]["repair_hint"]["target_color"], "#2458d3")

        severity_only, _ = self.analyze(
            reference,
            actual,
            max_hotspots=3,
        )
        self.assertEqual(len(severity_only["hotspots"]), 3)
        self.assertTrue(
            all(hotspot["bounds"][1] < 100 for hotspot in severity_only["hotspots"])
        )
        self.assertTrue(
            all("repair_hint" not in hotspot for hotspot in severity_only["hotspots"])
        )

    def test_compare_reports_finds_net_improvement_and_local_regression(self) -> None:
        reference = self.canvas()
        baseline = self.canvas()
        candidate = self.canvas()
        ImageDraw.Draw(reference).rectangle((5, 5, 44, 34), fill="black")
        ImageDraw.Draw(candidate).rectangle((5, 5, 44, 34), fill="black")
        ImageDraw.Draw(candidate).rectangle((105, 65, 114, 74), fill="black")

        baseline_report, _ = self.analyze(reference, baseline)
        candidate_report, _ = self.analyze(reference, candidate)
        comparison = VISUAL_COMPARE.compare_reports(baseline_report, candidate_report)

        self.assertTrue(comparison["pass"])
        self.assertEqual(comparison["status"], "improved")
        self.assertGreater(comparison["overall"]["tolerant_match_gain_pct"], 0)
        self.assertGreater(comparison["summary"]["improved_regions"], 0)
        self.assertGreater(comparison["summary"]["regressed_regions"], 0)
        self.assertEqual(comparison["regressed_regions"][0]["id"], "r3c3")

    def test_local_tolerance_gain_cannot_hide_severe_mae_regression(self) -> None:
        reference = Image.new("RGB", (100, 100), "black")
        baseline = reference.copy()
        candidate = reference.copy()
        points = [(x, y) for y in range(10) for x in range(10)]
        for point in points:
            baseline.putpixel(point, (10, 10, 10))
        for point in points[1:]:
            candidate.putpixel(point, (255, 255, 255))

        baseline_report, _ = self.analyze(reference, baseline)
        candidate_report, _ = self.analyze(reference, candidate)
        comparison = VISUAL_COMPARE.compare_reports(baseline_report, candidate_report)

        region = next(item for item in comparison["regions"] if item["id"] == "r0c0")
        self.assertEqual(region["status"], "mixed")
        self.assertLess(region["mae_reduction"], 0)
        self.assertNotIn(region, comparison["improved_regions"])
        self.assertIn(region, comparison["regressed_regions"])

    def test_dynamic_region_is_removed_from_static_hotspots_but_structurally_measured(self) -> None:
        reference = self.canvas()
        actual = self.canvas()
        ImageDraw.Draw(reference).line((25, 50, 55, 25, 95, 45), fill="#1957d2", width=3)
        ImageDraw.Draw(actual).line((25, 45, 55, 50, 95, 25), fill="#1957d2", width=3)

        report, diff = self.analyze_dynamic(reference, actual)

        self.assertEqual(report["tolerant_match_pct"], 100.0)
        self.assertEqual(report["hotspots"], [])
        self.assertIsNone(diff.getbbox())
        self.assertLess(report["static_coverage_pct"], 100)
        self.assertEqual(report["dynamic_region_count"], 1)
        dynamic = report["dynamic_regions"][0]
        self.assertLess(dynamic["fidelity"]["edge_distribution_pct"], 100)
        self.assertEqual(dynamic["temporal"]["state"], "unavailable")

    def test_narrow_dynamic_boundary_cannot_leak_into_static_layers(self) -> None:
        reference = Image.new("RGB", (100, 100), "white")
        actual = reference.copy()
        ImageDraw.Draw(actual).rectangle((49, 0, 50, 99), fill="black")

        report, _ = self.analyze(
            reference,
            actual,
            dynamic_regions=[
                {
                    "id": "canvas#stripe",
                    "kind": "canvas",
                    "bounds": [49, 0, 2, 100],
                }
            ],
        )

        for metric in (
            "tolerant_match_pct",
            "layout_match_pct",
            "structure_similarity_pct",
            "edge_match_pct",
            "color_similarity_pct",
        ):
            self.assertEqual(report[metric], 100.0, metric)

    def test_dynamic_delta_reports_structure_improvement_separately(self) -> None:
        reference = self.canvas()
        blank = self.canvas()
        ImageDraw.Draw(reference).line((25, 50, 55, 25, 95, 45), fill="#1957d2", width=3)

        baseline_report, _ = self.analyze_dynamic(reference, blank)
        candidate_report, _ = self.analyze_dynamic(reference, reference.copy())
        comparison = VISUAL_COMPARE.compare_reports(baseline_report, candidate_report)

        self.assertEqual(comparison["status"], "unchanged")
        self.assertEqual(comparison["dynamic"]["summary"]["improved"], 1)
        self.assertEqual(comparison["dynamic"]["regions"][0]["status"], "improved")

    def test_dynamic_temporal_drift_makes_delta_indeterminate(self) -> None:
        reference = self.canvas()
        ImageDraw.Draw(reference).line((25, 50, 55, 25, 95, 45), fill="#1957d2", width=3)
        temporal = reference.copy()
        ImageDraw.Draw(temporal).rectangle((20, 20, 99, 59), fill="black")

        baseline_report, _ = self.analyze_dynamic(reference, reference.copy())
        candidate_report, _ = self.analyze_dynamic(
            reference, reference.copy(), temporal=temporal
        )
        comparison = VISUAL_COMPARE.compare_reports(baseline_report, candidate_report)

        self.assertEqual(candidate_report["dynamic_regions"][0]["temporal"]["state"], "active")
        self.assertEqual(comparison["dynamic"]["regions"][0]["status"], "indeterminate")

    def test_dynamic_temporal_activity_uses_strongest_rgb_channel(self) -> None:
        reference = self.canvas()
        ImageDraw.Draw(reference).rectangle((20, 20, 99, 59), fill="black")
        temporal = reference.copy()
        ImageDraw.Draw(temporal).rectangle((20, 20, 99, 59), fill=(0, 0, 50))

        report, _ = self.analyze_dynamic(
            reference,
            reference.copy(),
            temporal=temporal,
        )

        drift = report["dynamic_regions"][0]["temporal"]
        self.assertEqual(drift["state"], "active")
        self.assertEqual(drift["changed_pct"], 100.0)

    def test_dynamic_indeterminate_vector_cannot_be_hidden_by_improvement(self) -> None:
        base = {
            "id": "canvas#trend",
            "kind": "canvas",
            "bounds": [20, 20, 80, 40],
            "fidelity": {
                "coarse_structure_pct": 50,
                "edge_distribution_pct": 50,
                "color_similarity_pct": 50,
            },
            "temporal": {
                "structure_drift_pct": 6,
                "edge_drift_pct": 0,
                "color_drift_pct": 0,
            },
        }
        candidate = {
            **base,
            "fidelity": {
                "coarse_structure_pct": 60,
                "edge_distribution_pct": 60,
                "color_similarity_pct": 50,
            },
            "temporal": {
                "structure_drift_pct": 0,
                "edge_drift_pct": 0,
                "color_drift_pct": 0,
            },
        }

        comparison = VISUAL_COMPARE.compare_dynamic_region_reports([base], [candidate])

        self.assertEqual(comparison["regions"][0]["status"], "indeterminate")

    def test_changed_dynamic_bounds_make_static_delta_indeterminate(self) -> None:
        reference = self.canvas()
        actual = self.canvas()
        ImageDraw.Draw(actual).rectangle((20, 20, 39, 39), fill="black")
        baseline, _ = self.analyze(
            reference,
            actual,
            dynamic_regions=[
                {"id": "canvas#trend", "kind": "canvas", "bounds": [80, 20, 20, 20]}
            ],
        )
        candidate, _ = self.analyze(
            reference,
            actual,
            dynamic_regions=[
                {"id": "canvas#trend", "kind": "canvas", "bounds": [20, 20, 20, 20]}
            ],
        )

        comparison = VISUAL_COMPARE.compare_reports(baseline, candidate)

        self.assertTrue(comparison["static_basis_changed"])
        self.assertEqual(comparison["status"], "indeterminate")
        self.assertIsNone(comparison["overall"]["tolerant_match_gain_pct"])
        geometry = comparison["dynamic"]["regions"][0]["geometry"]
        self.assertEqual(geometry["dx"], -60.0)

    def test_dynamic_color_error_cannot_hide_behind_matching_structure(self) -> None:
        reference = self.canvas()
        actual = self.canvas()
        ImageDraw.Draw(reference).rectangle((30, 28, 90, 50), fill="#d52b2b")
        ImageDraw.Draw(actual).rectangle((30, 28, 90, 50), fill="#2458d3")

        report, _ = self.analyze_dynamic(reference, actual)
        fidelity = report["dynamic_regions"][0]["fidelity"]

        self.assertGreater(fidelity["coarse_structure_pct"], 99)
        self.assertGreater(fidelity["edge_distribution_pct"], 99)
        self.assertLess(fidelity["color_similarity_pct"], 95)

    def test_size_mismatch_is_an_explicit_non_comparable_result(self) -> None:
        reference = Image.new("RGB", (20, 20), "white")
        actual = Image.new("RGB", (21, 20), "white")

        report, diff = self.analyze(reference, actual)

        self.assertFalse(report["pass"])
        self.assertEqual(report["reason"], "size mismatch")
        self.assertEqual(report["reference_size"], [20, 20])
        self.assertEqual(report["actual_size"], [21, 20])
        self.assertIsNone(diff)


if __name__ == "__main__":
    unittest.main()
