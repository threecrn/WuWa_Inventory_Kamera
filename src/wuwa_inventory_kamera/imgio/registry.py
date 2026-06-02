from __future__ import annotations

import os
from typing import TYPE_CHECKING

from .capabilities import BackendCapabilities
from .errors import ImgioBackendUnavailableError

if TYPE_CHECKING:
    from .types import ImgioBackend


class _BackendState:
    def __init__(self) -> None:
        self.backend: ImgioBackend | None = None
        self.name: str | None = None


_STATE = _BackendState()


def _requested_backend_name(name: str | None = None) -> str:
    if name is not None and name.strip():
        return name.strip().lower()

    env_name = os.getenv("WUWA_IMGIO_BACKEND", "").strip().lower()
    if env_name:
        return env_name

    try:
        from ..config.app_config import app_config

        configured = str(getattr(app_config, 'imageBackend', '')).strip().lower()
        if configured:
            return configured
    except (ImportError, AttributeError):
        pass

    return 'auto'


def _load_backend(name: str) -> tuple[ImgioBackend, str]:
    if name == "cv2":
        from .backends import Cv2Backend

        return Cv2Backend(), "cv2"

    if name == "auto":
        try:
            from .backends import Cv2Backend

            return Cv2Backend(), "cv2"
        except Exception as exc:
            raise ImgioBackendUnavailableError(
                "Unable to initialize imgio backend in auto mode. "
                "Install opencv-python or select an implemented backend."
            ) from exc

    if name in {"pillow", "skimage"}:
        raise ImgioBackendUnavailableError(
            f"imgio backend '{name}' is planned but not implemented yet. "
            "Use backend 'cv2' or 'auto'."
        )

    raise ImgioBackendUnavailableError(
        f"Unknown imgio backend '{name}'. Supported values: auto, cv2, pillow, skimage."
    )


def get_backend(name: str | None = None) -> ImgioBackend:
    requested = _requested_backend_name(name)
    backend = _STATE.backend
    backend_name = _STATE.name

    if backend is not None and (
        backend_name == requested or (requested == 'auto' and backend_name == 'cv2')
    ):
        return backend

    backend, resolved_name = _load_backend(requested)
    _STATE.backend = backend
    _STATE.name = resolved_name
    return backend


def set_backend(name: str) -> None:
    _STATE.backend = None
    _STATE.name = None
    get_backend(name)


def get_backend_name() -> str:
    if _STATE.name is None:
        get_backend()
    backend_name = _STATE.name
    assert isinstance(backend_name, str)
    return backend_name


def get_backend_capabilities() -> BackendCapabilities:
    return get_backend().capabilities
