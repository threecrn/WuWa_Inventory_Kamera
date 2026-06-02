from __future__ import annotations

import cv2
import numpy as np
from numpy.typing import NDArray

from ..capabilities import BackendCapabilities
from ..enums import ColorCode, FontFace, ImreadMode, Interpolation, LineType, MatchMethod, MorphOp, MorphShape, ThresholdMode
from ..types import Color, ImageArray, MaskArray, PathLike, Point, PointArray


_IMREAD_MODE_MAP: dict[str, int] = {
    ImreadMode.COLOR.value: cv2.IMREAD_COLOR,
    ImreadMode.GRAYSCALE.value: cv2.IMREAD_GRAYSCALE,
    ImreadMode.UNCHANGED.value: cv2.IMREAD_UNCHANGED,
}

_COLOR_CODE_MAP: dict[ColorCode, int] = {
    ColorCode.BGR2RGB: cv2.COLOR_BGR2RGB,
    ColorCode.RGB2BGR: cv2.COLOR_RGB2BGR,
    ColorCode.RGBA2RGB: cv2.COLOR_RGBA2RGB,
    ColorCode.RGB2GRAY: cv2.COLOR_RGB2GRAY,
    ColorCode.BGR2GRAY: cv2.COLOR_BGR2GRAY,
    ColorCode.GRAY2RGB: cv2.COLOR_GRAY2RGB,
    ColorCode.GRAY2BGR: cv2.COLOR_GRAY2BGR,
    ColorCode.BGR2HSV: cv2.COLOR_BGR2HSV,
    ColorCode.HSV2BGR: cv2.COLOR_HSV2BGR,
    ColorCode.BGR2LAB: cv2.COLOR_BGR2LAB,
    ColorCode.LAB2BGR: cv2.COLOR_LAB2BGR,
}

_INTERP_MAP: dict[Interpolation, int] = {
    Interpolation.NEAREST: cv2.INTER_NEAREST,
    Interpolation.LINEAR: cv2.INTER_LINEAR,
    Interpolation.AREA: cv2.INTER_AREA,
    Interpolation.CUBIC: cv2.INTER_CUBIC,
    Interpolation.LANCZOS4: cv2.INTER_LANCZOS4,
}

_LINE_TYPE_MAP: dict[LineType, int] = {
    LineType.LINE_8: cv2.LINE_8,
    LineType.LINE_AA: cv2.LINE_AA,
}

_FONT_FACE_MAP: dict[FontFace, int] = {
    FontFace.SIMPLEX: cv2.FONT_HERSHEY_SIMPLEX,
}

_MATCH_METHOD_MAP: dict[MatchMethod, int] = {
    MatchMethod.CCOEFF_NORMED: cv2.TM_CCOEFF_NORMED,
}

_THRESHOLD_MODE_MAP: dict[ThresholdMode, int] = {
    ThresholdMode.BINARY: cv2.THRESH_BINARY,
    ThresholdMode.BINARY_INV: cv2.THRESH_BINARY_INV,
    ThresholdMode.OTSU: cv2.THRESH_BINARY + cv2.THRESH_OTSU,
}

_MORPH_SHAPE_MAP: dict[MorphShape, int] = {
    MorphShape.RECT: cv2.MORPH_RECT,
    MorphShape.ELLIPSE: cv2.MORPH_ELLIPSE,
    MorphShape.CROSS: cv2.MORPH_CROSS,
}

_MORPH_OP_MAP: dict[MorphOp, int] = {
    MorphOp.ERODE: cv2.MORPH_ERODE,
    MorphOp.DILATE: cv2.MORPH_DILATE,
    MorphOp.OPEN: cv2.MORPH_OPEN,
    MorphOp.CLOSE: cv2.MORPH_CLOSE,
    MorphOp.GRADIENT: cv2.MORPH_GRADIENT,
    MorphOp.TOPHAT: cv2.MORPH_TOPHAT,
    MorphOp.BLACKHAT: cv2.MORPH_BLACKHAT,
}


class Cv2Backend:
    name = "cv2"
    capabilities = BackendCapabilities(
        io_basic=True,
        color_basic=True,
        resize=True,
        draw=True,
        mask_ops=True,
        template_matching=True,
        perspective_warp=True,
        morphology=True,
    )

    def imread(self, path: PathLike, mode: str) -> ImageArray | None:
        flag = _IMREAD_MODE_MAP.get(str(mode).lower(), cv2.IMREAD_COLOR)
        return cv2.imread(str(path), flag)

    def imwrite(self, path: PathLike, image: ImageArray) -> bool:
        return bool(cv2.imwrite(str(path), image))

    def convert_color(self, image: ImageArray, code: ColorCode) -> ImageArray:
        return cv2.cvtColor(image, _COLOR_CODE_MAP[code])

    def resize(
        self,
        image: ImageArray,
        size: tuple[int, int],
        interpolation: Interpolation,
    ) -> ImageArray:
        return cv2.resize(image, size, interpolation=_INTERP_MAP[interpolation])

    def lut(self, image: ImageArray, table: MaskArray) -> ImageArray:
        return cv2.LUT(image, table)

    def in_range(
        self,
        image: ImageArray,
        low: NDArray[np.uint8],
        high: NDArray[np.uint8],
    ) -> MaskArray:
        return cv2.inRange(image, low, high)

    def count_nonzero(self, mask: MaskArray) -> int:
        return int(cv2.countNonZero(mask))

    def find_nonzero(self, mask: MaskArray) -> PointArray | None:
        return cv2.findNonZero(mask)

    def bounding_rect(self, points: PointArray) -> tuple[int, int, int, int]:
        pts = points
        if points.ndim == 2 and points.shape[-1] == 2:
            pts = points.reshape(-1, 1, 2)
        x, y, w, h = cv2.boundingRect(pts)
        return int(x), int(y), int(w), int(h)

    def bitwise_and(
        self,
        src1: ImageArray,
        src2: ImageArray,
        mask: MaskArray | None = None,
    ) -> ImageArray:
        return cv2.bitwise_and(src1, src2, mask=mask)

    def line(
        self,
        image: ImageArray,
        pt1: Point,
        pt2: Point,
        color: Color,
        thickness: int = 1,
        line_type: LineType = LineType.LINE_8,
    ) -> ImageArray:
        cv2.line(image, pt1, pt2, color, thickness, _LINE_TYPE_MAP[line_type])
        return image

    def rectangle(
        self,
        image: ImageArray,
        pt1: Point,
        pt2: Point,
        color: Color,
        thickness: int = 1,
        line_type: LineType = LineType.LINE_8,
    ) -> ImageArray:
        cv2.rectangle(image, pt1, pt2, color, thickness, _LINE_TYPE_MAP[line_type])
        return image

    def circle(
        self,
        image: ImageArray,
        center: Point,
        radius: int,
        color: Color,
        thickness: int = 1,
        line_type: LineType = LineType.LINE_8,
    ) -> ImageArray:
        cv2.circle(image, center, radius, color, thickness, _LINE_TYPE_MAP[line_type])
        return image

    def polylines(
        self,
        image: ImageArray,
        points: list[PointArray],
        is_closed: bool,
        color: Color,
        thickness: int = 1,
        line_type: LineType = LineType.LINE_8,
    ) -> ImageArray:
        cv2.polylines(
            image,
            points,
            isClosed=is_closed,
            color=color,
            thickness=thickness,
            lineType=_LINE_TYPE_MAP[line_type],
        )
        return image

    def put_text(
        self,
        image: ImageArray,
        text: str,
        org: Point,
        font_scale: float,
        color: Color,
        thickness: int = 1,
        line_type: LineType = LineType.LINE_AA,
        font_face: FontFace = FontFace.SIMPLEX,
    ) -> ImageArray:
        cv2.putText(
            image,
            text,
            org,
            _FONT_FACE_MAP[font_face],
            font_scale,
            color,
            thickness,
            _LINE_TYPE_MAP[line_type],
        )
        return image

    def match_template(
        self,
        image: ImageArray,
        template: ImageArray,
        method: MatchMethod,
        mask: MaskArray | None = None,
    ) -> NDArray[np.float32]:
        out = cv2.matchTemplate(image, template, _MATCH_METHOD_MAP[method], mask=mask)
        return out.astype(np.float32, copy=False)

    def warp_perspective(
        self,
        image: ImageArray,
        src_quad: NDArray[np.float32],
        dst_size: tuple[int, int],
    ) -> ImageArray:
        width, height = dst_size
        src = np.asarray(src_quad, dtype=np.float32).reshape(4, 2)
        dst = np.array(
            [
                [0.0, 0.0],
                [float(width - 1), 0.0],
                [float(width - 1), float(height - 1)],
                [0.0, float(height - 1)],
            ],
            dtype=np.float32,
        )
        matrix = cv2.getPerspectiveTransform(src, dst)
        return cv2.warpPerspective(image, matrix, (width, height))

    def threshold(
        self,
        image: MaskArray,
        thresh: int,
        maxval: int,
        mode: ThresholdMode,
    ) -> tuple[float, MaskArray]:
        threshold_value, result = cv2.threshold(
            image,
            thresh,
            maxval,
            _THRESHOLD_MODE_MAP[mode],
        )
        return float(threshold_value), result

    def get_structuring_element(
        self,
        shape: MorphShape,
        ksize: tuple[int, int],
    ) -> MaskArray:
        return cv2.getStructuringElement(_MORPH_SHAPE_MAP[shape], ksize)

    def morphology_ex(
        self,
        image: MaskArray,
        op: MorphOp,
        kernel: MaskArray,
        iterations: int = 1,
    ) -> MaskArray:
        return cv2.morphologyEx(image, _MORPH_OP_MAP[op], kernel, iterations=iterations)

    def dilate(
        self,
        image: MaskArray,
        kernel: MaskArray,
        iterations: int = 1,
    ) -> MaskArray:
        return cv2.dilate(image, kernel, iterations=iterations)

    def erode(
        self,
        image: MaskArray,
        kernel: MaskArray,
        iterations: int = 1,
    ) -> MaskArray:
        return cv2.erode(image, kernel, iterations=iterations)
