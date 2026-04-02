"""
wuwa_inventory_kamera.game.screen
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Window detection, screenshot capture, and screen-layout resolution.

This module provides:

* :class:`GameWindow` — finds the WuWa window, reads its geometry and DPI,
  and produces a :class:`ScreenLayout` that maps every UI element to pixel
  coordinates for the current resolution.
* :func:`capture` / :func:`capture_region` — screenshot helpers that return
  RGB ``np.ndarray`` images using ``mss``.
* :class:`ScreenLayout` — resolution-aware coordinate tree wrapping the
  existing ``game.gameROI.COORDINATES`` data.

These are all **Qt-free** and can be used from CLI tools or the UI.

Windowed-mode support
~~~~~~~~~~~~~~~~~~~~~
When the game runs in windowed mode the screenshots and input coordinates
must be relative to the **client area** of the window (the renderable
region, excluding the title bar and borders).

:class:`GameWindow` exposes ``client_origin`` and ``client_size`` for this
purpose.  The ``capture_*`` functions accept an explicit ``origin`` tuple
instead of a monitor index so they work for both fullscreen (monitor
origin) and windowed (client-area origin) modes.

``PrintWindow`` with ``PW_RENDERFULLCONTENT`` is used as the capture
backend in windowed mode, producing occlusion-immune screenshots.

Usage::

    from wuwa_inventory_kamera.game.screen import GameWindow, capture, capture_region

    gw = GameWindow()
    layout = gw.layout            # ScreenLayout for the game's resolution
    img    = capture(gw)          # full-window RGB screenshot
    crop   = capture_region(gw, layout.echoes.echoCard)  # ROI crop
"""
from __future__ import annotations

import ctypes
import enum
import logging
import re

import numpy as np

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Capture backend enum
# ---------------------------------------------------------------------------

class CaptureBackend(enum.Enum):
    """Screenshot capture method."""
    MSS = 'mss'                    # screen-space grab (fullscreen default)
    PRINTWINDOW = 'printwindow'    # Win32 PrintWindow (windowed default)


# ---------------------------------------------------------------------------
# ScreenLayout — thin wrapper around the existing ScreenInfo / gameROI
# ---------------------------------------------------------------------------

class ScreenLayout:
    """
    Resolution-aware coordinate tree for every game UI element.

    Wraps ``game.screenInfo.ScreenInfo`` so the rest of the v2 code never
    imports it directly.  Attribute access is forwarded to the underlying
    ``ScreenInfoObject``, so ``layout.echoes.fullStatsName.x`` etc. all
    work.

    Parameters
    ----------
    width, height:
        Game window client dimensions in **logical** pixels (after DPI).
    monitor:
        1-based mss monitor index.
    """

    def __init__(self, width: int, height: int, monitor: int = 1) -> None:
        from wuwa_inventory_kamera.game.screen_info import ScreenInfo
        self._si = ScreenInfo(width, height, monitor)
        self.width = width
        self.height = height
        self.monitor = monitor

    def __getattr__(self, name: str):
        # Delegate attribute lookup to the inner ScreenInfo (which in turn
        # delegates to its ScreenInfoObject tree).
        return getattr(self._si, name)

    def __repr__(self) -> str:
        return f'ScreenLayout({self.width}x{self.height}, monitor={self.monitor})'


# ---------------------------------------------------------------------------
# GameWindow — window detection + geometry
# ---------------------------------------------------------------------------

MIN_CLIENT_WIDTH  = 1280
MIN_CLIENT_HEIGHT = 720


class GameWindow:
    """
    Locates the WuWa game window and provides geometry, DPI, and screenshot
    facilities.

    Parameters
    ----------
    window_name:
        Substring of the game window title.
    process_name:
        Substring of the process executable name.
    windowed:
        If *True*, coordinates and captures are relative to the window's
        client area (using ``PrintWindow``).  If *False* (default),
        coordinates are relative to the monitor origin (using ``mss``).
    """

    def __init__(
        self,
        window_name: str | None = None,
        process_name: str | None = None,
        *,
        windowed: bool = False,
    ) -> None:
        from wuwa_inventory_kamera.game.constants import PROCESS_NAME, WINDOW_NAME
        self._window_name = window_name or WINDOW_NAME
        self._process_name = process_name or PROCESS_NAME
        self._window = self._find_window()
        self._layout: ScreenLayout | None = None
        self.windowed = windowed

    # ── Window discovery ─────────────────────────────────────────────────

    def _find_window(self):
        import pywinctl as pwc
        for win in pwc.getWindowsWithTitle(
            title=self._window_name,
            app=self._process_name,
            condition=pwc.Re.CONTAINS,
        ):
            return win
        logger.warning(
            'Game window not found (title=%r, process=%r)',
            self._window_name, self._process_name,
        )
        return None

    @property
    def found(self) -> bool:
        return self._window is not None

    # ── Geometry ─────────────────────────────────────────────────────────

    @property
    def hwnd(self) -> int | None:
        """Native Win32 window handle (HWND), or *None* if not found."""
        if not self._window:
            return None
        return self._window._hWnd

    @property
    def client_origin(self) -> tuple[int, int]:
        """Top-left of the game client area in physical screen pixels."""
        if not self._window:
            return (0, 0)
        import win32gui
        return win32gui.ClientToScreen(self._window._hWnd, (0, 0))

    @property
    def client_size(self) -> tuple[int, int]:
        """(width, height) of the client area in physical pixels."""
        if not self._window:
            return (1920, 1080)
        import win32gui
        _, _, cw, ch = win32gui.GetClientRect(self._window._hWnd)
        return (cw, ch)

    @property
    def capture_backend(self) -> CaptureBackend:
        """Capture method determined by the windowed flag."""
        return CaptureBackend.PRINTWINDOW if self.windowed else CaptureBackend.MSS

    @property
    def dpi_scale(self) -> float:
        """DPI scaling factor (1.0 = 96 DPI, 1.25 = 120 DPI, ...)."""
        if not self._window:
            return 1.0
        try:
            user32 = ctypes.WinDLL('user32', use_last_error=True)
            dpi = user32.GetDpiForWindow(self._window._hWnd)
            return dpi / 96.0 if dpi else 1.0
        except Exception:
            return 1.0

    @property
    def size(self) -> tuple[int, int]:
        """Logical (width, height) of the game viewport (after DPI)."""
        if not self._window:
            return (1920, 1080)
        if self.windowed:
            cw, ch = self.client_size
            dpi = self.dpi_scale
            return (int(cw / dpi), int(ch / dpi))
        dpi = self.dpi_scale
        return (int(self._window.width / dpi), int(self._window.height / dpi))

    @property
    def monitor_index(self) -> int:
        """1-based mss monitor index the game window is on."""
        if not self._window:
            return 1
        display = self._window.getDisplay()[0]
        m = re.search(r'\d+', display)
        return int(m.group()) if m else 1

    @property
    def layout(self) -> ScreenLayout:
        """
        :class:`ScreenLayout` for the current game window geometry.

        The layout is computed lazily and cached.
        """
        if self._layout is None:
            w, h = self.size
            self._layout = ScreenLayout(w, h, self.monitor_index)
        return self._layout

    @property
    def origin(self) -> tuple[int, int]:
        """
        Screen-pixel origin for coordinate math.

        In fullscreen mode this is the monitor's top-left corner.
        In windowed mode this is the client-area top-left corner.
        """
        if self.windowed:
            return self.client_origin
        sct = _mss()
        mon = sct.monitors[self.monitor_index]
        return (mon['left'], mon['top'])

    def check_minimum_size(self) -> None:
        """
        Raise if the client area is too small for reliable OCR.

        Only relevant in windowed mode — fullscreen resolutions are
        inherently large enough.
        """
        if not self.windowed:
            return
        w, h = self.size
        if w < MIN_CLIENT_WIDTH or h < MIN_CLIENT_HEIGHT:
            raise RuntimeError(
                f'Game window is too small for reliable scanning '
                f'({w}x{h}; minimum {MIN_CLIENT_WIDTH}x{MIN_CLIENT_HEIGHT}). '
                f'Resize the game window and try again.'
            )

    # ── Foreground management ────────────────────────────────────────────

    def activate(self) -> bool:
        """
        Bring the game window to the foreground.  Returns success.

        Uses ``AttachThreadInput`` to temporarily borrow the foreground
        permission from whichever thread currently owns it, then calls
        ``SetForegroundWindow`` directly.  This avoids the synthetic ALT
        key-press workaround, which injects a stale key event into the
        calling process's console input buffer.
        """
        if not self._window:
            return False
        import ctypes
        import win32con
        import win32gui

        hwnd = self._window._hWnd

        # Restore the window if it is minimised.
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)

        user32   = ctypes.WinDLL('user32', use_last_error=True)
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)

        fg_hwnd = user32.GetForegroundWindow()
        if fg_hwnd and fg_hwnd != hwnd:
            fg_tid = user32.GetWindowThreadProcessId(fg_hwnd, None)
            my_tid = kernel32.GetCurrentThreadId()
            user32.AttachThreadInput(fg_tid, my_tid, True)
            try:
                user32.SetForegroundWindow(hwnd)
            finally:
                user32.AttachThreadInput(fg_tid, my_tid, False)
        else:
            user32.SetForegroundWindow(hwnd)

        return True

    def is_foreground(self) -> bool:
        """Check whether the game window is currently in the foreground."""
        if not self._window:
            return False
        return self._window.isActive


# ---------------------------------------------------------------------------
# Screenshot helpers
# ---------------------------------------------------------------------------

def _mss():
    """Lazily import and return a thread-local mss instance."""
    from mss import mss
    return mss()


# ── PrintWindow backend ──────────────────────────────────────────────────

_PW_CLIENTONLY        = 0x00000001
_PW_RENDERFULLCONTENT = 0x00000002  # Win10 1903+

_user32 = ctypes.WinDLL('user32', use_last_error=True)
_gdi32  = ctypes.WinDLL('gdi32',  use_last_error=True)


class _BITMAPINFOHEADER(ctypes.Structure):
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


def _capture_printwindow(hwnd: int, width: int, height: int) -> np.ndarray:
    """
    Capture the game window via ``PrintWindow(PW_RENDERFULLCONTENT)``.

    Returns BGR uint8 array of shape ``(height, width, 3)``.
    """
    hdc_screen = _user32.GetDC(0)
    hdc_mem    = _gdi32.CreateCompatibleDC(hdc_screen)

    bmi = _BITMAPINFOHEADER()
    bmi.biSize      = ctypes.sizeof(_BITMAPINFOHEADER)
    bmi.biWidth     = width
    bmi.biHeight    = -height  # negative = top-down
    bmi.biPlanes    = 1
    bmi.biBitCount  = 32       # BGRA
    bmi.biCompression = 0      # BI_RGB

    p_bits = ctypes.c_void_p()
    hbmp = _gdi32.CreateDIBSection(
        hdc_screen, ctypes.byref(bmi), 0,
        ctypes.byref(p_bits), None, 0,
    )
    try:
        old_bmp = _gdi32.SelectObject(hdc_mem, hbmp)
        result = _user32.PrintWindow(hwnd, hdc_mem, _PW_CLIENTONLY | _PW_RENDERFULLCONTENT)
        _gdi32.SelectObject(hdc_mem, old_bmp)
        if not result:
            raise RuntimeError(
                f'PrintWindow failed (GetLastError={ctypes.get_last_error()})'
            )
        buf_size = width * height * 4
        buf = (ctypes.c_uint8 * buf_size).from_address(p_bits.value)
        arr = np.frombuffer(buf, dtype=np.uint8).reshape(height, width, 4)
        return arr[:, :, :3].copy()  # BGRA -> BGR
    finally:
        _gdi32.DeleteObject(hbmp)
        _gdi32.DeleteDC(hdc_mem)
        _user32.ReleaseDC(0, hdc_screen)


# ── Public capture API ───────────────────────────────────────────────────

def capture(gw: GameWindow) -> np.ndarray:
    """
    Capture the full game viewport as an RGB ``np.ndarray``.

    Uses PrintWindow in windowed mode, mss in fullscreen mode.
    """
    w, h = gw.size
    if gw.windowed:
        return _capture_printwindow(gw.hwnd, w, h)
    return _capture_full_mss(w, h, gw.monitor_index)


def capture_full(
    width: int,
    height: int,
    monitor: int = 1,
    *,
    gw: GameWindow | None = None,
) -> np.ndarray:
    """
    Capture a full-viewport screenshot at the given dimensions.

    Parameters
    ----------
    width, height:
        Logical viewport dimensions.
    monitor:
        1-based mss monitor index (used in fullscreen / legacy mode).
    gw:
        If provided **and** ``gw.windowed`` is True, uses PrintWindow
        instead of mss.
    """
    if gw is not None and gw.windowed:
        return _capture_printwindow(gw.hwnd, width, height)
    return _capture_full_mss(width, height, monitor)


def _capture_full_mss(width: int, height: int, monitor: int = 1) -> np.ndarray:
    """mss-based full screenshot (original fullscreen path)."""
    sct = _mss()
    mon = sct.monitors[monitor]
    region = {
        'top': mon['top'],
        'left': mon['left'],
        'width': width,
        'height': height,
    }
    raw = sct.grab(region)
    return np.array(raw)[:, :, :3]


def capture_region(
    gw: GameWindow,
    roi,
) -> np.ndarray:
    """
    Capture a sub-region of the game viewport.

    Parameters
    ----------
    gw:
        :class:`GameWindow`.
    roi:
        An object with ``.x``, ``.y``, ``.w``, ``.h`` attributes.
    """
    if gw.windowed:
        # PrintWindow grabs the whole client area; crop in software.
        full = _capture_printwindow(gw.hwnd, *gw.size)
        return full[
            int(roi.y) : int(roi.y + roi.h),
            int(roi.x) : int(roi.x + roi.w),
        ]
    sct = _mss()
    mon = sct.monitors[gw.monitor_index]
    region = {
        'top': mon['top'] + int(roi.y),
        'left': mon['left'] + int(roi.x),
        'width': int(roi.w),
        'height': int(roi.h),
    }
    raw = sct.grab(region)
    return np.array(raw)[:, :, :3]
