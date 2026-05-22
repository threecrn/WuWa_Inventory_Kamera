"""
wuwa_inventory_kamera.game.stop_signal
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Hardware key monitoring that works even when another window (the game)
has keyboard focus.

``GetAsyncKeyState`` queries the low-level key state directly from the
Windows input subsystem, so it fires regardless of which window is
focused — the user can press Enter in-game to stop a running scan.

Usage::

    from .stop_signal import StopSignal

    signal = StopSignal()          # starts polling thread immediately
    # ... do work ...
    if signal.is_set():
        print('Cancelled')
    signal.stop()                  # optional: stop polling thread
"""
from __future__ import annotations

import ctypes
import logging
import threading
import time

logger = logging.getLogger(__name__)

# Virtual-key code for the Enter key (main keyboard + numpad)
_VK_RETURN: int = 0x0D

_user32 = ctypes.windll.user32  # type: ignore[attr-defined]


class StopSignal:
    """
    Monitors a hardware key press using ``GetAsyncKeyState`` and sets a
    :class:`threading.Event` when the key goes down.

    The polling runs on a daemon thread so it never prevents process exit.

    Parameters
    ----------
    vk:
        Windows virtual-key code to watch (default: ``VK_RETURN`` = Enter).
    poll_interval:
        How often (in seconds) to check the key state (default: 0.1 s).
    """

    def __init__(
        self,
        vk: int = _VK_RETURN,
        poll_interval: float = 0.1,
    ) -> None:
        self._vk = vk
        self._poll = poll_interval
        self._event = threading.Event()
        self._shutdown = threading.Event()
        self._thread = threading.Thread(
            target=self._poll_loop,
            name='StopSignal-poller',
            daemon=True,
        )
        self._thread.start()

    # ── Public interface ─────────────────────────────────────────────────

    @property
    def event(self) -> threading.Event:
        """The underlying event — set when the key has been pressed."""
        return self._event

    def is_set(self) -> bool:
        """Return ``True`` if the stop key has been pressed."""
        return self._event.is_set()

    def stop(self) -> None:
        """
        Stop the polling thread.

        Call this when the scan finishes normally so the thread exits
        cleanly instead of being reaped by process exit.
        """
        self._shutdown.set()
        if self._thread.is_alive():
            self._thread.join(timeout=self._poll * 2)

    # ── Background polling ───────────────────────────────────────────────

    def _poll_loop(self) -> None:
        while not self._event.is_set() and not self._shutdown.is_set():
            try:
                state = _user32.GetAsyncKeyState(self._vk)
                if state & 0x8000:
                    logger.info(
                        'Stop key (VK 0x%02X) pressed — signalling cancellation', self._vk
                    )
                    self._event.set()
                    break
            except Exception:
                logger.exception('StopSignal poll error')
            time.sleep(self._poll)
