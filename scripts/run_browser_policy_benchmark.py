#!/usr/bin/env python3
"""Verify browser selection is safe for Codex screenshot backtests.

The policy is intentionally conservative:
- Chrome-like aliases resolve to Playwright's bundled Chromium.
- System Chrome is blocked unless explicitly opted in for local debugging.
"""
from __future__ import annotations

import json
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for_server(port: int, timeout: float = 8.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.1)
    return False


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)


def write_fixture(root: Path, out_dir: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    (root / "index.html").write_text(
        """<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <style>
      html, body { margin: 0; width: 160px; height: 100px; background: #ffffff; }
      .stage { position: relative; width: 160px; height: 100px; }
      [data-element="probe"] { position: absolute; left: 12px; top: 16px; width: 84px; height: 28px; color: #123456; }
    </style>
  </head>
  <body>
    <main class="stage">
      <button data-element="probe">Probe</button>
    </main>
  </body>
</html>
""",
        encoding="utf-8",
    )
    (out_dir / "lab-config.json").write_text(json.dumps({"width": 160, "height": 100}), encoding="utf-8")


def expect_bundled_meta(meta: dict, source: str) -> list[str]:
    failures: list[str] = []
    if meta.get("requested_browser_input") != source:
        failures.append(f"{source}: requested_browser_input={meta.get('requested_browser_input')!r}")
    if meta.get("requested_browser_channel") != "bundled":
        failures.append(f"{source}: requested_browser_channel={meta.get('requested_browser_channel')!r}")
    if meta.get("browser_channel") != "bundled":
        failures.append(f"{source}: browser_channel={meta.get('browser_channel')!r}")
    if meta.get("browser_source") == "system":
        failures.append(f"{source}: browser_source unexpectedly system")
    return failures


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="browser-policy-bench-"))
    web_root = work / "web"
    out_dir = work / "lab"
    write_fixture(web_root, out_dir)

    port = free_port()
    server = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1", "--directory", str(web_root)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    failures: list[str] = []
    try:
        if not wait_for_server(port):
            print("browser policy benchmark: server did not start", file=sys.stderr)
            return 2
        url = f"http://127.0.0.1:{port}/"

        capture = run(
            [
                "node",
                str(SCRIPTS / "capture_modes.cjs"),
                "--url",
                url,
                "--out-dir",
                str(out_dir),
                "--modes",
                "reference",
                "--browser",
                "chrome",
                "--wait-until",
                "load",
            ]
        )
        if capture.returncode != 0:
            failures.append("capture --browser chrome failed: " + (capture.stderr or capture.stdout).strip())
        else:
            meta = json.loads((out_dir / "capture-meta.json").read_text(encoding="utf-8"))
            failures.extend(expect_bundled_meta(meta, "chrome"))

        measure = run(
            [
                "node",
                str(SCRIPTS / "measure_dom_elements.cjs"),
                "--url",
                url,
                "--out-dir",
                str(out_dir),
                "--browser",
                "chrome",
            ]
        )
        if measure.returncode != 0:
            failures.append("measure --browser chrome failed: " + (measure.stderr or measure.stdout).strip())
        else:
            dom = json.loads((out_dir / "dom-elements.json").read_text(encoding="utf-8"))
            failures.extend(expect_bundled_meta(dom.get("browser") or {}, "chrome"))

        system_capture = run(
            [
                "node",
                str(SCRIPTS / "capture_modes.cjs"),
                "--url",
                url,
                "--out-dir",
                str(out_dir),
                "--modes",
                "reference",
                "--browser",
                "system",
                "--wait-until",
                "load",
            ]
        )
        if system_capture.returncode == 0:
            failures.append("capture --browser system unexpectedly succeeded without opt-in")
        elif "System Chrome capture is disabled" not in (system_capture.stderr or ""):
            failures.append("capture --browser system failed for the wrong reason")
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
        shutil.rmtree(work, ignore_errors=True)

    print("\nbrowser policy benchmark")
    if failures:
        for failure in failures:
            print(f"  x {failure}")
        print(f"\nFAIL - {len(failures)} issue(s)")
        return 1
    print("  ok chrome alias uses bundled Chromium")
    print("  ok system Chrome is blocked by default")
    print("\nPASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
