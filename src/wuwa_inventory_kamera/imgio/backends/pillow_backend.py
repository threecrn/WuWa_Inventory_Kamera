from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray
from PIL import Image, ImageDraw, ImageFont

from ..capabilities import BackendCapabilities
from ..enums import ColorCode, FontFace, ImreadMode, Interpolation, LineType, MatchMethod
from ..errors import ImgioUnsupportedOperationError
from ..types import Color, ImageArray, MaskArray, PathLike, Point, PointArray

_RESAMPLE_MAP: dict[Interpolation, Image.Resampling] = {
    Interpolation.NEAREST: Image.Resampling.NEAREST,
    Interpolation.LINEAR: Image.Resampling.BILINEAR,
    Interpolation.AREA: Image.Resampling.BOX,
    Interpolation.CUBIC: Image.Resampling.BICUBIC,
    Interpolation.LANCZOS4: Image.Resampling.LANCZOS,
}


def _as_uint8(arr: np.ndarray) -> np.ndarray:
    if arr.dtype == np.uint8:
        return arr
    return np.clip(arr, 0, 255).astype(np.uint8)


def _to_pil_image(image: np.ndarray) -> tuple[Image.Image, str]:
    arr = _as_uint8(np.asarray(image))
    if arr.ndim == 2:
        return Image.fromarray(arr, mode='L'), 'gray'
    if arr.ndim != 3:
        raise ValueError(f'Unsupported image rank: {arr.ndim}')

    channels = arr.shape[2]
    if channels == 3:
        rgb = arr[..., ::-1]
        return Image.fromarray(rgb, mode='RGB'), 'bgr3'
    if channels == 4:
        rgba = arr[..., [2, 1, 0, 3]]
        return Image.fromarray(rgba, mode='RGBA'), 'bgra4'

    raise ValueError(f'Unsupported channel count: {channels}')


def _from_pil_image(image: Image.Image, flavor: str) -> np.ndarray:
    if flavor == 'gray':
        return np.asarray(image.convert('L')).copy()
    if flavor == 'bgr3':
        rgb = np.asarray(image.convert('RGB'))
        return rgb[..., ::-1].copy()
    if flavor == 'bgra4':
        rgba = np.asarray(image.convert('RGBA'))
        return rgba[..., [2, 1, 0, 3]].copy()
    raise ValueError(f'Unknown image flavor: {flavor}')


def _color_to_pil(color: Color, channels: int) -> int | tuple[int, int, int]:
    if isinstance(color, int):
        val = int(np.clip(color, 0, 255))
        if channels == 1:
            return val
        return (val, val, val)

    values = [int(np.clip(v, 0, 255)) for v in color]
    if channels == 1:
        return values[0] if values else 0

    while len(values) < 3:
        values.append(values[-1] if values else 0)

    b, g, r = values[:3]
    return (r, g, b)


def _grayscale_from_rgb(rgb: np.ndarray) -> np.ndarray:
    return ((77 * rgb[..., 0] + 150 * rgb[..., 1] + 29 * rgb[..., 2]) >> 8).astype(np.uint8)


def _grayscale_from_bgr(bgr: np.ndarray) -> np.ndarray:
    return ((77 * bgr[..., 2] + 150 * bgr[..., 1] + 29 * bgr[..., 0]) >> 8).astype(np.uint8)


def _draw_text(draw: ImageDraw.ImageDraw, xy: Point, text: str, fill: int | tuple[int, int, int], font: Any, thickness: int) -> None:
    if thickness <= 1:
        draw.text(xy, text, fill=fill, font=font)
        return

    radius = max(1, thickness - 1)
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            draw.text((xy[0] + dx, xy[1] + dy), text, fill=fill, font=font)


class PillowBackend:
    name = 'pillow'
    capabilities = BackendCapabilities(
        io_basic=True,
        color_basic=True,
        resize=True,
        draw=True,
        mask_ops=True,
        template_matching=False,
        perspective_warp=False,
    )

    def imread(self, path: PathLike, mode: str) -> ImageArray | None:
        mode_key = str(mode).lower()
        try:
            with Image.open(Path(path)) as image:
                if mode_key == ImreadMode.GRAYSCALE.value:
                    return np.asarray(image.convert('L')).copy()

                if mode_key == ImreadMode.UNCHANGED.value:
                    arr = np.asarray(image)
                    if arr.ndim == 2:
                        return arr.copy()
                    if arr.ndim == 3 and arr.shape[2] == 3:
                        return arr[..., ::-1].copy()
                    if arr.ndim == 3 and arr.shape[2] == 4:
                        return arr[..., [2, 1, 0, 3]].copy()
                    return arr.copy()

                rgb = np.asarray(image.convert('RGB'))
                return rgb[..., ::-1].copy()
        except (FileNotFoundError, OSError, ValueError):
            return None

    def imwrite(self, path: PathLike, image: ImageArray) -> bool:
        arr = np.asarray(image)
        try:
            if arr.ndim == 2:
                out = Image.fromarray(_as_uint8(arr), mode='L')
            elif arr.ndim == 3 and arr.shape[2] == 3:
                rgb = _as_uint8(arr[..., ::-1])
                out = Image.fromarray(rgb, mode='RGB')
            elif arr.ndim == 3 and arr.shape[2] == 4:
                rgba = _as_uint8(arr[..., [2, 1, 0, 3]])
                out = Image.fromarray(rgba, mode='RGBA')
            else:
                return False
            out.save(Path(path))
            return True
        except (OSError, ValueError):
            return False

    def convert_color(self, image: ImageArray, code: ColorCode) -> ImageArray:
        arr = _as_uint8(np.asarray(image))

        if code in (ColorCode.BGR2RGB, ColorCode.RGB2BGR):
            return arr[..., ::-1].copy()
        if code == ColorCode.RGBA2RGB:
            return arr[..., :3].copy()
        if code == ColorCode.RGB2GRAY:
            return _grayscale_from_rgb(arr)
        if code == ColorCode.BGR2GRAY:
            return _grayscale_from_bgr(arr)
        if code == ColorCode.GRAY2RGB:
            if arr.ndim != 2:
                raise ValueError('GRAY2RGB expects a 2D array')
            return np.repeat(arr[:, :, None], 3, axis=2)
        if code == ColorCode.GRAY2BGR:
            if arr.ndim != 2:
                raise ValueError('GRAY2BGR expects a 2D array')
            return np.repeat(arr[:, :, None], 3, axis=2)
        if code == ColorCode.BGR2HSV:
            rgb = arr[..., ::-1]
            hsv = np.asarray(Image.fromarray(rgb, mode='RGB').convert('HSV'))
            return hsv.copy()
        if code == ColorCode.HSV2BGR:
            rgb = np.asarray(Image.fromarray(arr, mode='HSV').convert('RGB'))
            return rgb[..., ::-1].copy()

        raise ImgioUnsupportedOperationError(
            f'Color conversion {code.value!r} is not supported by pillow backend.'
        )

    def resize(
        self,
        image: ImageArray,
        size: tuple[int, int],
        interpolation: Interpolation,
    ) -> ImageArray:
        pil, flavor = _to_pil_image(np.asarray(image))
        resized = pil.resize(size, resample=_RESAMPLE_MAP[interpolation])
        return _from_pil_image(resized, flavor)

    def lut(self, image: ImageArray, table: MaskArray) -> ImageArray:
        table_arr = _as_uint8(np.asarray(table)).reshape(-1)
        if table_arr.size != 256:
            raise ValueError(f'LUT must contain 256 elements, got {table_arr.size}')
        src = _as_uint8(np.asarray(image))
        return np.take(table_arr, src)

    def in_range(
        self,
        image: ImageArray,
        low: NDArray[np.uint8],
        high: NDArray[np.uint8],
    ) -> MaskArray:
        src = np.asarray(image)
        low_arr = np.asarray(low, dtype=src.dtype)
        high_arr = np.asarray(high, dtype=src.dtype)

        mask = (src >= low_arr) & (src <= high_arr)
        if src.ndim == 3:
            mask = np.all(mask, axis=2)
        return (mask.astype(np.uint8) * 255)

    def count_nonzero(self, mask: MaskArray) -> int:
        return int(np.count_nonzero(np.asarray(mask)))

    def find_nonzero(self, mask: MaskArray) -> PointArray | None:
        ys, xs = np.nonzero(np.asarray(mask))
        if ys.size == 0:
            return None
        points = np.column_stack((xs, ys)).astype(np.int32)
        return points.reshape(-1, 1, 2)

    def bounding_rect(self, points: PointArray) -> tuple[int, int, int, int]:
        pts = np.asarray(points).reshape(-1, 2)
        if pts.size == 0:
            raise ValueError('bounding_rect requires at least one point')

        xs = pts[:, 0]
        ys = pts[:, 1]
        x_min = int(xs.min())
        y_min = int(ys.min())
        x_max = int(xs.max())
        y_max = int(ys.max())
        return x_min, y_min, x_max - x_min + 1, y_max - y_min + 1

    def bitwise_and(
        self,
        src1: ImageArray,
        src2: ImageArray,
        mask: MaskArray | None = None,
    ) -> ImageArray:
        a = np.asarray(src1)
        b = np.asarray(src2)
        out = np.bitwise_and(a, b)

        if mask is None:
            return out

        m = np.asarray(mask) > 0
        result = np.zeros_like(out)
        if out.ndim == 2:
            result[m] = out[m]
        else:
            result[m, :] = out[m, :]
        return result

    def line(
        self,
        image: ImageArray,
        pt1: Point,
        pt2: Point,
        color: Color,
        thickness: int = 1,
        line_type: LineType = LineType.LINE_8,
    ) -> ImageArray:
        _ = line_type
        pil, flavor = _to_pil_image(np.asarray(image))
        draw = ImageDraw.Draw(pil)
        channels = 1 if flavor == 'gray' else 3
        draw.line([pt1, pt2], fill=_color_to_pil(color, channels), width=max(1, thickness))
        return _from_pil_image(pil, flavor)

    def rectangle(
        self,
        image: ImageArray,
        pt1: Point,
        pt2: Point,
        color: Color,
        thickness: int = 1,
        line_type: LineType = LineType.LINE_8,
    ) -> ImageArray:
        _ = line_type
        pil, flavor = _to_pil_image(np.asarray(image))
        draw = ImageDraw.Draw(pil)
        channels = 1 if flavor == 'gray' else 3
        fill = _color_to_pil(color, channels)
        if thickness < 0:
            draw.rectangle([pt1, pt2], outline=fill, fill=fill)
        else:
            draw.rectangle([pt1, pt2], outline=fill, width=max(1, thickness))
        return _from_pil_image(pil, flavor)

    def circle(
        self,
        image: ImageArray,
        center: Point,
        radius: int,
        color: Color,
        thickness: int = 1,
        line_type: LineType = LineType.LINE_8,
    ) -> ImageArray:
        _ = line_type
        pil, flavor = _to_pil_image(np.asarray(image))
        draw = ImageDraw.Draw(pil)
        channels = 1 if flavor == 'gray' else 3
        fill = _color_to_pil(color, channels)
        x, y = center
        box = [(x - radius, y - radius), (x + radius, y + radius)]
        if thickness < 0:
            draw.ellipse(box, outline=fill, fill=fill)
        else:
            draw.ellipse(box, outline=fill, width=max(1, thickness))
        return _from_pil_image(pil, flavor)

    def polylines(
        self,
        image: ImageArray,
        points: list[PointArray],
        is_closed: bool,
        color: Color,
        thickness: int = 1,
        line_type: LineType = LineType.LINE_8,
    ) -> ImageArray:
        _ = line_type
        pil, flavor = _to_pil_image(np.asarray(image))
        draw = ImageDraw.Draw(pil)
        channels = 1 if flavor == 'gray' else 3
        fill = _color_to_pil(color, channels)

        for pts in points:
            arr = np.asarray(pts, dtype=np.int32).reshape(-1, 2)
            if arr.size == 0:
                continue
            xy = [tuple(int(v) for v in p) for p in arr.tolist()]
            if is_closed and len(xy) > 1:
                xy.append(xy[0])
            draw.line(xy, fill=fill, width=max(1, thickness))

        return _from_pil_image(pil, flavor)

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
        _ = (line_type, font_face)
        pil, flavor = _to_pil_image(np.asarray(image))
        draw = ImageDraw.Draw(pil)
        channels = 1 if flavor == 'gray' else 3
        fill = _color_to_pil(color, channels)

        font_size = max(8, int(round(12.0 * max(font_scale, 0.1))))
        try:
            font = ImageFont.truetype('DejaVuSans.ttf', font_size)
        except OSError:
            font = ImageFont.load_default()

        _draw_text(draw, org, text, fill, font, max(1, thickness))
        return _from_pil_image(pil, flavor)

    def match_template(
        self,
        image: ImageArray,
        template: ImageArray,
        method: MatchMethod,
        mask: MaskArray | None = None,
    ) -> NDArray[np.float32]:
        _ = (image, template, method, mask)
        raise ImgioUnsupportedOperationError(
            'match_template is not supported by pillow backend. Use cv2 or skimage backend.'
        )

    def warp_perspective(
        self,
        image: ImageArray,
        src_quad: NDArray[np.float32],
        dst_size: tuple[int, int],
    ) -> ImageArray:
        _ = (image, src_quad, dst_size)
        raise ImgioUnsupportedOperationError(
            'warp_perspective is not supported by pillow backend. Use cv2 or skimage backend.'
        )
