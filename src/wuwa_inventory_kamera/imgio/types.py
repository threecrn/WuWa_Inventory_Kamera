from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, TypeAlias

from .capabilities import BackendCapabilities
from .enums import ColorCode, FontFace, Interpolation, LineType, MatchMethod

PathLike: TypeAlias = str | Path
ImageArray: TypeAlias = Any
MaskArray: TypeAlias = Any
PointArray: TypeAlias = Any
Color: TypeAlias = int | tuple[int, int, int]
Point: TypeAlias = tuple[int, int]


class ImgioBackend(Protocol):
    name: str
    capabilities: BackendCapabilities

    def imread(self, path: PathLike, mode: str) -> ImageArray | None: ...

    def imwrite(self, path: PathLike, image: ImageArray) -> bool: ...

    def convert_color(self, image: ImageArray, code: ColorCode) -> ImageArray: ...

    def resize(
        self,
        image: ImageArray,
        size: tuple[int, int],
        interpolation: Interpolation,
    ) -> ImageArray: ...

    def lut(self, image: ImageArray, table: MaskArray) -> ImageArray: ...

    def in_range(self, image: ImageArray, low: Any, high: Any) -> MaskArray: ...

    def count_nonzero(self, mask: MaskArray) -> int: ...

    def find_nonzero(self, mask: MaskArray) -> PointArray | None: ...

    def bounding_rect(self, points: PointArray) -> tuple[int, int, int, int]: ...

    def bitwise_and(
        self,
        src1: ImageArray,
        src2: ImageArray,
        mask: MaskArray | None = None,
    ) -> ImageArray: ...

    def line(
        self,
        image: ImageArray,
        pt1: Point,
        pt2: Point,
        color: Color,
        thickness: int = 1,
        line_type: LineType = LineType.LINE_8,
    ) -> ImageArray: ...

    def rectangle(
        self,
        image: ImageArray,
        pt1: Point,
        pt2: Point,
        color: Color,
        thickness: int = 1,
        line_type: LineType = LineType.LINE_8,
    ) -> ImageArray: ...

    def circle(
        self,
        image: ImageArray,
        center: Point,
        radius: int,
        color: Color,
        thickness: int = 1,
        line_type: LineType = LineType.LINE_8,
    ) -> ImageArray: ...

    def polylines(
        self,
        image: ImageArray,
        points: list[PointArray],
        is_closed: bool,
        color: Color,
        thickness: int = 1,
        line_type: LineType = LineType.LINE_8,
    ) -> ImageArray: ...

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
    ) -> ImageArray: ...

    def match_template(
        self,
        image: ImageArray,
        template: ImageArray,
        method: MatchMethod,
        mask: MaskArray | None = None,
    ) -> Any: ...

    def warp_perspective(
        self,
        image: ImageArray,
        src_quad: Any,
        dst_size: tuple[int, int],
    ) -> ImageArray: ...
