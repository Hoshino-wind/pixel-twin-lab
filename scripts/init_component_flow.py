#!/usr/bin/env python3
"""Initialize a full pixel-twin componentization run."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from fnmatch import fnmatch
from pathlib import Path


IGNORED_SCAN_PARTS = {
    ".git",
    ".next",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "out",
    "outputs",
    "target",
    "work",
}

# Caps for the single project scan so huge repos stay fast.
SCAN_FILE_LIMIT = 20000
CSS_CONTENT_SCAN_LIMIT = 200


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "pixel-twin-run"


def is_relative_safe(path_value: str) -> bool:
    path = Path(path_value)
    return not path.is_absolute() and ".." not in path.parts


def load_package(project_dir: Path) -> dict:
    package_json = project_dir / "package.json"
    if not package_json.exists():
        return {}
    try:
        return json.loads(package_json.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def deps_from_package(package: dict) -> dict[str, str]:
    deps: dict[str, str] = {}
    for key in ("dependencies", "devDependencies", "peerDependencies"):
        deps.update(package.get(key, {}))
    return deps


def detect_package_manager(project_dir: Path) -> str:
    if (project_dir / "pnpm-lock.yaml").exists():
        return "pnpm"
    if (project_dir / "yarn.lock").exists():
        return "yarn"
    if (project_dir / "bun.lockb").exists() or (project_dir / "bun.lock").exists():
        return "bun"
    if (project_dir / "package-lock.json").exists():
        return "npm"
    if (project_dir / "package.json").exists():
        package = load_package(project_dir)
        manager = package.get("packageManager", "")
        if manager:
            return manager.split("@", 1)[0]
        return "npm"
    return "none"


def scan_files(project_dir: Path, limit: int = SCAN_FILE_LIMIT) -> list[Path]:
    """Walk the project once, pruning ignored and hidden directories."""
    found: list[Path] = []
    for root, dirs, files in os.walk(project_dir):
        dirs[:] = [d for d in dirs if d not in IGNORED_SCAN_PARTS and not d.startswith(".")]
        for name in files:
            found.append(Path(root) / name)
            if len(found) >= limit:
                return found
    return found


def any_file_matches(files: list[Path], *patterns: str) -> bool:
    return any(fnmatch(path.name, pattern) for path in files for pattern in patterns)


def css_declares_tailwind(files: list[Path]) -> bool:
    scanned = 0
    for path in files:
        if path.suffix not in (".css", ".scss", ".sass"):
            continue
        try:
            head = path.read_text(encoding="utf-8", errors="ignore")[:4096]
        except OSError:
            continue
        if "@tailwind" in head or '@import "tailwindcss' in head or "@import 'tailwindcss" in head:
            return True
        scanned += 1
        if scanned >= CSS_CONTENT_SCAN_LIMIT:
            return False
    return False


def detect_framework(project_dir: Path, deps: dict[str, str]) -> str:
    if "next" in deps:
        return "next-react"
    if "vite" in deps and "react" in deps:
        return "vite-react"
    if "vite" in deps and "vue" in deps:
        return "vite-vue"
    if "react" in deps:
        return "react"
    if "vue" in deps:
        return "vue"
    if "svelte" in deps or "@sveltejs/kit" in deps:
        return "svelte"
    if (project_dir / "pubspec.yaml").exists():
        return "flutter"
    if (project_dir / "Package.swift").exists():
        return "swift"
    if (project_dir / "package.json").exists():
        return "javascript"
    return "unknown"


def detect_style_system(files: list[Path], deps: dict[str, str]) -> dict[str, object]:
    systems: list[str] = []
    evidence: list[str] = []

    tailwind = "tailwindcss" in deps or any_file_matches(files, "tailwind.config.*") or css_declares_tailwind(files)
    if tailwind:
        systems.append("tailwind")
        evidence.append("tailwindcss dependency, tailwind config, or @tailwind directive")
    if any_file_matches(files, "*.module.css", "*.module.scss", "*.module.sass"):
        systems.append("css-modules")
        evidence.append("*.module.css/scss files")
    if "sass" in deps or any_file_matches(files, "*.scss", "*.sass"):
        systems.append("sass")
        evidence.append("sass dependency or files")
    if any_file_matches(files, "*.css"):
        systems.append("css")
        evidence.append("css files")
    if "styled-components" in deps:
        systems.append("styled-components")
        evidence.append("styled-components dependency")
    if "@emotion/react" in deps or "@emotion/styled" in deps:
        systems.append("emotion")
        evidence.append("emotion dependency")
    if any(path.name == "components.json" for path in files):
        systems.append("shadcn")
        evidence.append("components.json")

    defaulted = not systems
    if defaulted:
        systems.append("tailwind")
        evidence.append("default because no project style system was detected")

    primary = systems[0]
    if "tailwind" in systems:
        primary = "tailwind"
    elif "css-modules" in systems:
        primary = "css-modules"

    return {"primary": primary, "all": systems, "evidence": evidence, "defaulted": defaulted}


def detect_ui_libraries(deps: dict[str, str]) -> list[str]:
    candidates = {
        "antd": "antd",
        "@mui/material": "mui",
        "@chakra-ui/react": "chakra-ui",
        "@radix-ui/react-slot": "radix-ui",
        "lucide-react": "lucide-react",
        "framer-motion": "framer-motion",
        "recharts": "recharts",
        "echarts": "echarts",
        "@visx/visx": "visx",
    }
    return [label for dep, label in candidates.items() if dep in deps]


def detect_source_roots(project_dir: Path) -> list[str]:
    roots = []
    for name in ("src", "app", "pages", "components", "client", "frontend"):
        if (project_dir / name).exists():
            roots.append(name)
    return roots


def detect_routing(project_dir: Path, framework: str, deps: dict[str, str]) -> str:
    if framework == "next-react" and (project_dir / "app").exists():
        return "next-app-router"
    if framework == "next-react" and (project_dir / "pages").exists():
        return "next-pages-router"
    if "react-router-dom" in deps:
        return "react-router"
    if framework in ("vite-react", "react"):
        return "spa"
    return "unknown"


def inspect_project(project_dir: Path) -> dict[str, object]:
    package = load_package(project_dir)
    deps = deps_from_package(package)
    files = scan_files(project_dir)
    framework = detect_framework(project_dir, deps)
    style_system = detect_style_system(files, deps)
    defaults_applied = []
    if style_system["defaulted"]:
        defaults_applied.append("style: tailwind")

    return {
        "package_manager": detect_package_manager(project_dir),
        "framework": framework,
        "routing": detect_routing(project_dir, framework, deps),
        "style_system": style_system,
        "ui_libraries": detect_ui_libraries(deps),
        "source_roots": detect_source_roots(project_dir),
        "scripts": package.get("scripts", {}),
        "defaults_applied": defaults_applied,
        "implementation_rule": "Follow detected framework and style system. Use React + Tailwind only when no frontend framework/style system is detected.",
    }


def write_if_missing(path: Path, content: str) -> None:
    if not path.exists():
        path.write_text(content, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", required=True, help="Absolute path to source UI image")
    parser.add_argument("--project-dir", required=True, help="Absolute path to target project")
    parser.add_argument("--final-dir", required=True, help="Project-relative final source directory")
    parser.add_argument("--name", help="Run name slug; defaults to reference filename")
    parser.add_argument("--work-root", default="work/pixel-twin-lab", help="Project-relative intermediate root")
    parser.add_argument("--framework", help="Framework name; inferred when omitted")
    parser.add_argument("--threshold", type=int, default=70, help="Initial prepare_lab foreground threshold")
    parser.add_argument("--min-area", type=int, default=10000, help="Initial prepare_lab min component area")
    parser.add_argument("--max-slices", type=int, default=12, help="Initial prepare_lab max slices")
    parser.add_argument("--manifest", help="Slice manifest JSON passed through to prepare_lab (replaces auto detection)")
    parser.add_argument(
        "--no-cover-gaps",
        action="store_true",
        help="With --manifest, do not generate gap slices for uncovered canvas area",
    )
    args = parser.parse_args()

    reference = Path(args.reference).expanduser().resolve()
    project_dir = Path(args.project_dir).expanduser().resolve()
    if not reference.exists():
        raise SystemExit(f"Reference image does not exist: {reference}")
    if not project_dir.exists():
        raise SystemExit(f"Project directory does not exist: {project_dir}")
    if not is_relative_safe(args.final_dir):
        raise SystemExit("--final-dir must be project-relative and cannot contain '..'")
    if not is_relative_safe(args.work_root):
        raise SystemExit("--work-root must be project-relative and cannot contain '..'")

    run_name = slugify(args.name or reference.stem)
    work_root = project_dir / args.work_root
    run_dir = work_root / run_name
    lab_dir = run_dir / "lab"
    final_dir = project_dir / args.final_dir
    project_profile = inspect_project(project_dir)
    if args.framework:
        project_profile["framework"] = args.framework
        project_profile.setdefault("defaults_applied", []).append("framework override from --framework")
    framework = str(project_profile["framework"])
    skill_root = Path(__file__).resolve().parents[1]

    # final_dir is only recorded here; it is created when final code is written.
    run_dir.mkdir(parents=True, exist_ok=True)

    prepare_script = skill_root / "scripts" / "prepare_lab.py"
    prepare_args = [
        sys.executable,
        str(prepare_script),
        "--reference",
        str(reference),
        "--out-dir",
        str(lab_dir),
        "--threshold",
        str(args.threshold),
        "--min-area",
        str(args.min_area),
        "--max-slices",
        str(args.max_slices),
    ]
    if args.manifest:
        manifest = Path(args.manifest).expanduser().resolve()
        if not manifest.exists():
            raise SystemExit(f"Manifest file does not exist: {manifest}")
        prepare_args += ["--manifest", str(manifest)]
        if args.no_cover_gaps:
            prepare_args.append("--no-cover-gaps")
    subprocess.run(prepare_args, check=True)

    contract = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "reference": str(reference),
        "project_dir": str(project_dir),
        "work_root": str(work_root),
        "run_dir": str(run_dir),
        "lab_dir": str(lab_dir),
        "final_dir": str(final_dir),
        "final_dir_project_relative": args.final_dir,
        "framework": framework,
        "project_profile": project_profile,
        "status": "initialized",
        "rules": {
            "intermediate_artifacts": "Keep reference images, slices, captures, diffs, and QA logs in run_dir/lab.",
            "final_product": "Write production component code only under final_dir or existing project source files.",
            "project_native_componentization": "Inspect and follow the target project's framework, router, styling system, and component conventions before writing final code.",
            "default_stack": "Use React + Tailwind only when no frontend framework or style system is detectable.",
        },
    }
    (run_dir / "component-contract.json").write_text(json.dumps(contract, indent=2), encoding="utf-8")

    write_if_missing(
        run_dir / "component-map.md",
        "# Component Map\n\n"
        "## Project Profile\n\n"
        f"- Framework: {project_profile['framework']}\n"
        f"- Style system: {project_profile['style_system']['primary']}\n"
        f"- Routing: {project_profile['routing']}\n"
        f"- UI libraries: {', '.join(project_profile['ui_libraries']) or 'none detected'}\n\n"
        "## Regions\n\n"
        "- App shell:\n"
        "- Navigation/sidebar:\n"
        "- Top app bar:\n"
        "- Filters/actions:\n"
        "- Primary content:\n"
        "- Detail panel:\n"
        "- Charts/cards/media:\n"
        "- Interactions/state:\n",
    )
    write_if_missing(
        run_dir / "implementation-ledger.md",
        "# Implementation Ledger\n\n"
        "Record each componentization pass here.\n\n"
        "| Iteration | Final files changed | Capture | Mismatch (overall / worst regions) | Notes |\n"
        "| --- | --- | --- | --- | --- |\n",
    )

    print(json.dumps(contract, indent=2))


if __name__ == "__main__":
    main()
