from __future__ import annotations

from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from ..capabilities import BackendCapabilities
from ..enums import ColorCode, FontFace, ImreadMode, Interpolation, LineType, MatchMethod, MorphOp, MorphShape, ThresholdMode
from ..errors import ImgioUnsupportedOperationError
from ..types import Color, ImageArray, MaskArray, PathLike, Point, PointArray

# Import skimage/scipy lazily when methods are called
_skimage_available = False
_scipy_available = False

try:
    from skimage import transform as _sktransform
    from skimage.feature import match_template as _sk_match_template
    from skimage.morphology import disk as _sk_disk, square as _sk_square, diamond as _sk_diamond
    from skimage.morphology import binary_dilation, binary_erosion, binary_opening, binary_closing
    from skimage.filters import threshold_otsu
    _skimage_available = True
except ImportError:
    pass

try:
    from scipy.ndimage import grey_dilation, grey_erosion, grey_opening, grey_closing
    _scipy_available = True
except ImportError:
    pass

# Pillow imports for basic operations
try:
    from PIL import Image, ImageDraw, ImageFont
    _pil_available = True
except ImportError:
    _pil_available = False


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


def _draw_text(draw: ImageDraw.ImageDraw, xy: Point, text: str, fill: int | tuple[int, int, int], font, thickness: int) -> None:
    if thickness <= 1:
        draw.text(xy, text, fill=fill, font=font)
        return

    radius = max(1, thickness - 1)
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            draw.text((xy[0] + dx, xy[1] + dy), text, fill=fill, font=font)


class SkimageBackend:
    """Advanced backend using scikit-image and scipy for operations not in pillow."""
    
    name = 'skimage'
    capabilities = BackendCapabilities(
        io_basic=True,
        color_basic=True,
        resize=True,
        draw=True,
        mask_ops=True,
        template_matching=_skimage_available,
        perspective_warp=_skimage_available,
        morphology=_scipy_available or _skimage_available,
    )

    def imread(self, path: PathLike, mode: str) -> ImageArray | None:
        if not _pil_available:
            raise ImgioUnsupportedOperationError('imread requires Pillow')
        
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
        if not _pil_available:
            raise ImgioUnsupportedOperationError('imwrite requires Pillow')
        
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
            if not _pil_available:
                raise ImgioUnsupportedOperationError('BGR2HSV requires Pillow')
            rgb = arr[..., ::-1]
            hsv = np.asarray(Image.fromarray(rgb, mode='RGB').convert('HSV'))
            return hsv.copy()
        if code == ColorCode.HSV2BGR:
            if not _pil_available:
                raise ImgioUnsupportedOperationError('HSV2BGR requires Pillow')
            rgb = np.asarray(Image.fromarray(arr, mode='HSV').convert('RGB'))
            return rgb[..., ::-1].copy()
        if code == ColorCode.BGR2LAB:
            raise ImgioUnsupportedOperationError('BGR2LAB not yet implemented in skimage backend')
        if code == ColorCode.LAB2BGR:
            raise ImgioUnsupportedOperationError('LAB2BGR not yet implemented in skimage backend')

        raise ImgioUnsupportedOperationError(
            f'Color conversion {code.value!r} is not supported by skimage backend.'
        )

    def resize(
        self,
        image: ImageArray,
        size: tuple[int, int],
        interpolation: Interpolation,
    ) -> ImageArray:
        if not _pil_available:
            raise ImgioUnsupportedOperationError('resize requires Pillow')
        
        from PIL import Image as PILImage
        
        resample_map = {
            Interpolation.NEAREST: PILImage.Resampling.NEAREST,
            Interpolation.LINEAR: PILImage.Resampling.BILINEAR,
            Interpolation.AREA: PILImage.Resampling.BOX,
            Interpolation.CUBIC: PILImage.Resampling.BICUBIC,
            Interpolation.LANCZOS4: PILImage.Resampling.LANCZOS,
        }
        
        pil, flavor = _to_pil_image(np.asarray(image))
        resized = pil.resize(size, resample=resample_map[interpolation])
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
        if not _pil_available:
            raise ImgioUnsupportedOperationError('line requires Pillow')
        
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
        if not _pil_available:
            raise ImgioUnsupportedOperationError('rectangle requires Pillow')
        
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
        if not _pil_available:
            raise ImgioUnsupportedOperationError('circle requires Pillow')
        
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
        if not _pil_available:
            raise ImgioUnsupportedOperationError('polylines requires Pillow')
        
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
        if not _pil_available:
            raise ImgioUnsupportedOperationError('put_text requires Pillow')
        
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
        if not _skimage_available:
            raise ImgioUnsupportedOperationError(
                'match_template requires scikit-image. Install it with: pip install scikit-image'
            )
        
        # scikit-image match_template doesn't support masks directly
        # We'll implement basic template matching without mask for now
        if mask is not None:
            # Could implement masked matching by zeroing out masked regions
            pass
        
        img_arr = np.asarray(image).astype(np.float32)
        tmpl_arr = np.asarray(template).astype(np.float32)
        
        # Normalize to 0-1 range
        img_norm = img_arr / 255.0
        tmpl_norm = tmpl_arr / 255.0
        
        result = _sk_match_template(img_norm, tmpl_norm)
        return result.astype(np.float32)

    def warp_perspective(
        self,
        image: ImageArray,
        src_quad: NDArray[np.float32],
        dst_size: tuple[int, int],
    ) -> ImageArray:
        if not _skimage_available:
            raise ImgioUnsupportedOperationError(
                'warp_perspective requires scikit-image. Install it with: pip install scikit-image'
            )
        
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
        
        # Compute perspective transform
        tform = _sktransform.ProjectiveTransform()
        tform.estimate(src, dst)
        
        # Apply warp
        warped = _sktransform.warp(
            np.asarray(image),
            tform.inverse,
            output_shape=(height, width),
            preserve_range=True,
        )
        
        return warped.astype(np.uint8)

    def threshold(
        self,
        image: MaskArray,
        thresh: int,
        maxval: int,
        mode: ThresholdMode,
    ) -> tuple[float, MaskArray]:
        arr = np.asarray(image)
        
        if mode == ThresholdMode.BINARY:
            result = np.where(arr > thresh, maxval, 0).astype(np.uint8)
            return float(thresh), result
        
        if mode == ThresholdMode.BINARY_INV:
            result = np.where(arr > thresh, 0, maxval).astype(np.uint8)
            return float(thresh), result
        
        if mode == ThresholdMode.OTSU:
            if not _skimage_available:
                raise ImgioUnsupportedOperationError(
                    'OTSU thresholding requires scikit-image'
                )
            otsu_thresh = threshold_otsu(arr)
            result = np.where(arr > otsu_thresh, maxval, 0).astype(np.uint8)
            return float(otsu_thresh), result
        
        raise ValueError(f'Unknown threshold mode: {mode}')

    def get_structuring_element(
        self,
        shape: MorphShape,
        ksize: tuple[int, int],
    ) -> MaskArray:
        h, w = ksize
        
        if shape == MorphShape.RECT:
            return np.ones((h, w), dtype=np.uint8)
        
        if shape == MorphShape.ELLIPSE:
            if not _skimage_available:
                # Fallback to approximate with disk
                radius = min(h, w) // 2
                kernel = _sk_disk(radius) if _skimage_available else np.ones((h, w), dtype=np.uint8)
                return kernel.astype(np.uint8)
            # Use skimage disk for circular kernel
            radius = min(h, w) // 2
            return _sk_disk(radius).astype(np.uint8)
        
        if shape == MorphShape.CROSS:
            kernel = np.zeros((h, w), dtype=np.uint8)
            kernel[h // 2, :] = 1
            kernel[:, w // 2] = 1
            return kernel
        
        raise ValueError(f'Unknown morphology shape: {shape}')

    def morphology_ex(
        self,
        image: MaskArray,
        op: MorphOp,
        kernel: MaskArray,
        iterations: int = 1,
    ) -> MaskArray:
        if not (_scipy_available or _skimage_available):
            raise ImgioUnsupportedOperationError(
                'morphology_ex requires scipy or scikit-image'
            )
        
        arr = np.asarray(image)
        kern = np.asarray(kernel)
        result = arr
        
        for _ in range(iterations):
            if op == MorphOp.ERODE:
                if _scipy_available:
                    result = grey_erosion(result, footprint=kern)
                else:
                    result = binary_erosion(result > 127, footprint=kern).astype(np.uint8) * 255
            elif op == MorphOp.DILATE:
                if _scipy_available:
                    result = grey_dilation(result, footprint=kern)
                else:
                    result = binary_dilation(result > 127, footprint=kern).astype(np.uint8) * 255
            elif op == MorphOp.OPEN:
                if _scipy_available:
                    result = grey_opening(result, footprint=kern)
                else:
                    result = binary_opening(result > 127, footprint=kern).astype(np.uint8) * 255
            elif op == MorphOp.CLOSE:
                if _scipy_available:
                    result = grey_closing(result, footprint=kern)
                else:
                    result = binary_closing(result > 127, footprint=kern).astype(np.uint8) * 255
            elif op == MorphOp.GRADIENT:
                if _scipy_available:
                    dilated = grey_dilation(result, footprint=kern)
                    eroded = grey_erosion(result, footprint=kern)
                    result = dilated - eroded
                else:
                    raise ImgioUnsupportedOperationError('GRADIENT requires scipy')
            else:
                raise ImgioUnsupportedOperationError(f'Morphology op {op} not implemented')
        
        return result.astype(np.uint8)

    def dilate(
        self,
        image: MaskArray,
        kernel: MaskArray,
        iterations: int = 1,
    ) -> MaskArray:
        return self.morphology_ex(image, MorphOp.DILATE, kernel, iterations=iterations)

    def erode(
        self,
        image: MaskArray,
        kernel: MaskArray,
        iterations: int = 1,
    ) -> MaskArray:
        return self.morphology_ex(image, MorphOp.ERODE, kernel, iterations=iterations)
