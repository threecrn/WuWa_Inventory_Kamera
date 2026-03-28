"""
wuwa_inventory_kamera.game.constants
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Game-specific constants needed by the navigation and screen-capture layer.

These are **not** application-level preferences (no Qt, no config file
required) — they identify the game process and window so the automation
layer can locate and interact with the running game.

If you need user-configurable equivalents (e.g. a custom window title or
a different executable name for a regional client), override these via the
:class:`~...game.screen.GameWindow` constructor parameters rather than
editing this file.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Game process identification
# ---------------------------------------------------------------------------

#: Substring matched against the executable name when searching for the
#: game window.  Matches the standard WuWa Windows executable.
PROCESS_NAME: str = 'Client-Win64-Shipping.exe'

#: Substring matched against the window title when searching for the game
#: window.
WINDOW_NAME: str = 'Wuthering Waves'
