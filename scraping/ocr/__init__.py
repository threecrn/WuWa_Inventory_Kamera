"""
scraping.ocr
~~~~~~~~~~~~

OCR backend abstraction layer.

Backends are registered by name and instantiated on demand.  The active
default backend is used by :func:`~scraping.utils.common.imageToString`
when no explicit backend is supplied.

Built-in backends
-----------------
``'rapidocr'`` (default)
    Wraps ``rapidocr_onnxruntime.RapidOCR``.  All constructor keyword
    arguments are forwarded, so any upstream parameterisation is available.

``'tesserocr'``
    Wraps ``tesserocr.PyTessBaseAPI``.  Requires the ``tesserocr`` Python
    package and the Tesseract system library (see
    :mod:`scraping.ocr._tesserocr` for installation instructions).
    A thread-local API instance is kept alive across calls for performance.
    Module-level ``PSM_*`` / ``OEM_*`` constants and all constructor
    arguments are documented in :class:`~scraping.ocr._tesserocr.TesserOcrBackend`.

Switching the global default
----------------------------
Call :func:`set_default` once at application startup, before any OCR work::

    import scraping.ocr

    # RapidOCR with a custom confidence threshold:
    scraping.ocr.set_default('rapidocr', text_score=0.6)

    # Tesseract, single-line mode, digits + percent only:
    scraping.ocr.set_default('tesserocr', psm=7, char_whitelist='0123456789.%')

Registering a custom backend
-----------------------------
::

    class MyBackend:
        def recognize(self, image: np.ndarray) -> list[OcrResult]:
            ...  # return list of (bbox, text, float_conf) tuples

    scraping.ocr.register('mybackend', MyBackend)
    scraping.ocr.set_default('mybackend')

Passing a backend directly to imageToString
-------------------------------------------
::

    from scraping.utils.common import imageToString
    import scraping.ocr

    one_shot = scraping.ocr.get_backend('rapidocr', text_score=0.5)
    result = imageToString(crop, backend=one_shot)
"""
from __future__ import annotations

import logging
import re
from typing import Any

import numpy as np

# Re-export the Protocol and type alias so callers only need to import from
# ``scraping.ocr`` — not from the private ``_types`` sub-module.
from scraping.ocr._types import OcrBackend, OcrResult  # noqa: F401

__all__ = [
    'OcrResult',
    'OcrBackend',
    'register',
    'list_backends',
    'get_backend',
    'set_default',
    'get_default',
    'imageToString',
]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_registry: dict[str, Any] = {}          # name → factory callable
_cache: dict[str, OcrBackend] = {}      # name → cached no-kwarg instance
_default: OcrBackend | None = None      # active default; set via set_default()


def register(name: str, factory: Any) -> None:
    """
    Register a backend *factory* under *name*.

    *factory* is called as ``factory(**kwargs)`` to produce instances.

    Parameters
    ----------
    name:
        Short identifier used in ``set_default`` and ``get_backend``,
        e.g. ``'rapidocr'``, ``'easyocr'``.
    factory:
        A class or callable that returns an :class:`OcrBackend`.
    """
    _registry[name] = factory
    logger.debug("Registered OCR backend %r", name)


def list_backends() -> list[str]:
    """Return the names of all registered backends, sorted alphabetically."""
    return sorted(_registry)


def get_backend(name: str, **kwargs: Any) -> OcrBackend:
    """
    Return a backend instance for *name*.

    Without *kwargs* the instance is cached — repeated calls return the same
    object.  Supplying *kwargs* always creates a fresh, uncached instance so
    that differently-parameterised variants of the same backend can coexist.

    Parameters
    ----------
    name:
        Backend name as previously registered with :func:`register`.
    **kwargs:
        Constructor arguments forwarded to the backend factory.

    Raises
    ------
    KeyError
        When *name* has not been registered.
    """
    if name not in _registry:
        raise KeyError(
            f"Unknown OCR backend {name!r}. "
            f"Available: {list_backends()}"
        )
    if kwargs:
        logger.debug("Creating OCR backend %r with params %s", name, kwargs)
        return _registry[name](**kwargs)
    if name not in _cache:
        logger.debug("Creating (and caching) OCR backend %r", name)
        _cache[name] = _registry[name]()
    return _cache[name]


def set_default(backend: OcrBackend | str, **kwargs: Any) -> None:
    """
    Set the module-level default OCR backend.

    This is the backend used by :func:`~scraping.utils.common.imageToString`
    when no explicit ``backend`` argument is supplied.  Call it once at
    application startup, before any OCR work begins.

    Parameters
    ----------
    backend:
        Either a string name (looked up via :func:`get_backend`) or a
        ready-made :class:`OcrBackend` instance.
    **kwargs:
        Backend constructor arguments, forwarded to :func:`get_backend`
        when *backend* is a string.  Ignored when *backend* is an instance.
    """
    global _default
    if isinstance(backend, str):
        _default = get_backend(backend, **kwargs)
        logger.info("Default OCR backend → %r  params=%s", backend, kwargs or {})
    else:
        _default = backend
        logger.info("Default OCR backend → %r", type(backend).__name__)


def get_default() -> OcrBackend:
    """
    Return the current default backend, lazily initialising it if needed.

    If :func:`set_default` has not been called, the ``'rapidocr'`` backend
    is created with no extra parameters on first use.

    Raises
    ------
    RuntimeError
        When no default has been configured and ``'rapidocr'`` is not
        registered (i.e. the built-in registration was somehow bypassed).
    """
    global _default
    if _default is None:
        if 'rapidocr' in _registry:
            logger.debug("Lazily initialising default OCR backend 'rapidocr'")
            _default = get_backend('rapidocr')
        else:
            raise RuntimeError(
                "No default OCR backend configured. "
                "Call scraping.ocr.set_default() or register a 'rapidocr' backend."
            )
    return _default


# ---------------------------------------------------------------------------
# Register built-in backends
#
# The import of the adapter module is deferred inside this function so that
# ``import scraping.ocr`` does not eagerly load ``rapidocr_onnxruntime``.
# The ONNX runtime is only initialised when a backend instance is first
# created (either lazily in get_default() or explicitly via set_default()).
# ---------------------------------------------------------------------------

def _register_builtins() -> None:
    from scraping.ocr._rapidocr import RapidOcrBackend
    register('rapidocr', RapidOcrBackend)

    from scraping.ocr._tesserocr import TesserOcrBackend
    register('tesserocr', TesserOcrBackend)


_register_builtins()
del _register_builtins


# ---------------------------------------------------------------------------
# imageToString — high-level OCR helper
# ---------------------------------------------------------------------------

def imageToString(
    image: np.ndarray,
    divisor: str = ' ',
    allowedChars: str = None,
    bannedChars: str = None,
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
        :class:`OcrBackend` instance to use for this call.  When ``None``
        (default) the active global default from :func:`get_default` is used.
    """
    from scraping.utils.common import _trace, _logger  # avoid circular at module level
    try:
        active_backend = backend if backend is not None else get_default()
        ocrResults = active_backend.recognize(image)
        _trace(_logger, 'imageToString — raw OCR results (%d token(s)): %s',
               len(ocrResults), ocrResults)
        
        logging.debug('imageToString — raw OCR results (%d token(s)): %s',
               len(ocrResults), ocrResults)

        banned_pattern = re.compile(f"[{re.escape(bannedChars)}]") if bannedChars else None
        allowed_pattern = re.compile(f"[^{re.escape(allowedChars)}]") if allowedChars else None

        lines = []
        for bbox, text, conf in ocrResults:
            original = text
            if banned_pattern:
                text = banned_pattern.sub('', text)
            if allowed_pattern:
                text = allowed_pattern.sub('', text)
            _trace(_logger, '  token: %r  conf=%.3f  \u2192  %r', original, float(conf), text)
            lines.append((bbox, text))

        groupedLines = []
        currentRow = []
        lastY = None

        for bbox, text in lines:
            yMin = min(point[1] for point in bbox)
            yMax = max(point[1] for point in bbox)

            if lastY is None or (yMin < lastY + 10):
                currentRow.append(text)
            else:
                groupedLines.append(currentRow)
                currentRow = [text]

            lastY = yMax

        if currentRow:
            groupedLines.append(currentRow)

        finalOutput = []
        for row in groupedLines:
            finalOutput.append(divisor.join(row))

        result = '\n'.join(finalOutput).strip()
        _trace(_logger, 'imageToString — final output: %r', result)
        return result

    except Exception:
        _trace(_logger, 'imageToString — OCR raised an exception, returning empty string',
               exc_info=True)
        return ''
