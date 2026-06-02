from __future__ import annotations

from enum import Enum


class ImreadMode(str, Enum):
    COLOR = "color"
    GRAYSCALE = "grayscale"
    UNCHANGED = "unchanged"


class ColorCode(str, Enum):
    BGR2RGB = "bgr2rgb"
    RGB2BGR = "rgb2bgr"
    RGBA2RGB = "rgba2rgb"
    RGB2GRAY = "rgb2gray"
    BGR2GRAY = "bgr2gray"
    GRAY2RGB = "gray2rgb"
    GRAY2BGR = "gray2bgr"
    BGR2HSV = "bgr2hsv"
    HSV2BGR = "hsv2bgr"
    BGR2LAB = "bgr2lab"
    LAB2BGR = "lab2bgr"


class Interpolation(str, Enum):
    NEAREST = "nearest"
    LINEAR = "linear"
    AREA = "area"
    CUBIC = "cubic"
    LANCZOS4 = "lanczos4"


class MatchMethod(str, Enum):
    CCOEFF_NORMED = "ccoeff_normed"


class LineType(str, Enum):
    LINE_8 = "line_8"
    LINE_AA = "line_aa"


class FontFace(str, Enum):
    SIMPLEX = "simplex"
