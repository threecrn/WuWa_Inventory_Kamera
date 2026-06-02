from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

from .capabilities import BackendCapabilities
from .enums import ColorCode, FontFace, ImreadMode, Interpolation, LineType, MatchMethod, MorphOp, MorphShape, ThresholdMode
from .registry import get_backend, get_backend_capabilities, get_backend_name, set_backend
from .types import Color, ImageArray, MaskArray, PathLike, Point, PointArray


def imread(path: PathLike, mode: ImreadMode | str = ImreadMode.COLOR) -> ImageArray | None:
    mode_key = mode.value if isinstance(mode, ImreadMode) else str(mode)
    return get_backend().imread(path, mode_key)


def imwrite(path: PathLike, image: ImageArray) -> bool:
    return get_backend().imwrite(path, image)


def convert_color(image: ImageArray, code: ColorCode) -> ImageArray:
    return get_backend().convert_color(image, code)


def resize(
    image: ImageArray,
    size: tuple[int, int],
    interpolation: Interpolation = Interpolation.LINEAR,
) -> ImageArray:
    return get_backend().resize(image, size, interpolation)


def lut(image: ImageArray, table: MaskArray) -> ImageArray:
    return get_backend().lut(image, table)


def in_range(image: ImageArray, low: NDArray[np.uint8], high: NDArray[np.uint8]) -> MaskArray:
    return get_backend().in_range(image, low, high)


def count_nonzero(mask: MaskArray) -> int:
    return get_backend().count_nonzero(mask)


def find_nonzero(mask: MaskArray) -> PointArray | None:
    return get_backend().find_nonzero(mask)


def bounding_rect(points: PointArray) -> tuple[int, int, int, int]:
    return get_backend().bounding_rect(points)


def bitwise_and(
    src1: ImageArray,
    src2: ImageArray,
    mask: MaskArray | None = None,
) -> ImageArray:
    return get_backend().bitwise_and(src1, src2, mask=mask)


def line(
    image: ImageArray,
    pt1: Point,
    pt2: Point,
    color: Color,
    thickness: int = 1,
    line_type: LineType = LineType.LINE_8,
) -> ImageArray:
    return get_backend().line(image, pt1, pt2, color, thickness=thickness, line_type=line_type)


def rectangle(
    image: ImageArray,
    pt1: Point,
    pt2: Point,
    color: Color,
    thickness: int = 1,
    line_type: LineType = LineType.LINE_8,
) -> ImageArray:
    return get_backend().rectangle(image, pt1, pt2, color, thickness=thickness, line_type=line_type)


def circle(
    image: ImageArray,
    center: Point,
    radius: int,
    color: Color,
    thickness: int = 1,
    line_type: LineType = LineType.LINE_8,
) -> ImageArray:
    return get_backend().circle(
        image,
        center,
        radius,
        color,
        thickness=thickness,
        line_type=line_type,
    )


def polylines(
    image: ImageArray,
    points: list[PointArray],
    is_closed: bool,
    color: Color,
    thickness: int = 1,
    line_type: LineType = LineType.LINE_8,
) -> ImageArray:
    return get_backend().polylines(
        image,
        points,
        is_closed,
        color,
        thickness=thickness,
        line_type=line_type,
    )


def put_text(
    image: ImageArray,
    text: str,
    org: Point,
    font_scale: float,
    color: Color,
    thickness: int = 1,
    line_type: LineType = LineType.LINE_AA,
    font_face: FontFace = FontFace.SIMPLEX,
) -> ImageArray:
    return get_backend().put_text(
        image,
        text,
        org,
        font_scale,
        color,
        thickness=thickness,
        line_type=line_type,
        font_face=font_face,
    )


def match_template(
    image: ImageArray,
    template: ImageArray,
    method: MatchMethod = MatchMethod.CCOEFF_NORMED,
    mask: MaskArray | None = None,
) -> NDArray[np.float32]:
    return get_backend().match_template(image, template, method, mask=mask)


def warp_perspective(
    image: ImageArray,
    src_quad: NDArray[np.float32],
    dst_size: tuple[int, int],
) -> ImageArray:
    return get_backend().warp_perspective(image, src_quad, dst_size)


def threshold(
    image: MaskArray,
    thresh: int,
    maxval: int,
    mode: ThresholdMode = ThresholdMode.BINARY,
) -> tuple[float, MaskArray]:
    return get_backend().threshold(image, thresh, maxval, mode)


def get_structuring_element(
    shape: MorphShape,
    ksize: tuple[int, int],
) -> MaskArray:
    return get_backend().get_structuring_element(shape, ksize)


def morphology_ex(
    image: MaskArray,
    op: MorphOp,
    kernel: MaskArray,
    iterations: int = 1,
) -> MaskArray:
    return get_backend().morphology_ex(image, op, kernel, iterations=iterations)


def dilate(
    image: MaskArray,
    kernel: MaskArray,
    iterations: int = 1,
) -> MaskArray:
    return get_backend().dilate(image, kernel, iterations=iterations)


def erode(
    image: MaskArray,
    kernel: MaskArray,
    iterations: int = 1,
) -> MaskArray:
    return get_backend().erode(image, kernel, iterations=iterations)


def backend_name() -> str:
    return get_backend_name()


def backend_capabilities() -> BackendCapabilities:
    return get_backend_capabilities()


__all__ = [
    "backend_capabilities",
    "backend_name",
    "bitwise_and",
    "bounding_rect",
    "circle",
    "convert_color",
    "count_nonzero",
    "dilate",
    "erode",
    "find_nonzero",
    "get_structuring_element",
    "imread",
    "imwrite",
    "in_range",
    "line",
    "lut",
    "match_template",
    "morphology_ex",
    "polylines",
    "put_text",
    "rectangle",
    "resize",
    "set_backend",
    "threshold",
    "warp_perspective",
]
