from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BackendCapabilities:
    io_basic: bool = False
    color_basic: bool = False
    resize: bool = False
    draw: bool = False
    mask_ops: bool = False
    template_matching: bool = False
    perspective_warp: bool = False
