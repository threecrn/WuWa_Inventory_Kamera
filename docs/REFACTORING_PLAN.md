# Echo Scraper Refactoring Plan

## Motivation

The echo scraper currently couples four concerns in a single loop:

1. **Game navigation** — mouse clicks, keyboard presses, scrolling
2. **Screenshot capture** — full screen and region grabs
3. **OCR and image processing** — `imageToString`, `convertToBlackWhite`, rarity detection
4. **Data parsing** — fuzzy name matching, stat extraction, result building

This makes it impossible to debug OCR quality or iterate on parsing logic without
replaying the entire game interaction. The goal is to split the scraper into two
independent phases with a clean, serialisable boundary between them.

---

## Two-Phase Architecture

### Phase 1 — Scanner *(game only, no OCR)*

Navigates the game, captures all needed images, and **persists them to disk** in a
structured session folder. Produces a list of `RawEchoScan` objects.

### Phase 2 — Processor *(offline, no game)*

Reads saved images (from disk or from in-memory `RawEchoScan` objects), runs OCR,
parses results, and produces the final structured echo list. Can be re-run any number
of times without the game running.

---

## New Directory Structure

```
scraping/
    scanning/
        __init__.py
        echoesScanner.py        ← NEW: game navigation + screenshot capture only
    processing/
        __init__.py
        echoesProcessor.py      ← NEW: OCR + parsing (no game access)
    models/
        __init__.py
        rawScan.py              ← NEW: RawEchoScan dataclass
    echoesScraper.py            ← becomes thin orchestrator (scanner → processor)
    scraperManager.py           ← unchanged
    utils/
        __init__.py             ← export saveRawScan / loadRawScans
        common.py               ← add saveRawScan / loadRawScans helpers
        mouse_keyboard.py       ← unchanged
```

### Session Folder Layout

Under `export/{session_id}/raw/`:

```
echo_0000/
    full.png        ← full screenshot at the moment this echo was selected
    sonata.png      ← cropped sonata region (captured after scroll-to-sonata)
    meta.json       ← { session_id, index, page, row, col,
                         screen_width, screen_height, monitor }
echo_0001/
    ...
echo_0001/
    debug/          ← written only when logging.DEBUG is active
        card_annotated.png
        stats_name_annotated.png
        stats_value_annotated.png
```

---

## Step-by-Step Implementation Plan

### ✅ Step 1 — Define the `RawEchoScan` model

**File:** `scraping/models/rawScan.py`

A `@dataclass` holding all data captured per echo *before* any OCR. This is the
**contract between Phase 1 and Phase 2**. Serialisable to/from disk via `meta()`.

| Field | Type | Description |
|---|---|---|
| `session_id` | `str` | Scan session identifier (matches folder name) |
| `index` | `int` | Sequential scan index within the session |
| `page` | `int` | Inventory page number |
| `row` | `int` | Grid row (0-based) |
| `col` | `int` | Grid column (0-based) |
| `full_screenshot` | `np.ndarray` | Full screen when echo is selected |
| `sonata_screenshot` | `np.ndarray` | Sonata region crop (after scroll) |
| `screen_width` | `int` | Game window width (for `ScreenInfo` reconstruction) |
| `screen_height` | `int` | Game window height |
| `monitor` | `int` | Monitor index |

---

### ✅ Step 2 — Add session save / load helpers

**File:** `scraping/utils/common.py`

Two new public helpers:

#### `saveRawScan(scan: RawEchoScan, base_path: Path) -> Path`

Writes to `{base_path}/echo_{index:04d}/`:
- `full.png` — lossless PNG of the full screenshot (RGB → BGR for `cv2.imwrite`)
- `sonata.png` — lossless PNG of the sonata crop
- `meta.json` — all non-image fields from `scan.meta()`

Returns the echo directory path for logging purposes.

#### `loadRawScans(base_path: Path) -> list[RawEchoScan]`

Reads all `echo_XXXX/` directories under `base_path` in sorted order.
For each directory: loads `meta.json`, reads both PNGs (`cv2.imread` BGR → RGB),
and reconstructs a `RawEchoScan`. Directories with missing files are skipped with
a warning log.

---

### ✅ Step 3 — Create `echoesScanner.py` (Phase 1)

**File:** `scraping/scanning/echoesScanner.py`

Extract all game-navigation code from `echoesScraper.py` into:

```python
def echoScanner(
    controller: WindowsInputController,
    x: float, y: float,
    screenInfo: ScreenInfo,
    session_id: str,
    raw_base: Path,
) -> list[RawEchoScan]:
```

Loop body (per echo cell):
1. `controller.leftClick(center_x, center_y)`
2. `full = screenshot(width=..., height=..., monitor=...)` — full screen
3. `controller.moveMouse(...); controller.mouseScroll(-scroll_y)` — scroll to sonata
4. `sonata_img = screenshot(sonata_roi)` — sonata crop
5. `controller.mouseScroll(+scroll_y)` — scroll back
6. Build `RawEchoScan`, call `saveRawScan(scan, raw_base)`, append to list

**No `imageToString`, no `convertToBlackWhite`, no `echoesID` lookups here.**

---

### ✅ Step 4 — Create `echoesProcessor.py` (Phase 2)

**File:** `scraping/processing/echoesProcessor.py`

Move all OCR and fuzzy-matching logic:

```python
def echoProcessor(
    scans: list[RawEchoScan],
    session_id: str,
) -> list[dict]:
```

- Accepts only `RawEchoScan` objects (no game access needed at all)
- Reconstructs `ScreenInfo` from `scan.screen_width/height/monitor`
- Existing helpers (`processGridEcho`, `processStats`, `getSonata`) refactored to
  accept `np.ndarray` arguments directly instead of calling `screenshot()` internally
- When `logging.DEBUG` is active, writes annotated crop images to
  `{scan_dir}/debug/` alongside a structured log entry pointing at the file

---

### ✅ Step 5 — Slim `echoesScraper.py` to an orchestrator

```python
def echoScraper(controller, x, y, screenInfo, session_id) -> list[dict]:
    raw_base = Path(cfg.get(cfg.exportFolder)) / session_id / "raw"
    scans = echoScanner(controller, x, y, screenInfo, session_id, raw_base)
    return echoProcessor(scans, session_id)
```

`scraperManager.py` interface is **unchanged**.

---

### ✅ Step 6 — Add structured debug dumping in the processor

Log failures with a direct file path to the saved image so they are immediately
openable:

```python
logger.warning(
    "Name not found: '%s' | image: %s",
    raw_name,
    raw_base / f"echo_{scan.index:04d}" / "full.png",
)
```

When `logging.DEBUG`, write annotated failure images (ROI bounding boxes drawn with
`cv2.rectangle`) to `echo_{index:04d}/debug/`.

---

### ✅ Step 7 — Add offline reprocess entry point

```python
# scraping/processing/echoesProcessor.py
def reprocessSession(session_id: str) -> list[dict]:
    raw_base = Path(cfg.get(cfg.exportFolder)) / session_id / "raw"
    scans = loadRawScans(raw_base)
    return echoProcessor(scans, session_id)
```

Wire to a new **"Reprocess"** button in the UI (next to the existing Export button)
so the user can re-run OCR on any previously captured session without touching the
game.

A standalone **CLI entry point** (`reprocess.py` at the project root) is also
provided for headless / cross-platform use.  It stubs out `qfluentwidgets`,
`win32api`, `win32con`, `win32clipboard`, and `mss` at startup so the OCR pipeline
can run without any GUI or Win32 dependency installed:

```
python reprocess.py --list
python reprocess.py --session-id 2026-02-28_14-30-00
python reprocess.py --raw-dir export/2026-02-28_14-30-00/raw
python reprocess.py --raw-dir ./raw --min-rarity 4 --min-level 10 --log-level DEBUG
```

---

## Migration Order *(always shippable)*

| Step | Files changed | Game broken? | Payoff |
|---|---|---|---|
| 1–2 | Add model + helpers | No | Foundation for all later steps |
| 3 | Add `echoesScanner.py` | No | Images now saved to disk during every scan |
| 4 | Add `echoesProcessor.py` | No | OCR logic isolated and independently testable |
| 5 | Slim `echoesScraper.py` | No | Clean public API, `scraperManager` untouched |
| 6 | Debug dumps in processor | No | Bad OCR results point directly to the image file |
| 7 | `reprocessSession` + UI | No | Full offline re-processing without game |
