#!/usr/bin/env python3
"""Regression benchmark for blueprint validation-only contracts."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"


def run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def check_case(case: dict[str, Any], report: dict[str, Any]) -> list[str]:
    fails: list[str] = []
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    got_pass = bool(summary.get("pass"))
    if "expect_pass" in case and got_pass != bool(case["expect_pass"]):
        fails.append(f"expected pass={case['expect_pass']}, got pass={got_pass}")
    messages = "\n".join(str(issue.get("message") or "") for issue in report.get("issues") or [])
    for sub in case.get("expect_issue_substrings") or []:
        if sub not in messages:
            fails.append(f"expected a validation issue containing '{sub}', none found")
    return fails


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", default="taxonomy-contract", help="benchmark/<suite> directory name")
    parser.add_argument("--keep", action="store_true", help="keep the temp work dir")
    args = parser.parse_args()

    suite = ROOT / "benchmark" / args.suite
    golden_path = suite / "golden.json"
    if not golden_path.exists():
        print(f"environment error: missing {golden_path}", file=sys.stderr)
        return 2
    cases = (load_json(golden_path).get("cases") or [])
    work = Path(tempfile.mkdtemp(prefix="blueprint-validation-bench-"))
    results: list[tuple[str, list[str]]] = []
    try:
        for case in cases:
            name = str(case.get("name") or "")
            source = suite / str(case.get("file") or "")
            if not name or not source.exists():
                results.append((name or "<unnamed>", [f"missing fixture {source}"]))
                continue
            case_dir = work / name
            case_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy(source, case_dir / "ui-blueprint.json")
            for extra in case.get("copy_files") or []:
                extra_source = suite / str(extra)
                if not extra_source.exists():
                    results.append((name, [f"missing fixture {extra_source}"]))
                    continue
                shutil.copy(extra_source, case_dir / extra_source.name)
            cmd = ["python3", str(SCRIPTS / "validate_blueprint.py"), "--out-dir", str(case_dir)]
            if case.get("allow_unmeasured"):
                cmd.append("--allow-unmeasured")
            validation = run(cmd, ROOT)
            report_path = case_dir / "blueprint-validation.json"
            if not report_path.exists():
                results.append((name, [f"validate_blueprint.py produced no report (exit {validation.returncode})"]))
                continue
            results.append((name, check_case(case, load_json(report_path))))
    finally:
        if not args.keep:
            shutil.rmtree(work, ignore_errors=True)

    print(f"\nblueprint validation benchmark: {args.suite}")
    ok = True
    for name, fails in results:
        if fails:
            ok = False
            print(f"  ✗ {name}")
            for fail in fails:
                print(f"      - {fail}")
        else:
            print(f"  ✓ {name}")
    if args.keep:
        print(f"  (work dir kept: {work})")
    print(f"\n{'PASS' if ok else 'FAIL'} - {sum(1 for _, f in results if not f)}/{len(results)} cases")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
