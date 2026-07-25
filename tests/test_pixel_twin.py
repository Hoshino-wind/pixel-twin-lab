from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "pixel_twin.py"

SPEC = importlib.util.spec_from_file_location("pixel_twin", CLI)
assert SPEC and SPEC.loader
PIXEL_TWIN = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PIXEL_TWIN)


class ProjectFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="pixel-twin-test-")
        self.project = Path(self.temporary.name) / "project"
        (self.project / "src" / "components").mkdir(parents=True)
        (self.project / "src" / "store").mkdir(parents=True)
        (self.project / "public").mkdir()
        (self.project / "package.json").write_text(
            json.dumps(
                {
                    "dependencies": {"react": "latest"},
                    "scripts": {
                        "lint": "node -e \"process.exit(0)\"",
                        "build": "node -e \"process.exit(0)\"",
                    },
                }
            ),
            encoding="utf-8",
        )
        (self.project / "src" / "components" / "Card.tsx").write_text(
            "export function Card() { return <div className=\"card\">Old</div>; }\n",
            encoding="utf-8",
        )
        (self.project / "src" / "store" / "session.ts").write_text(
            "export const session = { role: 'user' };\n",
            encoding="utf-8",
        )
        self.git("init", "-q")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "user.name", "Pixel Twin Test")
        self.git("add", ".")
        self.git("commit", "-qm", "fixture")
        self.sessions: list[str] = []

    def tearDown(self) -> None:
        for session in self.sessions:
            subprocess.run(
                [sys.executable, str(CLI), "finish", "--session", session],
                capture_output=True,
                check=False,
            )
        self.temporary.cleanup()

    def git(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(self.project), *args],
            capture_output=True,
            text=True,
            check=True,
        )

    def cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(CLI), *args],
            capture_output=True,
            text=True,
            check=False,
        )

    def begin(self, *extra: str) -> str:
        result = self.cli(
            "begin",
            "--project",
            str(self.project),
            "--ui-root",
            "src/components",
            "--asset-root",
            "public",
            "--json",
            *extra,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        session = json.loads(result.stdout)["session"]
        self.sessions.append(session)
        return session

    def check(self, session: str, *extra: str) -> subprocess.CompletedProcess[str]:
        return self.cli(
            "check",
            "--session",
            session,
            "--run",
            "none",
            "--keep-session",
            "--json",
            *extra,
        )

    def test_inspect_is_read_only_and_concise(self) -> None:
        before = sorted(path.relative_to(self.project) for path in self.project.rglob("*") if ".git" not in path.parts)
        result = self.cli("inspect", "--project", str(self.project), "--json")
        after = sorted(path.relative_to(self.project) for path in self.project.rglob("*") if ".git" not in path.parts)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["framework"], "react")
        self.assertEqual(data["ui_roots"], ["src/components"])
        self.assertEqual(data["checks"], ["lint"])
        self.assertEqual(data["optional_checks"], ["build"])
        self.assertEqual(before, after)

    def test_preexisting_dirty_file_is_not_counted_when_unchanged(self) -> None:
        store = self.project / "src" / "store" / "session.ts"
        store.write_text("export const session = { role: 'admin' };\n", encoding="utf-8")
        session = self.begin()
        card = self.project / "src" / "components" / "Card.tsx"
        card.write_text("export function Card() { return <div className=\"card new\">New</div>; }\n", encoding="utf-8")

        result = self.check(session)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual([item["path"] for item in report["scope"]["changes"]], ["src/components/Card.tsx"])
        self.assertEqual(
            store.read_text(encoding="utf-8"),
            "export const session = { role: 'admin' };\n",
        )

    def test_modifying_preexisting_dirty_file_is_blocked(self) -> None:
        store = self.project / "src" / "store" / "session.ts"
        store.write_text("export const session = { role: 'admin' };\n", encoding="utf-8")
        session = self.begin()
        store.write_text("export const session = { role: 'owner' };\n", encoding="utf-8")

        result = self.check(session)
        self.assertEqual(result.returncode, 2)
        report = json.loads(result.stdout)
        self.assertIn("pre-existing user change", report["scope"]["violations"][0]["reason"])

    def test_business_path_and_network_delta_are_blocked(self) -> None:
        session = self.begin()
        (self.project / "src" / "store" / "new-store.ts").write_text("export const value = 1;\n", encoding="utf-8")
        card = self.project / "src" / "components" / "Card.tsx"
        card.write_text(
            "export function Card() { fetch('/api/card'); return <div>New</div>; }\n",
            encoding="utf-8",
        )

        result = self.check(session)
        self.assertEqual(result.returncode, 2)
        reasons = "\n".join(item["reason"] for item in json.loads(result.stdout)["scope"]["violations"])
        self.assertIn("protected behavior path", reasons)
        self.assertIn("business behavior delta", reasons)

    def test_safe_asset_passes_and_unsafe_svg_is_blocked(self) -> None:
        session = self.begin()
        Image.new("RGB", (8, 8), "red").save(self.project / "public" / "badge.png")
        result = self.check(session)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        (self.project / "public" / "bad.svg").write_text(
            '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
            encoding="utf-8",
        )
        result = self.check(session)
        self.assertEqual(result.returncode, 2)
        self.assertIn("unsafe SVG", result.stdout)

    def test_index_changes_are_blocked(self) -> None:
        session = self.begin()
        card = self.project / "src" / "components" / "Card.tsx"
        card.write_text("export function Card() { return <div>New</div>; }\n", encoding="utf-8")
        self.git("add", "src/components/Card.tsx")

        result = self.check(session)
        self.assertEqual(result.returncode, 2)
        self.assertIn("staging state changed", result.stdout)

    def test_policy_paths_cannot_escape_or_use_globs(self) -> None:
        escaped = self.cli(
            "begin",
            "--project",
            str(self.project),
            "--ui-root",
            "../outside",
        )
        self.assertEqual(escaped.returncode, 1)
        self.assertIn("project-relative", escaped.stderr)

        globbed = self.cli(
            "begin",
            "--project",
            str(self.project),
            "--editable",
            "src/**/*.ts",
        )
        self.assertEqual(globbed.returncode, 1)
        self.assertIn("wildcards", globbed.stderr)

    def test_symlink_asset_changes_are_blocked(self) -> None:
        target = self.project / "icon-source.svg"
        target.write_text('<svg xmlns="http://www.w3.org/2000/svg"/>', encoding="utf-8")
        link = self.project / "public" / "icon.svg"
        os.symlink("../icon-source.svg", link)
        self.git("add", "icon-source.svg", "public/icon.svg")
        self.git("commit", "-qm", "add linked asset")
        session = self.begin()
        link.unlink()

        result = self.check(session)
        self.assertEqual(result.returncode, 2)
        self.assertIn("symlink/submodule/non-file", result.stdout)

    def test_ignored_secret_and_diagnostic_directory_are_protected(self) -> None:
        (self.project / ".gitignore").write_text(".env*\noutputs/\n", encoding="utf-8")
        (self.project / ".env").write_text("TOKEN=before\n", encoding="utf-8")
        (self.project / ".envrc").write_text("export TOKEN=before\n", encoding="utf-8")
        outputs = self.project / "outputs"
        outputs.mkdir()
        (outputs / "capture.png").write_bytes(b"before")
        self.git("add", ".gitignore")
        self.git("commit", "-qm", "ignore local files")
        session = self.begin()
        (self.project / ".env").write_text("TOKEN=after\n", encoding="utf-8")
        (self.project / ".envrc").write_text("export TOKEN=after\n", encoding="utf-8")
        (outputs / "capture.png").write_bytes(b"after!")

        result = self.check(session)
        self.assertEqual(result.returncode, 2)
        violations = json.loads(result.stdout)["scope"]["violations"]
        reasons = "\n".join(item["reason"] for item in violations)
        paths = {item["path"] for item in violations}
        self.assertIn("ignored secret", reasons)
        self.assertIn("project-local diagnostic", reasons)
        self.assertTrue({".env", ".envrc", "outputs"}.issubset(paths))

    def test_presentational_auth_component_is_allowed(self) -> None:
        auth = self.project / "src" / "features" / "auth"
        auth.mkdir(parents=True)
        login = auth / "LoginForm.tsx"
        login.write_text("export const LoginForm = () => <form className=\"old\" />;\n", encoding="utf-8")
        self.git("add", "src/features/auth/LoginForm.tsx")
        self.git("commit", "-qm", "add login UI")
        session = self.begin()
        login.write_text("export const LoginForm = () => <form className=\"new\" />;\n", encoding="utf-8")

        result = self.check(session)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_exact_presentational_auth_support_file_is_allowed(self) -> None:
        auth = self.project / "src" / "features" / "auth"
        auth.mkdir(parents=True)
        theme = auth / "theme.ts"
        theme.write_text("export const accent = '#111111';\n", encoding="utf-8")
        self.git("add", "src/features/auth/theme.ts")
        self.git("commit", "-qm", "add auth theme")
        session = self.begin("--editable", "src/features/auth/theme.ts")
        theme.write_text("export const accent = '#2255ff';\n", encoding="utf-8")

        result = self.check(session)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_nested_frontend_package_uses_repository_wide_guard(self) -> None:
        app = self.project / "packages" / "web"
        component = app / "src" / "components" / "Panel.tsx"
        component.parent.mkdir(parents=True)
        (app / "package.json").write_text(
            json.dumps({"dependencies": {"react": "latest"}, "scripts": {}}),
            encoding="utf-8",
        )
        component.write_text("export const Panel = () => <section>Old</section>;\n", encoding="utf-8")
        self.git("add", "packages/web")
        self.git("commit", "-qm", "add nested app")

        begin = self.cli("begin", "--project", str(app), "--json")
        self.assertEqual(begin.returncode, 0, begin.stderr)
        payload = json.loads(begin.stdout)
        session = payload["session"]
        self.sessions.append(session)
        self.assertEqual(Path(payload["repository"]), self.project.resolve())
        component.write_text("export const Panel = () => <section>New</section>;\n", encoding="utf-8")
        result = self.check(session)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        paths = [item["path"] for item in json.loads(result.stdout)["scope"]["changes"]]
        self.assertEqual(paths, ["packages/web/src/components/Panel.tsx"])

    def test_successful_check_removes_session_by_default(self) -> None:
        session = self.begin()
        card = self.project / "src" / "components" / "Card.tsx"
        card.write_text("export function Card() { return <div>New</div>; }\n", encoding="utf-8")
        result = self.cli("check", "--session", session, "--run", "none", "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(PIXEL_TWIN.session_directory(session).exists())
        self.sessions.remove(session)

    def test_auto_check_runs_only_existing_project_scripts(self) -> None:
        session = self.begin()
        card = self.project / "src" / "components" / "Card.tsx"
        card.write_text("export function Card() { return <div>Checked</div>; }\n", encoding="utf-8")
        result = self.cli(
            "check",
            "--session",
            session,
            "--keep-session",
            "--json",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        checks = json.loads(result.stdout)["checks"]
        self.assertEqual(checks, [{"name": "lint", "pass": True}])

    def test_repair_source_scan_stays_inside_session_ui_scope(self) -> None:
        session_id = self.begin()
        session = PIXEL_TWIN.load_session(session_id)

        files, truncated = PIXEL_TWIN.repair_source_files(self.project, session)

        paths = {relative for relative, _ in files}
        self.assertIn("src/components/Card.tsx", paths)
        self.assertNotIn("src/store/session.ts", paths)
        self.assertFalse(truncated)

    def test_explicit_build_extends_automatic_checks(self) -> None:
        session = self.begin()
        card = self.project / "src" / "components" / "Card.tsx"
        card.write_text("export function Card() { return <div>Built</div>; }\n", encoding="utf-8")
        result = self.cli(
            "check",
            "--session",
            session,
            "--run",
            "build",
            "--keep-session",
            "--json",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        names = [item["name"] for item in json.loads(result.stdout)["checks"]]
        self.assertEqual(names, ["lint", "build"])


class ImageComparisonTests(unittest.TestCase):
    def test_public_visual_payload_omits_internal_region_grids(self) -> None:
        report = {
            "pass": True,
            "_grid": {"cells": [{"id": "r0c0"}]},
            "_dom_index": {"nodes": [{"secret": "must not escape"}]},
            "_analysis_version": 2,
            "hotspots": [{"rank": rank} for rank in range(1, 6)],
            "dynamic_regions": [{"id": str(index)} for index in range(5)],
        }
        delta = {
            "pass": True,
            "regions": [{"id": "r0c0"}],
            "improved_regions": [{"id": str(index)} for index in range(5)],
            "regressed_regions": [{"id": str(index)} for index in range(5)],
        }
        report["hotspots"][0]["repair_hint"] = {
            "kind": "color",
            "_evidence": "must not escape",
            "source_candidates": [
                {"path": f"src/{index}.tsx", "line": 1, "snippet": "must not escape"}
                for index in range(4)
            ],
        }

        public_report = PIXEL_TWIN.public_visual_report(report)
        public_delta = PIXEL_TWIN.public_visual_delta(delta)

        self.assertNotIn("_grid", public_report)
        self.assertNotIn("_dom_index", public_report)
        self.assertNotIn("_analysis_version", public_report)
        self.assertEqual(len(public_report["hotspots"]), 3)
        self.assertEqual(len(public_report["dynamic_regions"]), 3)
        repair = public_report["hotspots"][0]["repair_hint"]
        self.assertNotIn("_evidence", repair)
        self.assertEqual(len(repair["source_candidates"]), 2)
        self.assertNotIn("snippet", repair["source_candidates"][0])
        self.assertNotIn("regions", public_delta)
        self.assertEqual(len(public_delta["improved_regions"]), 3)
        self.assertEqual(len(public_delta["regressed_regions"]), 3)

    def test_hotspot_maps_to_one_bounded_dom_css_hint(self) -> None:
        report = {
            "hotspots": [
                {
                    "rank": 1,
                    "bounds": [24, 18, 34, 14],
                    "dominant_color_hint": {"actual": "#111111", "reference": "#222222"},
                }
            ]
        }
        dom_index = {
            "space": "capture-css-px",
            "truncated": False,
            "nodes": [
                {
                    "selector": "body",
                    "tag": "body",
                    "bounds": [0, 0, 200, 100],
                    "depth": 1,
                    "unique": True,
                    "visual": False,
                    "computed": {"display": "block"},
                },
                {
                    "selector": "main > div.card",
                    "tag": "div",
                    "bounds": [10, 10, 80, 40],
                    "depth": 3,
                    "unique": True,
                    "visual": True,
                    "has_direct_text": True,
                    "role": "text",
                    "computed": {
                        "color": "rgb(17, 17, 17)",
                        "font-size": "16px",
                        "line-height": "24px",
                        "background-image": "url(https://secret.example/token)",
                    },
                },
            ],
        }

        PIXEL_TWIN.attach_dom_hints(report, dom_index)

        hint = report["hotspots"][0]["dom"]
        self.assertEqual(hint["selector"], "main > div.card")
        self.assertEqual(hint["css_levers"], ["color", "font-size", "line-height"])
        self.assertEqual(set(hint["computed"]), {"color", "font-size", "line-height"})
        self.assertNotIn("secret", json.dumps(report))
        self.assertEqual(report["dom_mapping"]["mapped_hotspots"], 1)

    def test_dom_hint_output_is_limited_to_three_hotspots(self) -> None:
        report = {
            "hotspots": [
                {"rank": index + 1, "bounds": [index * 10, 0, 8, 8]}
                for index in range(5)
            ]
        }
        dom_index = {
            "space": "capture-css-px",
            "truncated": True,
            "nodes": [
                {
                    "selector": "." + "component" * 30,
                    "tag": "div",
                    "bounds": [0, 0, 100, 20],
                    "depth": 3,
                    "unique": True,
                    "visual": True,
                    "computed": {"background-color": "rgb(1, 2, 3)"},
                }
            ],
        }

        PIXEL_TWIN.attach_dom_hints(report, dom_index)

        self.assertEqual(sum("dom" in hotspot for hotspot in report["hotspots"]), 3)
        self.assertLessEqual(len(report["hotspots"][0]["dom"]["selector"]), 160)
        self.assertLessEqual(len(report["hotspots"][0]["dom"]["css_levers"]), 3)
        self.assertTrue(report["dom_mapping"]["truncated"])

    def test_dom_hint_uses_visible_bounds_but_preserves_full_media_size(self) -> None:
        report = {"hotspots": [{"rank": 1, "bounds": [90, 10, 5, 5]}]}
        dom_index = {
            "space": "capture-css-px",
            "truncated": False,
            "nodes": [
                {
                    "selector": "img.hero",
                    "tag": "img",
                    "role": "img",
                    "bounds": [-20, 0, 200, 50],
                    "visible_bounds": [0, 0, 100, 50],
                    "depth": 3,
                    "unique": True,
                    "visual": True,
                    "computed": {"object-fit": "cover"},
                }
            ],
        }

        PIXEL_TWIN.attach_dom_hints(report, dom_index)

        hint = report["hotspots"][0]["dom"]
        self.assertEqual(hint["bounds"], [-20.0, 0.0, 200.0, 50.0])
        self.assertEqual(hint["css_levers"], ["width", "height", "object-fit"])
        self.assertEqual(hint["computed"]["width"], "200px")

    def test_dom_mapping_reports_empty_or_truncated_index(self) -> None:
        report = {"hotspots": [{"rank": 1, "bounds": [0, 0, 5, 5]}]}

        PIXEL_TWIN.attach_dom_hints(
            report,
            {"space": "capture-css-px", "truncated": True, "nodes": []},
        )

        self.assertEqual(
            report["dom_mapping"],
            {"mapped_hotspots": 0, "indexed_nodes": 0, "truncated": True},
        )

    def test_unstable_browser_capture_is_rejected(self) -> None:
        capture = subprocess.CompletedProcess(
            ["node"],
            0,
            json.dumps(
                {
                    "status": 200,
                    "settled": {
                        "stable": False,
                        "fonts_ready": True,
                        "pending_images": 0,
                        "layout_stable": False,
                    },
                    "console_errors": [],
                    "page_errors": [],
                }
            ),
            "",
        )
        with (
            tempfile.TemporaryDirectory(prefix="pixel-twin-capture-test-") as temporary,
            mock.patch.object(PIXEL_TWIN, "run_process", return_value=capture),
        ):
            result = PIXEL_TWIN.capture_page(
                Path(temporary),
                Path(temporary) / "actual.png",
                url="http://127.0.0.1:3000/",
                width=100,
                height=100,
                wait_ms=250,
                color_scheme="light",
                selector=None,
                allow_remote=False,
                timeout=10,
            )
        self.assertFalse(result["pass"])
        self.assertIn("did not settle", result["reason"])

    def test_failed_image_resource_is_rejected_even_if_helper_claims_stable(self) -> None:
        capture = subprocess.CompletedProcess(
            ["node"],
            0,
            json.dumps(
                {
                    "status": 200,
                    "settled": {
                        "stable": True,
                        "pending_images": 0,
                        "failed_images": 1,
                        "layout_stable": True,
                    },
                    "console_errors": [],
                    "page_errors": [],
                }
            ),
            "",
        )
        with (
            tempfile.TemporaryDirectory(prefix="pixel-twin-capture-test-") as temporary,
            mock.patch.object(PIXEL_TWIN, "run_process", return_value=capture),
        ):
            result = PIXEL_TWIN.capture_page(
                Path(temporary),
                Path(temporary) / "actual.png",
                url="http://127.0.0.1:3000/",
                width=100,
                height=100,
                wait_ms=250,
                color_scheme="light",
                selector=None,
                allow_remote=False,
                timeout=10,
            )
        self.assertFalse(result["pass"])
        self.assertIn("failed to load", result["reason"])

    def test_viewport_limits_apply_to_explicit_and_reference_sizes(self) -> None:
        self.assertEqual(PIXEL_TWIN.parse_viewport(None, (4096, 2160)), (4096, 2160))
        with self.assertRaises(PIXEL_TWIN.PixelTwinError):
            PIXEL_TWIN.parse_viewport("10000x1000", (1440, 900))
        with self.assertRaises(PIXEL_TWIN.PixelTwinError):
            PIXEL_TWIN.parse_viewport(None, (5000, 5000))

    def test_minimum_match_must_be_a_finite_percentage(self) -> None:
        for value in (-1.0, 100.1, float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value), self.assertRaises(PIXEL_TWIN.PixelTwinError):
                PIXEL_TWIN.validate_min_match(value)
        for value in (None, 0.0, 98.0, 100.0):
            with self.subTest(value=value):
                PIXEL_TWIN.validate_min_match(value)

    def test_dynamic_selectors_are_bounded_and_sanitized(self) -> None:
        self.assertEqual(
            PIXEL_TWIN.validate_dynamic_selectors(["canvas#trend", ".map"]),
            ["canvas#trend", ".map"],
        )
        with self.assertRaises(PIXEL_TWIN.PixelTwinError):
            PIXEL_TWIN.validate_dynamic_selectors(["a", "b", "c", "d"])
        with self.assertRaises(PIXEL_TWIN.PixelTwinError):
            PIXEL_TWIN.validate_dynamic_selectors(["x" * 161])
        with self.assertRaises(PIXEL_TWIN.PixelTwinError):
            PIXEL_TWIN.validate_dynamic_selectors(["canvas\niframe"])
        self.assertEqual(
            PIXEL_TWIN.validate_dynamic_selectors(['  [data-title="a  b"]  ']),
            ['[data-title="a  b"]'],
        )

    def test_reference_decompression_bomb_is_a_controlled_error(self) -> None:
        error = PIXEL_TWIN.Image.DecompressionBombError("oversized image")
        with (
            mock.patch.object(PIXEL_TWIN.Image, "open", side_effect=error),
            self.assertRaisesRegex(PIXEL_TWIN.PixelTwinError, "Unable to read reference image"),
        ):
            PIXEL_TWIN.reference_size(Path("oversized.png"))

    @unittest.skipIf(os.name == "nt", "POSIX process-group behavior")
    def test_timeout_always_kills_the_process_group(self) -> None:
        process = mock.Mock()
        process.pid = 424242
        process.stdout = mock.Mock()
        process.stderr = mock.Mock()
        process.communicate.side_effect = [
            subprocess.TimeoutExpired(["fake-command"], 1),
            ("", ""),
        ]
        process.wait.return_value = 0
        with (
            mock.patch.object(PIXEL_TWIN.subprocess, "Popen", return_value=process),
            mock.patch.object(PIXEL_TWIN.os, "killpg") as killpg,
            self.assertRaises(PIXEL_TWIN.PixelTwinError),
        ):
            PIXEL_TWIN.run_process(["fake-command"], timeout=1)
        self.assertEqual(
            killpg.call_args_list,
            [
                mock.call(process.pid, PIXEL_TWIN.signal.SIGTERM),
                mock.call(process.pid, PIXEL_TWIN.signal.SIGKILL),
            ],
        )

    def test_dirty_git_directory_fingerprint_tracks_content(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pixel-twin-submodule-test-") as temporary:
            repository = Path(temporary) / "nested"
            repository.mkdir()
            subprocess.run(["git", "-C", str(repository), "init", "-q"], check=True)
            subprocess.run(
                ["git", "-C", str(repository), "config", "user.email", "test@example.com"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(repository), "config", "user.name", "Pixel Twin Test"],
                check=True,
            )
            tracked = repository / "tracked.txt"
            tracked.write_text("committed\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repository), "add", "tracked.txt"], check=True)
            subprocess.run(
                ["git", "-C", str(repository), "commit", "-qm", "fixture"],
                check=True,
            )

            tracked.write_text("dirty one\n", encoding="utf-8")
            first = PIXEL_TWIN.file_fingerprint(repository)
            tracked.write_text("dirty two\n", encoding="utf-8")
            second = PIXEL_TWIN.file_fingerprint(repository)
            self.assertNotEqual(first, second)

            untracked = repository / "local.txt"
            untracked.write_text("local one\n", encoding="utf-8")
            third = PIXEL_TWIN.file_fingerprint(repository)
            untracked.write_text("local two\n", encoding="utf-8")
            fourth = PIXEL_TWIN.file_fingerprint(repository)
            self.assertNotEqual(third, fourth)

    def test_image_metrics_are_in_memory_until_debug_is_requested(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pixel-twin-image-test-") as temporary:
            root = Path(temporary)
            reference = root / "reference.png"
            actual = root / "actual.png"
            Image.new("RGB", (10, 10), "white").save(reference)
            Image.new("RGB", (10, 10), "white").save(actual)

            metrics, diff = PIXEL_TWIN.compare_images(reference, actual, tolerance=8)
            self.assertEqual(metrics["tolerant_match_pct"], 100.0)
            self.assertIsNotNone(diff)
            self.assertEqual(sorted(path.name for path in root.iterdir()), ["actual.png", "reference.png"])

    def test_debug_directory_must_be_external_and_empty(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pixel-twin-debug-test-") as temporary:
            root = Path(temporary)
            project = root / "project"
            project.mkdir()
            debug = root / "debug"
            debug.mkdir()
            (debug / "actual.png").write_bytes(b"user data")
            with self.assertRaises(PIXEL_TWIN.PixelTwinError):
                PIXEL_TWIN.ensure_debug_outside_project(project, str(debug))

    def test_temporary_root_rejects_symlinks(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pixel-twin-root-test-") as temporary:
            root = Path(temporary)
            target = root / "target"
            target.mkdir()
            link = root / "sessions"
            os.symlink(target, link)
            original = PIXEL_TWIN.SESSION_ROOT
            PIXEL_TWIN.SESSION_ROOT = link
            try:
                with self.assertRaises(PIXEL_TWIN.PixelTwinError):
                    PIXEL_TWIN.ensure_session_root()
            finally:
                PIXEL_TWIN.SESSION_ROOT = original

    def test_matching_reference_cannot_override_http_error(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pixel-twin-http-test-") as temporary:
            root = Path(temporary)
            reference = root / "reference.png"
            Image.new("RGB", (100, 100), "white").save(reference)
            args = Namespace(
                url="http://127.0.0.1:3000/missing",
                reference=str(reference),
                viewport=None,
                wait_ms=1,
                color_scheme="light",
                selector=None,
                allow_remote=False,
                timeout=10,
                tolerance=8,
                min_match=98.0,
                fail_console_errors=False,
                keep_debug=None,
            )
            capture = subprocess.CompletedProcess(
                ["node"],
                0,
                json.dumps(
                    {
                        "status": 404,
                        "settled": {"stable": True},
                        "console_errors": [],
                        "page_errors": [],
                    }
                ),
                "",
            )

            def fake_capture(command, **_options):
                output = Path(command[command.index("--output") + 1])
                Image.new("RGB", (100, 100), "white").save(output)
                return capture

            with mock.patch.object(PIXEL_TWIN, "run_process", side_effect=fake_capture):
                result = PIXEL_TWIN.run_visual_check(args, root, root, root)
            self.assertIsNotNone(result)
            self.assertFalse(result["pass"])
            self.assertNotIn("tolerant_match_pct", result)


if __name__ == "__main__":
    unittest.main()
