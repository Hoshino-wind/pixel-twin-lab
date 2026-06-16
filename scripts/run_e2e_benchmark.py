#!/usr/bin/env python3
"""End-to-end benchmark harness: serve a fixed lab page, capture it in a real
browser, and assert the element/style contract against committed goldens.

Unlike the Python smoke (smoke_workflow.py, which exercises the pure-Python
pipeline with no browser), this is the real e2e: it starts an http server,
drives measure_dom_elements.cjs (Playwright + bundled-or-system Chrome) to read
getComputedStyle from the rendered DOM, runs verify_elements.py, and compares the
verification result to benchmark/<suite>/golden.json. Deterministic by design —
the asserted properties (computed font-size/weight/color/vertical-align of
inline-set styles, geometry) do not depend on font availability, so it passes on
any machine with a Chromium-family browser. Pixel goldens are deliberately NOT
asserted here (they are machine-dependent); use pixel_diff/fidelity_gate for that.

Exit codes: 0 all cases pass; 1 a case failed its golden; 2 environment error
(no browser, server failed, scripts missing).
"""
from __future__ import annotations

import argparse
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


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def check_case(case: dict, verification: dict) -> list[str]:
    """Return a list of golden-assertion failures (empty = case passed)."""
    fails: list[str] = []
    summary = verification.get("summary", {})
    got_pass = bool(summary.get("pass"))
    if "expect_pass" in case and got_pass != bool(case["expect_pass"]):
        fails.append(f"expected pass={case['expect_pass']}, got pass={got_pass} (ok {summary.get('ok')}/{summary.get('total')})")
    if case.get("expect_ok_equals_total") and summary.get("ok") != summary.get("total"):
        fails.append(f"expected ok==total, got {summary.get('ok')}/{summary.get('total')}")
    subs = case.get("expect_problem_substrings") or []
    if subs:
        blob = "\n".join(
            f"{el.get('id')}: {p}"
            for region in verification.get("regions", [])
            for el in region.get("elements", [])
            for p in (el.get("problems", []) + el.get("warnings", []))
        )
        for sub in subs:
            if sub not in blob:
                fails.append(f"expected a problem containing '{sub}', none found")
    return fails


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", default="style-contract", help="benchmark/<suite> directory name")
    parser.add_argument("--browser", default=None, help="bundled|system; default lets measure_dom_elements auto-detect")
    parser.add_argument("--keep", action="store_true", help="keep the temp work dir for inspection")
    args = parser.parse_args()

    suite_dir = ROOT / "benchmark" / args.suite
    golden_path = suite_dir / "golden.json"
    manifest_src = suite_dir / "element-manifest.json"
    config_src = suite_dir / "lab-config.json"
    for required in (golden_path, manifest_src, config_src):
        if not required.exists():
            print(f"environment error: missing {required}", file=sys.stderr)
            return 2

    golden = json.loads(golden_path.read_text(encoding="utf-8"))
    cases = golden.get("cases") or []

    port = free_port()
    server = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1", "--directory", str(ROOT / "benchmark")],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    work = Path(tempfile.mkdtemp(prefix="e2e-bench-"))
    results: list[tuple[str, list[str]]] = []
    try:
        if not wait_for_server(port):
            print("environment error: http server did not come up", file=sys.stderr)
            return 2
        for case in cases:
            name = case["name"]
            case_dir = work / name
            case_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy(manifest_src, case_dir / "element-manifest.json")
            shutil.copy(config_src, case_dir / "lab-config.json")
            url = f"http://127.0.0.1:{port}/{args.suite}/{case['page']}"

            measure_cmd = ["node", str(SCRIPTS / "measure_dom_elements.cjs"), "--url", url, "--out-dir", str(case_dir)]
            if args.browser:
                measure_cmd += ["--browser", args.browser]
            m = run(measure_cmd)
            if m.returncode != 0:
                low = (m.stderr or "").lower()
                if "playwright" in low or "chrome" in low or "chromium" in low or "browser" in low:
                    print("environment error: no usable browser for measure_dom_elements.cjs\n" + m.stderr, file=sys.stderr)
                    return 2
                results.append((name, [f"measure_dom_elements failed: {m.stderr.strip()[:200]}"]))
                continue

            v = run(["python3", str(SCRIPTS / "verify_elements.py"), "--out-dir", str(case_dir)])
            verification = json.loads((case_dir / "element-verification.json").read_text(encoding="utf-8"))
            results.append((name, check_case(case, verification)))
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
        if not args.keep:
            shutil.rmtree(work, ignore_errors=True)

    print(f"\ne2e benchmark: {args.suite}")
    ok = True
    for name, fails in results:
        if fails:
            ok = False
            print(f"  ✗ {name}")
            for f in fails:
                print(f"      - {f}")
        else:
            print(f"  ✓ {name}")
    if args.keep:
        print(f"  (work dir kept: {work})")
    print(f"\n{'PASS' if ok else 'FAIL'} — {sum(1 for _, f in results if not f)}/{len(results)} cases")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
