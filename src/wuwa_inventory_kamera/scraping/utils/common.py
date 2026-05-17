import cv2
import json
import ctypes
import logging
import numpy as np
from pathlib import Path

from ...config.app_config import app_config, INVENTORY
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

def savingScraped(scannedData: dict = {'inventory_wuwainventorykamera.json': (INVENTORY['items'], dict)}, START_DATE: str = ''):
    savePATH: Path = Path(app_config.exportFolder) / START_DATE
    
    if any(data != emptyType() for data, emptyType in scannedData.values()):
        savePATH.mkdir(parents=True, exist_ok=True)

        for filename, (data, emptyType) in scannedData.items():
            if data != emptyType():
                filePATH = savePATH / filename
                with open(filePATH, 'w', encoding='utf-8') as f:
                    json.dump(data, f)

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


def saveRawScan(scan, base_path: Path) -> Path:
    """
    Persist a ``RawEchoScan`` to disk.

    Creates the following structure under *base_path*::

        echo_{index:04d}/
            full.png        <- lossless PNG of scan.full_screenshot (RGB→BGR)
            sonata.png      <- lossless PNG of scan.sonata_screenshot (RGB→BGR)
            meta.json       <- scan.meta() as JSON

    Parameters
    ----------
    scan:
        A ``RawEchoScan`` instance (type-hinted as ``Any`` to avoid a
        top-level circular import; the actual type is
        ``scraping.models.rawScan.RawEchoScan``).
    base_path:
        Root directory for the session's raw scans, e.g.
        ``export/{session_id}/raw``.

    Returns
    -------
    Path
        The echo-specific sub-directory that was created, e.g.
        ``export/{session_id}/raw/echo_0042``.
    """
    echo_dir: Path = Path(base_path) / f"echo_{scan.index:04d}"
    echo_dir.mkdir(parents=True, exist_ok=True)

    # Screenshots are stored as RGB in-memory; cv2.imwrite expects BGR.
    cv2.imwrite(str(echo_dir / "full.png"),
                cv2.cvtColor(scan.full_screenshot, cv2.COLOR_RGB2BGR))
    cv2.imwrite(str(echo_dir / "sonata.png"),
                cv2.cvtColor(scan.sonata_screenshot, cv2.COLOR_RGB2BGR))

    with open(echo_dir / "meta.json", 'w', encoding='utf-8') as f:
        json.dump(scan.meta(), f, indent=2)

    _raw_logger.debug("Saved raw scan %d → %s", scan.index, echo_dir)
    return echo_dir


def loadRawScans(base_path: Path) -> list:
    """
    Reconstruct all ``RawEchoScan`` objects previously saved by
    :func:`saveRawScan`.

    Scans directories named ``echo_XXXX/`` under *base_path* in sorted order.
    Directories that are missing ``full.png`` or ``meta.json`` are skipped
    with a warning.  ``sonata.png`` is optional — new-format sessions
    captured by the v2 workflow (``echo_workflow.py``) omit it and rely on
    icon matching during reprocessing.

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
    from ..models.raw_scan import RawEchoScan  # local import — avoids circular deps

    base_path = Path(base_path)
    scans: list = []

    # Load session manifest once for fallback screen dimensions (may be absent).
    _manifest_file = base_path.parent / 'manifest.json'
    _session_manifest: dict = {}
    if _manifest_file.exists():
        with open(_manifest_file, 'r', encoding='utf-8') as _mf:
            _session_manifest = json.load(_mf)

    for echo_dir in sorted(base_path.glob("echo_*/")):
        meta_path   = echo_dir / "meta.json"
        full_path   = echo_dir / "full.png"
        sonata_path = echo_dir / "sonata.png"

        # full.png and meta.json are required; sonata.png is optional
        # (v2-workflow sessions do not save it — sonata is derived from
        # full.png via icon matching during reprocessing).
        if not (meta_path.exists() and full_path.exists()):
            _raw_logger.warning(
                "Skipping incomplete raw scan directory: %s "
                "(missing: %s)",
                echo_dir,
                ", ".join(
                    p.name for p in [meta_path, full_path]
                    if not p.exists()
                ),
            )
            continue

        optional_sonata_path = sonata_path if sonata_path.exists() else None
        #if optional_sonata_path is None:
        #    _raw_logger.debug(
        #        "Raw scan %s has no sonata.png — sonata will be derived "
        #        "from full.png via icon matching during processing.",
        #        echo_dir.name,
        #    )

        with open(meta_path, 'r', encoding='utf-8') as f:
            meta = json.load(f)

        # Images are loaded lazily — store only the paths here.
        # RawEchoScan.load_images() will cv2.imread each file just before OCR.
        scans.append(RawEchoScan(
            session_id    = meta['session_id'],
            index         = meta['index'],
            page          = meta['page'],
            row           = meta['row'],
            col           = meta['col'],
            full_path     = full_path,
            sonata_path   = optional_sonata_path,
            screen_width  = meta.get('screen_width', _session_manifest.get('screen_width', 1920)),
            screen_height = meta.get('screen_height', _session_manifest.get('screen_height', 1080)),
            monitor       = meta.get('monitor', _session_manifest.get('monitor', 1)),
        ))

    _raw_logger.debug("Loaded %d raw scan(s) from %s", len(scans), base_path)
    return scans