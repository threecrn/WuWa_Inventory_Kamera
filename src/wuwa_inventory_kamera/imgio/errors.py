from __future__ import annotations


class ImgioError(RuntimeError):
    """Base class for imgio-related runtime errors."""


class ImgioBackendUnavailableError(ImgioError):
    """Raised when a requested backend cannot be initialized."""


class ImgioUnsupportedOperationError(ImgioError):
    """Raised when the active backend does not support an operation."""
