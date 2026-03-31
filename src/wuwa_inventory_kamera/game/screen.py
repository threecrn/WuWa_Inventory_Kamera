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

Usage::

    from wuwa_inventory_kamera.game.screen import GameWindow, capture, capture_region

    gw = GameWindow()
    layout = gw.layout            # ScreenLayout for the game's resolution
    img    = capture(gw)          # full-window RGB screenshot
    crop   = capture_region(gw, layout.echoes.echoCard)  # ROI crop
"""
from __future__ import annotations

import logging
import re

import numpy as np

logger = logging.getLogger(__name__)


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
    """

    def __init__(
        self,
        window_name: str | None = None,
        process_name: str | None = None,
    ) -> None:
        from wuwa_inventory_kamera.game.constants import PROCESS_NAME, WINDOW_NAME
        self._window_name = window_name or WINDOW_NAME
        self._process_name = process_name or PROCESS_NAME
        self._window = self._find_window()
        self._layout: ScreenLayout | None = None

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
    def dpi_scale(self) -> float:
        """DPI scaling factor (1.0 = 96 DPI, 1.25 = 120 DPI, …)."""
        if not self._window:
            return 1.0
        import ctypes
        try:
            user32 = ctypes.WinDLL('user32', use_last_error=True)
            dpi = user32.GetDpiForWindow(self._window._hWnd)
            return dpi / 96.0 if dpi else 1.0
        except Exception:
            return 1.0

    @property
    def size(self) -> tuple[int, int]:
        """Logical (width, height) of the game window (after DPI)."""
        if not self._window:
            return (1920, 1080)
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


def capture(gw: GameWindow) -> np.ndarray:
    """
    Capture the full game window as an RGB ``np.ndarray``.

    Parameters
    ----------
    gw:
        :class:`GameWindow` instance providing monitor and size info.
    """
    w, h = gw.size
    return capture_full(w, h, gw.monitor_index)


def capture_full(width: int, height: int, monitor: int = 1) -> np.ndarray:
    """Capture a full-window screenshot at the given dimensions."""
    sct = _mss()
    mon = sct.monitors[monitor]
    region = {
        'top': mon['top'],
        'left': mon['left'],
        'width': width,
        'height': height,
    }
    raw = sct.grab(region)
    return np.array(raw)[:, :, :3]  # BGRA → BGR; callers expect RGB


def capture_region(
    gw: GameWindow,
    roi,
) -> np.ndarray:
    """
    Capture a sub-region of the game window.

    Parameters
    ----------
    gw:
        :class:`GameWindow`.
    roi:
        An object with ``.x``, ``.y``, ``.w``, ``.h`` attributes (a
        ``Coordinates`` or ``ScreenInfoObject``).
    """
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
