"""
wuwa_inventory_kamera.game
~~~~~~~~~~~~~~~~~~~~~~~~~~~

UI-independent game manipulation layer.

This package provides everything needed to interact with the WuWa game
window — input simulation, screenshot capture, window management, screen
layout resolution, and high-level navigation primitives — without any
dependency on the Qt UI layer.

Submodules
----------
input_controller
    Low-level mouse/keyboard/scroll primitives via win32api.
screen
    Window detection, screenshot capture, and ScreenInfo resolution.
navigation
    High-level game navigation: open inventory, switch tabs, sort orders.
"""
