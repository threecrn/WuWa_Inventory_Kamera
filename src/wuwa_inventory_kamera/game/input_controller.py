"""
wuwa_inventory_kamera.game.input_controller
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Low-level input simulation for the WuWa game window.

This module wraps win32api-based mouse, keyboard, and scroll operations
in a clean interface that can be consumed by navigation and scanning code
without coupling to the Qt UI.

The :class:`InputController` class is the single entry point.  All
coordinates are **relative to the game window's monitor**, matching the
conventions established in the original ``scraping.utils.mouse_keyboard``
module.

Usage::

    from wuwa_inventory_kamera.game.input_controller import InputController

    ctrl = InputController(monitor_index=1)
    ctrl.click(500, 300)
    ctrl.scroll(-3)            # scroll up 3 notches
    ctrl.press_key('esc')
    ctrl.hotkey('ctrl', 'v')   # Ctrl+V
"""
from __future__ import annotations

import logging
import time
from typing import Union

logger = logging.getLogger(__name__)


class InputController:
    """
    Win32-based mouse / keyboard / scroll controller.

    All public methods accept optional *wait* overrides (seconds to
    ``time.sleep`` after the action).  The defaults are conservative
    enough for the game to register every input.

    Parameters
    ----------
    monitor_index:
        1-based mss monitor index.  ``1`` is the primary monitor.
    get_origin:
        Optional callable returning ``(left, top)`` in screen pixels.
        When provided the returned coordinates are used as the origin for
        mouse input instead of the monitor's top-left corner.  This is
        used in windowed mode so the origin tracks the game window's
        client area.
    """

    # ── Key scancode tables ──────────────────────────────────────────────

    _OFFSET_EXTENDED = 0x0100
    _OFFSET_SHIFT    = 0x0200
    _SHIFT_SCANCODE  = 0x2A

    MODIFIER_KEYS: dict[str, int] = {
        'ctrl':  0x1D,
        'alt':   0x38,
        'shift': _SHIFT_SCANCODE,
        'win':   0x5B + _OFFSET_EXTENDED,
    }

    KEY_MAP: dict[str, int] = {
        # Function keys
        'f1': 0x3B, 'f2': 0x3C, 'f3': 0x3D, 'f4': 0x3E,
        'f5': 0x3F, 'f6': 0x40, 'f7': 0x41, 'f8': 0x42,
        'f9': 0x43, 'f10': 0x44, 'f11': 0x57, 'f12': 0x58,
        # Navigation / special
        'escape': 0x01, 'esc': 0x01, 'enter': 0x1C, 'return': 0x1C,
        'tab': 0x0F, 'space': 0x39, 'backspace': 0x0E,
        'delete': 0x53 + _OFFSET_EXTENDED, 'del': 0x53 + _OFFSET_EXTENDED,
        'end': 0x4F + _OFFSET_EXTENDED,
        # Modifiers (also usable as stand-alone presses)
        'shift': _SHIFT_SCANCODE, 'ctrl': 0x1D, 'alt': 0x38,
        'capslock': 0x3A,
        # Alphanumeric
        '0': 0x0B, '1': 0x02, '2': 0x03, '3': 0x04, '4': 0x05,
        '5': 0x06, '6': 0x07, '7': 0x08, '8': 0x09, '9': 0x0A,
        'a': 0x1E, 'b': 0x30, 'c': 0x2E, 'd': 0x20, 'e': 0x12,
        'f': 0x21, 'g': 0x22, 'h': 0x23, 'i': 0x17, 'j': 0x24,
        'k': 0x25, 'l': 0x26, 'm': 0x32, 'n': 0x31, 'o': 0x18,
        'p': 0x19, 'q': 0x10, 'r': 0x13, 's': 0x1F, 't': 0x14,
        'u': 0x16, 'v': 0x2F, 'w': 0x11, 'x': 0x2D, 'y': 0x15,
        'z': 0x2C,
        # Punctuation
        '`': 0x29, '-': 0x0C, '=': 0x0D, '[': 0x1A, ']': 0x1B,
        '\\': 0x2B, ';': 0x27, "'": 0x28, ',': 0x33, '.': 0x34,
        '/': 0x35,
        # Shifted punctuation
        '~': 0x29 + _OFFSET_SHIFT, '!': 0x02 + _OFFSET_SHIFT,
        '@': 0x03 + _OFFSET_SHIFT, '#': 0x04 + _OFFSET_SHIFT,
        '$': 0x05 + _OFFSET_SHIFT, '%': 0x06 + _OFFSET_SHIFT,
        '^': 0x07 + _OFFSET_SHIFT, '&': 0x08 + _OFFSET_SHIFT,
        '*': 0x09 + _OFFSET_SHIFT, '(': 0x0A + _OFFSET_SHIFT,
        ')': 0x0B + _OFFSET_SHIFT, '_': 0x0C + _OFFSET_SHIFT,
        '+': 0x0D + _OFFSET_SHIFT, '{': 0x1A + _OFFSET_SHIFT,
        '}': 0x1B + _OFFSET_SHIFT, '|': 0x2B + _OFFSET_SHIFT,
        '"': 0x28 + _OFFSET_SHIFT, '<': 0x33 + _OFFSET_SHIFT,
        '>': 0x34 + _OFFSET_SHIFT, '?': 0x35 + _OFFSET_SHIFT,
        ' ': 0x39,
    }

    # ── Construction ─────────────────────────────────────────────────────

    def __init__(
        self,
        monitor_index: int = 1,
        get_origin: 'Callable[[], tuple[int, int]] | None' = None,
    ) -> None:
        import win32api as _w32
        from mss import mss

        self._w32 = _w32
        self._sct = mss()
        self._monitor_index = monitor_index
        self._monitor = self._sct.monitors[monitor_index]
        self._get_origin = get_origin

    @property
    def monitor_index(self) -> int:
        return self._monitor_index

    @property
    def monitor_rect(self) -> dict:
        """mss monitor dict with ``top``, ``left``, ``width``, ``height``."""
        return self._monitor

    # ── Mouse ────────────────────────────────────────────────────────────

    def _origin(self) -> tuple[int, int]:
        """Return the current (left, top) origin in screen pixels."""
        if self._get_origin is not None:
            return self._get_origin()
        return (self._monitor['left'], self._monitor['top'])

    def move(self, x: Union[int, float], y: Union[int, float], wait: float = 0.1) -> None:
        """Move the cursor to (*x*, *y*) relative to the game viewport."""
        ox, oy = self._origin()
        abs_x = int(x) + ox
        abs_y = int(y) + oy
        self._w32.SetCursorPos((abs_x, abs_y))
        time.sleep(wait)

    def click(self, x: Union[int, float], y: Union[int, float], wait: float = 0.1) -> None:
        """Left-click at (*x*, *y*) relative to the game monitor."""
        import win32con
        self.move(x, y, wait=0.0)
        cx, cy = self._w32.GetCursorPos()
        self._w32.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, cx, cy, 0, 0)
        self._w32.mouse_event(win32con.MOUSEEVENTF_LEFTUP, cx, cy, 0, 0)
        time.sleep(wait)

    def drag(self, x1: Union[int, float], y1: Union[int, float],
             x2: Union[int, float], y2: Union[int, float],
             wait: float = 0.1, wait_after: float = 0.1, steps: int = 20) -> None:
        """Hold left button at (*x1*, *y1*), move to (*x2*, *y2*), release.

        Movement is sent as incremental ``MOUSEEVENTF_MOVE`` events so the
        game registers the drag rather than a teleport.
        """
        import win32con
        self.move(x1, y1, wait=0.05)
        cx, cy = self._w32.GetCursorPos()
        self._w32.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, cx, cy, 0, 0)
        time.sleep(0.05)
        dx = int(x2) - int(x1)
        dy = int(y2) - int(y1)
        for i in range(1, steps + 1):
            step_dx = round(dx * i / steps) - round(dx * (i - 1) / steps)
            step_dy = round(dy * i / steps) - round(dy * (i - 1) / steps)
            self._w32.mouse_event(win32con.MOUSEEVENTF_MOVE, step_dx, step_dy, 0, 0)
            time.sleep(0.01)
        cx, cy = self._w32.GetCursorPos()
        time.sleep(wait)
        self._w32.mouse_event(win32con.MOUSEEVENTF_LEFTUP, cx, cy, 0, 0)
        time.sleep(wait_after)

    def scroll(self, amount: Union[int, float], wait: float = 0.1) -> None:
        """
        Scroll the mouse wheel.

        *amount* > 0 scrolls **down** (towards the user); < 0 scrolls
        **up**.  The value is scaled by 120 internally to match Windows
        conventions.
        """
        import win32con
        scaled = int(amount * 120)
        self._w32.mouse_event(win32con.MOUSEEVENTF_WHEEL, 0, 0, scaled, 0)
        time.sleep(wait)

    # ── Keyboard ─────────────────────────────────────────────────────────

    def press_key(self, key: str, wait: float = 0.1) -> None:
        """
        Press and release a single key.

        *key* is looked up in :attr:`KEY_MAP`.  Keys that require Shift
        (e.g. ``'@'``) are handled transparently.
        """
        code = self.KEY_MAP.get(key.lower())
        if code is None:
            logger.warning('Unknown key %r — ignoring', key)
            return

        needs_shift = bool(code & self._OFFSET_SHIFT)
        scancode = code & 0xFF

        if needs_shift:
            self._scan_down(self._SHIFT_SCANCODE)
        self._scan_down(scancode)
        self._scan_up(scancode)
        if needs_shift:
            self._scan_up(self._SHIFT_SCANCODE)

        time.sleep(wait)

    def hotkey(self, *keys: str, wait: float = 0.1) -> None:
        """
        Press a multi-key combination (e.g. ``hotkey('ctrl', 'v')``).

        Modifiers are held in order, the final key is tapped, then
        modifiers are released in reverse order.
        """
        codes: list[int] = []
        for k in keys:
            code = self.MODIFIER_KEYS.get(k.lower()) or self.KEY_MAP.get(k.lower())
            if code is None:
                logger.warning('Unknown key %r in hotkey — ignoring', k)
                return
            codes.append(code & 0xFF)

        for sc in codes[:-1]:
            self._scan_down(sc)
        self._scan_down(codes[-1])
        self._scan_up(codes[-1])
        for sc in reversed(codes[:-1]):
            self._scan_up(sc)

        time.sleep(wait)

    def type_text(self, text: str, interval: float = 0.03) -> None:
        """Type *text* character by character."""
        for ch in text:
            self.press_key(ch, wait=interval)

    # ── Clipboard paste helper ───────────────────────────────────────────

    @staticmethod
    def copy_to_clipboard(text: str) -> None:
        """Copy *text* to the Windows clipboard."""
        import win32clipboard
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardText(text, win32clipboard.CF_UNICODETEXT)
        finally:
            win32clipboard.CloseClipboard()

    def paste(self, text: str, wait: float = 0.1) -> None:
        """Copy *text* to clipboard and press Ctrl+V."""
        self.copy_to_clipboard(text)
        self.hotkey('ctrl', 'v', wait=wait)

    # ── Internal scancode helpers ────────────────────────────────────────

    def _scan_down(self, scancode: int) -> None:
        import win32con
        vk = self._w32.MapVirtualKey(scancode, 1)  # MAPVK_VSC_TO_VK
        self._w32.keybd_event(vk, scancode, 0, 0)

    def _scan_up(self, scancode: int) -> None:
        import win32con
        vk = self._w32.MapVirtualKey(scancode, 1)
        self._w32.keybd_event(vk, scancode, win32con.KEYEVENTF_KEYUP, 0)
