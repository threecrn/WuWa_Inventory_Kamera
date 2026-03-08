import re
import cv2
import json
import ctypes
import logging
import numpy as np
from pathlib import Path

from properties.app_config import app_config, INVENTORY
from scraping.data import (
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

def imageToString(
    image: np.ndarray,
    divisor: str = ' ',
    allowedChars: str = None,
    bannedChars: str = None,
    backend=None,
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
        An :class:`~scraping.ocr.OcrBackend` instance to use for this
        call.  When ``None`` (default) the active global default from
        :func:`scraping.ocr.get_default` is used.  Pass an explicit
        backend to use a one-off parameterisation without changing the
        global default.
    """
    try:
        if backend is None:
            import scraping.ocr as _ocr_mod
            backend = _ocr_mod.get_default()
        ocrResults = backend.recognize(image)
        _trace(_logger, 'imageToString — raw OCR results (%d token(s)): %s',
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

            _trace(_logger, '  token: %r  conf=%.3f  →  %r', original, float(conf), text)
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

    except:
        _trace(_logger, 'imageToString — OCR raised an exception, returning empty string',
               exc_info=True)
        return ''

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
    Directories that are missing any of ``full.png``, ``sonata.png``, or
    ``meta.json`` are skipped with a warning.

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
    from scraping.models.rawScan import RawEchoScan  # local import — avoids circular deps

    base_path = Path(base_path)
    scans: list = []

    for echo_dir in sorted(base_path.glob("echo_*/")):
        meta_path   = echo_dir / "meta.json"
        full_path   = echo_dir / "full.png"
        sonata_path = echo_dir / "sonata.png"

        if not (meta_path.exists() and full_path.exists() and sonata_path.exists()):
            _raw_logger.warning(
                "Skipping incomplete raw scan directory: %s "
                "(missing: %s)",
                echo_dir,
                ", ".join(
                    p.name for p in [meta_path, full_path, sonata_path]
                    if not p.exists()
                ),
            )
            continue

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
            sonata_path   = sonata_path,
            screen_width  = meta['screen_width'],
            screen_height = meta['screen_height'],
            monitor       = meta['monitor'],
        ))

    _raw_logger.debug("Loaded %d raw scan(s) from %s", len(scans), base_path)
    return scans