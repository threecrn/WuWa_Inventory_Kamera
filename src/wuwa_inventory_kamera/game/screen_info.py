"""
wuwa_inventory_kamera.game.screen_info
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Coordinate resolver for the supported WuWa game UI layouts.

This is a migration of ``game/screenInfo.py`` from the legacy package root
into the ``wuwa_inventory_kamera`` package.  Only the actively supported
layouts are retained: 1920x1080 and 1920x1200.

Usage::

    from .screen_info import ScreenInfo

    si = ScreenInfo(1920, 1080)
    print(si.echoes.echoCard.x)   # → 1296
"""
from __future__ import annotations

from .game_roi import Coordinates, COORDINATES


class ScreenInfoObject:
    def __init__(self, data):
        for key, value in data.items():
            if isinstance(value, dict):
                setattr(self, key, ScreenInfoObject(value))
            else:
                setattr(self, key, value)

    def __reduce__(self):
        return (self.__class__, (self.__getstate__(),))

    def __getstate__(self):
        return self.__dict__

    def __setstate__(self, state):
        self.__dict__.update(state)


class ScreenInfo:
    def __init__(self, width: int | float, height: int | float, monitor: int = 1):
        self.width = int(width)
        self.height = int(height)
        self.monitor = monitor

        try:
            self.data = COORDINATES[self.getRatio()][(self.width, self.height)]
        except KeyError:
            supported = ', '.join(
                f'{supported_width}x{supported_height}'
                for ratio in COORDINATES.values()
                for supported_width, supported_height in ratio
            )
            raise ValueError(
                f'Unsupported WuWa resolution {self.width}x{self.height}. '
                f'Supported resolutions: {supported}'
            )

        self.data = self._convertToObject(self.data)

    def __reduce__(self):
        return (self.__class__, (self.width, self.height, self.monitor))

    def _convertToObject(self, obj):
        if isinstance(obj, dict):
            return ScreenInfoObject(obj)
        return obj

    def __getattr__(self, item):
        """Dynamically access attributes from the nested data dictionary."""
        if isinstance(self.data, dict) and item in self.data:
            return self.data[item]
        if hasattr(self.data, item):
            return getattr(self.data, item)
        raise AttributeError(f"'ScreenInfo' object has no attribute '{item}'")

    def getRatio(self):
        """Return the simplified ratio of the screen."""
        from .utils.geometry import reduce_ratio
        return reduce_ratio(self.width, self.height)

