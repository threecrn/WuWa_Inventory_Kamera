"""
tools/check_printwindow/main.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Spike tool: tests whether ``PrintWindow`` / ``PW_RENDERFULLCONTENT`` can
produce a valid (non-black) frame from the running WuWa game process.

This is the first step of Phase 1 described in WINDOWED_MODE_FEASIBILITY.md
§ 2.3 — the answer determines which screenshot backend to build for
windowed-mode support.

Usage
-----
    python tools/check_printwindow/main.py [--out OUTPUT_DIR]

The tool:
  1. Finds the game window (same logic as ``GameWindow``).
  2. Captures via ``mss`` (current production method).
  3. Captures via ``PrintWindow(PW_RENDERFULLCONTENT)`` (candidate method).
  4. Saves both PNGs to OUTPUT_DIR (default: ``tools/check_printwindow/out/``).
  5. Prints a verdict: whether the PrintWindow frame is non-black and whether
     it visually matches the mss frame (mean absolute pixel difference).

Exit codes
----------
  0 — PrintWindow returned a usable, matching frame.
  1 — PrintWindow returned a black frame or a frame that diverges too much
      from the mss reference.
  2 — Game window not found.
"""
from __future__ import annotations

import argparse
import ctypes
import sys
from pathlib import Path

import numpy as np


# ---------------------------------------------------------------------------
# Win32 helpers
# ---------------------------------------------------------------------------

PW_RENDERFULLCONTENT = 0x00000002  # capture DX/GL surface, Win10 1903+

user32   = ctypes.WinDLL('user32',   use_last_error=True)
gdi32    = ctypes.WinDLL('gdi32',    use_last_error=True)
kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)


def _find_hwnd(window_name: str, process_name: str) -> int | None:
    """Return the HWND of the first matching window, or None."""
    import pywinctl as pwc

    for win in pwc.getWindowsWithTitle(
        title=window_name,
        app=process_name,
        condition=pwc.Re.CONTAINS,
    ):
        return win._hWnd
    return None


def _client_rect(hwnd: int) -> tuple[int, int, int, int]:
    """
    Return ``(left, top, width, height)`` of the client area in screen pixels.
    """
    import win32gui

    # Client origin in screen coordinates
    cx, cy = win32gui.ClientToScreen(hwnd, (0, 0))
    # Client dimensions (always starts at 0,0 in client space)
    _, _, cw, ch = win32gui.GetClientRect(hwnd)
    return cx, cy, cw, ch


def capture_mss(
    hwnd: int,
) -> np.ndarray:
    """Capture the game client area via ``mss`` (current production method)."""
    from mss import mss as _mss

    cx, cy, cw, ch = _client_rect(hwnd)
    with _mss() as sct:
        region = {'top': cy, 'left': cx, 'width': cw, 'height': ch}
        raw = sct.grab(region)
        return np.array(raw)[:, :, :3]  # BGRA → BGR


def capture_printwindow(hwnd: int) -> np.ndarray:
    """
    Capture the game window via ``PrintWindow(PW_RENDERFULLCONTENT)``.

    Creates a compatible memory DC + HBITMAP sized to the client area,
    calls ``PrintWindow``, then reads the pixels back into a numpy array.

    Returns BGR uint8 array of shape ``(h, w, 3)``.
    """
    import win32gui

    _, _, cw, ch = _client_rect(hwnd)

    # Create a memory DC compatible with the desktop
    hdc_screen = user32.GetDC(0)
    hdc_mem    = gdi32.CreateCompatibleDC(hdc_screen)

    # --- BITMAPINFOHEADER (40 bytes) for a top-down DIB -----------------
    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ('biSize',          ctypes.c_uint32),
            ('biWidth',         ctypes.c_int32),
            ('biHeight',        ctypes.c_int32),
            ('biPlanes',        ctypes.c_uint16),
            ('biBitCount',      ctypes.c_uint16),
            ('biCompression',   ctypes.c_uint32),
            ('biSizeImage',     ctypes.c_uint32),
            ('biXPelsPerMeter', ctypes.c_int32),
            ('biYPelsPerMeter', ctypes.c_int32),
            ('biClrUsed',       ctypes.c_uint32),
            ('biClrImportant',  ctypes.c_uint32),
        ]

    bmi = BITMAPINFOHEADER()
    bmi.biSize      = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.biWidth     = cw
    bmi.biHeight    = -ch  # negative = top-down
    bmi.biPlanes    = 1
    bmi.biBitCount  = 32   # BGRA
    bmi.biCompression = 0  # BI_RGB

    # Allocate DIB with a raw pixel pointer
    p_bits = ctypes.c_void_p()
    hbmp = gdi32.CreateDIBSection(
        hdc_screen,
        ctypes.byref(bmi),
        0,                       # DIB_RGB_COLORS
        ctypes.byref(p_bits),
        None,
        0,
    )

    try:
        old_bmp = gdi32.SelectObject(hdc_mem, hbmp)

        # PrintWindow — flag 2 asks for the GPU surface content
        result = user32.PrintWindow(hwnd, hdc_mem, PW_RENDERFULLCONTENT)

        gdi32.SelectObject(hdc_mem, old_bmp)

        if not result:
            raise RuntimeError(
                f'PrintWindow returned 0; GetLastError={ctypes.get_last_error()}'
            )

        # Copy pixels from the DIB into a numpy array (BGRA, top-down)
        buf_size = cw * ch * 4
        buf = (ctypes.c_uint8 * buf_size).from_address(p_bits.value)
        arr = np.frombuffer(buf, dtype=np.uint8).reshape(ch, cw, 4)
        return arr[:, :, :3].copy()  # drop alpha → BGR
    finally:
        gdi32.DeleteObject(hbmp)
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(0, hdc_screen)


# ---------------------------------------------------------------------------
# Verdict helpers
# ---------------------------------------------------------------------------

_BLACK_THRESHOLD  = 5    # mean pixel value below which frame is "black"
_MATCH_THRESHOLD  = 30   # mean absolute difference above which frames diverge


def _is_black(img: np.ndarray) -> bool:
    return float(img.mean()) < _BLACK_THRESHOLD


def _mean_abs_diff(a: np.ndarray, b: np.ndarray) -> float:
    """Resize ``b`` to ``a``'s shape if needed, then compute MAD."""
    if a.shape != b.shape:
        import cv2
        b = cv2.resize(b, (a.shape[1], a.shape[0]), interpolation=cv2.INTER_AREA)
    return float(np.abs(a.astype(np.int16) - b.astype(np.int16)).mean())


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    from wuwa_inventory_kamera.game.constants import PROCESS_NAME, WINDOW_NAME

    parser = argparse.ArgumentParser(
        description='Test PrintWindow capture against mss for the WuWa game window.',
    )
    parser.add_argument(
        '--out', default='tools/check_printwindow/out',
        help='Directory to write captured PNGs into (default: tools/check_printwindow/out)',
    )
    parser.add_argument(
        '--window-name', default=WINDOW_NAME,
        help=f'Game window title substring (default: {WINDOW_NAME!r})',
    )
    parser.add_argument(
        '--process-name', default=PROCESS_NAME,
        help=f'Game process name substring (default: {PROCESS_NAME!r})',
    )
    args = parser.parse_args(argv)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── 1. Find window ────────────────────────────────────────────────────
    print(f'Searching for window: {args.window_name!r} / {args.process_name!r} …')
    hwnd = _find_hwnd(args.window_name, args.process_name)
    if hwnd is None:
        print('ERROR: game window not found.  Is the game running?', file=sys.stderr)
        return 2

    import win32gui
    _, _, cw, ch = _client_rect(hwnd)
    print(f'Found  HWND=0x{hwnd:08X}  client={cw}x{ch}')

    # ── 2. mss capture ────────────────────────────────────────────────────
    print('\n[mss] capturing ... ', end='', flush=True)
    img_mss = capture_mss(hwnd)
    path_mss = out_dir / 'capture_mss.png'
    _save_png(img_mss, path_mss)
    print(f'saved -> {path_mss}  (mean={img_mss.mean():.1f})')

    # ── 3. PrintWindow capture ────────────────────────────────────────────
    print('[PW]  capturing ... ', end='', flush=True)
    pw_error: str | None = None
    img_pw: np.ndarray | None = None
    try:
        img_pw = capture_printwindow(hwnd)
        path_pw = out_dir / 'capture_printwindow.png'
        _save_png(img_pw, path_pw)
        print(f'saved -> {path_pw}  (mean={img_pw.mean():.1f})')
    except Exception as exc:
        pw_error = str(exc)
        print(f'FAILED -- {exc}')

    # ── 4. Verdict ────────────────────────────────────────────────────────
    print()
    if pw_error:
        print(f'VERDICT: FAIL -- PrintWindow raised an exception: {pw_error}')
        print('  -> Fall back to mss + HWND_TOPMOST strategy.')
        return 1

    if _is_black(img_pw):
        print(
            f'VERDICT: FAIL -- PrintWindow returned a black frame '
            f'(mean pixel value {img_pw.mean():.2f} < threshold {_BLACK_THRESHOLD}).'
        )
        print('  -> The DX renderer did not cooperate with PrintWindow.')
        print('  -> Fall back to mss + HWND_TOPMOST strategy.')
        return 1

    mad = _mean_abs_diff(img_mss, img_pw)
    if mad > _MATCH_THRESHOLD:
        print(
            f'VERDICT: WARN -- PrintWindow returned a non-black frame but it differs '
            f'significantly from the mss reference (MAD={mad:.1f} > {_MATCH_THRESHOLD}).'
        )
        print('  -> Inspect the two saved PNGs manually before adopting PrintWindow.')
        return 0

    print(
        f'VERDICT: PASS -- PrintWindow frame is valid and matches mss '
        f'(MAD={mad:.1f} <= {_MATCH_THRESHOLD}).'
    )
    print('  -> PrintWindow / PW_RENDERFULLCONTENT is safe to adopt for windowed mode.')
    return 0


def _save_png(img: np.ndarray, path: Path) -> None:
    """Save BGR uint8 array as PNG (uses cv2 if available, else PIL)."""
    try:
        import cv2
        cv2.imwrite(str(path), img)
    except ImportError:
        from PIL import Image
        Image.fromarray(img[:, :, ::-1]).save(path)  # BGR → RGB for PIL


if __name__ == '__main__':
    sys.exit(main())
