#!/usr/bin/env python3
"""
check_cv2_imports.py
~~~~~~~~~~~~~~~~~~~~

Lint check to ensure that `cv2` is only imported inside the imgio backend
package. All other code should use the `imgio` module instead.

Exit code 0: No violations found.
Exit code 1: Found disallowed cv2 imports.

Usage:
    python tools/check_cv2_imports.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# Allow cv2 imports only in the backend module
ALLOWED_PATHS = [
    "src/wuwa_inventory_kamera/imgio/backends/cv2_backend.py",
]

# Patterns to match cv2 imports
CV2_IMPORT_PATTERNS = [
    re.compile(r"^import\s+cv2", re.MULTILINE),
    re.compile(r"^from\s+cv2\s+import", re.MULTILINE),
]


def check_file(path: Path) -> list[tuple[int, str]]:
    """Return list of (line_number, line_text) for cv2 imports in file."""
    violations = []
    try:
        content = path.read_text(encoding='utf-8')
        for line_no, line in enumerate(content.splitlines(), start=1):
            for pattern in CV2_IMPORT_PATTERNS:
                if pattern.match(line.strip()):
                    violations.append((line_no, line.strip()))
                    break
    except Exception as e:
        print(f"Warning: Could not read {path}: {e}", file=sys.stderr)
    return violations


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    src_dir = repo_root / "src"
    
    if not src_dir.exists():
        print(f"Error: Source directory not found: {src_dir}", file=sys.stderr)
        return 1
    
    # Normalize allowed paths to absolute
    allowed_absolute = {
        (repo_root / p).resolve() for p in ALLOWED_PATHS
    }
    
    # Find all Python files
    python_files = list(src_dir.rglob("*.py"))
    
    violations_found = False
    
    for py_file in python_files:
        if py_file.resolve() in allowed_absolute:
            continue
        
        violations = check_file(py_file)
        if violations:
            violations_found = True
            rel_path = py_file.relative_to(repo_root)
            print(f"\n{rel_path}:")
            for line_no, line_text in violations:
                print(f"  Line {line_no}: {line_text}")
    
    if violations_found:
        print("\nERROR: Found disallowed cv2 imports.")
        print("Please use 'from wuwa_inventory_kamera import imgio' instead.")
        return 1
    
    print("✓ No disallowed cv2 imports found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
