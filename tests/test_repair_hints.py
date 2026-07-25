from __future__ import annotations

import importlib.util
import tempfile
import tracemalloc
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "repair_hints.py"
SPEC = importlib.util.spec_from_file_location("repair_hints", MODULE_PATH)
assert SPEC and SPEC.loader
REPAIR_HINTS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REPAIR_HINTS)


class RepairHintTests(unittest.TestCase):
    @staticmethod
    def canvas() -> Image.Image:
        return Image.new("RGB", (180, 120), "#f4f6fb")

    @staticmethod
    def draw_card(image: Image.Image, bounds: tuple[int, int, int, int], color: str = "#244a9b") -> None:
        x, y, width, height = bounds
        draw = ImageDraw.Draw(image)
        draw.rectangle((x, y, x + width - 1, y + height - 1), fill="#ffffff", outline=color, width=2)
        draw.line((x + 5, y + height - 7, x + width // 2, y + 6, x + width - 6, y + 12), fill=color, width=2)
        draw.rectangle((x + 5, y + 5, x + 10, y + 9), fill="#d95555")

    @staticmethod
    def dom_index(bounds: tuple[int, int, int, int], *, unique: bool = True) -> dict:
        return {
            "space": "capture-css-px",
            "truncated": False,
            "nodes": [
                {
                    "selector": "main > div.card",
                    "tag": "div",
                    "role": "surface",
                    "bounds": list(bounds),
                    "visible_bounds": list(bounds),
                    "depth": 3,
                    "unique": unique,
                    "visual": True,
                    "computed": {
                        "display": "block",
                        "background-color": "rgb(255, 255, 255)",
                    },
                }
            ],
        }

    def solve(
        self,
        reference: Image.Image,
        actual: Image.Image,
        current: tuple[int, int, int, int],
        hotspot: list[int],
        **hotspot_values,
    ) -> dict:
        report = {"hotspots": [{"rank": 1, "bounds": hotspot, **hotspot_values}]}
        REPAIR_HINTS.attach_repair_hints(
            report,
            self.dom_index(current),
            reference,
            actual,
        )
        return report["hotspots"][0]["repair_hint"]

    def test_unique_translation_returns_target_delta(self) -> None:
        current = (78, 54, 42, 30)
        target = (64, 46, 42, 30)
        reference = self.canvas()
        actual = self.canvas()
        self.draw_card(reference, target)
        self.draw_card(actual, current)

        hint = self.solve(reference, actual, current, [64, 46, 56, 38])

        self.assertIn(hint["kind"], {"position", "position_size"})
        self.assertLessEqual(abs(hint["delta"]["x"] - (target[0] - current[0])), 2)
        self.assertLessEqual(abs(hint["delta"]["y"] - (target[1] - current[1])), 2)

    def test_unique_resize_returns_visual_size_delta(self) -> None:
        current = (64, 44, 42, 30)
        target = (64, 44, 50, 36)
        reference = self.canvas()
        actual = self.canvas()
        self.draw_card(reference, target)
        self.draw_card(actual, current)

        hint = self.solve(reference, actual, current, [64, 44, 50, 36])

        self.assertIn(hint["kind"], {"size", "position_size"})
        self.assertLessEqual(abs(hint["delta"]["width"] - 8), 3)
        self.assertLessEqual(abs(hint["delta"]["height"] - 6), 3)

    def test_same_geometry_color_change_does_not_claim_layout(self) -> None:
        current = (64, 44, 50, 36)
        reference = self.canvas()
        actual = self.canvas()
        ImageDraw.Draw(reference).rectangle((64, 44, 113, 79), fill="#2458d3")
        ImageDraw.Draw(actual).rectangle((64, 44, 113, 79), fill="#d52b2b")

        hint = self.solve(
            reference,
            actual,
            current,
            [64, 44, 50, 36],
            dominant_color_hint={"actual": "#d52b2b", "reference": "#2458d3"},
        )

        self.assertEqual(hint["kind"], "color")
        self.assertEqual(hint["target_color"], "#2458d3")
        self.assertNotIn("delta", hint)

    def test_non_unique_dom_target_is_uncertain(self) -> None:
        image = self.canvas()
        self.draw_card(image, (64, 44, 50, 36))
        report = {"hotspots": [{"rank": 1, "bounds": [64, 44, 50, 36]}]}

        REPAIR_HINTS.attach_repair_hints(
            report,
            self.dom_index((64, 44, 50, 36), unique=False),
            image,
            image,
        )

        hint = report["hotspots"][0]["repair_hint"]
        self.assertEqual(hint["kind"], "uncertain")
        self.assertIn("not unique", hint["reason"])

    def test_medium_parent_dom_mapping_is_not_actionable(self) -> None:
        reference = self.canvas()
        actual = self.canvas()
        ImageDraw.Draw(reference).rectangle((30, 30, 59, 49), fill="#2458d3")
        ImageDraw.Draw(actual).rectangle((30, 30, 59, 49), fill="#d52b2b")
        report = {"hotspots": [{"rank": 1, "bounds": [30, 30, 30, 20]}]}
        dom_index = self.dom_index((20, 20, 140, 80))

        REPAIR_HINTS.attach_repair_hints(report, dom_index, reference, actual)

        hotspot = report["hotspots"][0]
        self.assertEqual(hotspot["dom"]["confidence"], "medium")
        self.assertEqual(hotspot["repair_hint"]["kind"], "uncertain")
        self.assertIn("ownership", hotspot["repair_hint"]["reason"])

    def test_repeated_visual_targets_are_not_presented_as_a_repair(self) -> None:
        current = (78, 46, 42, 30)
        reference = self.canvas()
        actual = self.canvas()
        self.draw_card(reference, (43, 46, 42, 30))
        self.draw_card(reference, (113, 46, 42, 30))
        self.draw_card(actual, current)

        hint = self.solve(reference, actual, current, [43, 46, 112, 30])

        self.assertEqual(hint["kind"], "uncertain")

    def test_missing_reference_content_is_uncertain(self) -> None:
        current = (70, 46, 42, 30)
        reference = self.canvas()
        actual = self.canvas()
        self.draw_card(actual, current)

        hint = self.solve(reference, actual, current, [70, 46, 42, 30])

        self.assertEqual(hint["kind"], "uncertain")

    def test_dynamic_overlap_is_never_a_static_repair(self) -> None:
        current = (64, 44, 50, 36)
        image = self.canvas()
        self.draw_card(image, current)
        report = {"hotspots": [{"rank": 1, "bounds": [64, 44, 50, 36]}]}

        REPAIR_HINTS.attach_repair_hints(
            report,
            self.dom_index(current),
            image,
            image,
            dynamic_regions=[{"bounds": [64, 44, 50, 36]}],
        )

        hint = report["hotspots"][0]["repair_hint"]
        self.assertEqual(hint["kind"], "uncertain")
        self.assertIn("dynamic", hint["reason"])

    def test_dom_overlap_with_dynamic_surface_is_also_uncertain(self) -> None:
        current = (64, 44, 50, 36)
        image = self.canvas()
        self.draw_card(image, current)
        report = {"hotspots": [{"rank": 1, "bounds": [64, 44, 38, 36]}]}

        REPAIR_HINTS.attach_repair_hints(
            report,
            self.dom_index(current),
            image,
            image,
            dynamic_regions=[{"bounds": [103, 44, 11, 36]}],
        )

        hint = report["hotspots"][0]["repair_hint"]
        self.assertEqual(hint["kind"], "uncertain")
        self.assertIn("dynamic", hint["reason"])

    def test_source_candidates_are_bounded_and_never_include_source_text(self) -> None:
        with tempfile.TemporaryDirectory(prefix="repair-source-test-") as temporary:
            root = Path(temporary)
            component = root / "Card.tsx"
            stylesheet = root / "card.css"
            component.write_text(
                'export const Card = () => <div className="card">SECRET_TEXT</div>;\n',
                encoding="utf-8",
            )
            stylesheet.write_text(
                ".card { color: #123456; padding: 8px; } /* SECRET_STYLE */\n",
                encoding="utf-8",
            )
            report = {
                "hotspots": [
                    {
                        "dom": {
                            "selector": "main > div.card",
                            "css_levers": ["color", "font-size", "line-height"],
                        },
                        "repair_hint": {"kind": "color", "confidence": "high"},
                    }
                ]
            }

            REPAIR_HINTS.attach_source_candidates(
                report,
                [("src/Card.tsx", component), ("src/card.css", stylesheet)],
            )

            candidates = report["hotspots"][0]["repair_hint"]["source_candidates"]
            self.assertEqual(len(candidates), 2)
            self.assertEqual(candidates[0]["path"], "src/card.css")
            self.assertEqual(candidates[0]["kind"], "css-selector")
            self.assertEqual(candidates[1]["path"], "src/Card.tsx")
            self.assertNotIn("SECRET", str(candidates))
            self.assertLessEqual(len(candidates), 2)

    def test_commented_source_candidate_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory(prefix="repair-comment-test-") as temporary:
            stylesheet = Path(temporary) / "card.css"
            stylesheet.write_text(
                "/* deprecated: .card { color: #123456; } */\n",
                encoding="utf-8",
            )
            report = {
                "hotspots": [
                    {
                        "dom": {"selector": "div.card", "css_levers": ["color"]},
                        "repair_hint": {"kind": "color", "confidence": "high"},
                    }
                ]
            }

            REPAIR_HINTS.attach_source_candidates(
                report,
                [("src/card.css", stylesheet)],
            )

            self.assertNotIn("source_candidates", report["hotspots"][0]["repair_hint"])
            self.assertEqual(report["source_mapping"]["mapped_hotspots"], 0)

    def test_uncertain_hint_does_not_scan_or_attach_source_candidates(self) -> None:
        report = {
            "hotspots": [
                {
                    "dom": {"selector": "div.card", "css_levers": ["color"]},
                    "repair_hint": {"kind": "uncertain", "confidence": "low"},
                }
            ]
        }

        REPAIR_HINTS.attach_source_candidates(
            report,
            [("src/missing.css", "/path/that/must/not/be/read")],
        )

        self.assertNotIn("source_candidates", report["hotspots"][0]["repair_hint"])
        self.assertNotIn("source_mapping", report)

    def test_text_color_requires_matching_foreground_masks(self) -> None:
        reference = Image.new("RGB", (120, 60), "white")
        actual = Image.new("RGB", (120, 60), "white")
        ImageDraw.Draw(reference).rectangle((80, 20, 94, 39), fill="#2458d3")
        ImageDraw.Draw(actual).rectangle((20, 20, 34, 39), fill="#d52b2b")

        target = REPAIR_HINTS._target_color_from_pixels(
            reference,
            actual,
            (10, 10, 100, 40),
            {"role": "text", "computed": {"color": "rgb(213, 43, 43)"}},
        )

        self.assertIsNone(target)

    def test_large_color_sampling_has_a_fixed_memory_budget(self) -> None:
        reference = Image.new("RGB", (1600, 1000), "#2458d3")
        actual = Image.new("RGB", (1600, 1000), "#d52b2b")
        tracemalloc.start()
        try:
            target = REPAIR_HINTS._target_color_from_pixels(
                reference,
                actual,
                (0, 0, 1600, 1000),
                {"role": "surface", "computed": {}},
            )
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()

        self.assertEqual(target, "#2458d3")
        self.assertLess(peak, 12 * 1024 * 1024)
        self.assertLessEqual(len(REPAIR_HINTS._refinement_values(2000, 400)), 20)
        self.assertLessEqual(len(REPAIR_HINTS._position_values(2000, 400)), 13)

    def test_candidate_pool_keeps_solver_and_public_output_bounded(self) -> None:
        image = Image.new("RGB", (640, 120), "white")
        hotspots = []
        nodes = []
        for index in range(10):
            bounds = [10 + index * 60, 40, 40, 24]
            hotspots.append(
                {
                    "rank": index + 1,
                    "bounds": bounds,
                    "score": 100 - index,
                    "changed_pixels": 100 - index,
                }
            )
            nodes.append(
                {
                    "selector": f"div.card-{index}",
                    "tag": "div",
                    "role": "surface",
                    "bounds": bounds,
                    "visible_bounds": bounds,
                    "depth": 3,
                    "unique": True,
                    "visual": True,
                    "computed": {
                        "display": "block",
                        "background-color": "rgb(213, 43, 43)",
                    },
                }
            )
        report = {"hotspots": hotspots}
        dom_index = {
            "space": "capture-css-px",
            "truncated": False,
            "nodes": nodes,
        }

        with mock.patch.object(
            REPAIR_HINTS,
            "_solve_repair",
            return_value={
                "kind": "color",
                "confidence": "high",
                "target_color": "#2458d3",
            },
        ) as solver:
            REPAIR_HINTS.attach_repair_hints(
                report,
                dom_index,
                image,
                image,
                output_limit=3,
            )

        self.assertEqual(solver.call_count, 6)
        self.assertEqual(len(report["hotspots"]), 3)
        self.assertEqual([item["rank"] for item in report["hotspots"]], [1, 2, 3])
        self.assertEqual(report["repair_summary"], {"actionable": 3, "uncertain": 0})
        self.assertEqual(report["dom_mapping"]["mapped_hotspots"], 3)

    def test_missing_selector_never_spends_solver_budget(self) -> None:
        image = self.canvas()
        report = {"hotspots": [{"rank": 1, "bounds": [64, 44, 50, 36]}]}
        dom_index = self.dom_index((64, 44, 50, 36))
        dom_index["nodes"][0]["selector"] = ""

        with mock.patch.object(REPAIR_HINTS, "_solve_repair") as solver:
            REPAIR_HINTS.attach_repair_hints(
                report,
                dom_index,
                image,
                image,
            )

        solver.assert_not_called()
        self.assertEqual(report["hotspots"][0]["repair_hint"]["kind"], "uncertain")
        self.assertIn("selector", report["hotspots"][0]["repair_hint"]["reason"])

    def test_high_confidence_actionable_hotspot_wins_remaining_slot(self) -> None:
        image = Image.new("RGB", (300, 100), "white")
        hotspots = []
        nodes = []
        for index in range(4):
            bounds = [10 + index * 65, 30, 40, 24]
            hotspots.append(
                {
                    "rank": index + 1,
                    "bounds": bounds,
                    "score": 100 - index,
                    "changed_pixels": 100 - index,
                }
            )
            nodes.append(
                {
                    "selector": f"div.card-{index}",
                    "tag": "div",
                    "role": "surface",
                    "bounds": bounds,
                    "visible_bounds": bounds,
                    "depth": 3,
                    "unique": True,
                    "visual": True,
                    "computed": {"display": "block"},
                }
            )
        report = {"hotspots": hotspots}
        dom_index = {
            "space": "capture-css-px",
            "truncated": False,
            "nodes": nodes,
        }
        hints = [
            {"kind": "uncertain", "confidence": "low", "reason": "ambiguous"},
            {"kind": "color", "confidence": "medium", "target_color": "#111111"},
            {"kind": "color", "confidence": "medium", "target_color": "#222222"},
            {"kind": "color", "confidence": "high", "target_color": "#333333"},
        ]

        with mock.patch.object(REPAIR_HINTS, "_solve_repair", side_effect=hints):
            REPAIR_HINTS.attach_repair_hints(
                report,
                dom_index,
                image,
                image,
                output_limit=3,
            )

        self.assertEqual(
            [item["bounds"] for item in report["hotspots"]],
            [hotspots[0]["bounds"], hotspots[3]["bounds"], hotspots[1]["bounds"]],
        )
        self.assertEqual([item["rank"] for item in report["hotspots"]], [1, 2, 3])

    def test_full_output_still_enriches_selected_selector_evidence(self) -> None:
        image = Image.new("RGB", (260, 100), "white")
        anchor_bounds = [15, 35, 10, 8]
        target_bounds = [10, 30, 40, 24]
        other_bounds = ([80, 30, 40, 24], [150, 30, 40, 24])
        hotspots = [
            {"rank": 1, "bounds": anchor_bounds, "score": 100},
            {"rank": 2, "bounds": list(other_bounds[0]), "score": 99},
            {"rank": 3, "bounds": list(other_bounds[1]), "score": 98},
            {"rank": 4, "bounds": target_bounds, "score": 97},
        ]
        node_specs = [
            ("div.card-a", target_bounds),
            ("div.card-b", list(other_bounds[0])),
            ("div.card-c", list(other_bounds[1])),
        ]
        nodes = [
            {
                "selector": selector,
                "tag": "div",
                "role": "surface",
                "bounds": bounds,
                "visible_bounds": bounds,
                "depth": 3,
                "unique": True,
                "visual": True,
                "computed": {"display": "block"},
            }
            for selector, bounds in node_specs
        ]
        report = {"hotspots": hotspots}
        dom_index = {
            "space": "capture-css-px",
            "truncated": False,
            "nodes": nodes,
        }
        hints = [
            {"kind": "color", "confidence": "high", "target_color": "#111111"},
            {"kind": "color", "confidence": "high", "target_color": "#222222"},
            {"kind": "color", "confidence": "medium", "target_color": "#333333"},
        ]

        with mock.patch.object(REPAIR_HINTS, "_solve_repair", side_effect=hints):
            REPAIR_HINTS.attach_repair_hints(
                report,
                dom_index,
                image,
                image,
                output_limit=3,
            )

        self.assertEqual(len(report["hotspots"]), 3)
        self.assertEqual(report["hotspots"][0]["bounds"], anchor_bounds)
        self.assertEqual(report["hotspots"][0]["dom"]["confidence"], "high")
        self.assertEqual(
            report["hotspots"][0]["repair_hint"]["target_color"],
            "#333333",
        )
        self.assertEqual(report["repair_summary"], {"actionable": 3, "uncertain": 0})

    def test_duplicate_selector_occupies_only_one_public_hotspot(self) -> None:
        image = self.canvas()
        report = {
            "hotspots": [
                {"rank": 1, "bounds": [64, 44, 50, 18], "score": 10},
                {"rank": 2, "bounds": [64, 62, 50, 18], "score": 9},
            ]
        }
        dom_index = self.dom_index((64, 44, 50, 36))

        with mock.patch.object(
            REPAIR_HINTS,
            "_solve_repair",
            return_value={
                "kind": "color",
                "confidence": "high",
                "target_color": "#2458d3",
            },
        ):
            REPAIR_HINTS.attach_repair_hints(
                report,
                dom_index,
                image,
                image,
                output_limit=3,
            )

        self.assertEqual(len(report["hotspots"]), 1)
        self.assertEqual(report["hotspots"][0]["dom"]["selector"], "main > div.card")

    def test_precise_same_selector_candidate_enriches_imprecise_anchor(self) -> None:
        image = self.canvas()
        report = {
            "hotspots": [
                {"rank": 1, "bounds": [70, 50, 10, 8], "score": 10},
                {"rank": 2, "bounds": [64, 44, 50, 36], "score": 9},
            ]
        }
        dom_index = self.dom_index((64, 44, 50, 36))

        with mock.patch.object(
            REPAIR_HINTS,
            "_solve_repair",
            return_value={
                "kind": "color",
                "confidence": "high",
                "target_color": "#2458d3",
            },
        ) as solver:
            REPAIR_HINTS.attach_repair_hints(
                report,
                dom_index,
                image,
                image,
                output_limit=3,
            )

        self.assertEqual(solver.call_count, 1)
        self.assertEqual(len(report["hotspots"]), 1)
        self.assertEqual(report["hotspots"][0]["bounds"], [70, 50, 10, 8])
        self.assertEqual(report["hotspots"][0]["score"], 10)
        self.assertEqual(report["hotspots"][0]["dom"]["confidence"], "high")
        self.assertEqual(report["hotspots"][0]["repair_hint"]["kind"], "color")


if __name__ == "__main__":
    unittest.main()
