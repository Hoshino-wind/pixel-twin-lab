#!/usr/bin/env python3
"""Inspect an existing frontend project and guard an in-place UI edit.

The target project's Git diff is the product. Runtime state, screenshots, logs, and
pixel diffs live under the system temporary directory and are removed by default.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import math
import os
import re
import signal
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from PIL import Image
except ImportError:  # pragma: no cover - exercised by the CLI environment
    Image = None


VERSION = 2
SESSION_ROOT = Path(tempfile.gettempdir()) / "pixel-twin-ui"
SCRIPT_DIR = Path(__file__).resolve().parent
BROWSER_HELPER = SCRIPT_DIR / "browser_capture.cjs"
MAX_VISUAL_SIDE = 10_000
MAX_VISUAL_PIXELS = 9_000_000
MAX_REPAIR_SOURCE_FILES = 500
MAX_REPAIR_SOURCE_FILE_BYTES = 512 * 1024
MAX_REPAIR_SOURCE_TOTAL_BYTES = 8 * 1024 * 1024

IGNORED_DIRS = {
    ".git",
    ".next",
    ".nuxt",
    ".svelte-kit",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "out",
    "target",
}

DEFAULT_UI_ROOTS = (
    "src/app",
    "src/pages",
    "src/components",
    "src/features",
    "src/views",
    "src/screens",
    "src/styles",
    "app",
    "pages",
    "components",
    "features",
    "views",
    "screens",
    "styles",
)
DEFAULT_ASSET_ROOTS = ("public", "src/assets", "assets")

VIEW_EXTENSIONS = {".astro", ".html", ".jsx", ".svelte", ".tsx", ".vue"}
STYLE_EXTENSIONS = {".css", ".less", ".pcss", ".sass", ".scss", ".styl"}
ASSET_EXTENSIONS = {
    ".avif",
    ".gif",
    ".ico",
    ".jpeg",
    ".jpg",
    ".otf",
    ".png",
    ".svg",
    ".ttf",
    ".webp",
    ".woff",
    ".woff2",
}

HARD_DENY_PARTS = {
    ".github",
    "backend",
    "contracts",
    "database",
    "db",
    "domain",
    "functions",
    "jobs",
    "migrations",
    "repositories",
    "repository",
    "schemas",
    "server",
    "servers",
    "usecase",
    "workers",
}
NON_OVERRIDE_BEHAVIOR_PARTS = {"api", "store", "stores"}
HARD_DENY_NAMES = {
    "bun.lock",
    "bun.lockb",
    "middleware.js",
    "middleware.ts",
    "package-lock.json",
    "package.json",
    "pnpm-lock.yaml",
    "yarn.lock",
}

RISK_PATTERNS = {
    "network request": re.compile(r"\b(?:fetch\s*\(|axios\s*\.|useQuery\s*\(|useMutation\s*\()"),
    "API path": re.compile(r"[\"'`]\/api\/"),
    "server action": re.compile(r"[\"']use server[\"']|\bcreateServerAction\b"),
    "persistence": re.compile(r"\b(?:localStorage|sessionStorage|indexedDB|document\.cookie)\b"),
    "database client": re.compile(r"\b(?:prisma|drizzle|mongoose|sequelize)\b", re.IGNORECASE),
    "business import": re.compile(
        r"\b(?:from|require\s*\()[^\n]*"
        r"(?:\/api(?:\/|[\"'])|\/stores?(?:\/|[\"'])|"
        r"\/services?(?:\/|[\"'])|\/repositories?(?:\/|[\"']))"
    ),
}

SVG_RISK_PATTERNS = {
    "script": re.compile(r"<\s*script\b", re.IGNORECASE),
    "event handler": re.compile(r"\son[a-z]+\s*=", re.IGNORECASE),
    "javascript URL": re.compile(r"javascript\s*:", re.IGNORECASE),
    "external resource": re.compile(r"(?:href|xlink:href)\s*=\s*[\"']https?://", re.IGNORECASE),
}

MAX_TEXT_BYTES = 2 * 1024 * 1024
MAX_ASSET_BYTES = 5 * 1024 * 1024
MAX_ASSET_TOTAL_BYTES = 20 * 1024 * 1024
PROTECTED_LOCAL_DIRS = {".pixel-twin", "captures", "diffs", "outputs", "work"}


class PixelTwinError(RuntimeError):
    """Expected user-facing CLI error."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def run_process(
    command: list[str],
    *,
    cwd: Path | None = None,
    timeout: int = 30,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    popen_options: dict[str, Any] = {}
    if os.name == "nt":  # pragma: no cover - Windows-specific process control
        popen_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_options["start_new_session"] = True
    try:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            **popen_options,
        )
    except FileNotFoundError as error:
        raise PixelTwinError(f"Command not found: {command[0]}") from error
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as error:
        if os.name == "nt":  # pragma: no cover - Windows-specific process control
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                check=False,
            )
        else:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
            # The process-group leader may exit while a descendant ignores
            # SIGTERM. Always make one final group-wide kill attempt.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        try:
            process.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            # A descendant may have escaped the process group while retaining
            # an inherited pipe. Do not let that make the guard hang forever.
            for pipe in (process.stdout, process.stderr):
                if pipe is not None:
                    pipe.close()
            try:
                process.kill()
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
        raise PixelTwinError(f"Command timed out after {timeout}s: {' '.join(command)}") from error
    result = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise PixelTwinError(detail or f"Command failed: {' '.join(command)}")
    return result


def resolve_project(value: str) -> Path:
    project = Path(value).expanduser().resolve()
    if not project.is_dir():
        raise PixelTwinError(f"Project directory does not exist: {project}")
    return project


def load_package(project: Path) -> dict[str, Any]:
    package_path = project / "package.json"
    if not package_path.exists():
        return {}
    try:
        data = json.loads(package_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise PixelTwinError(f"Invalid package.json: {error}") from error
    return data if isinstance(data, dict) else {}


def package_dependencies(package: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for key in ("dependencies", "devDependencies", "peerDependencies"):
        value = package.get(key)
        if isinstance(value, dict):
            result.update({str(name): str(version) for name, version in value.items()})
    return result


def detect_package_manager(project: Path, package: dict[str, Any]) -> str:
    if (project / "pnpm-lock.yaml").exists():
        return "pnpm"
    if (project / "yarn.lock").exists():
        return "yarn"
    if (project / "bun.lock").exists() or (project / "bun.lockb").exists():
        return "bun"
    if (project / "package-lock.json").exists():
        return "npm"
    declared = str(package.get("packageManager") or "").split("@", 1)[0]
    return declared or ("npm" if package else "none")


def detect_framework(project: Path, dependencies: dict[str, str]) -> str:
    if "next" in dependencies:
        return "next-react"
    if "nuxt" in dependencies:
        return "nuxt-vue"
    if "@sveltejs/kit" in dependencies:
        return "sveltekit"
    if "vite" in dependencies and "react" in dependencies:
        return "vite-react"
    if "vite" in dependencies and "vue" in dependencies:
        return "vite-vue"
    if "react" in dependencies:
        return "react"
    if "vue" in dependencies:
        return "vue"
    if "svelte" in dependencies:
        return "svelte"
    if package_exists(project):
        return "javascript"
    return "unknown"


def package_exists(project: Path) -> bool:
    return (project / "package.json").exists()


def detect_styles(project: Path, dependencies: dict[str, str]) -> list[str]:
    styles: list[str] = []
    if "tailwindcss" in dependencies or any(project.glob("tailwind.config.*")):
        styles.append("tailwind")
    if "styled-components" in dependencies:
        styles.append("styled-components")
    if "@emotion/react" in dependencies or "@emotion/styled" in dependencies:
        styles.append("emotion")
    if "sass" in dependencies:
        styles.append("sass")
    search_roots = [project / "src", project / "app", project / "pages", project / "components"]
    sample_count = 0
    has_css_modules = False
    has_css = False
    for root in search_roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if sample_count >= 4000:
                break
            if not path.is_file():
                continue
            sample_count += 1
            if ".module." in path.name and path.suffix in STYLE_EXTENSIONS:
                has_css_modules = True
            if path.suffix in STYLE_EXTENSIONS:
                has_css = True
    if has_css_modules:
        styles.append("css-modules")
    if has_css:
        styles.append("css")
    return list(dict.fromkeys(styles))


def detect_ui_libraries(dependencies: dict[str, str]) -> list[str]:
    candidates = {
        "antd": "antd",
        "@mui/material": "mui",
        "@chakra-ui/react": "chakra-ui",
        "@radix-ui/react-slot": "radix-ui",
        "lucide-react": "lucide",
        "framer-motion": "framer-motion",
        "recharts": "recharts",
        "echarts": "echarts",
    }
    return [label for dependency, label in candidates.items() if dependency in dependencies]


def existing_roots(project: Path, candidates: tuple[str, ...]) -> list[str]:
    return [candidate for candidate in candidates if (project / candidate).is_dir()]


def git_output(project: Path, args: list[str], *, timeout: int = 30) -> bytes:
    try:
        result = subprocess.run(
            ["git", "-C", str(project), *args],
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as error:
        raise PixelTwinError(f"Unable to run Git: {error}") from error
    if result.returncode != 0:
        raise PixelTwinError(result.stderr.decode("utf-8", errors="replace").strip() or "Git command failed")
    return result.stdout


def is_git_repository(project: Path) -> bool:
    result = subprocess.run(
        ["git", "-C", str(project), "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def git_root(project: Path) -> Path:
    output = git_output(project, ["rev-parse", "--show-toplevel"])
    return Path(os.fsdecode(output).strip()).resolve()


def inspect_project(project: Path) -> dict[str, Any]:
    package = load_package(project)
    dependencies = package_dependencies(package)
    scripts = package.get("scripts") if isinstance(package.get("scripts"), dict) else {}
    automatic_checks = [name for name in ("format:check", "lint", "typecheck") if name in scripts]
    optional_checks = ["build"] if "build" in scripts else []
    dirty_count: int | None = None
    if is_git_repository(project):
        status = git_output(project, ["status", "--porcelain=v1", "--untracked-files=all"])
        dirty_count = len([line for line in status.splitlines() if line])
    return {
        "project": str(project),
        "framework": detect_framework(project, dependencies),
        "package_manager": detect_package_manager(project, package),
        "styles": detect_styles(project, dependencies),
        "ui_libraries": detect_ui_libraries(dependencies),
        "ui_roots": existing_roots(project, DEFAULT_UI_ROOTS),
        "asset_roots": existing_roots(project, DEFAULT_ASSET_ROOTS),
        "checks": automatic_checks,
        "optional_checks": optional_checks,
        "preexisting_changes": dirty_count,
    }


def print_inspection(data: dict[str, Any]) -> None:
    def joined(key: str) -> str:
        values = data.get(key) or []
        return ", ".join(values) if values else "none detected"

    print(f"project: {data['project']}")
    print(f"stack: {data['framework']} · {data['package_manager']}")
    print(f"styles: {joined('styles')}")
    print(f"ui libraries: {joined('ui_libraries')}")
    print(f"ui roots: {joined('ui_roots')}")
    print(f"asset roots: {joined('asset_roots')}")
    print(f"checks: {joined('checks')}")
    print(f"optional checks: {joined('optional_checks')}")
    dirty = data.get("preexisting_changes")
    print(f"pre-existing changes: {dirty if dirty is not None else 'not a Git repository'}")


def parse_git_paths(raw: bytes) -> list[str]:
    return [os.fsdecode(item) for item in raw.split(b"\0") if item]


def tracked_and_untracked(project: Path) -> tuple[set[str], set[str]]:
    tracked = set(parse_git_paths(git_output(project, ["ls-files", "-z"])))
    untracked = set(
        parse_git_paths(git_output(project, ["ls-files", "--others", "--exclude-standard", "-z"]))
    )
    return tracked, untracked


def status_paths(project: Path) -> set[str]:
    raw = git_output(project, ["status", "--porcelain=v1", "-z", "--untracked-files=all"])
    items = raw.split(b"\0")
    paths: set[str] = set()
    index = 0
    while index < len(items):
        entry = items[index]
        index += 1
        if not entry:
            continue
        text = os.fsdecode(entry)
        if len(text) < 4:
            continue
        status = text[:2]
        paths.add(text[3:])
        if "R" in status or "C" in status:
            if index < len(items) and items[index]:
                paths.add(os.fsdecode(items[index]))
                index += 1
    return paths


def index_digest(project: Path) -> str:
    return hashlib.sha256(git_output(project, ["ls-files", "--stage", "-z"])).hexdigest()


def file_fingerprint(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        return {"kind": "symlink", "target": os.readlink(path)}
    if not path.exists():
        return {"kind": "missing"}
    if path.is_dir():
        head = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
        status_result = subprocess.run(
            ["git", "-C", str(path), "status", "--porcelain=v1", "--untracked-files=all"],
            capture_output=True,
            text=True,
            check=False,
        )
        if head.returncode == 0 and status_result.returncode == 0:
            state = hashlib.sha256()
            for args in (
                ["status", "--porcelain=v1", "-z", "--untracked-files=all"],
                ["diff", "--binary", "--no-ext-diff", "--no-textconv"],
                ["diff", "--cached", "--binary", "--no-ext-diff", "--no-textconv"],
            ):
                payload = git_output(path, args)
                state.update(len(payload).to_bytes(8, "big"))
                state.update(payload)
            untracked = parse_git_paths(
                git_output(path, ["ls-files", "--others", "--exclude-standard", "-z"])
            )
            for relative in sorted(untracked):
                encoded = relative.encode("utf-8", errors="surrogateescape")
                state.update(len(encoded).to_bytes(8, "big"))
                state.update(encoded)
                fingerprint = json.dumps(
                    file_fingerprint(path / relative),
                    ensure_ascii=False,
                    sort_keys=True,
                ).encode("utf-8", errors="surrogateescape")
                state.update(len(fingerprint).to_bytes(8, "big"))
                state.update(fingerprint)
            return {
                "kind": "submodule",
                "head": head.stdout.strip(),
                "state": state.hexdigest(),
            }
        return {"kind": "other"}
    if not path.is_file():
        return {"kind": "other"}
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    stat = path.stat()
    return {
        "kind": "file",
        "sha256": digest.hexdigest(),
        "size": stat.st_size,
        "mode": stat.st_mode & 0o777,
    }


def snapshot_files(project: Path, paths: set[str]) -> dict[str, dict[str, Any]]:
    return {path: file_fingerprint(project / path) for path in sorted(paths)}


def safe_session_id(value: str) -> str:
    if not re.fullmatch(r"[a-f0-9]{16}", value):
        raise PixelTwinError("Invalid session id")
    return value


def session_directory(session_id: str) -> Path:
    return SESSION_ROOT / safe_session_id(session_id)


def session_manifest(session_id: str) -> Path:
    return session_directory(session_id) / "session.json"


def ensure_session_root() -> None:
    if os.path.lexists(SESSION_ROOT):
        metadata = os.lstat(SESSION_ROOT)
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise PixelTwinError(f"Unsafe temporary session root: {SESSION_ROOT}")
        if hasattr(os, "getuid") and metadata.st_uid != os.getuid():
            raise PixelTwinError(f"Temporary session root is not owned by this user: {SESSION_ROOT}")
        os.chmod(SESSION_ROOT, 0o700)
        return
    SESSION_ROOT.mkdir(mode=0o700)


def cleanup_stale_sessions(max_age_hours: int = 24) -> None:
    if not SESSION_ROOT.exists():
        return
    cutoff = time.time() - max_age_hours * 3600
    for path in SESSION_ROOT.iterdir():
        try:
            if (
                re.fullmatch(r"[a-f0-9]{16}", path.name)
                and not path.is_symlink()
                and path.is_dir()
                and path.stat().st_mtime < cutoff
            ):
                shutil.rmtree(path, ignore_errors=True)
        except OSError:
            continue


def directory_fingerprint(path: Path) -> dict[str, Any]:
    """Hash a protected directory tree without following symbolic links."""
    digest = hashlib.sha256()
    entries = 0
    total_bytes = 0

    def add(value: bytes) -> None:
        digest.update(len(value).to_bytes(8, "big"))
        digest.update(value)

    try:
        for root, dirs, files in os.walk(path, topdown=True, followlinks=False):
            dirs.sort()
            files.sort()
            root_path = Path(root)
            kept_dirs: list[str] = []
            for name in dirs:
                child = root_path / name
                relative = child.relative_to(path).as_posix()
                metadata = child.lstat()
                entries += 1
                if child.is_symlink():
                    add(b"L")
                    add(os.fsencode(relative))
                    add(os.fsencode(os.readlink(child)))
                else:
                    add(b"D")
                    add(os.fsencode(relative))
                    add(str(metadata.st_mode & 0o777).encode())
                    kept_dirs.append(name)
            dirs[:] = kept_dirs
            for name in files:
                child = root_path / name
                relative = child.relative_to(path).as_posix()
                metadata = child.lstat()
                entries += 1
                if child.is_symlink():
                    add(b"L")
                    add(os.fsencode(relative))
                    add(os.fsencode(os.readlink(child)))
                    continue
                if not child.is_file():
                    add(b"O")
                    add(os.fsencode(relative))
                    add(str(metadata.st_mode).encode())
                    continue
                add(b"F")
                add(os.fsencode(relative))
                add(str(metadata.st_mode & 0o777).encode())
                add(str(metadata.st_size).encode())
                total_bytes += metadata.st_size
                with child.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
    except OSError as error:
        raise PixelTwinError(f"Unable to fingerprint protected directory {path}: {error}") from error

    return {
        "kind": "protected-dir",
        "sha256": digest.hexdigest(),
        "entries": entries,
        "bytes": total_bytes,
    }


def protected_local_state(repository: Path) -> dict[str, dict[str, Any]]:
    """Fingerprint ignored secrets and Pixel Twin-style artifact locations."""
    result: dict[str, dict[str, Any]] = {}
    for root, dirs, files in os.walk(repository):
        root_path = Path(root)
        relative_root = root_path.relative_to(repository)
        kept_dirs: list[str] = []
        for name in dirs:
            if name in IGNORED_DIRS:
                continue
            path = root_path / name
            relative = (relative_root / name).as_posix()
            if name.lower() in PROTECTED_LOCAL_DIRS:
                if path.is_symlink():
                    result[relative] = {"kind": "symlink", "target": os.readlink(path)}
                    continue
                result[relative] = directory_fingerprint(path)
                continue
            kept_dirs.append(name)
        dirs[:] = kept_dirs
        for name in files:
            if name.startswith(".env"):
                path = root_path / name
                result[(relative_root / name).as_posix()] = file_fingerprint(path)
    return result


def load_session(session_id: str) -> dict[str, Any]:
    manifest = session_manifest(session_id)
    if not manifest.exists():
        raise PixelTwinError(f"Unknown or expired session: {session_id}")
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise PixelTwinError(f"Corrupt temporary session: {session_id}") from error
    if data.get("version") != VERSION:
        raise PixelTwinError("Session version is not supported; start a new session")
    return data


def copy_baseline_file(project: Path, session_dir: Path, relative: str) -> None:
    source = project / relative
    if source.is_symlink() or not source.is_file():
        return
    if source.stat().st_size > MAX_TEXT_BYTES:
        return
    head = source.read_bytes()[:4096]
    if b"\0" in head:
        return
    target = session_dir / "baseline" / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target, follow_symlinks=False)


def begin_session(args: argparse.Namespace) -> int:
    project = resolve_project(args.project)
    if not is_git_repository(project):
        raise PixelTwinError("UI edit sessions require a Git repository")
    repository = git_root(project)
    project_prefix = project.relative_to(repository).as_posix()
    if project_prefix == ".":
        project_prefix = ""
    ensure_session_root()
    cleanup_stale_sessions()

    inspection = inspect_project(project)
    def repository_relative(value: str, label: str) -> str:
        local = policy_path(value, label)
        return f"{project_prefix}/{local}" if project_prefix else local

    ui_candidates = list(inspection["ui_roots"]) + list(args.ui_root or [])
    asset_candidates = list(inspection["asset_roots"]) + list(args.asset_root or [])
    ui_roots = [repository_relative(value, "UI root") for value in ui_candidates]
    asset_roots = [repository_relative(value, "asset root") for value in asset_candidates]
    editable = [repository_relative(value, "editable path") for value in (args.editable or [])]
    if not ui_roots and not args.editable:
        raise PixelTwinError("No UI roots detected. Pass --ui-root or --editable explicitly")

    tracked, untracked = tracked_and_untracked(repository)
    initial_dirty = status_paths(repository)
    all_paths = tracked | untracked
    session_id = os.urandom(8).hex()
    directory = session_directory(session_id)
    directory.mkdir(parents=True, mode=0o700)
    (directory / "scratch").mkdir(mode=0o700)

    for relative in initial_dirty:
        copy_baseline_file(repository, directory, relative)

    session = {
        "version": VERSION,
        "session_id": session_id,
        "project": str(project),
        "repository": str(repository),
        "started_at": utc_now(),
        "ui_roots": sorted(set(ui_roots)),
        "asset_roots": sorted(set(asset_roots)),
        "editable": sorted(set(editable)),
        "initial_dirty": sorted(initial_dirty),
        "initial_untracked": sorted(untracked),
        "index_digest": index_digest(repository),
        "files": snapshot_files(repository, all_paths),
        "protected_local": protected_local_state(repository),
    }
    try:
        visual_baseline = create_visual_baseline(args, project, directory)
        if visual_baseline is not None:
            attach_source_hints(visual_baseline["report"], repository, session)
            session["visual_baseline"] = visual_baseline
        atomic_write_json(directory / "session.json", session)
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        raise

    output = {
        "session": session_id,
        "project": str(project),
        "repository": str(repository),
        "temporary": str(directory),
        "preexisting_changes": len(initial_dirty),
        "ui_roots": session["ui_roots"],
        "asset_roots": session["asset_roots"],
    }
    if session.get("visual_baseline"):
        output["visual"] = public_visual_report(session["visual_baseline"]["report"])
    if args.json:
        print(json.dumps(output, ensure_ascii=False))
    else:
        print(f"session: {session_id}")
        print(f"temporary: {directory}")
        print(f"protected pre-existing changes: {len(initial_dirty)}")
        if session.get("visual_baseline"):
            print_visual_summary(session["visual_baseline"]["report"], prefix="baseline")
    return 0


def policy_path(value: str, label: str) -> str:
    path = Path(value)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise PixelTwinError(f"{label} must be a project-relative path: {value}")
    if any(character in value for character in "*?["):
        raise PixelTwinError(f"{label} cannot contain wildcards: {value}")
    normalized = path.as_posix().strip("/")
    if not normalized or normalized == ".":
        raise PixelTwinError(f"{label} cannot be the whole project")
    return normalized


def path_within(relative: str, roots: list[str]) -> bool:
    path = Path(relative).as_posix().strip("/")
    return any(path == root.strip("/") or path.startswith(root.strip("/") + "/") for root in roots)


def is_explicitly_editable(relative: str, paths: list[str]) -> bool:
    return relative in paths


def hard_path_violation(relative: str) -> str | None:
    path = Path(relative)
    parts = {part.lower() for part in path.parts}
    name = path.name.lower()
    if parts & HARD_DENY_PARTS:
        return "protected business/backend path"
    if parts & NON_OVERRIDE_BEHAVIOR_PARTS and path.suffix.lower() not in VIEW_EXTENSIONS | STYLE_EXTENSIONS | ASSET_EXTENSIONS:
        return "protected behavior path"
    if name in HARD_DENY_NAMES or name.startswith(".env"):
        return "protected project/configuration file"
    if name.startswith("route.") or ".server." in name or name.startswith("schema."):
        return "protected route/server/schema file"
    if name.endswith((".sql", ".graphql", ".gql")):
        return "protected data contract file"
    test_names = (".test.ts", ".test.tsx", ".spec.ts", ".spec.tsx")
    if parts & {"test", "tests", "__tests__"} or name.endswith(test_names):
        return "test changes are outside the default UI-only scope"
    return None


def classify_path(relative: str, session: dict[str, Any]) -> tuple[bool, str, str]:
    hard = hard_path_violation(relative)
    if hard:
        return False, hard, "protected"
    path = Path(relative)
    suffix = path.suffix.lower()
    editable = session.get("editable") or []
    in_ui = path_within(relative, session.get("ui_roots") or [])
    in_assets = path_within(relative, session.get("asset_roots") or [])
    explicitly_editable = is_explicitly_editable(relative, editable)

    if suffix in ASSET_EXTENSIONS:
        if in_assets or explicitly_editable:
            return True, "asset", "asset"
        return False, "asset outside an asset root", "asset"
    if suffix in VIEW_EXTENSIONS or suffix in STYLE_EXTENSIONS:
        if in_ui or explicitly_editable:
            return True, "UI source", "ui"
        return False, "UI source outside an allowed UI root", "ui"
    if suffix in {".js", ".mjs", ".cjs", ".ts", ".json"} and explicitly_editable:
        return True, "explicitly editable UI support file", "ui"
    return False, "file type is not UI-only; use --editable for a reviewed support file", "unknown"


def read_baseline_bytes(project: Path, session_dir: Path, session: dict[str, Any], relative: str) -> bytes:
    if relative in set(session.get("initial_dirty") or []) or relative in set(session.get("initial_untracked") or []):
        backup = session_dir / "baseline" / relative
        return backup.read_bytes() if backup.is_file() else b""
    result = subprocess.run(
        ["git", "-C", str(project), "show", f":{relative}"],
        capture_output=True,
        check=False,
    )
    return result.stdout if result.returncode == 0 else b""


def changed_lines(before: bytes, after: bytes) -> list[str] | None:
    if len(before) > MAX_TEXT_BYTES or len(after) > MAX_TEXT_BYTES:
        return None
    if b"\0" in before[:4096] or b"\0" in after[:4096]:
        return []
    old = before.decode("utf-8", errors="replace").splitlines()
    new = after.decode("utf-8", errors="replace").splitlines()
    lines: list[str] = []
    for line in difflib.unified_diff(old, new, n=0):
        if line.startswith(("+++", "---", "@@")):
            continue
        if line.startswith(("+", "-")):
            lines.append(line[1:])
    return lines


def audit_session(session_id: str) -> dict[str, Any]:
    session = load_session(session_id)
    directory = session_directory(session_id)
    project = resolve_project(session["project"])
    repository = resolve_project(session.get("repository") or session["project"])
    tracked, untracked = tracked_and_untracked(repository)
    current_paths = tracked | untracked
    current = snapshot_files(repository, current_paths | set(session["files"]))
    baseline = session["files"]
    changed = sorted(path for path in set(baseline) | set(current) if baseline.get(path) != current.get(path))
    violations: list[dict[str, str]] = []
    accepted: list[dict[str, str]] = []

    baseline_local = session.get("protected_local") or {}
    current_local = protected_local_state(repository)
    for relative in sorted(set(baseline_local) | set(current_local)):
        if baseline_local.get(relative) != current_local.get(relative):
            violations.append({
                "path": relative,
                "reason": "ignored secret or project-local diagnostic changed during the UI edit",
            })

    if index_digest(repository) != session["index_digest"]:
        violations.append({"path": "<git-index>", "reason": "staging state changed during the UI edit session"})

    initial_dirty = set(session.get("initial_dirty") or [])
    editable = session.get("editable") or []
    asset_total = 0

    for relative in changed:
        project_path = repository / relative
        try:
            resolved_parent = project_path.parent.resolve()
            resolved_parent.relative_to(repository)
        except ValueError:
            violations.append({"path": relative, "reason": "path escapes the project"})
            continue

        if relative in initial_dirty and not is_explicitly_editable(relative, editable):
            violations.append({
                "path": relative,
                "reason": "pre-existing user change was modified; pass --editable at begin only when intentional",
            })
            continue

        allowed, reason, kind = classify_path(relative, session)
        if not allowed:
            violations.append({"path": relative, "reason": reason})
            continue
        baseline_kind = (baseline.get(relative) or {}).get("kind")
        current_kind = (current.get(relative) or {}).get("kind")
        if baseline_kind in {"symlink", "other"} or current_kind in {"symlink", "other"}:
            violations.append({"path": relative, "reason": "symlink/submodule/non-file changes are not allowed"})
            continue

        if kind == "asset" and project_path.exists():
            size = project_path.stat().st_size
            asset_total += size
            if size > MAX_ASSET_BYTES:
                violations.append({"path": relative, "reason": "asset exceeds 5 MiB"})
                continue
            if project_path.suffix.lower() == ".svg":
                text = project_path.read_text(encoding="utf-8", errors="replace")
                svg_hits = [label for label, pattern in SVG_RISK_PATTERNS.items() if pattern.search(text)]
                if svg_hits:
                    violations.append({"path": relative, "reason": "unsafe SVG: " + ", ".join(svg_hits)})
                    continue

        if kind == "ui":
            before = read_baseline_bytes(repository, directory, session, relative)
            after = project_path.read_bytes() if project_path.is_file() else b""
            delta = changed_lines(before, after)
            if delta is None:
                violations.append({"path": relative, "reason": "UI source exceeds the 2 MiB review limit"})
                continue
            joined = "\n".join(delta)
            risks = [label for label, pattern in RISK_PATTERNS.items() if pattern.search(joined)]
            if risks:
                violations.append({"path": relative, "reason": "business behavior delta: " + ", ".join(risks)})
                continue

        action = "deleted" if not project_path.exists() else ("created" if relative not in baseline else "modified")
        accepted.append({"path": relative, "action": action})

    if asset_total > MAX_ASSET_TOTAL_BYTES:
        violations.append({"path": "<assets>", "reason": "new/changed assets exceed 20 MiB total"})

    return {
        "pass": not violations,
        "session": session_id,
        "project": str(project),
        "repository": str(repository),
        "changes": accepted,
        "violations": violations,
    }


def command_for_script(manager: str, script: str) -> list[str]:
    if manager == "npm":
        return ["npm", "run", script]
    if manager == "pnpm":
        return ["pnpm", "run", script]
    if manager == "yarn":
        return ["yarn", script]
    if manager == "bun":
        return ["bun", "run", script]
    raise PixelTwinError("No supported package manager was detected")


def select_checks(project: Path, requested: list[str]) -> list[str]:
    package = load_package(project)
    scripts = package.get("scripts") if isinstance(package.get("scripts"), dict) else {}
    if requested == ["none"]:
        return []
    selected: list[str] = []
    if "none" in requested:
        raise PixelTwinError("--run none cannot be combined with other checks")
    values = ["auto", *requested]
    for value in values:
        if value == "auto":
            for name in ("format:check", "lint", "typecheck"):
                if name in scripts and name not in selected:
                    selected.append(name)
        elif value not in scripts:
            raise PixelTwinError(f"package.json has no script named {value!r}")
        elif value not in selected:
            selected.append(value)
    return selected


def tail_output(result: subprocess.CompletedProcess[str], lines: int = 20) -> list[str]:
    combined = "\n".join(part for part in (result.stdout, result.stderr) if part)
    return combined.splitlines()[-lines:]


def run_project_checks(project: Path, requested: list[str], timeout: int) -> list[dict[str, Any]]:
    package = load_package(project)
    manager = detect_package_manager(project, package)
    results: list[dict[str, Any]] = []
    for script in select_checks(project, requested):
        command = command_for_script(manager, script)
        result = run_process(command, cwd=project, timeout=timeout)
        item: dict[str, Any] = {"name": script, "pass": result.returncode == 0}
        if result.returncode != 0:
            item["output_tail"] = tail_output(result)
        results.append(item)
        if result.returncode != 0:
            break
    return results


def validate_viewport_size(width: int, height: int, label: str = "Viewport") -> tuple[int, int]:
    if not (100 <= width <= MAX_VISUAL_SIDE and 100 <= height <= MAX_VISUAL_SIDE):
        raise PixelTwinError(
            f"{label} must be between 100 and {MAX_VISUAL_SIDE} pixels per side"
        )
    if width * height > MAX_VISUAL_PIXELS:
        raise PixelTwinError(f"{label} must not exceed {MAX_VISUAL_PIXELS} total pixels")
    return width, height


def parse_viewport(value: str | None, fallback: tuple[int, int]) -> tuple[int, int]:
    if not value:
        return validate_viewport_size(*fallback)
    match = re.fullmatch(r"(\d+)x(\d+)", value.lower())
    if not match:
        raise PixelTwinError("Viewport must look like 1440x900")
    return validate_viewport_size(int(match.group(1)), int(match.group(2)))


def compare_images(
    reference_path: Path,
    actual_path: Path,
    tolerance: int,
    *,
    dynamic_regions: list[dict[str, Any]] | None = None,
    temporal_path: Path | None = None,
    dom_index: dict[str, Any] | None = None,
    repair_exclusions: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], Any]:
    try:
        try:
            from visual_compare import analyze_images
        except ImportError:
            from scripts.visual_compare import analyze_images
        return analyze_images(
            reference_path,
            actual_path,
            tolerance=tolerance,
            max_hotspots=3,
            dynamic_regions=dynamic_regions,
            temporal_path=temporal_path,
            dom_index=dom_index,
            repair_exclusions=repair_exclusions,
        )
    except ModuleNotFoundError as error:
        if error.name == "PIL":
            raise PixelTwinError("Pillow is required: pip install -r scripts/requirements.txt") from error
        raise
    except (OSError, ValueError) as error:
        raise PixelTwinError(f"Unable to compare images: {error}") from error


def compare_visual_reports(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    try:
        try:
            from visual_compare import compare_reports
        except ImportError:
            from scripts.visual_compare import compare_reports
        return compare_reports(baseline, candidate)
    except ModuleNotFoundError as error:
        if error.name == "PIL":
            raise PixelTwinError("Pillow is required: pip install -r scripts/requirements.txt") from error
        raise


def _public_visual_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _public_visual_value(item)
            for key, item in value.items()
            if not str(key).startswith("_")
        }
    if isinstance(value, list):
        return [_public_visual_value(item) for item in value]
    return value


def public_visual_report(report: dict[str, Any]) -> dict[str, Any]:
    """Remove internal evidence recursively before printing or returning JSON."""
    public = _public_visual_value(
        {
            key: value
            for key, value in report.items()
            if key not in {"grid", "region_grid", "pass"}
        }
    )
    if isinstance(public.get("hotspots"), list):
        public["hotspots"] = public["hotspots"][:3]
        for hotspot in public["hotspots"]:
            hint = hotspot.get("repair_hint") if isinstance(hotspot, dict) else None
            if isinstance(hint, dict) and isinstance(hint.get("source_candidates"), list):
                hint["source_candidates"] = [
                    {
                        key: candidate[key]
                        for key in ("path", "line", "kind", "confidence", "matched_levers")
                        if key in candidate
                    }
                    for candidate in hint["source_candidates"][:2]
                    if isinstance(candidate, dict)
                ]
    if isinstance(public.get("dynamic_regions"), list):
        public["dynamic_regions"] = public["dynamic_regions"][:3]
    return public


def public_visual_delta(delta: dict[str, Any]) -> dict[str, Any]:
    public = {key: value for key, value in delta.items() if key != "regions"}
    for key in ("improved_regions", "regressed_regions"):
        if isinstance(public.get(key), list):
            public[key] = public[key][:3]
    dynamic = public.get("dynamic")
    if isinstance(dynamic, dict) and isinstance(dynamic.get("regions"), list):
        dynamic["regions"] = dynamic["regions"][:3]
    return public


def attach_dom_hints(report: dict[str, Any], dom_index: dict[str, Any] | None) -> None:
    """Compatibility wrapper for the bounded repair-hint module."""

    try:
        from repair_hints import attach_dom_hints as implementation
    except ImportError:
        from scripts.repair_hints import attach_dom_hints as implementation
    implementation(report, dom_index)


def repair_source_files(
    repository: Path,
    session: dict[str, Any],
) -> tuple[list[tuple[str, Path]], bool]:
    """Return a bounded set of Git-visible UI files approved by the session."""

    repository = repository.resolve()
    tracked, untracked = tracked_and_untracked(repository)
    initial_dirty = set(session.get("initial_dirty") or [])
    editable = set(session.get("editable") or [])
    selected: list[tuple[str, Path]] = []
    total_bytes = 0
    truncated = False
    for relative in sorted(tracked | untracked):
        allowed, _, kind = classify_path(relative, session)
        if not allowed or kind != "ui":
            continue
        if relative in initial_dirty and relative not in editable:
            continue
        path = repository / relative
        if path.is_symlink() or not path.is_file():
            continue
        try:
            size = path.stat().st_size
            path.resolve().relative_to(repository)
        except (OSError, ValueError):
            continue
        if size > MAX_REPAIR_SOURCE_FILE_BYTES:
            truncated = True
            continue
        if len(selected) >= MAX_REPAIR_SOURCE_FILES or total_bytes + size > MAX_REPAIR_SOURCE_TOTAL_BYTES:
            truncated = True
            break
        selected.append((relative, path))
        total_bytes += size
    return selected, truncated


def attach_source_hints(
    report: dict[str, Any],
    repository: Path,
    session: dict[str, Any] | None,
) -> None:
    if not session:
        return
    files, truncated = repair_source_files(repository, session)
    try:
        from repair_hints import attach_source_candidates
    except ImportError:
        from scripts.repair_hints import attach_source_candidates
    attach_source_candidates(report, files, truncated=truncated)


def capture_failure_reason(capture: dict[str, Any]) -> str:
    if capture.get("reason"):
        return str(capture["reason"])
    if capture.get("page_errors"):
        return "page error: " + concise_browser_error(capture["page_errors"][0])
    status = capture.get("status")
    if status is not None:
        try:
            if not 200 <= int(status) < 400:
                return f"HTTP status {status}"
        except (TypeError, ValueError):
            return f"invalid HTTP status {status!r}"
    settled = capture.get("settled") or {}
    failed_images = settled.get("failed_images", 0)
    if failed_images:
        return f"{failed_images} image resource(s) failed to load"
    if settled and not settled.get("stable", False):
        return "render did not settle (fonts, images, or layout remained unstable)"
    return "browser capture failed"


def capture_page(
    project: Path,
    output: Path,
    *,
    url: str,
    width: int,
    height: int,
    wait_ms: int,
    color_scheme: str,
    selector: str | None,
    allow_remote: bool,
    timeout: int,
    dynamic_selectors: list[str] | None = None,
    auto_dynamic: bool = True,
    sample_output: Path | None = None,
) -> dict[str, Any]:
    command = [
        "node",
        str(BROWSER_HELPER),
        "--url",
        url,
        "--output",
        str(output),
        "--width",
        str(width),
        "--height",
        str(height),
        "--wait-ms",
        str(wait_ms),
        "--color-scheme",
        color_scheme,
    ]
    if selector:
        command.extend(["--selector", selector])
    for dynamic_selector in dynamic_selectors or []:
        command.extend(["--dynamic-selector", dynamic_selector])
    if not auto_dynamic:
        command.append("--no-auto-dynamic")
    if sample_output is not None:
        command.extend(["--sample-output", str(sample_output)])
    if allow_remote:
        command.append("--allow-remote")
    capture = run_process(command, cwd=project, timeout=timeout)
    if capture.returncode != 0:
        return {
            "pass": False,
            "reason": (capture.stderr or capture.stdout).strip() or "browser capture failed",
        }
    try:
        browser = json.loads(capture.stdout.strip() or "{}")
    except json.JSONDecodeError:
        return {"pass": False, "reason": "browser capture returned invalid JSON"}

    status = browser.get("status")
    try:
        http_ok = status is None or 200 <= int(status) < 400
    except (TypeError, ValueError):
        http_ok = False
    settled = browser.get("settled") or {}
    stable = bool(
        settled.get("stable")
        and settled.get("pending_images", 0) == 0
        and settled.get("failed_images", 0) == 0
    )
    page_errors = browser.get("page_errors") or []
    result: dict[str, Any] = {
        "pass": http_ok and stable and not page_errors,
        "url": browser.get("url") or url,
        "status": status,
        "viewport": browser.get("viewport") or {"width": width, "height": height},
        "color_scheme": browser.get("color_scheme") or color_scheme,
        "settled": settled,
        "console_errors": browser.get("console_errors") or [],
        "page_errors": page_errors,
    }
    if isinstance(browser.get("_dom_index"), dict):
        result["_dom_index"] = browser["_dom_index"]
    if isinstance(browser.get("_dynamic_regions"), list):
        result["_dynamic_regions"] = browser["_dynamic_regions"][:3]
    if browser.get("evidence_version") is not None:
        result["evidence_version"] = browser.get("evidence_version")
    if isinstance(browser.get("temporal_sample"), dict):
        result["temporal_sample"] = bool(browser["temporal_sample"].get("captured"))
    if not result["pass"]:
        result["reason"] = capture_failure_reason(result)
    return result


def reference_size(path: Path) -> tuple[int, int]:
    if Image is None:
        raise PixelTwinError("Pillow is required: pip install -r scripts/requirements.txt")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as reference:
                width, height = reference.size
                if width <= 0 or height <= 0:
                    raise PixelTwinError("Reference image dimensions must be positive")
                if width > MAX_VISUAL_SIDE or height > MAX_VISUAL_SIDE:
                    raise PixelTwinError(
                        f"Reference image must be at most {MAX_VISUAL_SIDE} pixels per side"
                    )
                if width * height > MAX_VISUAL_PIXELS:
                    raise PixelTwinError(
                        f"Reference image must not exceed {MAX_VISUAL_PIXELS} total pixels"
                    )
                return width, height
    except (OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise PixelTwinError(f"Unable to read reference image {path}: {error}") from error


def validate_visual_parameters(wait_ms: int, tolerance: int) -> None:
    if not 1 <= wait_ms <= 60_000:
        raise PixelTwinError("--wait-ms must be between 1 and 60000")
    if not 0 <= tolerance <= 255:
        raise PixelTwinError("--tolerance must be between 0 and 255")


def validate_min_match(value: float | None) -> None:
    if value is None:
        return
    if not math.isfinite(value) or not 0 <= value <= 100:
        raise PixelTwinError("--min-match must be a finite percentage between 0 and 100")


def validate_dynamic_selectors(values: list[str] | None) -> list[str]:
    selectors = list(values or [])
    if len(selectors) > 3:
        raise PixelTwinError("At most three --dynamic-selector values are allowed")
    normalized: list[str] = []
    for selector in selectors:
        raw = str(selector)
        if any(ord(character) < 32 for character in raw):
            raise PixelTwinError("--dynamic-selector cannot contain control characters")
        value = raw.strip()
        if not value or len(value) > 160:
            raise PixelTwinError("--dynamic-selector must contain 1 to 160 characters")
        normalized.append(value)
    return normalized


def create_visual_baseline(
    args: argparse.Namespace,
    project: Path,
    session_dir: Path,
) -> dict[str, Any] | None:
    url = getattr(args, "url", None)
    reference_value = getattr(args, "reference", None)
    if not url and not reference_value:
        return None
    if not url or not reference_value:
        raise PixelTwinError("A visual baseline requires both --url and --reference")

    source = Path(reference_value).expanduser().resolve()
    if not source.is_file():
        raise PixelTwinError(f"Reference image does not exist: {source}")
    width, height = parse_viewport(getattr(args, "viewport", None), reference_size(source))
    visual_dir = session_dir / "visual"
    visual_dir.mkdir(mode=0o700)
    reference_path = visual_dir / "reference.png"
    baseline_path = visual_dir / "baseline.png"
    sample_path = visual_dir / "baseline-sample.png"
    shutil.copyfile(source, reference_path)

    dynamic_selectors = validate_dynamic_selectors(
        getattr(args, "dynamic_selector", None)
    )

    config = {
        "url": str(url),
        "viewport": [width, height],
        "selector": getattr(args, "selector", None),
        "wait_ms": int(getattr(args, "wait_ms", 250)),
        "color_scheme": str(getattr(args, "color_scheme", "light")),
        "allow_remote": bool(getattr(args, "allow_remote", False)),
        "timeout": int(getattr(args, "timeout", 300)),
        "tolerance": int(getattr(args, "tolerance", 8)),
        "dynamic_selectors": dynamic_selectors,
        "auto_dynamic": not bool(getattr(args, "no_auto_dynamic", False)),
        "reference": "visual/reference.png",
        "baseline_capture": "visual/baseline.png",
    }
    validate_visual_parameters(config["wait_ms"], config["tolerance"])
    capture = capture_page(
        project,
        baseline_path,
        url=config["url"],
        width=width,
        height=height,
        wait_ms=config["wait_ms"],
        color_scheme=config["color_scheme"],
        selector=config["selector"],
        allow_remote=config["allow_remote"],
        timeout=config["timeout"],
        dynamic_selectors=config["dynamic_selectors"],
        auto_dynamic=config["auto_dynamic"],
        sample_output=sample_path,
    )
    if not capture["pass"]:
        raise PixelTwinError("Unable to establish visual baseline: " + capture_failure_reason(capture))
    dynamic_regions = capture.pop("_dynamic_regions", [])
    dom_index = capture.pop("_dom_index", None)
    try:
        report, _ = compare_images(
            reference_path,
            baseline_path,
            config["tolerance"],
            dynamic_regions=dynamic_regions,
            temporal_path=sample_path if sample_path.is_file() else None,
            dom_index=dom_index,
        )
    finally:
        sample_path.unlink(missing_ok=True)
    if report.get("pass") is False:
        raise PixelTwinError("Unable to establish visual baseline: " + str(report.get("reason") or "comparison failed"))
    return {"config": config, "capture": capture, "report": report}


def ensure_debug_outside_project(project: Path, value: str) -> Path:
    debug = Path(value).expanduser().resolve()
    try:
        debug.relative_to(project)
    except ValueError:
        pass
    else:
        raise PixelTwinError("--keep-debug must point outside the target project")
    if debug.exists():
        if not debug.is_dir():
            raise PixelTwinError("--keep-debug must be a directory")
        if any(debug.iterdir()):
            raise PixelTwinError("--keep-debug directory must be empty")
    else:
        debug.mkdir(parents=True)
    return debug


def run_visual_check(
    args: argparse.Namespace,
    project: Path,
    repository: Path,
    scratch: Path,
    session: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    validate_min_match(getattr(args, "min_match", None))
    baseline = (session or {}).get("visual_baseline")
    if baseline:
        if args.url and args.url != baseline["config"]["url"]:
            raise PixelTwinError("--url must match the URL captured at begin")
        if args.reference:
            raise PixelTwinError("The reference is already stored in the temporary baseline; omit --reference")
        config = dict(baseline["config"])
        stored_viewport = f"{config['viewport'][0]}x{config['viewport'][1]}"
        overrides = {
            "--viewport": (args.viewport, stored_viewport),
            "--selector": (args.selector, config.get("selector")),
            "--wait-ms": (args.wait_ms, config.get("wait_ms")),
            "--color-scheme": (args.color_scheme, config.get("color_scheme")),
            "--tolerance": (args.tolerance, config.get("tolerance")),
            "--allow-remote": (args.allow_remote, config.get("allow_remote")),
        }
        for label, (requested, stored) in overrides.items():
            if requested is not None and requested != stored:
                raise PixelTwinError(f"{label} must match the visual baseline captured at begin")
        requested_dynamic = getattr(args, "dynamic_selector", None)
        if requested_dynamic is not None and validate_dynamic_selectors(requested_dynamic) != list(
            config.get("dynamic_selectors") or []
        ):
            raise PixelTwinError(
                "--dynamic-selector must match the visual baseline captured at begin"
            )
        requested_no_auto = getattr(args, "no_auto_dynamic", None)
        if requested_no_auto is not None and (not requested_no_auto) != bool(
            config.get("auto_dynamic", True)
        ):
            raise PixelTwinError(
                "--no-auto-dynamic must match the visual baseline captured at begin"
            )
        url = config["url"]
        reference_path = session_directory(str(session["session_id"])) / config["reference"]
        width, height = (int(value) for value in config["viewport"])
        wait_ms = int(config["wait_ms"])
        color_scheme = str(config["color_scheme"])
        selector = config.get("selector")
        allow_remote = bool(config.get("allow_remote"))
        timeout = int(config.get("timeout") or args.timeout)
        tolerance = int(config.get("tolerance", 8))
        dynamic_selectors = list(config.get("dynamic_selectors") or [])
        auto_dynamic = bool(config.get("auto_dynamic", True))
    else:
        if not args.url and not args.reference:
            return None
        if args.reference and not args.url:
            raise PixelTwinError("--reference requires --url")
        url = args.url
        reference_path = None
        fallback = (1440, 900)
        if args.reference:
            reference_path = Path(args.reference).expanduser().resolve()
            if not reference_path.is_file():
                raise PixelTwinError(f"Reference image does not exist: {reference_path}")
            fallback = reference_size(reference_path)
        width, height = parse_viewport(args.viewport, fallback)
        wait_ms = int(args.wait_ms if args.wait_ms is not None else 250)
        color_scheme = str(args.color_scheme or "light")
        selector = args.selector
        allow_remote = bool(args.allow_remote)
        timeout = int(args.timeout)
        tolerance = int(args.tolerance if args.tolerance is not None else 8)
        dynamic_selectors = validate_dynamic_selectors(
            getattr(args, "dynamic_selector", None)
        )
        auto_dynamic = not bool(getattr(args, "no_auto_dynamic", False))

    if not url:
        return None
    validate_visual_parameters(wait_ms, tolerance)
    actual_path = scratch / "actual.png"
    sample_path = scratch / "actual-sample.png"
    result = capture_page(
        project,
        actual_path,
        url=url,
        width=width,
        height=height,
        wait_ms=wait_ms,
        color_scheme=color_scheme,
        selector=selector,
        allow_remote=allow_remote,
        timeout=timeout,
        dynamic_selectors=dynamic_selectors,
        auto_dynamic=auto_dynamic,
        sample_output=sample_path,
    )
    dynamic_regions = result.pop("_dynamic_regions", [])
    dom_index = result.pop("_dom_index", None)
    diff = None
    try:
        if reference_path and actual_path.is_file() and result.get("pass"):
            report, diff = compare_images(
                reference_path,
                actual_path,
                tolerance,
                dynamic_regions=dynamic_regions,
                temporal_path=sample_path if sample_path.is_file() else None,
                dom_index=dom_index,
                repair_exclusions=(baseline or {}).get("report", {}).get(
                    "dynamic_regions"
                ),
            )
            attach_source_hints(report, repository, session)
            result.update(public_visual_report(report))
            result["pass"] = bool(result.get("pass")) and report.get("pass") is not False
            if baseline and report.get("pass") is not False:
                result["delta"] = public_visual_delta(
                    compare_visual_reports(baseline["report"], report)
                )
            result["min_match"] = args.min_match
            if args.min_match is not None and "tolerant_match_pct" in report:
                if float(report.get("static_coverage_pct", 100)) < 20:
                    result["pass"] = False
                    result["reason"] = "insufficient static coverage for --min-match"
                else:
                    result["pass"] = bool(
                        result["pass"]
                        and report["tolerant_match_pct"] >= args.min_match
                    )
    finally:
        sample_path.unlink(missing_ok=True)
    if args.fail_console_errors and (result.get("console_errors") or result.get("page_errors")):
        result["pass"] = False

    if args.keep_debug and actual_path.is_file():
        debug = ensure_debug_outside_project(repository, args.keep_debug)
        shutil.copyfile(actual_path, debug / "actual.png")
        if diff is not None:
            heat = diff.point(lambda value: min(255, value * 4))
            heat.save(debug / "diff.png")
        result["debug"] = str(debug)
    return result


def print_audit(report: dict[str, Any]) -> None:
    status = "PASS" if report["pass"] else "FAIL"
    print(f"scope: {status} · {len(report['changes'])} UI change(s)")
    for change in report["changes"][:12]:
        print(f"  {change['action']}: {change['path']}")
    for violation in report["violations"][:12]:
        print(f"  blocked: {violation['path']} — {violation['reason']}")


def concise_browser_error(value: Any, limit: int = 300) -> str:
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    compact = " ".join(text.split())
    return compact if len(compact) <= limit else compact[: limit - 1] + "…"


def signed_metric(value: Any) -> str:
    try:
        return f"{float(value):+.2f}"
    except (TypeError, ValueError):
        return "n/a"


def print_visual_summary(
    report: dict[str, Any],
    *,
    prefix: str = "visual",
    label: str | None = None,
) -> None:
    status = label or ("FAIL" if report.get("pass") is False else "MEASURED")
    if "tolerant_match_pct" in report:
        print(
            f"{prefix}: {status} · match {report['tolerant_match_pct']}% · "
            f"strict {report['strict_match_pct']}%"
        )
        layer_values = [
            ("layout", report.get("layout_match_pct")),
            ("structure", report.get("structure_similarity_pct")),
            ("edges", report.get("edge_match_pct")),
            ("color", report.get("color_similarity_pct")),
        ]
        if all(value is not None for _, value in layer_values):
            print("  layers: " + " · ".join(f"{name} {value}%" for name, value in layer_values))
        for hotspot in (report.get("hotspots") or [])[:3]:
            bounds = hotspot.get("bounds") or [
                hotspot.get("x", 0),
                hotspot.get("y", 0),
                hotspot.get("width", 0),
                hotspot.get("height", 0),
            ]
            print(
                f"  hotspot #{hotspot.get('rank', '?')}: "
                f"{bounds[0]},{bounds[1]} {bounds[2]}x{bounds[3]} · "
                f"page {hotspot.get('page_changed_pct', 0)}% · "
                f"mean delta {hotspot.get('mean_delta', 0)}"
            )
            dom = hotspot.get("dom") or {}
            if dom.get("selector"):
                levers = ", ".join((dom.get("css_levers") or [])[:3]) or "inspect styles"
                print(
                    f"    DOM {dom['selector']} · {dom.get('confidence', 'low')} · "
                    f"try {levers}"
                )
            repair = hotspot.get("repair_hint") or {}
            if repair.get("kind"):
                delta_values = repair.get("delta") or {}
                delta = ""
                if delta_values:
                    delta = (
                        f" · dx {signed_metric(delta_values.get('x'))}px"
                        f" · dy {signed_metric(delta_values.get('y'))}px"
                        f" · dw {signed_metric(delta_values.get('width'))}px"
                        f" · dh {signed_metric(delta_values.get('height'))}px"
                    )
                color = (
                    f" · color {repair['target_color']}"
                    if repair.get("target_color")
                    else ""
                )
                reason = (
                    f" · {repair['reason']}"
                    if repair.get("kind") == "uncertain" and repair.get("reason")
                    else ""
                )
                print(
                    f"    repair {repair['kind']} · {repair.get('confidence', 'low')}"
                    f"{delta}{color}{reason}"
                )
                sources = repair.get("source_candidates") or []
                if sources:
                    locations = ", ".join(
                        f"{item.get('path')}:{item.get('line')}" for item in sources[:2]
                    )
                    print(f"    source {locations}")
        for dynamic in (report.get("dynamic_regions") or [])[:3]:
            fidelity = dynamic.get("fidelity") or {}
            temporal = dynamic.get("temporal") or {}
            print(
                f"  dynamic {dynamic.get('kind', 'surface')}: "
                f"structure {fidelity.get('coarse_structure_pct', 'n/a')}% · "
                f"edges {fidelity.get('edge_distribution_pct', 'n/a')}% · "
                f"color {fidelity.get('color_similarity_pct', 'n/a')}% · "
                f"temporal {temporal.get('state', 'unavailable')}"
            )
    else:
        print(f"{prefix}: {status}")

    delta = report.get("delta") or {}
    if delta.get("pass"):
        overall = delta.get("overall") or {}
        print(
            "  gain: "
            f"layout {signed_metric(overall.get('layout_gain_pct'))} · "
            f"structure {signed_metric(overall.get('structure_gain_pct'))} · "
            f"edges {signed_metric(overall.get('edge_gain_pct'))} · "
            f"color {signed_metric(overall.get('color_gain_pct'))} · "
            f"pixels {signed_metric(overall.get('tolerant_match_gain_pct'))}"
        )
        summary = delta.get("summary") or {}
        print(
            "  regions: "
            f"{summary.get('improved_regions', 0)} improved · "
            f"{summary.get('regressed_regions', 0)} regressed/risky"
            + (
                f" ({summary.get('mixed_regions', 0)} mixed)"
                if summary.get("mixed_regions", 0)
                else ""
            )
            + " · "
            f"status {delta.get('status', 'unknown')}"
        )
        for regression in (delta.get("regressed_regions") or [])[:1]:
            bounds = regression.get("bounds") or [0, 0, 0, 0]
            print(
                f"  regression: {bounds[0]},{bounds[1]} {bounds[2]}x{bounds[3]} · "
                f"pixel gain {signed_metric(regression.get('tolerant_match_gain_pct'))}"
            )
        dynamic_delta = delta.get("dynamic") or {}
        dynamic_summary = dynamic_delta.get("summary") or {}
        if dynamic_summary:
            print(
                "  dynamic delta: "
                f"{dynamic_summary.get('improved', 0)} improved · "
                f"{dynamic_summary.get('regressed', 0) + dynamic_summary.get('mixed', 0)} risky · "
                f"{dynamic_summary.get('indeterminate', 0)} indeterminate"
            )


def check_session(args: argparse.Namespace) -> int:
    session = load_session(args.session)
    project = resolve_project(session["project"])
    repository = resolve_project(session.get("repository") or session["project"])
    pre_audit = audit_session(args.session)
    checks: list[dict[str, Any]] = []
    visual: dict[str, Any] | None = None

    if pre_audit["pass"]:
        checks = run_project_checks(project, args.run, args.timeout)
        if all(item["pass"] for item in checks):
            scratch_parent = session_directory(args.session) / "scratch"
            with tempfile.TemporaryDirectory(prefix="check-", dir=scratch_parent) as temporary:
                visual = run_visual_check(args, project, repository, Path(temporary), session)

    post_audit = audit_session(args.session)
    passed = (
        pre_audit["pass"]
        and post_audit["pass"]
        and all(item["pass"] for item in checks)
        and (visual is None or visual.get("pass") is True)
    )
    result = {
        "pass": passed,
        "scope": post_audit,
        "checks": checks,
        "visual": visual,
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print_audit(post_audit)
        for item in checks:
            print(f"{item['name']}: {'PASS' if item['pass'] else 'FAIL'}")
            for line in item.get("output_tail") or []:
                print(f"  {line}")
        if visual is not None:
            if "tolerant_match_pct" in visual:
                visual_label = "PASS" if visual.get("min_match") is not None and visual["pass"] else (
                    "FAIL" if not visual["pass"] else "MEASURED"
                )
            else:
                visual_label = "PASS" if visual.get("pass") else "FAIL"
            print_visual_summary(visual, label=visual_label)
            if visual.get("reason"):
                print(f"  {visual['reason']}")
            status = visual.get("status")
            if status is not None:
                try:
                    http_failed = not 200 <= int(status) < 400
                except (TypeError, ValueError):
                    http_failed = True
                if http_failed:
                    print(f"  HTTP status: {status}")
            if visual.get("page_errors"):
                errors = visual["page_errors"]
                print(f"  page error: {concise_browser_error(errors[0])}")
                if len(errors) > 1:
                    print(f"  page errors: {len(errors)} total")
            if visual.get("console_errors"):
                errors = visual["console_errors"]
                print(f"  console error: {concise_browser_error(errors[0])}")
                if len(errors) > 1:
                    print(f"  console errors: {len(errors)} total")
        print(f"result: {'PASS' if passed else 'FAIL'}")

    if passed and not args.keep_session:
        shutil.rmtree(session_directory(args.session), ignore_errors=True)
    return 0 if passed else 2


def finish_session(args: argparse.Namespace) -> int:
    directory = session_directory(args.session)
    if not directory.exists():
        raise PixelTwinError(f"Unknown or expired session: {args.session}")
    shutil.rmtree(directory)
    print(f"removed temporary session: {args.session}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pixel-twin",
        description="Apply UI changes in place while preserving project behavior and workspace hygiene.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subparsers.add_parser("inspect", help="Print a concise, read-only frontend profile")
    inspect_parser.add_argument("--project", required=True)
    inspect_parser.add_argument("--json", action="store_true")

    begin_parser = subparsers.add_parser("begin", help="Record a temporary pre-edit baseline")
    begin_parser.add_argument("--project", required=True)
    begin_parser.add_argument("--ui-root", action="append", default=[])
    begin_parser.add_argument("--asset-root", action="append", default=[])
    begin_parser.add_argument("--editable", action="append", default=[])
    begin_parser.add_argument("--url")
    begin_parser.add_argument("--reference")
    begin_parser.add_argument("--viewport")
    begin_parser.add_argument("--selector")
    begin_parser.add_argument("--dynamic-selector", action="append", default=[])
    begin_parser.add_argument("--no-auto-dynamic", action="store_true")
    begin_parser.add_argument("--wait-ms", type=int, default=250)
    begin_parser.add_argument("--color-scheme", choices=("light", "dark", "no-preference"), default="light")
    begin_parser.add_argument("--tolerance", type=int, default=8)
    begin_parser.add_argument("--timeout", type=int, default=300)
    begin_parser.add_argument("--allow-remote", action="store_true")
    begin_parser.add_argument("--json", action="store_true")

    check_parser = subparsers.add_parser("check", help="Run one UI boundary and project-native check")
    check_parser.add_argument("--session", required=True)
    check_parser.add_argument("--run", action="append", default=[])
    check_parser.add_argument("--url")
    check_parser.add_argument("--reference")
    check_parser.add_argument("--viewport")
    check_parser.add_argument("--selector")
    check_parser.add_argument("--dynamic-selector", action="append")
    check_parser.add_argument("--no-auto-dynamic", action="store_true", default=None)
    check_parser.add_argument("--wait-ms", type=int)
    check_parser.add_argument("--color-scheme", choices=("light", "dark", "no-preference"))
    check_parser.add_argument("--tolerance", type=int)
    check_parser.add_argument("--min-match", type=float)
    check_parser.add_argument("--timeout", type=int, default=300)
    check_parser.add_argument("--allow-remote", action="store_true", default=None)
    check_parser.add_argument("--fail-console-errors", action="store_true")
    check_parser.add_argument("--keep-debug")
    check_parser.add_argument("--keep-session", action="store_true")
    check_parser.add_argument("--json", action="store_true")

    finish_parser = subparsers.add_parser("finish", help="Remove an abandoned temporary session")
    finish_parser.add_argument("--session", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "inspect":
            data = inspect_project(resolve_project(args.project))
            if args.json:
                print(json.dumps(data, ensure_ascii=False))
            else:
                print_inspection(data)
            return 0
        if args.command == "begin":
            return begin_session(args)
        if args.command == "check":
            return check_session(args)
        if args.command == "finish":
            return finish_session(args)
        raise PixelTwinError(f"Unknown command: {args.command}")
    except PixelTwinError as error:
        print(f"pixel-twin: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
