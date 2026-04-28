# Inventory Scanner v2 — Architecture

> **Status:** Implemented. The game manipulation layer, scanning workflows
> (echoes + weapons), OcrService, assemblers, and CLI tools are all live.
> Sonata detection uses icon template matching (no OCR / no scrolling).
> Character, achievement, and shell scrapers remain on V1 / not yet ported.
> A concrete plan to consolidate or remove every legacy top-level module
> lives at the end of this document.

---

## Game Manipulation Layer

A UI-independent game interaction layer under
`src/wuwa_inventory_kamera/game/` cleanly separates game control from both
the Qt UI and the scraper logic.

### Module overview

```
game/
  __init__.py
  constants.py           — PROCESS_NAME, WINDOW_NAME
  input_controller.py    — Low-level mouse/keyboard/scroll (wraps win32api scancodes)
  screen.py              — GameWindow, ScreenLayout, capture_full / capture_region
  screen_info.py         — ScreenInfo (migrated from legacy game/screenInfo.py)
  game_roi.py            — ROI definitions (migrated from legacy game/gameROI.py)
  navigation.py          — GameNavigator — tabs, sort, grid coords, scrolling, menu detection
  state.py               — GameState, CellRef, WindowInfo — serialisable navigator snapshot
  stop_signal.py         — StopSignal — Enter-key polling via GetAsyncKeyState
```

### Key design decisions

| Decision | Rationale |
|---|---|
| `InputController` is a thin win32api wrapper | Testable with a mock; no mss leak |
| `ScreenLayout` wraps `ScreenInfo` | Single place to resolve coordinates; v2 code never imports `ScreenInfo` directly |
| `GameWindow` owns DPI, monitor index, layout | Replaces scattered `WindowManager` + `ScreenInfo` construction |
| `GameNavigator` is stateful | Tracks open tab, sort order, avoids redundant clicks |
| Sort-order control via `SortOrder` enum | Echo/weapon scanning can set a specific sort before scanning |
| All coordinate math lives in `navigation.py` | `click_grid_cell()`, `scroll_to_page()`, `scroll_to_sonata()` |
| Nav-only OCR is inline (not batched) | Page-count reads use the OCR registry default via `_nav_ocr()`; no OcrService overhead |

### `InputController`

```python
ctrl = InputController(monitor_index=1)
ctrl.click(500, 300)                   # left-click relative to monitor
ctrl.move(500, 300)                    # move cursor
ctrl.scroll(3)                         # scroll (amount > 0 = down)
ctrl.press_key('esc')                  # single key
ctrl.hotkey('ctrl', 'v')               # modifier combo
ctrl.paste("search text")             # clipboard + Ctrl+V
```

Properties: `monitor_index`, `monitor_rect`. Comprehensive scancode tables
for all keys, modifiers, and function keys.

### `GameWindow`

```python
gw = GameWindow()                      # auto-finds by WINDOW_NAME / PROCESS_NAME
gw.activate()                          # bring to foreground
gw.found          # → bool
gw.size            # → (logical_width, logical_height) after DPI scaling
gw.dpi_scale       # → float
gw.monitor_index   # → 1-based mss index
gw.layout          # → ScreenLayout (wraps ScreenInfo for current resolution)
```

### `GameNavigator`

```python
nav = GameNavigator(ctrl, gw, inventory_key='b')

nav.open_inventory()
nav.close_inventory()
nav.switch_tab(InventoryTab.ECHOES)
nav.set_sort_order(SortOrder.TIME_ADDED)
count, pages = nav.read_item_count()
nav.click_grid_cell(row=2, col=3)
nav.scroll_to_sonata()
nav.scroll_back_from_sonata()
nav.scroll_to_page(page=2, current_page=0)
nav.is_in_main_menu()                  # capture + pixel detect
```

**Enums:**
- `InventoryTab` — `WEAPONS`, `ECHOES`, `DEV_ITEMS`, `RESOURCES`
- `SortOrder` — `LEVEL=0`, `RARITY=1`, `TIME_ADDED=2`, `TUNING_STATUS=3`, `DISCARDED_FIRST=4`

**Constants:** `GRID_ROWS=4`, `GRID_COLS=6`, `CELLS_PER_PAGE=24`

### `GameState`

Serialisable snapshot of navigator position — used by `wuwa-nav` for
session save/restore and by scanning workflows for cycle-level reporting.

```python
state = GameState.from_navigator(nav, gw)
json_text = state.to_json()

state = GameState.from_json(json_text)
state.apply_to_navigator(nav)          # restore state (no game input sent)
```

Contains: `CellRef(row, col)`, `WindowInfo(found, width, height, monitor)`,
screen name, inventory tab, sort order, grid page, cell selection.

### `StopSignal`

Monitors a hardware key press using `GetAsyncKeyState` on a daemon thread
so a user can press Enter in-game to cancel a running scan.

```python
signal = StopSignal(vk=VK_RETURN, poll_interval=0.1)
if signal.is_set():
    print('Cancelled')
signal.stop()
```

### Callable from CLI or UI

The game layer has **zero Qt dependencies**. The CLI tools (`wuwa-scan`,
`wuwa-nav`) and the Qt UI can both construct the same
`InputController` + `GameNavigator` stack.

---

## Scanning Workflows

The scanning logic under `src/wuwa_inventory_kamera/scraping/scanning/` uses the
game manipulation layer and the OcrService to implement complete scan workflows.

### Module overview

```
scraping/scanning/
  scan_state.py            — ScanSession, ScanItem, GridPosition, ScanItemStatus
  grid_navigator.py        — Forward scan + random-access cell navigation (CellVisitor protocol)
  echo_workflow.py         — Full echo scan with lookahead + rescan support
  weapon_workflow.py       — Weapon/item scan (synchronous per cell, hash dedup)
  session_orchestrator.py  — Top-level runner for multi-scraper sessions
```

### Scan state tracking

`ScanSession` maintains the complete lifecycle of one scan run:

```python
session = ScanSession(total_items=240, sort_order=SortOrder.TIME_ADDED)

session.mark_scanned(42, result=echo_dict)
session.request_rescan(42, reason="missing substats: 2/5 parsed")
idx = session.pop_rescan()        # → 42
session.mark_rescanned(42, result=better_dict)

print(session.progress)           # 0.0–1.0
print(session.rescan_pending)     # 0
```

`GridPosition` (frozen dataclass): `page`, `row`, `col`, `scan_index` with
`from_index(i)` / `to_index()` converters.

`ScanItemStatus` enum: `PENDING` → `SCANNED` → `NEEDS_RESCAN` → `RESCANNED` | `FAILED` | `SKIPPED`.

Each `ScanItem` tracks: position, status, rescan_reason, result, attempts.

### Grid navigator

`GridNavigator` drives the `GameNavigator` through the inventory grid:

```python
grid = GridNavigator(nav, total_items=240, total_pages=10)

# Forward scan — visits every cell in order
cells_visited = grid.scan_forward(visitor_callback, start_index=0)

# Random access — for rescans
grid.navigate_to_cell(GridPosition(page=3, row=2, col=4, scan_index=76))

# Batch rescan — sorted by page to minimize scrolling
grid.visit_positions([pos1, pos2, pos3], visitor_callback)
```

`CellVisitor` protocol: `__call__(position: GridPosition) -> bool`.

### Echo workflow (rescan-aware)

`EchoWorkflow` constructor:
```python
wf = EchoWorkflow(
    nav=nav, ocr_service=svc, session=session,
    sort_order=SortOrder.TIME_ADDED, save_raw=Path('export/raw'),
    max_rescans=2, stop_event=stop.event,
)
results = wf.run(on_progress=callback)  # → list[dict]
```

`EchoWorkflow.run()` executes:

1. Switch to echoes tab, optionally set sort order
2. Read echo count from UI (auto-updates session if count differs)
3. **Forward scan**: iterate all grid cells, capture 3 OCR crops + 1
   sonata icon crop per echo (no scrolling), submit `EchoCapture` to
   OcrService → collect `Future[EchoResult]`
4. **Collect results**: resolve all futures, mark session items
5. **Rescan pass(es)**: any echo where the assembler flagged
   "missing substats" is queued for rescan.  The grid navigator jumps
   to the specific cell, re-captures, and re-submits.  Up to
   `max_rescans` attempts per item.
6. Return accepted echo dicts.

### Weapon/item workflow

`WeaponWorkflow` is simpler — each cell is captured and the future
is resolved immediately (blocking via `future.result(timeout=30)`).
Image-hash dedup skips identical cells. Supports `InventoryTab.WEAPONS`,
`DEV_ITEMS`, and `RESOURCES` tabs via the `tab` parameter.

```python
wf = WeaponWorkflow(
    nav=nav, ocr_service=svc, session=session,
    tab=InventoryTab.WEAPONS, sort_order=None, stop_event=stop.event,
)
results = wf.run(on_progress=callback)  # → list[dict]
```

### Session orchestrator

`SessionOrchestrator` replaces the V1 `scraperManager.scrapers()`:

```python
orch = SessionOrchestrator(
    scrapers=['echoes', 'weapons', 'devItems', 'resources'],
    ocr_providers=['DmlExecutionProvider', 'CPUExecutionProvider'],
    min_rarity=4, min_level=10,
    sort_order=SortOrder.TIME_ADDED,
    save_raw=Path('export'),
    inventory_key='b',
    on_progress=my_callback,   # (step: str, scanned: int, total: int)
)
result = orch.run()
# result = {'date': '...', 'echoes': [...], 'weapons': [...], ...}
```

Run sequence:

1. Find game window, activate, build `InputController` + `GameNavigator`
2. Start `StopSignal` (Enter-key watcher)
3. Open `OcrService` as context manager
4. For each scraper: Esc → dispatch to `_run_echoes` or `_run_weapons`
5. Check `StopSignal` between scrapers; set `cancelled: True` if pressed
6. Return structured result dict

**Implemented scrapers:** `echoes`, `weapons`, `devItems`, `resources` (the
latter two reuse `WeaponWorkflow` with different `InventoryTab` values).

**Not yet implemented:** characters, achievements, shell (logged as warning,
return `{'error': '... not yet implemented'}`).

---

## CLI Tools

Three console scripts registered in `pyproject.toml`:

```toml
[project.scripts]
wuwa-scan      = "wuwa_inventory_kamera.cli.scan:main"
wuwa-nav       = "wuwa_inventory_kamera.cli.nav:main"
wuwa-reprocess = "wuwa_inventory_kamera.cli.reprocess:main"
```

### `wuwa-scan`

Headless scanning entry point — uses the game manipulation layer and
scanning workflows without any Qt UI:

```
wuwa-scan --scrapers echoes weapons --provider dml --min-rarity 4
wuwa-scan --scrapers echoes --sort-order level_desc --save-raw
wuwa-scan --scrapers echoes weapons devItems resources \
          --min-rarity 4 --min-level 10 --provider dml
```

Arguments: `--scrapers` (echoes, weapons, devItems, resources),
`--provider` (cpu | dml), `--min-rarity` 1–5, `--min-level` 0–25,
`--sort-order`, `--save-raw`, `--output-dir`, `--inventory-key`,
`--log-level`.

### `wuwa-nav`

Interactive REPL or script runner for game navigation:

```
wuwa-nav                              # interactive REPL
wuwa-nav session.py                   # run a Python script
wuwa-nav -c "focus_window(); switch_tab('echoes')"
wuwa-nav --state-in state.json --state-out state.json session.py
```

Exposes 20+ scripting functions: `focus_window`, `open_inventory`,
`close_inventory`, `switch_tab`, `set_sort`, `goto_page`, `goto_cell`,
`goto_index`, `read_count`, `sonata_down`, `sonata_up`, `click`, `move`,
`scroll`, `key`, `hotkey`, `screenshot`, `state`, `in_menu`, `wait`,
`ocr_roi`, `snapshot`, `mouse_pos`.

ROI aliases: `echo-card`, `echo-stats-name`, `echo-stats-value`, `sonata`,
`sonata-icon`, `weapon-name`, `weapon-level`.

Supports `GameState` save/restore via `--state-in` / `--state-out` for
resumable sessions.

### `wuwa-reprocess`

Offline re-processing of saved raw scans (no game needed):

```
wuwa-reprocess --session-id 2026-02-28_14-30-00 --service --provider dml
wuwa-reprocess --raw-dir export/2026-02-28_14-30-00/raw --extractor rapid_coord
```

Options: `--session-id` or `--raw-dir`, `--service` (v2 batched GPU OCR via
OcrService), `--extractor` (legacy path), `--provider`, `--use-bw`,
`--min-rarity`, `--min-level`, `--output-dir`.

### `detect-sonata-icon`

Sonata icon template management tool (not a registered console script;
invoked via `python -m`):

- `build` — create templates from labeled data
- `detect` — match templates against a screenshot

---

## Scraper inventory

| Scraper | Nav model | OCR reads per item | V2 status |
|---|---|---|---|
| **Echoes** | grid (up to ~1000 cells) | card name, level, rarity, stats×2 cols + sonata icon match | **Implemented** — `EchoWorkflow` |
| **Weapons** | grid (24/page, N pages) | name, level `x/y`, rank digit | **Implemented** — `WeaponWorkflow` |
| **Dev Items** | grid (24/page, N pages) | name, quantity | **Implemented** — `WeaponWorkflow` (tab=DEV_ITEMS) |
| **Resources** | grid (24/page, N pages) | name, quantity | **Implemented** — `WeaponWorkflow` (tab=RESOURCES) |
| **Characters** | list of 7 per screen | name, level, weapon, skills, chain | Not yet ported (V1 only) |
| **Achievements** | search-per-item | status button text | Not yet ported (V1 only) |
| **Shell** | single crop | currency amount | Not yet ported (V1 only) |

---

## Core principle changes (V1 → V2)

| V1 | V2 |
|---|---|
| Screenshot → disk → `RawEchoScan` → `echoProcessor` (threaded) | Screenshot → in-memory `Capture` → `OcrService` queue (one DML thread) |
| OCR called per-scan inside `ThreadPoolExecutor` | OCR batched across N captures in one GPU forward pass |
| Four OCR backends (`rapid`, `rapid_coord`, `tesser`, `tesser_coord`) | One backend: `RapidOcrBackend` with optional DML |
| Sonata detected by scrolling subwindow + OCR text match | Sonata detected by icon template matching (`SonataIconMatcher`) — no scrolling |
| Retry logic scattered in processor | Retry owned entirely by `OcrService` / `EchoAssembler` |
| Each scraper crops + OCRs inline | Each scraper submits `Capture` objects; OCR is centralised |
| `scraperManager.scrapers()` with multiprocessing | `SessionOrchestrator.run()` — single process, OcrService background thread |

---

## Threading model

All scrapers share a single `OcrService` instance within a single process.
No multiprocessing — the game interaction thread submits captures; the
OcrService thread does GPU work. The echo scraper benefits from lookahead
decoupling; weapon/item workflows block on `future.result()` per cell.

```
Main process
 ├── OcrService thread  ◄────────────────────────────────────────────────┐
 │   (single thread; owns DML + ONNX sessions)                          │
 │   · drain queue into batches (50ms timeout, max 32 items)             │
 │   · grouped ocr_images() call per crop type (card, name, val)          │
 │   · sonata resolved via SonataIconMatcher (no OCR)                    │
 │   · dispatch to per-type assembler                                    │
 │   · resolve Future on each item                                       │
 │                                                                       │
 ├── Echo scanner (main thread during echo phase)                        │
 │   · game nav + click + screenshot                                     │ queue
 │   · crop 3 OCR regions + 1 sonata icon                                │
 │   · submit(EchoCapture) → Future  (non-blocking)                     │
 │   · collect futures after grid sweep + rescan pass                    │
 │                                                                       │
 ├── Weapon/Item scanner  (main thread during weapon/item phase)         │
 │   · click cell → screenshot → submit(WeaponCapture) → .result()  ────┘
 │   · image-hash dedup skips repeated cells
 │
 └── StopSignal thread  (daemon; polls Enter key via GetAsyncKeyState)
```

The DML constraint (single-threaded ONNX) is satisfied because exactly one
thread (`OcrService`) ever touches the ONNX sessions.

---

## When to use `OcrService` vs inline OCR

| Condition | Recommendation |
|---|---|
| Many items, scanner moves faster than OCR | Use `OcrService` (echoes) |
| Sequential, OCR result gates next nav action | `OcrService.submit().result()` immediately (weapons, items) |
| One or two reads total | Inline `imageToString` (shell, achievements status) |
| Navigation-only reads (page count, menu detect) | Inline `_nav_ocr()` via OCR registry default |

---

## Package layout

```
src/wuwa_inventory_kamera/
  game/
    __init__.py
    constants.py            (PROCESS_NAME, WINDOW_NAME)
    input_controller.py     (InputController — win32api scancodes)
    screen.py               (GameWindow, ScreenLayout, capture_full, capture_region)
    screen_info.py          (ScreenInfo — migrated from legacy)
    game_roi.py             (ROI rectangles — migrated from legacy)
    navigation.py           (GameNavigator, InventoryTab, SortOrder, _nav_ocr)
    state.py                (GameState, CellRef, WindowInfo)
    stop_signal.py          (StopSignal)
  cli/
    __init__.py
    scan.py                 (wuwa-scan CLI entry point)
    nav.py                  (wuwa-nav REPL / script runner)
    reprocess.py            (wuwa-reprocess — offline re-OCR)
    detect_sonata_icon.py   (sonata template build + detect)
  scraping/
    __init__.py
    data.py                 (loadData — echoesID, weaponsID, echoStats, sonataName, etc.)
    matching/
      __init__.py
      sonata_icon.py        (SonataIconMatcher — NCC + colour-distance template matching)
    ocr/
      __init__.py           (OCR backend registry, imageToString, tokens_to_lines)
      _types.py             (OcrBackend protocol, OcrResult type)
      _rapidocr.py          (RapidOcrBackend + _provider_patch + thorough_recognize)
      batch.py              (BatchOcr — detect_batch, extract_crops, recognize_batch, ocr_images)
    service/
      __init__.py
      captures.py           (EchoCapture/Result, WeaponCapture/Result, ItemCapture/Result,
                             CharCapture/Result, CaptureType union, _Stop sentinel)
      ocr_service.py        (OcrService — queue + single DML thread + context manager)
      assemblers/
        __init__.py
        echo_assembler.py   (EchoAssembler — rarity detection, stat matching, sonata icon match)
        weapon_assembler.py (WeaponAssembler — name lookup, level/rank parse)
        item_assembler.py   (ItemAssembler — name + count parse)
        character_assembler.py (CharAssembler — multi-section accumulator)
    scanning/
      __init__.py
      scan_state.py         (ScanSession, ScanItem, ScanItemStatus, GridPosition)
      grid_navigator.py     (GridNavigator, CellVisitor protocol)
      echo_workflow.py      (EchoWorkflow — lookahead + rescan, no sonata scrolling)
      weapon_workflow.py    (WeaponWorkflow — sync per cell, hash dedup)
      session_orchestrator.py (SessionOrchestrator — top-level multi-scraper runner)
    processing/
      __init__.py
      echoesValidator.py    (validate_echo_stats, expected_sub_count — shared by V1 + V2)
      echo_stats_valid_values.yaml
```

### Legacy top-level modules (not yet migrated)

```
scraping/                   (legacy V1 — kept for backward compat / reprocess path)
  scanning/
    echoesScanner.py        (Phase 1 raw capture)
    echo.py
  processing/
    echoesProcessor.py      (Phase 2 offline processing)
    echoesValidator.py      (stat validation — also used by V2 assembler)
    statsExtractor.py       (legacy extractors — used by reprocess --extractor path)
  models/
    rawScan.py              (RawEchoScan — disk serialisation)
  ocr/
    __init__.py             (legacy OCR wrappers)
    _rapidocr.py
    _tesserocr.py
    _types.py
  utils/
    common.py               (isUserAdmin, itemsID, savingScraped)
    mouse_keyboard.py
  data.py                   (shared data loading — loadData, echoesID, weaponsID, etc.)
  achievementsScraper.py, charactersScraper.py, echoesScraper.py,
  itemsScraper.py, weaponsScraper.py, shellScraper.py
  scraperExectuter.py, scraperManager.py

game/                       (legacy game layer)
  foreground.py             (WindowManager — find/activate game window)
  gameROI.py                (legacy coordinate definitions by resolution)
  menu.py                   (menu detection helpers)
  screenInfo.py             (resolution-aware ROI accessor)
  stopKey.py                (legacy stop-key handler)

properties/                 (configuration)
  app_config.py             (plain Python config singleton)
  config.py                 (QConfig-based UI config, Qt-dependent)

ui/                         (Qt / PySide6-Fluent UI)
  homeUI.py                 (scan launch, reprocess, export)
  inventoryUI.py            (inventory display)
  loadingUI.py              (splash screen)
  mainUI.py                 (main window shell)
  settingsUI.py             (settings panel)
  custom_widgets/
    widget.py

updater/
  assetsUpdater.py          (download updated assets)
  databaseUpdater.py        (data JSON updates)

cli/                        (legacy CLI scripts — NOT under src/)
  debug_ocr.py
  reprocess.py              (legacy reprocess entry point)
  update_data.py

nav-scripts/                (wuwa-nav session scripts)
  build-sonata-templates.py
  build-sonata-templates-from-filter.py
  scan-echoes.py
  scan-sonata-icons.py
  session.py
  set-sort.py

tools/                      (development / one-off tools)
  check_printwindow/
  match_sonata_icon/        (prototype sonata matcher — now integrated into src/)
  scrape_sonata_icons/
  update_sonata_templates/
  cli/dimbreath_wuthering_data/  (vendored game data, large)

app.py                      (Qt application bootstrap)
main.py                     (entry point with log setup)
batch_ocr.py                (standalone batch OCR runner)
setup.py                    (legacy setuptools config)
conftest.py                 (root — adds project root to sys.path)
```

---

## Capture & result types

All defined in `scraping/service/captures.py`:

```python
@dataclass
class EchoCapture:
    echo_index:      int
    card:            np.ndarray        # name + level + rarity region (RGB)
    stats_name:      np.ndarray        # stat name column (RGB)
    stats_value:     np.ndarray        # stat value column (RGB)
    sonata_icon:     np.ndarray | None = None  # small circular sonata icon crop (BGR)
    full_screenshot: np.ndarray | None = None  # full frame, debug mode only

@dataclass
class EchoResult:
    echo_index: int
    data:       dict | None    # None = rejected
    warnings:   list[str]
    retried:    bool

@dataclass
class WeaponCapture:
    index: int
    name:  np.ndarray           # weapon / item name region
    value: np.ndarray           # quantity or level string
    rank:  np.ndarray | None    # refinement rank digit; None for plain items

@dataclass
class WeaponResult:
    index:     int
    is_weapon: bool
    data:      dict | None

@dataclass
class ItemCapture:
    index: int
    info:  np.ndarray           # single crop containing name + count lines

@dataclass
class ItemResult:
    index:   int
    name:    str
    item_id: str | None
    count:   int

@dataclass
class CharCapture:
    char_index: int
    section:    int                     # 0=resonator 1=weapon 2=echoes(skip) 3=skills 4=chain
    crops:      dict[str, np.ndarray]   # field_name → RGB crop

@dataclass
class CharResult:
    char_index: int
    section:    int
    fields:     dict                    # parsed values for this section

CaptureType = EchoCapture | WeaponCapture | ItemCapture | CharCapture
```

Each capture also carries a `_uid: int` field (set by `OcrService.submit()`,
not caller-visible) for queue round-trip routing.

---

## `OcrService` internals

```python
class OcrService:
    def __init__(
        self,
        providers: list[str] | None = None,    # default: DML + CPU fallback
        batch_timeout: float = 0.05,
        max_batch_size: int = 32,
        min_rarity: int = 1,
        min_level: int = 0,
        **backend_kwargs,
    ):
        self._backend   = RapidOcrBackend(onnx_providers=providers, **backend_kwargs)
        self._batch_ocr = BatchOcr(self._backend)
        self._echo_asm  = EchoAssembler(min_rarity=min_rarity, min_level=min_level)
        self._weapon_asm = WeaponAssembler(min_rarity=min_rarity, min_level=min_level)
        self._item_asm  = ItemAssembler()
        self._char_asm  = CharAssembler()
        # Single daemon thread drains queue
        self._thread = threading.Thread(target=self._run, daemon=True, name='OcrService')
        self._thread.start()

    def submit(self, capture: CaptureType) -> Future:
        ...  # wraps in _QueueItem(capture, uid, future), pushes to queue

    def shutdown(self, wait=True): ...
    def __enter__(self): return self
    def __exit__(self, *exc): self.shutdown(wait=exc[0] is None)
```

Service thread loop: `_drain_batch()` → `_process_batch()`.

`_drain_batch()` blocks on the first item, then greedily collects more
within `batch_timeout` (50ms) up to `max_batch_size` (32). Returns `None`
on `_Stop` sentinel.

`_process_batch()` groups items by capture type, then calls the per-type
processor. Each per-type processor:
1. Runs `self._batch_ocr.ocr_images()` once per crop kind — for echoes:
   card, stats_name, stats_value (3 batches; sonata is resolved by
   `SonataIconMatcher` inside the assembler, not OCR).
2. Passes tokens + sonata icon to the matching assembler.
3. Resolves the future via `item.future.set_result()`.

On error, the future receives the exception via `set_exception()`.

---

## Assembler details

### `EchoAssembler`

- Pixel-color-based rarity detection (`_detect_rarity`)
- Card tokens → name lookup in `echoesID` (exact + fuzzy)
- Sonata icon crop → `SonataIconMatcher.match_to_sonata_key()` (NCC +
  colour-distance, no OCR)
- Stat name + value tokens → `_match_stats` with fuzzy matching, line-wrap handling
- `_parse_stat_value` — handles `"5.00%"` → 5.0 and `"1234"` → 1234
- Validation via `validate_echo_stats` + `expected_sub_count`
- Monster cost from `_MONSTER_COST_MAP` (first two ID digits → slot cost)

### `WeaponAssembler`

- Name lookup in `weaponsID` with fuzzy fallback
- Level regex: `(\d+)\s*/\s*(\d+)` (e.g. "40/90")
- Rank regex: `\d` from rank crop
- Determines `is_weapon` vs plain item based on name match + rank presence
- Rarity/level threshold filtering

### `ItemAssembler`

- Single info crop → line split → name + count

### `CharAssembler`

- Accumulates partial results across 5 section submissions per character
- Resolves final `CharResult` on section 4

| Assembler | Inputs | Parsing complexity | Retry? |
|---|---|---|---|
| `EchoAssembler` | card (OCR), sonata icon (template match), name col (OCR), value col (OCR) | High — stat matching, substat count, validation | Yes |
| `WeaponAssembler` | name, level string, rank digit | Low — two integer parses + lookup | No |
| `ItemAssembler` | info block (2–3 lines) | Low — line split, regex digit strip | No |
| `CharAssembler` | name, level, weapon fields, skill levels, chain buttons | Medium — multi-section, each simple read | No |

---

## OCR backend

```
scraping/ocr/
  __init__.py    — registry: register, list_backends, get_backend, set_default,
                   get_default, imageToString, tokens_to_lines, tokens_to_string
  _types.py      — OcrBackend protocol (recognize → list[OcrResult]),
                   OcrResult = tuple[list, str, float] (bbox, text, confidence)
  _rapidocr.py   — RapidOcrBackend (wraps rapidocr_onnxruntime.RapidOCR)
                   · recognize(image) → list[OcrResult]
                   · thorough_recognize(image) — low-confidence fallback
                   · _provider_patch(providers) — context manager for ONNX provider control
                   · _merge_unique() — dedup tokens within 15px Y-center
  batch.py       — BatchOcr (wraps RapidOcrBackend)
                   · detect_batch(images_bgr) → list[ndarray] of K×4×2 quad boxes
                   · extract_crops(images_bgr, boxes) → list[ImageCrop] (perspective-warped)
                   · recognize_batch(crops) → list[tuple[str, float]]
                   · ocr_images(images) → end-to-end pipeline
                   · _warp_crop(img, box) — perspective transform
```

---

## What is reused from V1

- `echoesValidator.py` — `validate_echo_stats`, `expected_sub_count` (copied
  into `src/.../scraping/processing/` and still in legacy `scraping/processing/`)
- `scraping/data.py` — `loadData`, `echoesID`, `weaponsID`, `echoStats`,
  `sonataName` (duplicated into `src/.../scraping/data.py`)
- `databaseUpdater.py`, `scraperManager.py` — untouched, still legacy
- `wuwa-reprocess` (under `src/`) still supports the legacy `echoesProcessor`
  path via `--extractor`
- Legacy scrapers (characters, achievements, shell) remain in top-level `scraping/`
- Disk saving is opt-in debug mode (`--save-raw`) rather than the default

---

## Tradeoff notes

**Batch size vs latency:** At ~200ms per echo and a 50ms drain timeout, a
24-cell page fills a batch of ~8–12 before the timeout fires — enough to see
GPU utilisation gains without making the UI wait. Tune `batch_timeout` and
`max_batch_size` together.

**Weapons / items vs batching:** With DML the overhead of a single-item
forward pass is low enough that calling `.result()` immediately (blocking the
scanner thread) is acceptable. If inventory scanning speed becomes a
bottleneck, the same lookahead pattern used for echoes can be applied.

**Characters:** Not yet ported to V2. The character scraper is
navigation-bound (~0.8s per section due to UI transitions). When ported,
submitting and immediately resolving per section is fine.

**Retry:** Echo thorough retry runs single-image on the DML thread (not
re-batched), which is acceptable since it's uncommon.

**Disk saves for debug:** `EchoCapture.full_screenshot` is populated only in
debug mode; the reprocess path remains functional via `wuwa-reprocess
--service`.

---

## Sonata Icon Matching

Sonata detection was moved from scroll-into-subwindow + OCR text matching to
direct icon template matching.  This eliminates a fragile scroll step and
removes an entire OCR batch from the echo pipeline.

### Module: `scraping/matching/sonata_icon.py`

`SonataIconMatcher` loads RGBA reference PNGs from `assets/IconS/` once at
construction, then for each scanned icon:

1. Scale each reference to the scan icon dimensions (`INTER_AREA`).
2. Build a smooth circular mask (optionally using calibrated circle
   parameters from `sonataIconCircle` in the game ROI).
3. Combine the circular mask with each reference's alpha channel.
4. Score: `NCC − λ × colour_dist_norm` (λ = 1.5).
   NCC alone is colour-blind; the colour-distance penalty prevents
   hue-different references from winning on structure alone.
5. Try both BGR and RGB channel orderings (scanned PNGs may be stored
   with non-standard byte order).
6. Return the match with the highest combined score.

```python
matcher = SonataIconMatcher()
slug, score = matcher.match(icon_bgr)           # → ("moonlitclouds", 0.87)
key = matcher.match_to_sonata_key(icon_bgr, sonata_names, cx=cx, cy=cy, r=r)
```

Accuracy on a 964-echo reference session: 963/964 (99.9%).

### ROI coordinates

`game_roi.py` defines `sonataIcon` (the small icon crop) and
`sonataIconCircle` (calibrated circle centre/radius) per resolution.
The echo workflow crops `sonataIcon` from the single un-scrolled
screenshot — no second capture or scroll needed.

---

## Testing

### Unit tests (`tests/`)

Located in `tests/`, run by `uv run pytest` (the `testpaths` default in
`pyproject.toml`):

| File | What it tests |
|---|---|
| `test_echoesValidator.py` | `validate_echo_stats`, `expected_sub_count` |
| `test_ocrStats.py` | OCR stat value parsing |
| `test_ocrSubstatNames.py` | Substat name fuzzy matching |
| `data/` | Fixture data for test cases |

### Session integration tests (`session_tests/`)

Located in `session_tests/`, driven by `--session-dir` (external scan data
not checked into the repo):

```
uv run pytest session_tests/ --session-dir K:/wuwa/export/2026-03-29_15-04-03
```

The `session_tests/conftest.py` registers `--session-dir PATH` and
dynamically parametrizes two fixture types via `pytest_generate_tests`:

- **`echo_case`** — one case per `raw/echo_NNNN/` with `full.png` +
  ground truth from `echoes_wuwainventorykamera.json`.
- **`stats_case`** — one case per `raw/echo_NNNN/debug/` with
  `stats_name.png`, `stats_value.png`, `result.json`.

Without `--session-dir`, all session tests skip (zero cost).

| File | What it tests | Cases |
|---|---|---|
| `test_sonata_icon_matching.py` | `SonataIconMatcher` accuracy vs ground truth | 1 per echo |
| `test_stats_extractors.py` | All 4 OCR stats extractors × colour/bw vs ground truth | 4×2 per echo |

---

## Legacy Module Migration Plan

Every module still outside `src/wuwa_inventory_kamera/` needs to be moved in,
replaced by a V2 equivalent, or explicitly deleted.  The table below groups
modules by disposition and priority.

### Phase 1 — Delete (no remaining callers or superseded)

These modules have V2 replacements inside `src/` and are not imported by any
V2 code path.  They can be deleted outright once the UI is ported or dropped.

| Module | Superseded by | Notes |
|---|---|---|
| `game/foreground.py` | `src/.../game/screen.py` (`GameWindow`) | Legacy `WindowManager` |
| `game/gameROI.py` | `src/.../game/game_roi.py` | ROI definitions migrated |
| `game/screenInfo.py` | `src/.../game/screen_info.py` | Resolution accessor migrated |
| `game/menu.py` | `src/.../game/navigation.py` | Menu detection is in `GameNavigator` |
| `game/stopKey.py` | `src/.../game/stop_signal.py` | Stop-key handler migrated |
| `scraping/scanning/echoesScanner.py` | `src/.../scraping/scanning/echo_workflow.py` | Phase 1 capture |
| `scraping/scanning/echo.py` | `src/.../scraping/scanning/echo_workflow.py` | Echo capture helpers |
| `scraping/models/rawScan.py` | In-memory `EchoCapture` | Disk-based scan model |
| `scraping/utils/mouse_keyboard.py` | `src/.../game/input_controller.py` | Input abstraction migrated |
| `scraping/ocr/` (all 4 files) | `src/.../scraping/ocr/` | OCR backend migrated |
| `batch_ocr.py` | `src/.../scraping/ocr/batch.py` | Standalone batch runner |
| `setup.py` | `pyproject.toml` (hatchling) | Legacy setuptools |
| `tools/match_sonata_icon/` | `src/.../scraping/matching/sonata_icon.py` | Prototype integrated |

### Phase 2 — Move into `src/` (still needed, no V2 equivalent yet)

These modules provide functionality that V2 needs but doesn't yet have.
Move them under `src/wuwa_inventory_kamera/` with updated imports.

| Module | Target location | Action |
|---|---|---|
| `scraping/data.py` | Already duplicated in `src/.../scraping/data.py` | Delete legacy copy; update legacy callers to import from `src/` or inline |
| `scraping/processing/echoesValidator.py` | Already duplicated in `src/.../scraping/processing/` | Delete legacy copy; point legacy imports at `src/` |
| `scraping/processing/statsExtractor.py` | `src/.../scraping/processing/stats_extractor.py` | Move; used by `wuwa-reprocess --extractor` and session tests |
| `scraping/processing/echoesProcessor.py` | `src/.../scraping/processing/echoes_processor.py` | Move; used by `wuwa-reprocess --extractor` path + UI reprocess |
| `scraping/utils/common.py` | `src/.../scraping/utils/` or inline | `isUserAdmin` → `src/.../utils/`, `itemsID` / `savingScraped` → `src/.../scraping/export.py` |
| `properties/app_config.py` | `src/.../config/app_config.py` | Plain config singleton, no Qt dependency |
| `updater/databaseUpdater.py` | `src/.../updater/database.py` | Data JSON update logic |
| `updater/assetsUpdater.py` | `src/.../updater/assets.py` | Asset download logic |

### Phase 3 — Port or Remove (V1 scrapers + UI)

These are the V1 scrapers and the Qt UI layer.  Each scraper either gets
ported to a V2 workflow or is dropped if the feature is deprecated.

| Module | Plan |
|---|---|
| `scraping/echoesScraper.py` | **Remove** — fully superseded by `EchoWorkflow` |
| `scraping/weaponsScraper.py` | **Remove** — fully superseded by `WeaponWorkflow` |
| `scraping/itemsScraper.py` | **Remove** — fully superseded by `WeaponWorkflow` (tab=RESOURCES) |
| `scraping/charactersScraper.py` | **Port** to `src/.../scraping/scanning/character_workflow.py` |
| `scraping/achievementsScraper.py` | **Port** to `src/.../scraping/scanning/achievement_workflow.py` |
| `scraping/shellScraper.py` | **Port** to `src/.../scraping/scanning/shell_workflow.py` |
| `scraping/scraperManager.py` | **Remove** — replaced by `SessionOrchestrator` |
| `scraping/scraperExectuter.py` | **Remove** — replaced by `wuwa-scan` CLI |
| `ui/` (all) | **Port** — update imports from legacy `scraping/` / `game/` to `src/` package, or rewrite against V2 `SessionOrchestrator` |
| `properties/config.py` | **Port with UI** — Qt-dependent, moves when UI moves |
| `app.py` + `main.py` | **Port with UI** — entry points for the Qt application |

### Phase 4 — Cleanup

| Item | Action |
|---|---|
| `cli/` (top-level) | `debug_ocr.py` → nav-script or remove; `reprocess.py` → remove (superseded by `wuwa-reprocess`); `update_data.py` → `src/.../cli/` or remove |
| `conftest.py` (root) | Remove once no test imports legacy modules via bare `from scraping...` |
| `nav-scripts/` | Keep as-is (run via `wuwa-nav session.py`) |
| `tools/` (minus `match_sonata_icon/`) | Keep as development tools; not part of the distributed package |
| `scratchpad/` | Keep; not part of the package |

### Dependency on migration order

```
Phase 1 (delete dead code)
  └─► Phase 2 (move shared modules into src/)
        └─► Phase 3 (port remaining scrapers + UI)
              └─► Phase 4 (cleanup root conftest, top-level cli/, etc.)
```

Phase 1 can proceed immediately — nothing depends on the deleted modules
except the UI, which itself needs porting (Phase 3).  Phase 2 is
prerequisite to Phase 3 because the ported scrapers / UI will import from
`src/`.  Phase 4 is a final sweep once all functional code lives under
`src/`.
