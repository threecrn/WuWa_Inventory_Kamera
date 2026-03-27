# Inventory Scanner v2 — Architecture Proposal

## Game Manipulation Layer (NEW)

The v2 architecture introduces a **UI-independent game interaction layer** under
`src/wuwa_inventory_kamera/game/` that cleanly separates game control from both
the Qt UI and the scraper logic.

### Layer overview

```
game/
  __init__.py
  input_controller.py    — Low-level mouse/keyboard/scroll (wraps win32api)
  screen.py              — Window detection, ScreenLayout, screenshot capture
  navigation.py          — High-level: open inventory, switch tabs, sort orders,
                           grid cell coordinates, page scrolling, menu detection
```

### Key design decisions

| Decision | Rationale |
|---|---|
| `InputController` is a thin win32api wrapper | Testable with a mock; no mss leak |
| `ScreenLayout` wraps `game.screenInfo.ScreenInfo` | Single place to resolve coordinates; v2 code never imports `ScreenInfo` directly |
| `GameWindow` owns DPI, monitor index, layout | Replaces scattered `WindowManager` + `ScreenInfo` construction |
| `GameNavigator` is stateful | Tracks open tab, sort order, avoids redundant clicks |
| Sort-order control via `SortOrder` enum | Echo/weapon scanning can set a specific sort before scanning |
| All coordinate math lives in `navigation.py` | `grid_cell_center()`, `scroll_to_page()`, `scroll_to_sonata()` |
| Nav-only OCR is inline (not batched) | Page-count reads use the OCR registry default; no OcrService overhead |

### `InputController`

```python
ctrl = InputController(monitor_index=1)
ctrl.click(500, 300)                  # left-click relative to monitor
ctrl.scroll(-3)                        # scroll up 3 notches
ctrl.press_key('esc')                  # single key
ctrl.hotkey('ctrl', 'v')               # modifier combo
ctrl.paste("search text")             # clipboard + Ctrl+V
```

### `GameNavigator`

```python
nav = GameNavigator(ctrl, game_window)
nav.open_inventory()
nav.switch_tab(InventoryTab.ECHOES)
nav.set_sort_order(SortOrder.LEVEL_DESC)
count, pages = nav.read_item_count()
nav.click_grid_cell(row=2, col=3)
nav.scroll_to_sonata()                # echo-specific detail panel scroll
nav.scroll_back_from_sonata()
nav.scroll_to_page(target=2, current=0)
```

### Callable from CLI or UI

The game layer has **zero Qt dependencies**. The CLI tool (`wuwa-scan`) and
the Qt UI can both construct the same `InputController` + `GameNavigator` stack.

---

## Scanning Workflows (NEW)

The scanning logic under `src/wuwa_inventory_kamera/scraping/scanning/` uses the
game manipulation layer and the OcrService to implement complete scan workflows.

### Module overview

```
scraping/scanning/
  scan_state.py            — ScanSession, ScanItem, GridPosition, rescan queue
  grid_navigator.py        — Forward scan + random-access cell navigation
  echo_workflow.py         — Full echo scan with lookahead + rescan support
  weapon_workflow.py       — Weapon/item scan (synchronous per cell)
  session_orchestrator.py  — Top-level runner for multi-scraper sessions
```

### Scan state tracking

`ScanSession` maintains the complete lifecycle of one scan run:

```python
session = ScanSession(total_items=240, sort_order=SortOrder.NEWEST)

session.mark_scanned(42, result=echo_dict)
session.request_rescan(42, reason="missing substats: 2/5 parsed")
idx = session.pop_rescan()        # → 42
session.mark_rescanned(42, result=better_dict)

print(session.progress)           # 0.0–1.0
print(session.rescan_pending)     # 0
```

Each `ScanItem` tracks: position (page/row/col), status (pending → scanned →
needs_rescan → rescanned | failed | skipped), attempt count, and result.

### Grid navigator

`GridNavigator` drives the `GameNavigator` through the inventory grid:

```python
grid = GridNavigator(nav, total_items=240, total_pages=10)

# Forward scan — visits every cell in order
grid.scan_forward(visitor_callback)

# Random access — for rescans
grid.navigate_to_cell(GridPosition(page=3, row=2, col=4, scan_index=76))

# Batch rescan — sorted by page to minimize scrolling
grid.visit_positions([pos1, pos2, pos3], visitor_callback)
```

### Echo workflow (rescan-aware)

`EchoWorkflow.run()` executes:

1. Open inventory → echoes tab → set sort order
2. Read echo count from UI
3. **Forward scan**: iterate all grid cells, capture 4 crops per echo,
   submit `EchoCapture` to OcrService → collect `Future[EchoResult]`
4. **Collect results**: resolve all futures, mark session items
5. **Rescan pass(es)**: any echo where the assembler flagged
   "missing substats" or "sonata scroll failure" is queued for rescan.
   The grid navigator jumps to the specific cell, re-captures, and
   re-submits. Up to `max_rescans` attempts per item.
6. Return accepted echo dicts.

### Weapon/item workflow

`WeaponWorkflow.run()` is simpler — each cell is captured and the future
is resolved immediately (blocking). Image-hash dedup skips identical cells.

### Session orchestrator

`SessionOrchestrator` replaces the V1 `scraperManager.scrapers()`:

```python
orch = SessionOrchestrator(
    scrapers=['echoes', 'weapons', 'devItems'],
    ocr_providers=['DmlExecutionProvider', 'CPUExecutionProvider'],
    min_rarity=4, min_level=10,
    sort_order=SortOrder.LEVEL_DESC,
    save_raw=Path('export'),
    on_progress=my_callback,
)
result = orch.run()
# result = {'date': '...', 'echoes': [...], 'weapons': [...], ...}
```

---

## CLI Tools

### `wuwa-scan` (NEW)

Headless scanning entry point — uses the game manipulation layer and
scanning workflows without any Qt UI:

```
wuwa-scan --scrapers echoes weapons --provider dml --min-rarity 4
wuwa-scan --scrapers echoes --sort-order level_desc --save-raw
```

### `wuwa-reprocess` (existing)

Offline re-processing of saved raw scans (no game needed):

```
wuwa-reprocess --session-id 2026-02-28_14-30-00 --service --provider dml
```

---

## Scraper inventory

Before discussing the new design, here is a summary of what each scraper does now and what it needs from OCR.

| Scraper | Nav model | OCR reads per item | Retry needed? | Current issues |
|---|---|---|---|---|
| **Echoes** | grid (up to ~1000 cells) | card name, level, sonata, stats×2 cols | Yes — substat count + validation | Complex; substats vary |
| **Weapons** | grid (24/page, N pages) | name, level `x/y`, rank digit | No | Simple single-field reads |
| **Items / Resources** | scroll (no fixed page count) | info block (name + count in one crop) | No | Duplicate-detection loop |
| **Characters** | list of 7 per screen | name, level, weapon name+level+rank, 5×skill level, 5×chain status, inherent skills | No | Many sequenced sub-reads per character |
| **Achievements** | search-per-item (clipboard paste) | status button text only | No | OCR is trivial; bottleneck is the search loop |
| **Shell** | single crop | currency amount | No | One read, totally trivial |

---

## Core principle changes

| Current | Proposed |
|---|---|
| Screenshot → disk → `RawEchoScan` → `echoProcessor` (threaded) | Screenshot → in-memory `Capture` → `OcrService` queue (one DML thread) |
| OCR called per-scan inside `ThreadPoolExecutor` | OCR batched across N captures in one GPU forward pass |
| Four OCR backends (`rapid`, `rapid_coord`, `tesser`, `tesser_coord`) | One backend: `RapidOcrBackend` with optional DML |
| Retry logic scattered in processor | Retry owned entirely by `OcrService` |
| Each scraper crops + OCRs inline | Each scraper submits `Capture` objects; OCR is centralised |

---

## Threading model

All scrapers share a single `OcrService` instance. Scrapers that are sequential by nature (achievements, characters) run serially on the scanner thread and simply pick up results from their futures before moving to the next item. The echo scraper is the only one that genuinely benefits from the lookahead decoupling.

```
Main process
 └── scrapers() process (multiprocessing, existing)
       │
       ├── OcrService thread  ◄──────────────────────────────────────────────────┐
       │   (single thread; owns both DML sessions)                               │
       │   · drain queue into batches                                            │
       │   · grouped det forward pass per crop type                              │
       │   · single batched rec forward pass                                     │
       │   · assemble + validate → resolve Future                                │
       │                                                                         │
       ├── Echo scanner thread  ─── EchoCapture ──► OcrService queue            │
       │   · game nav + click                                                    │  queue
       │   · screenshot + crop 4 regions                                         │
       │   · submit(capture) → Future  (non-blocking)                           │
       │   · collect futures in order after scanning                             │
       │                                                                         │
       ├── Weapon scanner  (sequential; no dedicated thread needed)              │
       │   · click cell → screenshot → submit(WeaponCapture) → future.result()  ─┘
       │   · or use simple inline fast-path (see below)                         
       │                                                                         
       ├── Item scanner  (sequential)                                            
       │   · same pattern as weapons                                             
       │                                                                         
       ├── Character scanner  (sequential; multiple captures per character)      
       │   · submit one CharCapture per character section                        
       │   · resolve futures as needed for navigation decisions                  
       │                                                                         
       ├── Achievement scanner                                                   
       │   · purely sequential; one screenshot per search result                
       │   · status crop is simple enough to skip OcrService overhead:          
       │     use a fast inline imageToString call instead (see note below)      
       │                                                                         
       └── Shell  (single read — inline, no service needed)                     
```

---

## When to use `OcrService` vs inline OCR

Not every scraper needs the full batching machinery. The rule of thumb:

| Condition | Recommendation |
|---|---|
| Many items, scanner moves faster than OCR | Use `OcrService` (echoes, weapons with large inventories) |
| Strictly sequential, OCR result gates next nav action | Inline `imageToString` or `OcrService.submit().result()` immediately |
| One or two reads total | Inline (shell, achievements status) |

Characters sit in the middle: they are strictly sequential but have many sub-reads per character. The simplest approach is to submit all crops for one character at once as a `CharCapture`, then call `future.result()` before moving the UI to the next section. This still lets the GPU work while the CPU does Python + input logic for the first section.

---

## New package layout

```
src/wuwa_inventory_kamera/
  game/                     (NEW — UI-independent game manipulation layer)
    __init__.py
    input_controller.py     (low-level mouse/keyboard/scroll via win32api)
    screen.py               (GameWindow, ScreenLayout, capture helpers)
    navigation.py           (GameNavigator — tabs, sort, grid coords, scrolling)
  cli/
    __init__.py
    reprocess.py            (existing — offline reprocess CLI)
    scan.py                 (NEW — live scan CLI entry point)
  scraping/
    ocr/
      __init__.py           (OCR backend registry + imageToString)
      _types.py             (OcrBackend protocol, OcrResult type)
      _rapidocr.py          (RapidOcrBackend + _provider_patch + thorough mode)
      batch.py              (BatchOcr — detect + crop + recognize pipeline)
    service/
      __init__.py
      captures.py           (all Capture + Result dataclasses per scraper type)
      ocr_service.py        (OcrService — the queue + DML thread)
      assemblers/
        __init__.py
        echo_assembler.py
        weapon_assembler.py
        item_assembler.py
        character_assembler.py
    scanning/               (NEW — scanning workflows + state tracking)
      __init__.py
      scan_state.py         (ScanSession, ScanItem, GridPosition, rescan queue)
      grid_navigator.py     (forward scan + random-access navigation)
      echo_workflow.py      (echo scan with lookahead + rescan support)
      weapon_workflow.py    (weapon/item scan — synchronous per cell)
      session_orchestrator.py (top-level multi-scraper runner)

scraping/                   (legacy V1 — kept for backward compat)
  scanning/
    echoesScanner.py        (Phase 1 raw capture)
  processing/
    echoesProcessor.py      (Phase 2 offline processing)
    echoesValidator.py      (stat validation — shared)
    statsExtractor.py       (legacy extractors — reprocess path)
  models/
    rawScan.py              (RawEchoScan — disk serialisation)
```

---

## Per-scraper capture types

```python
# scraping/service/captures.py

@dataclass
class EchoCapture:
    echo_index:  int
    card:        np.ndarray   # name + level text region
    sonata:      np.ndarray   # set name region
    stats_name:  np.ndarray   # stat name column
    stats_value: np.ndarray   # stat value column
    full_screenshot: np.ndarray | None = None  # debug only

@dataclass
class EchoResult:
    echo_index: int
    data:       dict | None   # None = rejected
    warnings:   list[str]
    retried:    bool

# ── Weapons ──────────────────────────────────────────────────────────────────
@dataclass
class WeaponCapture:
    index: int
    name:  np.ndarray         # item/weapon name region
    value: np.ndarray         # quantity (items) or level string (weapons)
    rank:  np.ndarray | None  # weapon rank digit; None for plain items

@dataclass
class WeaponResult:
    index:     int
    is_weapon: bool
    data:      dict | None    # None = below rarity/level threshold or unrecognised

# ── Items / Resources ────────────────────────────────────────────────────────
@dataclass
class ItemCapture:
    index: int
    info:  np.ndarray         # single crop containing name + count lines

@dataclass
class ItemResult:
    index: int
    name:  str
    item_id: str | None
    count: int

# ── Characters ───────────────────────────────────────────────────────────────
# One capture covers all readable fields for a single character section.
# The scanner submits section by section; the assembler handles partial results.
@dataclass
class CharCapture:
    char_index:    int
    section:       int          # 0=resonator 1=weapon 2=skip 3=skills 4=chain
    crops:         dict[str, np.ndarray]  # keyed by field name

@dataclass
class CharResult:
    char_index: int
    section:    int
    fields:     dict           # parsed values for this section
```

---

## `OcrService` internals

```python
# scraping/service/ocrService.py

# One sentinel type per capture kind lets the service dispatch to the right assembler.
CaptureType = EchoCapture | WeaponCapture | ItemCapture | CharCapture

class OcrService:
    def __init__(
        self,
        providers: list[str] = ['DmlExecutionProvider', 'CPUExecutionProvider'],
        batch_timeout: float = 0.05,
        max_batch_size: int = 32,
    ):
        self._backend = RapidOcrBackend(onnx_providers=providers)
        self._queue:   queue.Queue[CaptureType | _Stop] = queue.Queue()
        self._futures: dict[int, Future] = {}
        self._counter  = itertools.count()          # unique IDs across all types
        self._thread   = threading.Thread(target=self._run, daemon=True, name='OcrService')
        self._thread.start()

    def submit(self, capture: CaptureType) -> Future:
        fut = concurrent.futures.Future()
        uid = next(self._counter)
        self._futures[uid] = fut
        capture._uid = uid      # attach uid to capture for round-trip routing
        self._queue.put(capture)
        return fut

    def shutdown(self): ...     # put _Stop sentinel, join thread

    # ── Service thread ────────────────────────────────────────────────────

    def _run(self):
        while True:
            batch = self._drain_batch()
            if batch is None:
                break
            self._process_batch(batch)

    def _process_batch(self, batch: list[CaptureType]):
        # Group captures by type for efficient batch det + rec
        by_type = defaultdict(list)
        for c in batch:
            by_type[type(c)].append(c)

        for cls, group in by_type.items():
            if cls is EchoCapture:
                self._process_echoes(group)
            elif cls is WeaponCapture:
                self._process_weapons(group)
            elif cls is ItemCapture:
                self._process_items(group)
            elif cls is CharCapture:
                self._process_chars(group)
```

The per-type methods each follow the same pattern:
1. Collect all image crops of each spatial type (all `name` crops, all `value` crops, …)
2. Run `detect_batch` once per crop kind (same spatial dimensions → no padding needed)
3. Run `recognize_batch` once across all crops from all items in the group
4. Fan results back out via `_build_token_map`
5. Call the matching assembler; `set_result` on each future

---

## `EchoAssembler` role

Replaces the parsing logic embedded in `_processRawScan`. Takes the raw OCR token map and applies:

1. Card tokens → name lookup (exact + fuzzy), level parse
2. Sonata tokens → set name match against `sonataName`
3. Name+value tokens → `_matchStats` / coord alignment (reuse existing logic)
4. Validate; if `not vresult.valid or missing_substats` → request thorough retry on the DML thread

Fully unit-testable with no image I/O.

---

## Per-scraper assembler complexity

| Assembler | OCR inputs | Parsing complexity | Retry? |
|---|---|---|---|
| `EchoAssembler` | card, sonata, name col, value col | High — stat matching, substat count, validation | Yes |
| `WeaponAssembler` | name, level string, rank digit | Low — two integer parses + lookup | No |
| `ItemAssembler` | info block (2–3 lines) | Low — line split, regex digit strip | No |
| `CharAssembler` | name, level, weapon fields, skill levels, chain buttons | Medium — multi-section, each simple read | No |

Characters are sequential by necessity (UI navigation controls which section is visible), so `CharAssembler` accumulates partial results across multiple `CharCapture` submissions for the same character index and merges them into the final `dict` once section 4 is resolved.

---

## Scanner changes summary

### Echo scanner
Replace `saveRawScan(scan, raw_base)` with:
```python
capture = EchoCapture(echo_index=index, card=full[card_roi],
                      sonata=sonata_crop, stats_name=full[name_roi],
                      stats_value=full[value_roi])
futures.append(service.submit(capture))
```
Collect futures after the grid traversal, or in a parallel collector thread.

### Weapon scanner
Replace the inline `processGridItem` with:
```python
capture = WeaponCapture(index=index, name=img[name_roi],
                        value=img[value_roi], rank=img[rank_roi])
result = service.submit(capture).result()   # OK to block — ~200ms/cell anyway
```
The hash-cache deduplication that currently short-circuits repeated identical cells can be moved into `WeaponAssembler` using the same image-hash trick.

### Item scanner
Same pattern as weapons. The duplicate-detection logic (encounter counting) stays in the scanner loop since it depends on the `name` returned by the assembler — just read `result.name` after `.result()`.

### Character scanner
```python
# Section 0: resonator name + level
capture = CharCapture(char_index=i, section=0,
                      crops={'name': img[name_roi], 'level': img[level_roi]})
res = service.submit(capture).result()
if res.fields.get('already_seen'):
    break   # duplicate detection — same logic as now
```
Each of the five sections submits one `CharCapture`. The scanner still controls timing and navigation; it just does not parse anything itself.

### Achievement scanner
No change. The status button read is a single-word match (`"Claim"` or `"Activated"`), already fast inline, and the bottleneck is the game's search UI, not OCR.

### Shell scraper
No change. Single read; OcrService overhead is not worthwhile.

---

## What is reused unchanged

- `RapidOcrBackend` + `_provider_patch` — already works
- `detect_batch`, `extract_crops`, `recognize_batch` from `batch_ocr.py` → move to `scraping/ocr/batch.py`
- `echoesValidator.py` — `validate_echo_stats`, `expected_sub_count`
- `databaseUpdater.py`, `scraperManager.py` — untouched
- `cli/reprocess.py` — still uses `echoesProcessor` on disk scans
- Disk saving becomes opt-in debug mode rather than the default

---

## Tradeoff notes

**Batch size vs latency:** At ~200ms per echo and a 50ms drain timeout, a 24-cell page fills a batch of ~8–12 before the timeout fires — enough to see GPU utilisation gains without making the UI wait. Tune `batch_timeout` and `max_batch_size` together.

**Weapons / items vs batching:** With DML the overhead of a single-item forward pass is low enough that calling `.result()` immediately (blocking the scanner thread) is acceptable for weapons and items. If inventory scanning speed becomes a bottleneck, the same lookahead pattern used for echoes can be applied — collect futures in submission order and resolve them a few items later.

**Characters:** The character scraper is navigation-bound (~0.8s per section due to `time.sleep` calls and UI transitions). OCR is not in the critical path; submitting and immediately resolving is fine.

**Retry:** Echo thorough retry runs single-image on the DML thread (not re-batched), which is acceptable since it's uncommon. For non-echo scrapers, retry is not needed — name lookups use fuzzy matching with a 0.9 cutoff and the fields are all simple strings or integers.

**Disk saves for debug:** `EchoCapture.full_screenshot` is populated only in debug mode; `OcrService` can optionally write crops after assembling, keeping the `reprocess` path working at no cost in normal runs.


| Current | Proposed |
|---|---|
| Screenshot → disk → `RawEchoScan` → `echoProcessor` (threaded) | Screenshot → in-memory `EchoCapture` → `OcrService` queue (one DML thread) |
| OCR called per-scan inside `ThreadPoolExecutor` | OCR batched across N captures in one GPU forward pass |
| Four OCR backends (`rapid`, `rapid_coord`, `tesser`, `tesser_coord`) | One backend: `RapidOcrBackend` with optional DML |
| Retry logic scattered in processor | Retry owned entirely by `OcrService` |

---

## Threading model

```
Main process
 └── scrapers() process (multiprocessing, existing)
       ├── Scanner thread  ─── EchoCapture ──►  OcrService thread (single; owns DML sessions)
       │   · game nav                           · drain request queue
       │   · click + screenshot                 · group by crop type
       │   · crop 4 regions                     · batched det forward
       │   · submit(capture) → Future           · batched rec forward
       │   · never blocks on GPU                · validate + retry
       │                                        · resolve Future[EchoResult]
       └── Collector thread
             · iterates futures in submission order
             · builds INVENTORY dict
             · fires progress signals → UI
```

The DML constraint (single-threaded ONNX) is satisfied because exactly one thread (`OcrService`) ever touches the two ONNX sessions.

---

## New package layout

```
scraping/
  ocr/
    _rapidocr.py          (existing — RapidOcrBackend + _provider_patch, keep as-is)
    batch.py              (NEW — extract detect_batch / extract_crops / recognize_batch
                                 from batch_ocr.py into a reusable module)
  service/                (NEW package)
    __init__.py
    request.py            (EchoCapture, EchoResult dataclasses)
    ocrService.py         (OcrService — the queue + DML thread)
    echoAssembler.py      (turns raw OCR tokens → validated EchoResult, replaces
                           the guts of _processRawScan)
  scanning/
    echoesScanner.py      (small change: submit to OcrService instead of saveRawScan)
  processing/
    echoesProcessor.py    (kept for offline cli/reprocess.py path only)
    echoesValidator.py    (unchanged)
    statsExtractor.py     (unchanged — still used by reprocess path)
```

---

## Key data structures

```python
# scraping/service/request.py

@dataclass
class EchoCapture:
    """All 4 in-memory crops for one echo — the unit pushed by the scanner."""
    echo_index:  int
    card:        np.ndarray   # echo name + level text
    sonata:      np.ndarray   # set name region
    stats_name:  np.ndarray   # stat name column
    stats_value: np.ndarray   # stat value column
    # Optional — only populated in debug mode:
    full_screenshot: np.ndarray | None = None


@dataclass
class EchoResult:
    echo_index: int
    data:       dict | None    # None means rejected (unrecognised sonata, etc.)
    warnings:   list[str]
    retried:    bool
```

---

## `OcrService` internals

```python
# scraping/service/ocrService.py

class OcrService:
    def __init__(
        self,
        providers: list[str] = ['DmlExecutionProvider', 'CPUExecutionProvider'],
        batch_timeout: float = 0.05,   # seconds to wait for more items
        max_batch_size: int = 32,
    ):
        self._backend = RapidOcrBackend(onnx_providers=providers)
        self._queue:   queue.Queue[EchoCapture | _Stop] = queue.Queue()
        self._futures: dict[int, concurrent.futures.Future[EchoResult]] = {}
        self._thread  = threading.Thread(target=self._run, daemon=True, name='OcrService')
        self._thread.start()

    # ── Scanner-side API ──────────────────────────────────────────────────

    def submit(self, capture: EchoCapture) -> Future[EchoResult]:
        fut = concurrent.futures.Future()
        self._futures[capture.echo_index] = fut
        self._queue.put(capture)
        return fut

    def shutdown(self):
        self._queue.put(_Stop())
        self._thread.join()

    # ── Service thread ────────────────────────────────────────────────────

    def _run(self):
        while True:
            batch = self._drain_batch()
            if batch is None:
                break
            self._process_batch(batch)

    def _drain_batch(self) -> list[EchoCapture] | None:
        # Block on first item (no CPU spin), then collect more within timeout
        first = self._queue.get()
        if isinstance(first, _Stop):
            return None
        batch = [first]
        deadline = time.monotonic() + self._batch_timeout
        while len(batch) < self._max_batch_size:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                item = self._queue.get(timeout=remaining)
                if isinstance(item, _Stop):
                    self._queue.put(item)  # re-queue for next drain
                    break
                batch.append(item)
            except queue.Empty:
                break
        return batch

    def _process_batch(self, batch: list[EchoCapture]):
        # 1. Batch detect on each crop type separately (different spatial dims)
        card_boxes   = detect_batch([c.card        for c in batch], self._backend)
        sonata_boxes = detect_batch([c.sonata      for c in batch], self._backend)
        name_boxes   = detect_batch([c.stats_name  for c in batch], self._backend)
        value_boxes  = detect_batch([c.stats_value for c in batch], self._backend)

        # 2. Collect all rec crops across all images, batch rec in one pass
        all_crops = (
            extract_crops_typed('card',   [c.card        for c in batch], card_boxes)   +
            extract_crops_typed('sonata', [c.sonata      for c in batch], sonata_boxes) +
            extract_crops_typed('name',   [c.stats_name  for c in batch], name_boxes)   +
            extract_crops_typed('value',  [c.stats_value for c in batch], value_boxes)
        )
        texts = recognize_batch([c.crop for c in all_crops], self._backend)

        # 3. Route recognised tokens back to their capture
        token_map = _build_token_map(all_crops, texts)

        # 4. Assemble + validate each echo; retry individually if needed
        for capture in batch:
            result = self._assemble_and_validate(capture, token_map)
            self._futures[capture.echo_index].set_result(result)
            del self._futures[capture.echo_index]

    def _assemble_and_validate(
        self, capture: EchoCapture, token_map: dict
    ) -> EchoResult:
        result = EchoAssembler.assemble(capture.echo_index, token_map)

        if result.data is None:
            return result

        vresult = validate_echo_stats(...)
        missing = len(result.data.get('sub', {})) < expected_sub_count(result.data['level'])

        if not vresult.valid or missing:
            # Thorough single-image retry (multi-pass; still on this thread)
            retry_tokens = self._backend.thorough_recognize(capture.stats_name)
            result = EchoAssembler.assemble_retry(capture.echo_index, retry_tokens, result)

        return result
```

---

## `EchoAssembler` role

This replaces the parsing logic currently embedded in `_processRawScan`. It takes the raw OCR token map (already a pure data structure) and applies:

1. Card tokens → name lookup (exact + fuzzy), level parse
2. Sonata tokens → set name match against `sonataName`
3. Name+value tokens → `_matchStats` / coord alignment (reuse existing logic)
4. Returns `EchoResult` with warnings

This is fully unit-testable without any image I/O.

---

## Scanner change

The scanner change is minimal — replace `saveRawScan(scan, raw_base)` with `service.submit(capture)`:

```python
# Before (echoesScanner.py)
scan = RawEchoScan(full_screenshot=full, sonata_screenshot=sonata, ...)
saveRawScan(scan, raw_base)
scans.append(scan)

# After
capture = EchoCapture(
    echo_index  = index,
    card        = full[card_roi],
    sonata      = sonata_crop,
    stats_name  = full[name_roi],
    stats_value = full[value_roi],
)
futures.append(service.submit(capture))
```

The scanner loop ticks at input-device speed (~200ms/echo) regardless of GPU speed. The `OcrService` queue depth absorbs any burst.

---

## What is reused unchanged

- `RapidOcrBackend` + `_provider_patch` — already works
- `detect_batch`, `extract_crops`, `recognize_batch` from `batch_ocr.py` → move to `scraping/ocr/batch.py`
- `echoesValidator.py` — `validate_echo_stats`, `expected_sub_count`
- `databaseUpdater.py`, `scraperManager.py` — untouched
- `cli/reprocess.py` — still uses `echoesProcessor` on disk scans (that path is valuable for debugging and can stay)
- Disk saving becomes opt-in debug mode rather than the default

---

## Tradeoff notes

**Batch size vs latency:** At ~200ms per echo and a 50ms drain timeout, a 24-cell page fills a batch of ~8–12 before the timeout fires — enough to see GPU utilisation gains without making the UI wait. Tune `batch_timeout` and `max_batch_size` together.

**Retry:** Thorough retry runs single-image on the DML thread (not re-batched), which is acceptable since it's uncommon. If needed in the future it can be batched too by collecting all retry candidates at the end of `_process_batch`.

**Disk saves for debug:** `EchoCapture.full_screenshot` is populated only in debug mode; `OcrService` can optionally call `saveRawScan`-equivalent after assembling, keeping the reprocess path working at no cost in normal runs.
