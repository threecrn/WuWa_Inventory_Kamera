# Inventory Scanner v2 — Architecture

> **Status:** Implemented. The game manipulation layer, scanning workflows
> (echoes, weapons, characters, achievements, shell), OcrService, assemblers, and CLI
> tools are all live. Sonata detection uses icon template matching (no OCR / no
> scrolling). The legacy module migration (Phases 1–4) is largely complete: all
> superseded `game/`, `scraping/ocr/`, and V1 scraper modules have been deleted;
> shared processing and utility modules are now canonical under `src/` with thin
> re-export shims kept for backward compatibility. The Qt UI layer and
> `updater/assetsUpdater.py` remain to be ported.

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
  character_workflow.py    — Resonator panel scan (sidebar list, 5 sections per character)
  achievement_workflow.py  — Achievements panel scan (search-per-achievement)
  shell_workflow.py        — Shell currency HUD counter (single-shot OCR)
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

### Character workflow

`CharacterWorkflow` scans the resonator panel sidebar. The character list
is not a grid — it is a sidebar with 7 slots visible at a time, scrolled via
`scroll_character_list()`.

```python
wf = CharacterWorkflow(
    nav=nav, ocr_service=svc, session=session,
    resonator_key='c', stop_event=stop.event,
)
results = wf.run(on_progress=callback)  # → {char_id: char_dict}
```

For each resonator the workflow submits four `CharCapture` sections:

| Section | Crops submitted | OCR reads |
|---|---|---|
| 0 — overview | `name`, `level` | Resonator name (fuzzy match) + level |
| 1 — weapon | `weaponName`, `weaponLevel`, `weaponRank` | Weapon name (fuzzy) + level/ascension + rank |
| 2 — echoes | *(skipped)* | Handled by the echo scraper |
| 3 — skills | `skill_0` … `skill_4` | One level-digit per skill node |
| 4 — chain | `chain_0` … `chain_5` | Activate/inactive text per chain node |

After section 0 the `already_seen` flag from `CharAssembler` is checked.
When `True` the sidebar has wrapped around and all characters have been seen.

Output format (per character ID key):
```python
{
    'level': int, 'ascension': int,
    'weapon': {'id': str, 'level': int, 'ascension': int, 'rank': int},
    'echoes': {},
    'skills': {'normal': int, 'resonance': int, 'forte': int,
               'liberation': int, 'intro': int, ...},
    'chain': int,   # count of activated nodes
}
```

### Achievement workflow

`AchievementWorkflow` iterates every entry in `achievementsID`, types the
name into the achievements search box, and OCR-reads the status button crop.

```python
wf = AchievementWorkflow(
    nav=nav, ocr_service=svc, session=session, stop_event=stop.event,
)
completed_ids = wf.run(on_progress=callback)  # → list[str]
```

Completion is flagged when the status text:
- matches the localised "claim" text (`PrefabTextItem_128820487_Text`), or
- contains `'/'` (numeric progress, e.g. `"3/3"`).

`ctrl.paste(achievement_name)` uses the controller's clipboard + Ctrl+V path
to handle non-ASCII names safely across keyboard layouts.

### Shell workflow

`ShellWorkflow` reads the shell-currency HUD counter — a number permanently
displayed in the top bar of the main screen.

```python
wf = ShellWorkflow(nav=nav, ocr_service=svc, session=session)
result = wf.run()   # → {'2': <amount>}
```

No navigation is needed beyond pressing Esc to close any open panel. A single
`ShellCapture` is submitted to OcrService and the resulting `ShellResult.amount`
is returned as `{'2': amount}` (the key `'2'` matches the V1 shell item-data
convention).

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
4. For each scraper: Esc → dispatch to the appropriate workflow
5. Check `StopSignal` between scrapers; set `cancelled: True` if pressed
6. Return structured result dict

**Implemented scrapers:** `echoes`, `weapons`, `devItems`, `resources` (the
latter two reuse `WeaponWorkflow` with different `InventoryTab` values),
`characters` (`CharacterWorkflow`), `achievements` (`AchievementWorkflow`),
`shell` (`ShellWorkflow`).

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

Arguments: `--scrapers` (echoes, weapons, devItems, resources, characters, achievements, shell),
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
| **Characters** | list of 7 per screen | name, level, weapon, skills, chain | **Implemented** — `CharacterWorkflow` |
| **Achievements** | search-per-item | status button text | **Implemented** — `AchievementWorkflow` |
| **Shell** | single crop | currency amount | **Implemented** — `ShellWorkflow` |

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
  config/
    __init__.py
    app_config.py           (basePATH, PROCESS_NAME, WINDOW_NAME, INVENTORY — plain config singleton)
  scraping/
    __init__.py
    data.py                 (loadData — echoesID, weaponsID, echoStats, sonataName, etc.)
    matching/
      __init__.py
      sonata_icon.py        (SonataIconMatcher — NCC + colour-distance template matching)
    models/
      __init__.py
      raw_scan.py           (RawEchoScan — disk serialisation for debug/reprocess)
    ocr/
      __init__.py           (OCR backend registry, imageToString, tokens_to_lines)
      _types.py             (OcrBackend protocol, OcrResult type)
      _rapidocr.py          (RapidOcrBackend + _provider_patch + thorough_recognize)
      batch.py              (BatchOcr — detect_batch, extract_crops, recognize_batch, ocr_images)
    service/
      __init__.py
      captures.py           (EchoCapture/Result, WeaponCapture/Result, ItemCapture/Result,
                             CharCapture/Result, AchievementCapture/Result,
                             ShellCapture/Result, CaptureType union, _Stop sentinel)
      ocr_service.py        (OcrService — queue + single DML thread + context manager)
      assemblers/
        __init__.py
        echo_assembler.py   (EchoAssembler — rarity detection, stat matching, sonata icon match)
        weapon_assembler.py (WeaponAssembler — name lookup, level/rank parse)
        item_assembler.py   (ItemAssembler — name + count parse)
        character_assembler.py (CharAssembler — multi-section accumulator)
        achievement_assembler.py (AchievementAssembler — status text / progress check)
        shell_assembler.py  (ShellAssembler — digit extraction from HUD crop)
    scanning/
      __init__.py
      scan_state.py         (ScanSession, ScanItem, ScanItemStatus, GridPosition)
      grid_navigator.py     (GridNavigator, CellVisitor protocol)
      echo_workflow.py      (EchoWorkflow — lookahead + rescan, no sonata scrolling)
      weapon_workflow.py    (WeaponWorkflow — sync per cell, hash dedup)
      character_workflow.py (CharacterWorkflow — sidebar list, 4 sections per resonator)
      achievement_workflow.py (AchievementWorkflow — search-per-achievement, clipboard paste)
      shell_workflow.py     (ShellWorkflow — single-shot HUD counter read)
      session_orchestrator.py (SessionOrchestrator — top-level multi-scraper runner)
    processing/
      __init__.py
      echoesValidator.py    (validate_echo_stats, expected_sub_count — shared by V1 + V2)
      echo_stats_valid_values.yaml
      echoes_processor.py   (EchoesProcessor — offline Phase-2 processing; used by wuwa-reprocess --extractor)
      stats_extractor.py    (legacy stat extractors: rapid, rapid_coord, tesser, tesser_coord)
    utils/
      __init__.py
      common.py             (isUserAdmin, itemsID, savingScraped)
  updater/
    __init__.py
    database.py             (BaseDataUpdater — data JSON update logic)
```

### Remaining legacy top-level modules

Most legacy modules have been deleted or replaced with thin re-export shims.
What follows is the post-migration state.

```
scraping/                   (re-export shims — canonical code is in src/)
  processing/
    echoesProcessor.py      (shim → src/.../scraping/processing/echoes_processor.py)
    echoesValidator.py      (shim → src/.../scraping/processing/echoesValidator.py)
    statsExtractor.py       (shim → src/.../scraping/processing/stats_extractor.py)
    echo_stats_valid_values.yaml
  models/
    rawScan.py              (shim → src/.../scraping/models/raw_scan.py)
  utils/
    common.py               (shim → src/.../scraping/utils/common.py)
  data.py                   (shim → src/.../scraping/data.py)
  [DELETED] scanning/echoesScanner.py, echo.py
  [DELETED] ocr/ (all 4 files)
  [DELETED] achievementsScraper.py, charactersScraper.py, echoesScraper.py,
            itemsScraper.py, weaponsScraper.py, shellScraper.py
  [DELETED] scraperExectuter.py, scraperManager.py

game/                       [DELETED — superseded by src/.../game/]
  [DELETED] foreground.py, gameROI.py, menu.py, screenInfo.py, stopKey.py

properties/
  app_config.py             (shim → src/.../config/app_config.py)
  config.py                 (QConfig-based UI config, Qt-dependent — not yet ported)

ui/                         (Qt / PySide6-Fluent UI — not yet ported to V2)
  homeUI.py
  inventoryUI.py
  loadingUI.py
  mainUI.py
  settingsUI.py
  custom_widgets/
    widget.py

updater/
  assetsUpdater.py          (asset download logic — not yet migrated to src/)
  databaseUpdater.py        (shim → src/.../updater/database.py)

cli/                        (legacy CLI scripts — NOT under src/)
  debug_ocr.py              (updated to use src/ imports; candidate for nav-script or removal)
  update_data.py            (updated to use src/ imports; candidate for src/ or removal)
  [DELETED] reprocess.py   (superseded by wuwa-reprocess)

nav-scripts/                (wuwa-nav session scripts — keep as-is)
  build-sonata-templates.py
  build-sonata-templates-from-filter.py
  scan-echoes.py
  scan-sonata-icons.py
  session.py
  set-sort.py

tools/                      (development / one-off tools — keep)
  check_printwindow/
  scrape_sonata_icons/
  update_sonata_templates/
  cli/dimbreath_wuthering_data/  (vendored game data, large)
  [DELETED] match_sonata_icon/  (integrated into src/.../scraping/matching/)

app.py                      (Qt application bootstrap — not yet ported)
main.py                     (entry point with log setup — not yet ported)
[DELETED] batch_ocr.py      (superseded by src/.../scraping/ocr/batch.py)
[DELETED] setup.py          (superseded by pyproject.toml)
conftest.py                 (root — adds project root to sys.path; keep until no legacy imports remain)
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

@dataclass
class AchievementCapture:
    achievement_name: str               # name pasted into search box
    achievement_id:   int               # from achievementsID
    status:           np.ndarray        # status button crop (RGB)

@dataclass
class AchievementResult:
    achievement_name: str
    achievement_id:   int
    completed:        bool

CaptureType = EchoCapture | WeaponCapture | ItemCapture | CharCapture | AchievementCapture
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
        self._echo_asm        = EchoAssembler(min_rarity=min_rarity, min_level=min_level)
        self._weapon_asm      = WeaponAssembler(min_rarity=min_rarity, min_level=min_level)
        self._item_asm        = ItemAssembler()
        self._char_asm        = CharAssembler()
        self._achievement_asm = AchievementAssembler()
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
| `AchievementAssembler` | status button text | Low — claim-text match or `/` check | No |

### `AchievementAssembler`

- Stateless — one `assemble(capture, status_tokens)` call per achievement
- Checks OCR text of the `status` crop against the localised
  `PrefabTextItem_128820487_Text` key ("claim") from `definedText`
- Also flags completion when status contains `'/'` (numeric progress)
- Returns `AchievementResult(achievement_name, achievement_id, completed: bool)`



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

- `echoesValidator.py` — `validate_echo_stats`, `expected_sub_count` — canonical
  copy is now `src/.../scraping/processing/echoesValidator.py`; legacy path is a
  re-export shim.
- `scraping/data.py` — `loadData`, `echoesID`, `weaponsID`, `echoStats`,
  `sonataName` — canonical copy is `src/.../scraping/data.py`; legacy path is a
  re-export shim.
- `scraping/processing/echoesProcessor.py` / `statsExtractor.py` — canonical
  copies moved to `src/.../scraping/processing/`; legacy paths are re-export shims.
- `wuwa-reprocess` still supports the legacy `echoesProcessor` path via `--extractor`.
- Legacy scrapers (characters, achievements, shell) were **removed** without a V2
  port. `SessionOrchestrator` logs a warning if these are requested.
- Disk saving is opt-in debug mode (`--save-raw`) rather than the default.

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

**Characters:** Not yet ported to V2 — now implemented as `CharacterWorkflow`.
Navigation-bound (~0.8s per section due to UI transitions). Each section is
submitted and resolved immediately per section — no lookahead needed.

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

The one failure was actually a mislabelled ground truth. The labelling had been done via scroll + OCR text match, but the sonata area had still shown the previous echo. The matcher identified the correct sonata by the icon, but the ground truth was wrong due to the scroll step's fragility.

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

> **Migration status (2026-04-28):** Phases 1–3 are complete. Phase 4 items
> (top-level `cli/` scripts, root `conftest.py`) remain as candidates for
> future cleanup. The Qt UI layer and `updater/assetsUpdater.py` are the only
> substantial pieces not yet ported to `src/`.

### Phase 1 — Delete (no remaining callers or superseded) ✓ Done

All Phase 1 modules have been deleted.

| Module | Superseded by | Status |
|---|---|---|
| `game/foreground.py` | `src/.../game/screen.py` (`GameWindow`) | **Deleted** |
| `game/gameROI.py` | `src/.../game/game_roi.py` | **Deleted** |
| `game/screenInfo.py` | `src/.../game/screen_info.py` | **Deleted** |
| `game/menu.py` | `src/.../game/navigation.py` | **Deleted** |
| `game/stopKey.py` | `src/.../game/stop_signal.py` | **Deleted** |
| `scraping/scanning/echoesScanner.py` | `src/.../scraping/scanning/echo_workflow.py` | **Deleted** |
| `scraping/scanning/echo.py` | `src/.../scraping/scanning/echo_workflow.py` | **Deleted** |
| `scraping/models/rawScan.py` | In-memory `EchoCapture` | **Moved** — re-export shim kept; canonical at `src/.../scraping/models/raw_scan.py` |
| `scraping/utils/mouse_keyboard.py` | `src/.../game/input_controller.py` | **Deleted** |
| `scraping/ocr/` (all 4 files) | `src/.../scraping/ocr/` | **Deleted** |
| `batch_ocr.py` | `src/.../scraping/ocr/batch.py` | **Deleted** |
| `setup.py` | `pyproject.toml` (hatchling) | **Deleted** |
| `tools/match_sonata_icon/` | `src/.../scraping/matching/sonata_icon.py` | **Deleted** |

### Phase 2 — Move into `src/` (still needed, no V2 equivalent yet) ✓ Done

All Phase 2 modules have been migrated. Legacy copies are now thin re-export
shims that forward all public names to their canonical `src/` counterparts.

| Module | Target location | Status |
|---|---|---|
| `scraping/data.py` | `src/.../scraping/data.py` | **Shim** — imports forwarded to `src/` |
| `scraping/processing/echoesValidator.py` | `src/.../scraping/processing/echoesValidator.py` | **Shim** — imports forwarded to `src/` |
| `scraping/processing/statsExtractor.py` | `src/.../scraping/processing/stats_extractor.py` | **Moved** — re-export shim kept |
| `scraping/processing/echoesProcessor.py` | `src/.../scraping/processing/echoes_processor.py` | **Moved** — re-export shim kept |
| `scraping/utils/common.py` | `src/.../scraping/utils/common.py` | **Moved** — re-export shim kept |
| `properties/app_config.py` | `src/.../config/app_config.py` | **Moved** — re-export shim kept |
| `updater/databaseUpdater.py` | `src/.../updater/database.py` | **Moved** — re-export shim kept |
| `updater/assetsUpdater.py` | `src/.../updater/assets.py` | **Pending** — not yet migrated |

### Phase 3 — Port or Remove (V1 scrapers + UI) — Partial

V1 scrapers have been removed. Character and achievement scrapers have V2
ports; shell scraper was deleted without a V2 port. The Qt UI layer is not
yet ported.

| Module | Plan | Status |
|---|---|---|
| `scraping/echoesScraper.py` | Remove — superseded by `EchoWorkflow` | **Deleted** |
| `scraping/weaponsScraper.py` | Remove — superseded by `WeaponWorkflow` | **Deleted** |
| `scraping/itemsScraper.py` | Remove — superseded by `WeaponWorkflow` (tab=RESOURCES) | **Deleted** |
| `scraping/charactersScraper.py` | Port to `src/.../scraping/scanning/character_workflow.py` | **Done** — `CharacterWorkflow` implemented |
| `scraping/achievementsScraper.py` | Port to `src/.../scraping/scanning/achievement_workflow.py` | **Done** — `AchievementWorkflow` implemented |
| `scraping/shellScraper.py` | Port to `src/.../scraping/scanning/shell_workflow.py` | **Deleted** — V2 port not yet implemented |
| `scraping/scraperManager.py` | Remove — replaced by `SessionOrchestrator` | **Deleted** |
| `scraping/scraperExectuter.py` | Remove — replaced by `wuwa-scan` CLI | **Deleted** |
| `ui/` (all) | Port — rewrite against V2 `SessionOrchestrator` | **Pending** — imports updated; full rewrite outstanding |
| `properties/config.py` | Port with UI — Qt-dependent | **Pending** |
| `app.py` + `main.py` | Port with UI — entry points for Qt application | **Pending** |

### Phase 4 — Cleanup — Partial

| Item | Action | Status |
|---|---|---|
| `cli/reprocess.py` | Remove — superseded by `wuwa-reprocess` | **Deleted** |
| `cli/debug_ocr.py` | Move to nav-script or remove | **Pending** — updated to use `src/` imports |
| `cli/update_data.py` | Move to `src/.../cli/` or remove | **Pending** — updated to use `src/` imports |
| `conftest.py` (root) | Remove once no test imports legacy modules | **Pending** |
| `nav-scripts/` | Keep as-is (run via `wuwa-nav session.py`) | **Done** |
| `tools/` (minus `match_sonata_icon/`) | Keep as development tools | **Done** |

### Dependency on migration order

```
Phase 1 (delete dead code)               ✓ Complete
  └─► Phase 2 (move shared modules into src/)   ✓ Complete (assetsUpdater.py pending)
        └─► Phase 3 (port remaining scrapers + UI)     Partial — UI pending; scrapers removed
              └─► Phase 4 (cleanup root conftest, top-level cli/, etc.)  Partial
```

Phase 1 can proceed immediately — nothing depends on the deleted modules
except the UI, which itself needs porting (Phase 3).  Phase 2 is
prerequisite to Phase 3 because the ported scrapers / UI will import from
`src/`.  Phase 4 is a final sweep once all functional code lives under
`src/`.
