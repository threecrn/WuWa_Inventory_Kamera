from __future__ import annotations

from typing import Any

Cv2Backend: Any = None
PillowBackend: Any = None

try:
    from .cv2_backend import Cv2Backend as _Cv2Backend
    Cv2Backend = _Cv2Backend
except ImportError:
    pass

try:
    from .pillow_backend import PillowBackend as _PillowBackend
    PillowBackend = _PillowBackend
except ImportError:
    pass

__all__ = ["Cv2Backend", "PillowBackend"]
