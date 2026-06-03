#!/usr/bin/env python3
"""
tools/prune_cx_freeze_build.py

Safely find and optionally remove/move common OpenCV and other unnecessary files
from a cx_Freeze build directory.

Usage:
  python tools/prune_cx_freeze_build.py --build-dir build/cx_freeze        # dry-run (default)
  python tools/prune_cx_freeze_build.py --build-dir build/cx_freeze --apply
  python tools/prune_cx_freeze_build.py --build-dir build/cx_freeze --apply --delete

Default behavior is a dry-run that prints matches. `--apply` moves matched files
into a timestamped backup folder next to the build directory. Use `--delete` to
permanently delete (use with care).
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path
import datetime
import sys
import fnmatch

# Common filename globs that tend to bloat cx_Freeze builds when OpenCV is pulled in.
DEFAULT_PATTERNS = [
    "haarcascade*.xml",
    "lbpcascade*.xml",
    "opencv_ffmpeg*.dll",
    "opencv_videoio_ffmpeg*.dll",
    "opencv_world*.dll",
    "opencv_*.dll",
    #"cv2*.pyd",
    #"cv2*.dll",
    #"cv2.pyd",
    "*/data/haarcascade*.xml",
    "*haarcascade*.xml",
]


def find_matches(build_dir: Path, patterns: list[str]) -> list[Path]:
    build_dir = build_dir.resolve()
    matches: list[Path] = []
    for p in build_dir.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(build_dir)
        for pat in patterns:
            # Match against the path relative to build_dir and the filename
            try:
                if rel.match(pat) or fnmatch.fnmatch(p.name, pat):
                    matches.append(p)
                    break
            except Exception:
                # Fallback to simple fnmatch on path string
                if fnmatch.fnmatch(str(rel).replace("\\\\", "/"), pat):
                    matches.append(p)
                    break
    return sorted(set(matches), key=lambda x: str(x))


def apply_prune(matches: list[Path], build_dir: Path, apply: bool, delete: bool) -> None:
    if not matches:
        print("No matching files found.")
        return

    # Normalize build_dir and matched paths to absolute form so relative_to() works.
    build_dir = Path(build_dir).resolve()
    matches = [m.resolve() for m in matches]

    if not apply:
        print("Dry-run: the following files match and would be moved/deleted:")
        for m in matches:
            try:
                print("  ", m.relative_to(build_dir))
            except ValueError:
                print("  ", m)
        return

    # Create timestamped backup dir next to build_dir
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_root = build_dir.parent / f"prune_backup_{ts}"
    for m in matches:
        try:
            rel = m.relative_to(build_dir)
        except ValueError:
            rel = Path(m.name)
        dest = backup_root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if delete:
            print(f"Deleting {m}")
            try:
                m.unlink()
            except Exception as e:
                print(f"  ERROR deleting {m}: {e}")
        else:
            print(f"Moving {m} -> {dest}")
            try:
                shutil.move(str(m), str(dest))
            except Exception as e:
                print(f"  ERROR moving {m}: {e}")

    if backup_root.exists() and any(backup_root.rglob("*")):
        print(f"Backup moved files into: {backup_root}")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Prune common bloat from a cx_Freeze build")
    p.add_argument("--build-dir", "-b", default="build/cx_freeze", help="Path to cx_Freeze build root")
    p.add_argument("--apply", action="store_true", help="Actually move matching files (default is dry-run)")
    p.add_argument("--delete", action="store_true", help="Permanently delete matches instead of moving (use with care)")
    p.add_argument("--pattern", "-p", action="append", help="Additional glob pattern to match (can be repeated)")
    p.add_argument("--force", action="store_true", help="Bypass safety check that build-dir contains 'build' or 'cx_freeze'")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    build_dir = Path(args.build_dir)
    if not build_dir.exists():
        print(f"Build directory does not exist: {build_dir}")
        sys.exit(2)

    # Safety: require a build-ish directory name unless forced
    if not args.force and not any(x in str(build_dir).lower() for x in ("build", "cx_freeze", "dist")):
        print("Refusing to operate on a directory that doesn't look like a build dir. Use --force to override.")
        sys.exit(2)

    patterns = list(DEFAULT_PATTERNS)
    if args.pattern:
        patterns.extend(args.pattern)

    matches = find_matches(build_dir, patterns)
    apply_prune(matches, build_dir, args.apply, args.delete)


if __name__ == "__main__":
    main()
