import cv2
import json
import ctypes
import logging
import numpy as np
from pathlib import Path

from ...config.app_config import app_config
from ...output_serialization import write_json_exports
from ..data import (
    itemsID, charactersID, weaponsID,
    echoesID, achievementsID, echoStats,
    definedText, sonataName,
)

# ---------------------------------------------------------------------------
# Logging — register a TRACE level (5) below DEBUG (10)
# ---------------------------------------------------------------------------

LEVEL_TRACE: int = 5
if not hasattr(logging, 'TRACE'):
    logging.addLevelName(LEVEL_TRACE, 'TRACE')
    logging.TRACE = LEVEL_TRACE  # type: ignore[attr-defined]

def _trace(logger: logging.Logger, msg: str, *args, **kwargs) -> None:
    """Emit a TRACE-level record on *logger*."""
    if logger.isEnabledFor(LEVEL_TRACE):
        logger.log(LEVEL_TRACE, msg, *args, **kwargs)

_logger = logging.getLogger(__name__)

def savingScraped(exports: dict[str, object] | None = None, START_DATE: str = ''):
    exports = {} if exports is None else exports
    savePATH: Path = Path(app_config.exportFolder) / START_DATE
    write_json_exports(exports, savePATH)

def screenshot(left: int = 0, top: int = 0, width: int = 0, height: int = 0, monitor: int = 1, bw: bool = False):
    import mss
    with mss.mss() as sct:
        num_monitors = len(sct.monitors) - 1
        if monitor < 1 or monitor > num_monitors:
            monitor = min(max(1, monitor), max(1, num_monitors))
        mon = sct.monitors[monitor]
        if all(coord == 0 for coord in [top, left, width, height]):
            left, top, width, height = tuple(coord for coord in mon.values())

        region = {
            'left': mon['left'] + left,
            'top': mon['top'] + top,
            'width': width,
            'height': height,
            'mon': monitor
        }
        image = np.array(sct.grab(region))
        image = cv2.cvtColor(image, cv2.COLOR_RGBA2RGB)
    
    if bw:
        image = convertToBlackWhite(image)

    return image

def darken_background_preserve_edges_ndarray(image: np.ndarray, threshold: int = 100) -> np.ndarray:
    """
    Convert a colour (RGB) or greyscale crop to greyscale and crush the
    low-luminance background to black while linearly stretching the
    foreground (text) range above *threshold* to full 0-255.

    This removes the game's colour-graded gradient background so that OCR
    engines see high-contrast white text on a black field.
    """
    if len(image.shape) == 3 and image.shape[2] == 3:
        img = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    elif len(image.shape) == 2:
        img = image
    else:
        raise ValueError(f"Unsupported image format: shape={image.shape}")
    lut = np.zeros(256, dtype=np.uint8)
    for i in range(256):
        if i >= threshold:
            lut[i] = int((i - threshold) * (255 / (255 - threshold)))
    return cv2.LUT(img, lut)


def convertToBlackWhite(image: np.ndarray):
    if len(image.shape) == 3 and image.shape[2] == 3:
        gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    elif len(image.shape) == 2:
        gray = image
    else:
        raise ValueError(f"Unsupported image format. Image shape: {image.shape}")
    
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8,8))
    contrasted = clahe.apply(gray)
    
    blurred = cv2.GaussianBlur(contrasted, (3, 3), 0)
    
    _, thresh = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    
    if np.mean(thresh) > 127: thresh = cv2.bitwise_not(thresh)
    
    kernel = np.ones((2,2), np.uint8)
    morph = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel)
    
    sharpen_kernel = np.array([[-1,-1,-1], [-1,9,-1], [-1,-1,-1]])
    sharpened = cv2.filter2D(morph, -1, sharpen_kernel)

    return sharpened

def isUserAdmin():
    return ctypes.windll.shell32.IsUserAnAdmin()

def copyToClipboard(text):
    try:
        import win32clipboard
        win32clipboard.OpenClipboard()
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardText(text)
    finally:
        win32clipboard.CloseClipboard()


# ---------------------------------------------------------------------------
# Raw-scan persistence helpers (Steps 1-2 of the refactoring plan)
# ---------------------------------------------------------------------------

_raw_logger = _logger  # alias — same module logger used by imageToString


def _load_raw_scans(base_path: Path, *, directory_glob: str) -> list:
    """
    Reconstruct raw scan records from directories matching *directory_glob*.

    The on-disk contract is shared by echo and weapon raw sessions: each scan
    directory contains ``full.png`` plus ``meta.json``.
    """
    from ..models.raw_scan import RawEchoScan  # local import — avoids circular deps

    base_path = Path(base_path)
    scans: list = []

    _manifest_file = base_path.parent / 'manifest.json'
    _session_manifest: dict = {}
    if _manifest_file.exists():
        with open(_manifest_file, 'r', encoding='utf-8') as _mf:
            _session_manifest = json.load(_mf)

    for scan_dir in sorted(base_path.glob(directory_glob)):
        meta_path   = scan_dir / "meta.json"
        full_path   = scan_dir / "full.png"

        if not (meta_path.exists() and full_path.exists()):
            _raw_logger.warning(
                "Skipping incomplete raw scan directory: %s "
                "(missing: %s)",
                scan_dir,
                ", ".join(
                    p.name for p in [meta_path, full_path]
                    if not p.exists()
                ),
            )
            continue

        with open(meta_path, 'r', encoding='utf-8') as f:
            meta = json.load(f)

        scans.append(RawEchoScan(
            session_id=meta['session_id'],
            index=meta['index'],
            page=meta['page'],
            row=meta['row'],
            col=meta['col'],
            full_path=full_path,
            screen_width=meta.get('screen_width', _session_manifest.get('screen_width', 1920)),
            screen_height=meta.get('screen_height', _session_manifest.get('screen_height', 1080)),
            monitor=meta.get('monitor', _session_manifest.get('monitor', 1)),
        ))

    _raw_logger.debug("Loaded %d raw scan(s) from %s via %s", len(scans), base_path, directory_glob)
    return scans


def loadRawScans(base_path: Path) -> list:
    """
    Reconstruct all ``RawEchoScan`` objects previously saved by
    the raw echo capture workflow.

    Scans directories named ``echo_XXXX/`` under *base_path* in sorted order.
    Directories that are missing ``full.png`` or ``meta.json`` are skipped
    with a warning.

    Parameters
    ----------
    base_path:
        Root directory for the session's raw scans, e.g.
        ``export/{session_id}/raw``.

    Returns
    -------
    list[RawEchoScan]
        Scans in ascending index order.
    """
    return _load_raw_scans(Path(base_path), directory_glob='echo_*/')


def loadWeaponRawScans(base_path: Path) -> list:
    """Reconstruct raw scan records from ``weapon_XXXX/`` directories."""

    return _load_raw_scans(Path(base_path), directory_glob='weapon_*/')


def loadDevItemRawScans(base_path: Path) -> list:
    """Reconstruct raw scan records from ``devItem_XXXX/`` directories."""

    return _load_raw_scans(Path(base_path), directory_glob='devItem_*/')


def loadResourceRawScans(base_path: Path) -> list:
    """Reconstruct raw scan records from ``resource_XXXX/`` directories."""

    return _load_raw_scans(Path(base_path), directory_glob='resource_*/')


def loadCharacterRawScans(base_path: Path) -> list:
    """Reconstruct character raw scans from ``char_XXXX/`` directories."""
    from ..models.raw_scan import RawCharacterScan  # local import — avoids circular deps

    base_path = Path(base_path)
    scans: list[RawCharacterScan] = []

    manifest_file = base_path.parent / 'manifest.json'
    session_manifest: dict = {}
    if manifest_file.exists():
        with open(manifest_file, 'r', encoding='utf-8') as mf:
            session_manifest = json.load(mf)

    required_sections = (0, 1, 3, 4)

    for char_dir in sorted(base_path.glob('char_*/')):
        meta_path = char_dir / 'meta.json'
        if not meta_path.exists():
            _raw_logger.warning(
                'Skipping incomplete raw character directory: %s (missing: meta.json)',
                char_dir,
            )
            continue

        with open(meta_path, 'r', encoding='utf-8') as f:
            meta = json.load(f)

        section_paths: dict[int, dict[str, Path]] = {}
        missing: list[str] = []

        for section in required_sections:
            section_dir = char_dir / f'section_{section}'
            if not section_dir.is_dir():
                missing.append(f'section_{section}/')
                continue

            if section in (0, 1):
                full_path = section_dir / 'full.png'
                if not full_path.exists():
                    missing.append(f'section_{section}/full.png')
                    continue
                section_paths[section] = {'full': full_path}
                continue

            image_paths = {
                path.stem: path
                for path in sorted(section_dir.glob('*.png'))
            }
            if not image_paths:
                missing.append(f'section_{section}/*.png')
                continue
            section_paths[section] = image_paths

        if missing:
            _raw_logger.warning(
                'Skipping incomplete raw character directory: %s (missing: %s)',
                char_dir,
                ', '.join(missing),
            )
            continue

        scans.append(RawCharacterScan(
            index=meta.get('char_index', int(char_dir.name.split('_')[1])),
            screen_width=meta.get('screen_width', session_manifest.get('screen_width', 1920)),
            screen_height=meta.get('screen_height', session_manifest.get('screen_height', 1080)),
            monitor=meta.get('monitor', session_manifest.get('monitor', 1)),
            section_paths=section_paths,
            base_path=char_dir,
        ))

    _raw_logger.debug('Loaded %d raw character scan(s) from %s', len(scans), base_path)
    return scans