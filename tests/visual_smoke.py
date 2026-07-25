#!/usr/bin/env python3
"""Exercise the real browser path without leaving project-local diagnostics."""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "pixel_twin.py"
BROWSER = ROOT / "scripts" / "browser_capture.cjs"


def free_port() -> int:
    with socket.socket() as handle:
        handle.bind(("127.0.0.1", 0))
        return int(handle.getsockname()[1])


def wait_for_server(port: int) -> None:
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        with socket.socket() as handle:
            if handle.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.05)
    raise RuntimeError("temporary HTTP server did not start")


def run(command: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False, timeout=60)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="pixel-twin-visual-smoke-") as temporary:
        root = Path(temporary)
        project = root / "project"
        public = project / "public"
        public.mkdir(parents=True)
        (project / "package.json").write_text(
            json.dumps({"private": True, "scripts": {}}), encoding="utf-8"
        )
        page = public / "index.html"
        page.write_text(
            """<!doctype html><html><head><style>
html,body{margin:0;width:160px;background:#f4f6fb}
.spacer{height:600px}
main{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:4px;width:160px;height:100px}
.card{padding:4px 10px;border:1px solid #99a3b8;border-radius:8px;color:#172033;font:14px/18px sans-serif}
canvas{display:block;width:80px;height:40px}
</style></head><body><div class="spacer"></div><main><div class="card">Pixel Twin</div><canvas id="trend" width="80" height="40"></canvas></main><script>
const context=document.querySelector('#trend').getContext('2d');context.strokeStyle='#1957d2';context.lineWidth=3;context.beginPath();context.moveTo(5,30);context.lineTo(40,10);context.lineTo(75,25);context.stroke();
</script></body></html>""",
            encoding="utf-8",
        )
        for command in (
            ["git", "init", "-q"],
            ["git", "config", "user.email", "test@example.com"],
            ["git", "config", "user.name", "Pixel Twin Test"],
            ["git", "add", "."],
            ["git", "commit", "-qm", "fixture"],
        ):
            result = run(command, cwd=project)
            if result.returncode != 0:
                raise RuntimeError(result.stderr or result.stdout)

        port = free_port()
        server = subprocess.Popen(
            [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1", "--directory", str(public)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        session: str | None = None
        try:
            wait_for_server(port)
            url = f"http://127.0.0.1:{port}/"
            reference = root / "reference.png"
            original = page.read_text(encoding="utf-8")

            pixel = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw=="
            page.write_text(
                "<!doctype html><html><body>"
                + "".join(f'<img src="{pixel}" alt="">' for _ in range(200))
                + '<img src="/missing.png" alt="missing">'
                + "</body></html>",
                encoding="utf-8",
            )
            broken_capture = run(
                [
                    "node",
                    str(BROWSER),
                    "--url",
                    url,
                    "--output",
                    str(root / "broken.png"),
                    "--width",
                    "160",
                    "--height",
                    "100",
                ],
                cwd=ROOT,
            )
            if broken_capture.returncode != 0:
                raise RuntimeError(broken_capture.stderr or broken_capture.stdout)
            broken_result = json.loads(broken_capture.stdout)
            if broken_result["settled"]["failed_images"] != 1 or broken_result["settled"]["stable"]:
                raise RuntimeError(f"late failed image was not rejected: {broken_result}")

            page.write_text(
                "<!doctype html><html><body>"
                "<script>location.href='https://example.invalid/blocked'</script>"
                "</body></html>",
                encoding="utf-8",
            )
            redirected_capture = run(
                [
                    "node",
                    str(BROWSER),
                    "--url",
                    url,
                    "--output",
                    str(root / "redirected.png"),
                    "--width",
                    "160",
                    "--height",
                    "100",
                ],
                cwd=ROOT,
            )
            if redirected_capture.returncode == 0:
                raise RuntimeError("remote main-frame navigation was not blocked")

            page.write_text(original, encoding="utf-8")
            desired = original.replace("#172033", "#13213d").replace(
                "context.lineTo(40,10)", "context.lineTo(40,20)"
            )
            page.write_text(desired, encoding="utf-8")
            capture = run(
                [
                    "node",
                    str(BROWSER),
                    "--url",
                    url,
                    "--output",
                    str(reference),
                    "--width",
                    "160",
                    "--height",
                    "100",
                    "--selector",
                    "main",
                ],
                cwd=ROOT,
            )
            if capture.returncode != 0:
                raise RuntimeError(capture.stderr or capture.stdout)
            page.write_text(original, encoding="utf-8")

            begin = run(
                [
                    sys.executable,
                    str(CLI),
                    "begin",
                    "--project",
                    str(project),
                    "--ui-root",
                    "public",
                    "--asset-root",
                    "public",
                    "--url",
                    url,
                    "--reference",
                    str(reference),
                    "--selector",
                    "main",
                    "--json",
                ],
                cwd=ROOT,
            )
            if begin.returncode != 0:
                raise RuntimeError(begin.stderr or begin.stdout)
            begin_result = json.loads(begin.stdout)
            session = begin_result["session"]
            if len(begin.stdout.encode("utf-8")) > 12_000:
                raise RuntimeError("baseline visual payload exceeded the 12KB budget")
            session_payload = (Path(begin_result["temporary"]) / "session.json").read_text(
                encoding="utf-8"
            )
            if "_dom_index" in session_payload or "_dynamic_regions" in session_payload:
                raise RuntimeError("raw browser evidence leaked into the saved session")
            if (Path(begin_result["temporary"]) / "visual" / "baseline-sample.png").exists():
                raise RuntimeError("temporal sample was retained after baseline analysis")
            if begin_result["visual"]["tolerant_match_pct"] >= 100.0:
                raise RuntimeError(f"baseline did not detect the visual defect: {begin_result['visual']}")
            if begin_result["visual"].get("dynamic_region_count") != 1:
                raise RuntimeError(f"canvas was not detected: {begin_result['visual']}")
            hotspots = begin_result["visual"].get("hotspots") or []
            if not hotspots or ".card" not in (hotspots[0].get("dom") or {}).get("selector", ""):
                raise RuntimeError(f"hotspot did not map to the card: {begin_result['visual']}")
            repair = hotspots[0].get("repair_hint") or {}
            if repair.get("kind") != "color" or repair.get("target_color") != "#13213d":
                raise RuntimeError(f"card color repair was not inferred: {begin_result['visual']}")
            source_paths = {
                item.get("path") for item in repair.get("source_candidates") or []
            }
            if "public/index.html" not in source_paths:
                raise RuntimeError(f"repair source was not located: {begin_result['visual']}")
            dom_bounds = hotspots[0]["dom"]["bounds"]
            if not (
                0 <= dom_bounds[0] <= 160
                and 0 <= dom_bounds[1] <= 100
                and dom_bounds[0] + dom_bounds[2] <= 160.5
                and dom_bounds[1] + dom_bounds[3] <= 100.5
            ):
                raise RuntimeError(f"selector crop coordinates are not local: {dom_bounds}")

            page.write_text(desired, encoding="utf-8")
            check = run(
                [
                    sys.executable,
                    str(CLI),
                    "check",
                    "--session",
                    session,
                    "--run",
                    "none",
                    "--json",
                ],
                cwd=ROOT,
            )
            if check.returncode != 0:
                raise RuntimeError(check.stderr or check.stdout)
            result = json.loads(check.stdout)
            if len(check.stdout.encode("utf-8")) > 15_000:
                raise RuntimeError("final visual payload exceeded the 15KB budget")
            if result["visual"]["tolerant_match_pct"] != 100.0:
                raise RuntimeError(f"unexpected visual result: {result['visual']}")
            if "delta" not in result["visual"]:
                raise RuntimeError(f"visual gain was not reported: {result['visual']}")
            dynamic_delta = result["visual"]["delta"].get("dynamic") or {}
            if (dynamic_delta.get("summary") or {}).get("improved") != 1:
                raise RuntimeError(f"dynamic improvement was not reported: {result['visual']}")
            project_files = sorted(
                path.relative_to(project).as_posix()
                for path in project.rglob("*")
                if ".git" not in path.parts and path.is_file()
            )
            if project_files != ["package.json", "public/index.html"]:
                raise RuntimeError(f"project-local diagnostics were created: {project_files}")
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
            if session:
                run([sys.executable, str(CLI), "finish", "--session", session])

    print("visual smoke: PASS · temporary capture cleaned · project artifacts 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
