"""
wuwa_inventory_kamera.game.screen_info
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Coordinate resolver for the supported WuWa game UI layouts.

This is a migration of ``game/screenInfo.py`` from the legacy package root
into the ``wuwa_inventory_kamera`` package.  The coordinate tables provide
base layouts for 1920x1080 and 1920x1200, and same-aspect-ratio resolutions
are scaled from those layouts at runtime.

Usage::

    from .screen_info import ScreenInfo

    si = ScreenInfo(1920, 1080)
    print(si.echoes.echoCard.x)   # → 1296
"""
from __future__ import annotations

from .game_roi import Coordinates, COORDINATES


_UNSCALED_LAYOUT_KEYS = {'scroll', 'visibleSlots'}


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

        ratio = self.getRatio()
        ratio_coordinates = COORDINATES.get(ratio)
        if ratio_coordinates is None:
            raise ValueError(
                f'Unsupported WuWa resolution {self.width}x{self.height}. '
                f'Supported base resolutions: {self._supported_resolutions()}. '
                'Scaled resolutions with the same aspect ratio are also supported.'
            )

        self.data = ratio_coordinates.get((self.width, self.height))
        if self.data is None:
            self.data = self._scale_from_reference(ratio_coordinates)

        self.data = self._convertToObject(self.data)

    def __reduce__(self):
        return (self.__class__, (self.width, self.height, self.monitor))

    def _convertToObject(self, obj):
        if isinstance(obj, dict):
            return ScreenInfoObject(obj)
        return obj

    @staticmethod
    def _supported_resolutions() -> str:
        return ', '.join(
            f'{supported_width}x{supported_height}'
            for ratio_coordinates in COORDINATES.values()
            for supported_width, supported_height in sorted(ratio_coordinates)
        )

    def _scale_from_reference(self, ratio_coordinates):
        reference_resolution = min(
            ratio_coordinates,
            key=lambda size: abs(size[0] - self.width) + abs(size[1] - self.height),
        )
        reference_data = ratio_coordinates[reference_resolution]
        width_scale = self.width / reference_resolution[0]
        height_scale = self.height / reference_resolution[1]

        def _scale(data, *, skip_scale: bool = False):
            if isinstance(data, Coordinates):
                if skip_scale:
                    return data
                return Coordinates(
                    x=self._scale_value(data.x, width_scale),
                    y=self._scale_value(data.y, height_scale),
                    w=self._scale_value(data.w, width_scale),
                    h=self._scale_value(data.h, height_scale),
                )
            if isinstance(data, dict):
                return {
                    key: _scale(value, skip_scale=(skip_scale or key in _UNSCALED_LAYOUT_KEYS))
                    for key, value in data.items()
                }
            if isinstance(data, list):
                return [_scale(item, skip_scale=skip_scale) for item in data]
            if isinstance(data, (int, float)) and not skip_scale:
                return self._scale_value(data, width_scale)
            return data

        return _scale(reference_data)

    @staticmethod
    def _scale_value(value: int | float, scale: float):
        return value * scale

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

