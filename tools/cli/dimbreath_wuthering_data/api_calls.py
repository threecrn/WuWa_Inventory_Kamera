"""
CLI tool for browsing, searching and fetching data from
Dimbreath/WutheringData via the GitHub API.

Designed for use by AI agents — all output is JSON on stdout,
errors go to stderr, and exit codes follow Unix conventions.

Usage
-----
    # Show repo metadata
    uv run tools/cli/dimbreath_wuthering_data/api_calls.py info

    # List top-level tree (flat)
    uv run tools/cli/dimbreath_wuthering_data/api_calls.py tree

    # List a subdirectory
    uv run tools/cli/dimbreath_wuthering_data/api_calls.py tree ConfigDB

    # Recursive tree (full repo manifest — large output)
    uv run tools/cli/dimbreath_wuthering_data/api_calls.py tree --recursive

    # Fetch a file (auto-decodes JSON)
    uv run tools/cli/dimbreath_wuthering_data/api_calls.py fetch README.md
    uv run tools/cli/dimbreath_wuthering_data/api_calls.py fetch ConfigDB/RoleInfo.json

    # Fetch and filter JSON keys (dot-separated path into first object/list)
    uv run tools/cli/dimbreath_wuthering_data/api_calls.py fetch ConfigDB/RoleInfo.json --jq "[0].Name"

    # Search code (GitHub code-search, limited to this repo)
    uv run tools/cli/dimbreath_wuthering_data/api_calls.py search "RoleId"

    # Search filenames only
    uv run tools/cli/dimbreath_wuthering_data/api_calls.py search "Weapon" --filename

    # List recent commits (optionally scoped to a path)
    uv run tools/cli/dimbreath_wuthering_data/api_calls.py commits
    uv run tools/cli/dimbreath_wuthering_data/api_calls.py commits --path ConfigDB

    # Download a raw blob by SHA (useful for large files)
    uv run tools/cli/dimbreath_wuthering_data/api_calls.py blob <sha>

Environment
-----------
    GITHUB_TOKEN   Optional. Raises rate-limit from 60 → 5 000 req/h.

Exit codes
----------
    0   Success
    1   Usage / argument error
    2   Network / API error
    3   Resource not found
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

# -----------¬ Constants
OWNER = "Dimbreath"
REPO = "WutheringData"
API_BASE = f"https://api.github.com/repos/{OWNER}/{REPO}"
DEFAULT_BRANCH = "master"

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_NETWORK = 2
EXIT_NOT_FOUND = 3

logging.basicConfig(
    level=logging.WARNING,
    format="%(levelname)s: %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger(__name__)


# -----------¬ HTTP helpers


def _headers() -> dict[str, str]:
    """Build request headers, including auth token when available."""
    h: dict[str, str] = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "WuWa-Inventory-Kamera-CLI/1.0",
    }
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _get(url: str, *, accept: str | None = None) -> bytes:
    """Perform a GET request. Returns the raw body bytes.

    Raises SystemExit on HTTP errors to keep call-sites clean.
    """
    headers = _headers()
    if accept:
        headers["Accept"] = accept
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            log.error("Not found: %s", url)
            sys.exit(EXIT_NOT_FOUND)
        if exc.code == 403:
            remaining = exc.headers.get("X-RateLimit-Remaining", "?")
            log.error(
                "Forbidden (rate-limit remaining: %s). "
                "Set GITHUB_TOKEN to raise limit.",
                remaining,
            )
            sys.exit(EXIT_NETWORK)
        log.error("HTTP %s: %s — %s", exc.code, exc.reason, url)
        sys.exit(EXIT_NETWORK)
    except urllib.error.URLError as exc:
        log.error("Network error: %s — %s", exc.reason, url)
        sys.exit(EXIT_NETWORK)


def _get_json(url: str) -> Any:
    return json.loads(_get(url))


# -----------¬ Pagination helper


def _get_all_pages(url: str, *, max_pages: int = 10) -> list[Any]:
    """Follow GitHub ``Link: <…>; rel="next"`` pagination."""
    items: list[Any] = []
    headers = _headers()
    pages = 0
    current: str | None = url
    while current and pages < max_pages:
        pages += 1
        req = urllib.request.Request(current, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = json.loads(resp.read())
                if isinstance(body, dict) and "items" in body:
                    items.extend(body["items"])
                elif isinstance(body, list):
                    items.extend(body)
                else:
                    items.append(body)
                # Parse Link header
                link_header = resp.headers.get("Link", "")
                current = _parse_next_link(link_header)
        except urllib.error.HTTPError as exc:
            log.error("HTTP %s during pagination: %s", exc.code, current)
            break
        except urllib.error.URLError as exc:
            log.error("Network error during pagination: %s", exc.reason)
            break
    return items


_LINK_RE = re.compile(r'<([^>]+)>;\s*rel="next"')


def _parse_next_link(header: str) -> str | None:
    m = _LINK_RE.search(header)
    return m.group(1) if m else None


# -----------¬ Output helper


def _emit(obj: Any) -> None:
    """Write *obj* as compact JSON to stdout."""
    json.dump(obj, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


# -----------¬ Sub-commands


def cmd_info(_args: argparse.Namespace) -> int:
    """Print repository metadata."""
    data = _get_json(API_BASE)
    _emit(
        {
            "full_name": data["full_name"],
            "description": data.get("description"),
            "default_branch": data.get("default_branch"),
            "stars": data.get("stargazers_count"),
            "size_kb": data.get("size"),
            "pushed_at": data.get("pushed_at"),
            "html_url": data.get("html_url"),
            "topics": data.get("topics", []),
        }
    )
    return EXIT_OK


def cmd_tree(args: argparse.Namespace) -> int:
    """List files/directories at *path* (default: repo root)."""
    path: str = args.path or ""
    recursive: bool = args.recursive

    if recursive and not path:
        # Full recursive tree from root
        url = f"{API_BASE}/git/trees/{DEFAULT_BRANCH}?recursive=1"
        data = _get_json(url)
        entries = [
            {
                "path": e["path"],
                "type": "dir" if e["type"] == "tree" else "file",
                "sha": e["sha"],
                **({"size": e["size"]} if "size" in e else {}),
            }
            for e in data.get("tree", [])
        ]
    elif recursive and path:
        # First resolve the subtree's SHA, then fetch recursively
        contents = _get_json(
            f"{API_BASE}/contents/{urllib.parse.quote(path, safe='/')}"
            f"?ref={DEFAULT_BRANCH}"
        )
        if isinstance(contents, dict) and contents.get("type") == "dir":
            sha = contents["sha"]
        else:
            # contents API for a directory returns a list
            # fall back to tree from that list's parent
            # resolve via git tree instead
            parent = path.rstrip("/")
            tree_url = (
                f"{API_BASE}/git/trees/{DEFAULT_BRANCH}?recursive=1"
            )
            full_tree = _get_json(tree_url)
            entries = [
                {
                    "path": e["path"],
                    "type": "dir" if e["type"] == "tree" else "file",
                    "sha": e["sha"],
                    **({"size": e["size"]} if "size" in e else {}),
                }
                for e in full_tree.get("tree", [])
                if e["path"].startswith(parent + "/") or e["path"] == parent
            ]
            _emit({"path": path, "count": len(entries), "entries": entries})
            return EXIT_OK

        tree_url = f"{API_BASE}/git/trees/{sha}?recursive=1"
        data = _get_json(tree_url)
        entries = [
            {
                "path": f"{path}/{e['path']}",
                "type": "dir" if e["type"] == "tree" else "file",
                "sha": e["sha"],
                **({"size": e["size"]} if "size" in e else {}),
            }
            for e in data.get("tree", [])
        ]
    else:
        # Flat listing via Contents API
        url = (
            f"{API_BASE}/contents/{urllib.parse.quote(path, safe='/')}"
            f"?ref={DEFAULT_BRANCH}"
        )
        data = _get_json(url)
        if isinstance(data, dict):
            # It's a file, not a dir
            _emit(
                {
                    "path": data["path"],
                    "type": data["type"],
                    "size": data.get("size"),
                    "sha": data["sha"],
                }
            )
            return EXIT_OK
        entries = [
            {
                "path": e["path"],
                "type": "dir" if e["type"] == "dir" else "file",
                "sha": e["sha"],
                **({"size": e["size"]} if "size" in e else {}),
            }
            for e in data
        ]

    _emit(
        {
            "path": path or "/",
            "count": len(entries),
            "entries": entries,
        }
    )
    return EXIT_OK


def cmd_fetch(args: argparse.Namespace) -> int:
    """Fetch a single file's contents. Auto-decodes JSON files."""
    path: str = args.path
    jq: str | None = args.jq
    raw: bool = args.raw
    max_size: int = args.max_size

    url = (
        f"{API_BASE}/contents/{urllib.parse.quote(path, safe='/')}"
        f"?ref={DEFAULT_BRANCH}"
    )
    meta = _get_json(url)

    if meta.get("type") == "dir":
        log.error("%s is a directory, use 'tree' instead.", path)
        sys.exit(EXIT_USAGE)

    size = meta.get("size", 0)
    if size > max_size:
        log.error(
            "File %s is %d bytes (limit %d). "
            "Use --max-size to raise or 'blob' to fetch by SHA.",
            path,
            size,
            max_size,
        )
        sys.exit(EXIT_USAGE)

    # For files ≤ 1 MB the content is inline base64; for larger we need blob
    if meta.get("content"):
        body = base64.b64decode(meta["content"])
    else:
        blob_url = meta.get("git_url")
        if not blob_url:
            log.error("No content or git_url for %s", path)
            sys.exit(EXIT_NETWORK)
        blob = _get_json(blob_url)
        body = base64.b64decode(blob["content"])

    text = body.decode("utf-8", errors="replace")

    if raw:
        sys.stdout.write(text)
        return EXIT_OK

    # Try JSON parse
    try:
        obj = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        # Not JSON — emit as wrapped text
        _emit({"path": path, "size": size, "content": text})
        return EXIT_OK

    # Optional --jq path traversal
    if jq:
        obj = _jq_traverse(obj, jq)

    _emit({"path": path, "size": size, "data": obj})
    return EXIT_OK


def _jq_traverse(obj: Any, expr: str) -> Any:
    """Minimal jq-like path traversal.

    Supports dot-separated keys and ``[N]`` array indices.
    Examples: ``[0].Name``, ``Id``, ``Data[2].Value``
    """
    parts = re.findall(r'\[(\d+)\]|([^.\[\]]+)', expr)
    cursor = obj
    for idx_str, key in parts:
        if idx_str:
            idx = int(idx_str)
            if not isinstance(cursor, list) or idx >= len(cursor):
                return None
            cursor = cursor[idx]
        elif key:
            if isinstance(cursor, dict):
                cursor = cursor.get(key)
            elif isinstance(cursor, list):
                # apply key to every element
                cursor = [
                    e.get(key) if isinstance(e, dict) else None
                    for e in cursor
                ]
            else:
                return None
        if cursor is None:
            return None
    return cursor


def _search_filenames_via_tree(
    pattern: str,
    *,
    extension: str | None = None,
    max_results: int = 20,
) -> list[dict[str, Any]]:
    """Fallback filename search using the git tree (no auth required)."""
    url = f"{API_BASE}/git/trees/{DEFAULT_BRANCH}?recursive=1"
    data = _get_json(url)
    pat = re.compile(re.escape(pattern), re.IGNORECASE)
    results: list[dict[str, Any]] = []
    for entry in data.get("tree", []):
        if entry["type"] != "blob":
            continue
        name = entry["path"].rsplit("/", 1)[-1]
        if extension and not name.endswith(f".{extension}"):
            continue
        if pat.search(name):
            results.append(
                {
                    "path": entry["path"],
                    "name": name,
                    "sha": entry["sha"],
                    "size": entry.get("size"),
                }
            )
            if len(results) >= max_results:
                break
    return results


def cmd_search(args: argparse.Namespace) -> int:
    """Search code in the repository.

    Uses GitHub code search when GITHUB_TOKEN is set.
    Falls back to filename-only search via the git tree when unauthenticated.
    """
    query: str = args.query
    filename: bool = args.filename
    extension: str | None = args.extension
    max_results: int = args.max_results

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")

    # -- Filename search (works without auth) --
    if filename or not token:
        if not filename and not token:
            log.warning(
                "GITHUB_TOKEN not set — falling back to filename search. "
                "Set GITHUB_TOKEN for full content search."
            )
        results = _search_filenames_via_tree(
            query, extension=extension, max_results=max_results
        )
        _emit(
            {
                "query": query,
                "mode": "filename_tree",
                "returned": len(results),
                "results": results,
            }
        )
        return EXIT_OK

    # -- Full code search (requires auth) --
    q_parts = [query, f"repo:{OWNER}/{REPO}"]
    if extension:
        q_parts.append(f"extension:{extension}")

    per_page = min(max_results, 100)
    url = (
        f"https://api.github.com/search/code"
        f"?q={urllib.parse.quote(' '.join(q_parts))}"
        f"&per_page={per_page}"
    )

    data = _get_json(url)
    items = data.get("items", [])[:max_results]

    results = [
        {
            "path": item["path"],
            "name": item["name"],
            "sha": item["sha"],
            "html_url": item["html_url"],
            "score": item.get("score"),
        }
        for item in items
    ]
    _emit(
        {
            "query": query,
            "mode": "code_search",
            "total_count": data.get("total_count", 0),
            "returned": len(results),
            "results": results,
        }
    )
    return EXIT_OK


def cmd_commits(args: argparse.Namespace) -> int:
    """List recent commits, optionally filtered by path."""
    path: str | None = args.path
    count: int = args.count

    params: dict[str, str] = {"per_page": str(min(count, 100))}
    if path:
        params["path"] = path
    qs = urllib.parse.urlencode(params)
    url = f"{API_BASE}/commits?{qs}"

    commits = _get_all_pages(url, max_pages=1)[:count]
    results = [
        {
            "sha": c["sha"][:12],
            "date": c["commit"]["committer"]["date"],
            "message": c["commit"]["message"].split("\n", 1)[0],
            "author": c["commit"]["author"]["name"],
        }
        for c in commits
    ]
    _emit({"path": path or "/", "count": len(results), "commits": results})
    return EXIT_OK


def cmd_blob(args: argparse.Namespace) -> int:
    """Fetch a git blob by SHA. Outputs decoded content."""
    sha: str = args.sha
    url = f"{API_BASE}/git/blobs/{sha}"
    data = _get_json(url)
    body = base64.b64decode(data["content"])
    text = body.decode("utf-8", errors="replace")

    # Try JSON
    try:
        obj = json.loads(text)
        _emit({"sha": sha, "size": data.get("size"), "data": obj})
    except (json.JSONDecodeError, ValueError):
        _emit({"sha": sha, "size": data.get("size"), "content": text})
    return EXIT_OK


# -----------¬ Argument parser


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="dimbreath_wuthering_data",
        description=(
            "Browse, search and fetch data from "
            f"{OWNER}/{REPO} via the GitHub API. "
            "All output is JSON on stdout."
        ),
    )
    sub = p.add_subparsers(dest="command", required=True)

    # info
    sub.add_parser("info", help="Repository metadata.")

    # tree
    sp = sub.add_parser("tree", help="List directory contents.")
    sp.add_argument(
        "path",
        nargs="?",
        default="",
        help="Path inside the repo (default: root).",
    )
    sp.add_argument(
        "-r",
        "--recursive",
        action="store_true",
        help="List the entire subtree recursively.",
    )

    # fetch
    sp = sub.add_parser("fetch", help="Fetch a file's contents.")
    sp.add_argument("path", help="File path inside the repo.")
    sp.add_argument(
        "--jq",
        default=None,
        help=(
            "Minimal jq-style path into JSON data "
            "(e.g. '[0].Name', 'Data[2].Value')."
        ),
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
        help="Maximum file size in bytes to fetch (default: 10 MB).",
    )

    # search
    sp = sub.add_parser("search", help="Search code in the repo.")
    sp.add_argument("query", help="Search query string.")
    sp.add_argument(
        "--filename",
        action="store_true",
        help="Search by filename instead of content.",
    )
    sp.add_argument(
        "--extension",
        default=None,
        help="Restrict to file extension (e.g. 'json').",
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

    # blob
    sp = sub.add_parser("blob", help="Fetch a raw git blob by SHA.")
    sp.add_argument("sha", help="Git blob SHA.")

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
        "blob": cmd_blob,
    }
    handler = dispatch.get(args.command)
    if handler is None:
        parser.print_help(sys.stderr)
        return EXIT_USAGE
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
