"""
debug_ocr.py — OCR back-end diagnostic tool for stats crops
============================================================

Runs one or more stats extractors on a pair of cropped images
(``stats_name.png`` + ``stats_value.png``) and prints detailed output about
what each extractor detects.  Optionally saves annotated copies of the images
with detected bounding boxes drawn on them.

Usage
-----
Run against a specific echo debug directory::

    python cli/debug_ocr.py K:/wuwa/export/2026-03-07_17-42-36/raw/echo_0088/debug

Or supply the two image files directly::

    python cli/debug_ocr.py --name path/to/stats_name.png --value path/to/stats_value.png

Filter extractors (default: rapid, rapid_coord)::

    python cli/debug_ocr.py <dir> --extractor rapid rapid_coord tesser tesser_coord

Save annotated images alongside the originals::

    python cli/debug_ocr.py <dir> --annotate

Control the ``pad_px`` used by RapidOCR backends (default: 10)::

    python cli/debug_ocr.py <dir> --pad-px 0
    python cli/debug_ocr.py <dir> --pad-px 20
"""
from __future__ import annotations

import argparse
import sys
import logging
from pathlib import Path

import numpy as np

# ---------------------------------------------------------------------------
# Project path bootstrap
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scraping.processing.echoesProcessor import darken_background_preserve_edges_ndarray  # noqa: E402

logger = logging.getLogger('debug_ocr')


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_image(path: Path) -> np.ndarray:
    """Load *path* as an RGB numpy array."""
    from PIL import Image
    return np.array(Image.open(path).convert('RGB'))


def _annotate(image: np.ndarray, results: list, label: str, out_path: Path) -> None:
    """
    Draw OCR bounding boxes on *image* and save to *out_path*.

    Works on both grayscale (2-D) and colour (3-D) arrays.
    """
    import cv2
    if image.ndim == 2:
        vis = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    else:
        vis = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)

    for bbox, text, conf in results:
        pts = np.array([[int(pt[0]), int(pt[1])] for pt in bbox], dtype=np.int32)
        cv2.polylines(vis, [pts], isClosed=True, color=(0, 255, 0), thickness=1)
        x, y = pts[0]
        cv2.putText(
            vis, f'{text} ({conf:.2f})',
            (max(x, 0), max(y - 3, 10)),
            cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 255, 0), 1,
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_path), vis)
    print(f'    Annotated image saved → {out_path}')


def _print_token_table(results: list) -> None:
    """Pretty-print raw OCR token results."""
    if not results:
        print('    (no tokens detected)')
        return
    for i, (bbox, text, conf) in enumerate(results, 1):
        ys = [pt[1] for pt in bbox]
        y_min, y_max = min(ys), max(ys)
        print(f'    [{i:2d}] y={y_min:.0f}-{y_max:.0f}  conf={conf:.3f}  text={text!r}')


# ---------------------------------------------------------------------------
# Extractor runner
# ---------------------------------------------------------------------------

def _run_extractor(
    name: str,
    extractor,
    name_img: np.ndarray,
    value_img: np.ndarray,
    thorough: bool = False,
) -> None:
    """Run *extractor* and print a human-readable summary of the result."""
    print(f'\n{"="*60}')
    print(f'  Extractor: {name}{" (thorough retry)" if thorough else ""}')
    print(f'  {extractor!r}')
    print('=' * 60)

    try:
        if thorough:
            tune_lv, stats, trace = extractor.retry_execute(name_img, value_img, scan_index=0)
        else:
            tune_lv, stats, trace = extractor.execute(name_img, value_img, {}, scan_index=0)
    except Exception as exc:
        print(f'  ERROR: {exc}')
        raise

    print(f'\n  tune_lv  : {tune_lv}')
    print(f'  stats    : {stats}')

    print('\n  --- raw name OCR tokens ---')
    raw_names = trace.get('raw_names_ocr', [])
    if isinstance(raw_names, list) and raw_names and isinstance(raw_names[0], tuple):
        # coord extractor: list of (bbox, text, conf)
        _print_token_table(raw_names)
    else:
        for tok in raw_names:
            print(f'    {tok!r}')

    print('\n  --- matched names ---')
    for n in trace.get('matched_names', []):
        print(f'    {n!r}')

    print('\n  --- raw value OCR tokens ---')
    raw_vals = trace.get('raw_values_ocr', [])
    if isinstance(raw_vals, list) and raw_vals and isinstance(raw_vals[0], tuple):
        _print_token_table(raw_vals)
    else:
        for tok in raw_vals:
            print(f'    {tok!r}')


# ---------------------------------------------------------------------------
# Backend-level raw dump (bypasses the extractor layer)
# ---------------------------------------------------------------------------

def _dump_backend_raw(
    backend,
    name_img: np.ndarray,
    value_img: np.ndarray,
    thorough: bool = False,
) -> tuple:
    """Run *backend* directly and return the raw token lists for both crops."""
    recognize = getattr(backend, 'thorough_recognize', None) if thorough else None
    if recognize is None:
        recognize = backend.recognize
        if thorough:
            print('  (backend has no thorough_recognize — using recognize)')

    name_results  = recognize(name_img)
    value_results = recognize(value_img)
    label = 'thorough' if thorough else 'fast'
    print(f'\n  --- backend raw [{label}]: name crop ---')
    _print_token_table(name_results)
    print(f'\n  --- backend raw [{label}]: value crop ---')
    _print_token_table(value_results)
    return name_results, value_results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description='Debug OCR extractor output on stats crop images.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        'debug_dir', nargs='?', default=None,
        help='Echo debug directory containing stats_name.png and stats_value.png.',
    )
    p.add_argument('--name',  default=None, help='Explicit path to stats_name.png.')
    p.add_argument('--value', default=None, help='Explicit path to stats_value.png.')
    p.add_argument(
        '--extractor', nargs='+',
        choices=['rapid', 'rapid_coord', 'tesser', 'tesser_coord'],
        default=['rapid', 'rapid_coord'],
        help='Extractors to run (default: rapid rapid_coord).',
    )
    p.add_argument('--pad-px', type=int, default=10,
                   help='pad_px for RapidOCR backends (default: 10). Use 0 to disable.')
    p.add_argument('--fallback-text-score', type=float, default=0.3, metavar='SCORE',
                   help='text_score for the low-conf fallback RapidOCR pass '
                        '(default: 0.3). Use 0 to disable.')
    p.add_argument('--thorough', action='store_true',
                   help='Run thorough_recognize (multi-pass) instead of fast single-pass '
                        'for both the raw dump and the extractor calls.')
    p.add_argument('--annotate', action='store_true',
                   help='Save annotated bounding-box images alongside the originals.')
    p.add_argument('--no-preprocess', action='store_true',
                   help='Skip darken_background_preserve_edges_ndarray preprocessing.')
    p.add_argument('--log-level', default='WARNING',
                   choices=['TRACE', 'DEBUG', 'INFO', 'WARNING', 'ERROR'],
                   help='Log verbosity (default: WARNING).')
    return p


def main() -> None:
    args = _build_parser().parse_args()

    # Logging
    numeric_level = getattr(logging, args.log_level.upper(), logging.WARNING)
    logging.basicConfig(
        level=numeric_level,
        format='%(levelname)-8s %(name)s: %(message)s',
        stream=sys.stdout,
    )

    # Resolve image paths
    if args.debug_dir:
        debug_dir = Path(args.debug_dir)
        name_path  = debug_dir / 'stats_name.png'
        value_path = debug_dir / 'stats_value.png'
    elif args.name and args.value:
        name_path  = Path(args.name)
        value_path = Path(args.value)
        debug_dir  = name_path.parent
    else:
        print('ERROR: supply a debug directory or both --name and --value.', file=sys.stderr)
        sys.exit(1)

    for p in (name_path, value_path):
        if not p.exists():
            print(f'ERROR: file not found: {p}', file=sys.stderr)
            sys.exit(1)

    name_img_raw  = _load_image(name_path)
    value_img_raw = _load_image(value_path)

    if args.no_preprocess:
        name_img  = name_img_raw
        value_img = value_img_raw
        print('Preprocessing: disabled (using raw colour images)')
    else:
        name_img  = darken_background_preserve_edges_ndarray(name_img_raw)
        value_img = darken_background_preserve_edges_ndarray(value_img_raw)
        print('Preprocessing: darken_background_preserve_edges_ndarray applied')

    print(f'Name  image shape : {name_img.shape}')
    print(f'Value image shape : {value_img.shape}')

    # Build extractors
    rapid_backend = None
    extractors: dict[str, object] = {}

    fallback_score = args.fallback_text_score if args.fallback_text_score > 0 else None

    if any(k.startswith('rapid') for k in args.extractor):
        try:
            from scraping.ocr._rapidocr import RapidOcrBackend
            rapid_backend = RapidOcrBackend(
                pad_px=args.pad_px,
                fallback_text_score=fallback_score,
            )
        except Exception as exc:
            print(f'WARNING: could not load RapidOCR backend: {exc}')

    if 'rapid' in args.extractor and rapid_backend:
        from scraping.processing.statsExtractor import RapidOcrStatsExtractor
        extractors['rapid'] = RapidOcrStatsExtractor()
        extractors['rapid']._backend = rapid_backend  # type: ignore[attr-defined]

    if 'rapid_coord' in args.extractor and rapid_backend:
        from scraping.processing.statsExtractor import RapidOcrCoordStatsExtractor
        extractors['rapid_coord'] = RapidOcrCoordStatsExtractor(
            pad_px=args.pad_px,
            fallback_text_score=fallback_score,
        )

    if 'tesser' in args.extractor:
        try:
            from scraping.processing.statsExtractor import TesserOcrStatsExtractor
            extractors['tesser'] = TesserOcrStatsExtractor()
        except Exception as exc:
            print(f'WARNING: skipping tesser — {exc}')

    if 'tesser_coord' in args.extractor:
        try:
            from scraping.processing.statsExtractor import TesserOcrCoordStatsExtractor
            extractors['tesser_coord'] = TesserOcrCoordStatsExtractor()
        except Exception as exc:
            print(f'WARNING: skipping tesser_coord — {exc}')

    # Dump raw backend output first (useful for RapidOCR)
    if rapid_backend and any(k.startswith('rapid') for k in args.extractor):
        print('\n' + '#' * 60)
        print('  RAW BACKEND OUTPUT (RapidOcrBackend)')
        print('#' * 60)
        name_raw_tokens, value_raw_tokens = _dump_backend_raw(
            rapid_backend, name_img, value_img, thorough=args.thorough
        )
        if args.annotate:
            suffix = '_thorough' if args.thorough else ''
            _annotate(name_img,  name_raw_tokens,  'rapid_name',
                      debug_dir / f'debug_ocr_rapid_name{suffix}.png')
            _annotate(value_img, value_raw_tokens, 'rapid_value',
                      debug_dir / f'debug_ocr_rapid_value{suffix}.png')

    # Run each extractor
    for key, extractor in extractors.items():
        _run_extractor(key, extractor, name_img, value_img, thorough=args.thorough)

    print('\nDone.')


if __name__ == '__main__':
    main()
