"""
wuwa_inventory_kamera.scraping.ocr
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

OCR backend registry and high-level helpers.

Built-in backends
-----------------
``'rapidocr'`` (default)
    Wraps ``rapidocr_onnxruntime.RapidOCR``.  All constructor keyword
    arguments are forwarded, so any upstream parameterisation is available.

Switching the global default
----------------------------
::

    import wuwa_inventory_kamera.scraping.ocr as ocr

    ocr.set_default('rapidocr',
                    onnx_providers=['DmlExecutionProvider', 'CPUExecutionProvider'])
"""
from __future__ import annotations

import logging
import re
from typing import Any

import numpy as np

from ._types import OcrBackend, OcrResult  # noqa: F401

__all__ = [
    'OcrResult',
    'OcrBackend',
    'register',
    'list_backends',
    'get_backend',
    'set_default',
    'get_default',
    'imageToString',
    'tokens_to_string',
    'tokens_to_lines',
]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_registry: dict[str, Any] = {}
_cache: dict[str, OcrBackend] = {}
_default: OcrBackend | None = None


def register(name: str, factory: Any) -> None:
    """Register a backend *factory* under *name*."""
    _registry[name] = factory
    logger.debug('Registered OCR backend %r', name)


def list_backends() -> list[str]:
    """Return the names of all registered backends, sorted alphabetically."""
    return sorted(_registry)


def get_backend(name: str, **kwargs: Any) -> OcrBackend:
    """
    Return a backend instance for *name*.

    Without *kwargs* the instance is cached.  With *kwargs* a fresh instance
    is always created.

    Raises ``KeyError`` when *name* is not registered.
    """
    if name not in _registry:
        raise KeyError(
            f'Unknown OCR backend {name!r}. Available: {list_backends()}'
        )
    if kwargs:
        return _registry[name](**kwargs)
    if name not in _cache:
        _cache[name] = _registry[name]()
    return _cache[name]


def set_default(backend: 'OcrBackend | str', **kwargs: Any) -> None:
    """
    Set the active default backend used by :func:`imageToString`.

    *backend* can be either an already-constructed :class:`OcrBackend`
    instance or a registered backend name string.  When a name is given
    *kwargs* are forwarded to the factory.
    """
    global _default
    if isinstance(backend, str):
        _default = get_backend(backend, **kwargs)
    else:
        _default = backend
    logger.debug('Default OCR backend set to %r', _default)


def get_default() -> OcrBackend:
    """
    Return the active default backend, initialising it on first access.

    The default backend is ``'rapidocr'`` with library defaults unless
    overridden via :func:`set_default`.
    """
    global _default
    if _default is None:
        _default = get_backend('rapidocr')
    return _default


# ---------------------------------------------------------------------------
# Register built-ins
# ---------------------------------------------------------------------------

def _register_builtins() -> None:
    from ._rapidocr import RapidOcrBackend
    register('rapidocr', RapidOcrBackend)


_register_builtins()
del _register_builtins


# ---------------------------------------------------------------------------
# Token utilities
# ---------------------------------------------------------------------------

def tokens_to_lines(
    tokens: list[OcrResult],
    divisor: str = ' ',
    bannedChars: str | None = None,
    allowedChars: str | None = None,
) -> list[str]:
    """
    Group OCR *tokens* into text lines.

    Tokens are assigned to the same row when their Y-min is within 10 px of
    the previous token's Y-max.  Within each row tokens are sorted
    left-to-right by their leftmost X coordinate.

    Returns
    -------
    list[str]
        One string per row.  An empty input returns an empty list.
    """
    banned_re = re.compile(f'[{re.escape(bannedChars)}]') if bannedChars else None
    allowed_re = re.compile(f'[^{re.escape(allowedChars)}]') if allowedChars else None

    filtered: list[tuple[list, str]] = []
    for bbox, text, _conf in tokens:
        if banned_re:
            text = banned_re.sub('', text)
        if allowed_re:
            text = allowed_re.sub('', text)
        filtered.append((bbox, text))

    grouped: list[list[tuple[list, str]]] = []
    current_row: list[tuple[list, str]] = []
    last_y_max: float | None = None

    for bbox, text in filtered:
        y_min = min(pt[1] for pt in bbox)
        y_max = max(pt[1] for pt in bbox)

        if last_y_max is None or y_min < last_y_max + 10:
            current_row.append((bbox, text))
        else:
            grouped.append(current_row)
            current_row = [(bbox, text)]

        last_y_max = y_max

    if current_row:
        grouped.append(current_row)

    result: list[str] = []
    for row in grouped:
        row.sort(key=lambda item: min(pt[0] for pt in item[0]))
        result.append(divisor.join(text for _, text in row))

    return result


def tokens_to_string(
    tokens: list[OcrResult],
    divisor: str = ' ',
    bannedChars: str | None = None,
    allowedChars: str | None = None,
) -> str:
    """
    Convert OCR *tokens* to a newline-separated string (same layout as
    :func:`imageToString` but operating on a pre-computed token list).
    """
    return '\n'.join(tokens_to_lines(tokens, divisor, bannedChars, allowedChars)).strip()


# ---------------------------------------------------------------------------
# imageToString — high-level OCR helper
# ---------------------------------------------------------------------------

def imageToString(
    image: np.ndarray,
    divisor: str = ' ',
    allowedChars: str | None = None,
    bannedChars: str | None = None,
    backend: OcrBackend | None = None,
) -> str:
    """
    Run OCR on *image* and return the recognised text as a string.

    Parameters
    ----------
    image:
        RGB uint8 numpy array to recognise.
    divisor:
        String inserted between tokens on the same text line.
    allowedChars:
        When set, only characters in this string are kept in each token.
    bannedChars:
        When set, characters in this string are stripped from each token.
    backend:
        :class:`OcrBackend` instance to use.  When ``None`` the global
        default from :func:`get_default` is used.
    """
    try:
        active_backend = backend if backend is not None else get_default()
        tokens = active_backend.recognize(image)
        return tokens_to_string(tokens, divisor, bannedChars, allowedChars)
    except Exception:
        logger.debug('imageToString raised an exception, returning empty string', exc_info=True)
        return ''
