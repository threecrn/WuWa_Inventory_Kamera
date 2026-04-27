"""
CLI tool for browsing, searching and fetching data from the
WutheringData git submodule located next to this script.

Mirrors the interface of main.py but works entirely offline —
no GitHub API calls, no rate-limits, no auth tokens required.

Usage
-----
    # Show submodule info (git log, structure)
    uv run tools/cli/dimbreath_wuthering_data/local.py info

    # List top-level directory
    uv run tools/cli/dimbreath_wuthering_data/local.py tree

    # List a subdirectory
    uv run tools/cli/dimbreath_wuthering_data/local.py tree ConfigDB

    # Recursive listing (all files under a path)
    uv run tools/cli/dimbreath_wuthering_data/local.py tree ConfigDB --recursive

    # Fetch a file (auto-decodes JSON)
    uv run tools/cli/dimbreath_wuthering_data/local.py fetch README.md
    uv run tools/cli/dimbreath_wuthering_data/local.py fetch ConfigDB/RoleInfo.json

    # Fetch and filter JSON keys
    uv run tools/cli/dimbreath_wuthering_data/local.py fetch ConfigDB/RoleInfo.json --jq "[0].Name"

    # Search filenames (fast, case-insensitive)
    uv run tools/cli/dimbreath_wuthering_data/local.py search "Weapon"
    uv run tools/cli/dimbreath_wuthering_data/local.py search "Weapon" --extension json

    # Full-text search inside JSON files (slower)
    uv run tools/cli/dimbreath_wuthering_data/local.py search "RoleId" --content

    # List recent commits from the submodule
    uv run tools/cli/dimbreath_wuthering_data/local.py commits
    uv run tools/cli/dimbreath_wuthering_data/local.py commits --path ConfigDB

Exit codes
----------
    0   Success
    1   Usage / argument error
    3   Resource not found
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

# -----------¬ Paths

_HERE = Path(__file__).resolve().parent
SUBMODULE = _HERE / "WutheringData"

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_NOT_FOUND = 3

logging.basicConfig(
    level=logging.WARNING,
    format="%(levelname)s: %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger(__name__)


# -----------¬ Guards


def _require_submodule() -> None:
    if not SUBMODULE.is_dir() or not (SUBMODULE / ".git").exists():
        log.error(
            "Submodule not initialised at %s. "
            "Run: git submodule update --init tools/cli/dimbreath_wuthering_data/WutheringData",
            SUBMODULE,
        )
        sys.exit(EXIT_NOT_FOUND)


def _resolve(path: str) -> Path:
    """Resolve *path* relative to the submodule root. Rejects traversal."""
    resolved = (SUBMODULE / path).resolve()
    if not str(resolved).startswith(str(SUBMODULE.resolve())):
        log.error("Path traversal rejected: %s", path)
        sys.exit(EXIT_USAGE)
    return resolved


# -----------¬ Output helper


def _emit(obj: Any) -> None:
    json.dump(obj, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


# -----------¬ Sub-commands


def cmd_info(_args: argparse.Namespace) -> int:
    """Print submodule metadata derived from git log."""
    _require_submodule()

    def _git(*args: str) -> str:
        try:
            return subprocess.check_output(
                ["git", *args],
                cwd=SUBMODULE,
                stderr=subprocess.DEVNULL,
                text=True,
            ).strip()
        except (subprocess.CalledProcessError, FileNotFoundError):
            return ""

    top_dirs = sorted(
        p.name for p in SUBMODULE.iterdir()
        if not p.name.startswith(".")
    )
    _emit(
        {
            "submodule_path": str(SUBMODULE),
            "commit": _git("rev-parse", "HEAD"),
            "date": _git("log", "-1", "--format=%cI"),
            "message": _git("log", "-1", "--format=%s"),
            "top_level": top_dirs,
        }
    )
    return EXIT_OK


def _entry(p: Path) -> dict[str, Any]:
    """Build a directory-entry dict for *p*."""
    rel = p.relative_to(SUBMODULE).as_posix()
    entry: dict[str, Any] = {
        "path": rel,
        "type": "dir" if p.is_dir() else "file",
    }
    if p.is_file():
        entry["size"] = p.stat().st_size
    return entry


def cmd_tree(args: argparse.Namespace) -> int:
    """List files/directories at *path* (default: repo root)."""
    _require_submodule()
    path: str = args.path or ""
    recursive: bool = args.recursive

    target = _resolve(path) if path else SUBMODULE

    if not target.exists():
        log.error("Not found: %s", path)
        sys.exit(EXIT_NOT_FOUND)

    if target.is_file():
        _emit(_entry(target))
        return EXIT_OK

    if recursive:
        entries = [
            _entry(p)
            for p in sorted(target.rglob("*"))
            if not p.name.startswith(".")
        ]
    else:
        entries = [
            _entry(p)
            for p in sorted(target.iterdir())
            if not p.name.startswith(".")
        ]

    _emit(
        {
            "path": path or "/",
            "count": len(entries),
            "entries": entries,
        }
    )
    return EXIT_OK


def _jq_traverse(obj: Any, expr: str) -> Any:
    """Minimal jq-like path traversal.

    Supports dot-separated keys and ``[N]`` array indices.
    Examples: ``[0].Name``, ``Id``, ``Data[2].Value``
    """
    parts = re.findall(r'\[(\d+)\]|([^.\[\]]+)', expr)
    cursor = obj
    for idx_str, key in parts:
        if cursor is None:
            return None
        if idx_str:
            idx = int(idx_str)
            if not isinstance(cursor, list) or idx >= len(cursor):
                return None
            cursor = cursor[idx]
        elif key:
            if isinstance(cursor, dict):
                cursor = cursor.get(key)
            elif isinstance(cursor, list):
                cursor = [
                    e.get(key) if isinstance(e, dict) else None
                    for e in cursor
                ]
            else:
                return None
    return cursor


def cmd_fetch(args: argparse.Namespace) -> int:
    """Fetch a single file's contents. Auto-decodes JSON files."""
    _require_submodule()
    path: str = args.path
    jq: str | None = args.jq
    raw: bool = args.raw
    max_size: int = args.max_size

    target = _resolve(path)

    if not target.exists():
        log.error("Not found: %s", path)
        sys.exit(EXIT_NOT_FOUND)

    if target.is_dir():
        log.error("%s is a directory, use 'tree' instead.", path)
        sys.exit(EXIT_USAGE)

    size = target.stat().st_size
    if size > max_size:
        log.error(
            "File %s is %d bytes (limit %d). Use --max-size to raise.",
            path,
            size,
            max_size,
        )
        sys.exit(EXIT_USAGE)

    text = target.read_text(encoding="utf-8", errors="replace")

    if raw:
        sys.stdout.write(text)
        return EXIT_OK

    try:
        obj = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        _emit({"path": path, "size": size, "content": text})
        return EXIT_OK

    if jq:
        obj = _jq_traverse(obj, jq)

    _emit({"path": path, "size": size, "data": obj})
    return EXIT_OK


def cmd_search(args: argparse.Namespace) -> int:
    """Search filenames (default) or file contents (--content).

    Filename search is instant; content search reads every matching file.
    """
    _require_submodule()
    query: str = args.query
    extension: str | None = args.extension
    content: bool = args.content
    scope: str | None = args.scope
    max_results: int = args.max_results

    base = _resolve(scope) if scope else SUBMODULE
    if not base.exists():
        log.error("Scope path not found: %s", scope)
        sys.exit(EXIT_NOT_FOUND)

    glob = f"**/*.{extension}" if extension else "**/*"
    pat = re.compile(re.escape(query), re.IGNORECASE)

    results: list[dict[str, Any]] = []

    for p in sorted(base.rglob(glob if extension else "*")):
        if p.is_dir() or p.name.startswith("."):
            continue
        if extension and p.suffix.lstrip(".") != extension:
            continue

        rel = p.relative_to(SUBMODULE).as_posix()

        if content:
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            matches = [
                {"line": i + 1, "text": line.rstrip()}
                for i, line in enumerate(text.splitlines())
                if pat.search(line)
            ]
            if matches:
                results.append(
                    {
                        "path": rel,
                        "size": p.stat().st_size,
                        "matches": matches,
                    }
                )
        else:
            if pat.search(p.name):
                results.append(
                    {
                        "path": rel,
                        "name": p.name,
                        "size": p.stat().st_size,
                    }
                )

        if len(results) >= max_results:
            break

    _emit(
        {
            "query": query,
            "mode": "content" if content else "filename",
            "scope": scope or "/",
            "returned": len(results),
            "results": results,
        }
    )
    return EXIT_OK


def cmd_commits(args: argparse.Namespace) -> int:
    """List recent commits from the submodule git log."""
    _require_submodule()
    path: str | None = args.path
    count: int = args.count

    cmd = [
        "git", "log",
        f"-{count}",
        "--format=%H%x1f%ci%x1f%s%x1f%an",
    ]
    if path:
        cmd += ["--", path]

    try:
        out = subprocess.check_output(
            cmd,
            cwd=SUBMODULE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        log.error("git log failed: %s", exc)
        sys.exit(EXIT_NOT_FOUND)

    commits = []
    for line in out.strip().splitlines():
        if not line:
            continue
        sha, date, message, author = line.split("\x1f", 3)
        commits.append(
            {
                "sha": sha[:12],
                "date": date,
                "message": message,
                "author": author,
            }
        )

    _emit({"path": path or "/", "count": len(commits), "commits": commits})
    return EXIT_OK


# -----------¬ Argument parser


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="local",
        description=(
            "Browse, search and fetch data from the WutheringData submodule. "
            "Works fully offline. All output is JSON on stdout."
        ),
    )
    sub = p.add_subparsers(dest="command", required=True)

    # info
    sub.add_parser("info", help="Submodule metadata (git log, structure).")

    # tree
    sp = sub.add_parser("tree", help="List directory contents.")
    sp.add_argument(
        "path",
        nargs="?",
        default="",
        help="Path inside the submodule (default: root).",
    )
    sp.add_argument(
        "-r", "--recursive",
        action="store_true",
        help="List the entire subtree recursively.",
    )

    # fetch
    sp = sub.add_parser("fetch", help="Fetch a file's contents.")
    sp.add_argument("path", help="File path inside the submodule.")
    sp.add_argument(
        "--jq",
        default=None,
        help="Minimal jq-style path (e.g. '[0].Name', 'Data[2].Value').",
    )
    sp.add_argument(
        "--raw",
        action="store_true",
        help="Output raw file text (not wrapped in JSON).",
    )
    sp.add_argument(
        "--max-size",
        type=int,
        default=10_000_000,
        help="Maximum file size in bytes to read (default: 10 MB).",
    )

    # search
    sp = sub.add_parser("search", help="Search filenames or file contents.")
    sp.add_argument("query", help="Search query string.")
    sp.add_argument(
        "--content",
        action="store_true",
        help="Search inside file contents instead of filenames.",
    )
    sp.add_argument(
        "--extension",
        default=None,
        help="Restrict to file extension without dot (e.g. 'json').",
    )
    sp.add_argument(
        "--scope",
        default=None,
        help="Limit search to this subdirectory (e.g. 'ConfigDB').",
    )
    sp.add_argument(
        "--max-results",
        type=int,
        default=20,
        help="Maximum number of results (default: 20).",
    )

    # commits
    sp = sub.add_parser("commits", help="List recent commits.")
    sp.add_argument(
        "--path",
        default=None,
        help="Scope commits to a specific path.",
    )
    sp.add_argument(
        "--count",
        type=int,
        default=10,
        help="Number of commits to return (default: 10).",
    )

    return p


# -----------¬ Main


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    dispatch = {
        "info": cmd_info,
        "tree": cmd_tree,
        "fetch": cmd_fetch,
        "search": cmd_search,
        "commits": cmd_commits,
    }
    handler = dispatch.get(args.command)
    if handler is None:
        parser.print_help(sys.stderr)
        return EXIT_USAGE
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
