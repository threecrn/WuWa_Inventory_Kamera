# Localization Data Plan

## Current State

### Data directory layout

```
data/
├── languages.json              ← {display_name: lang_code} ("English":"en", …)
├── de/
├── en/
│   ├── achievements.json
│   ├── characters.json
│   ├── definedText.json        ← game UI strings keyed by PrefabTextItem IDs
│   ├── echoes.json
│   ├── echoStats.json          ← stat names & sub-stat names (localized)
│   ├── ItemInfo.json           ← ConfigDB dump (written by updater)
│   ├── items.json
│   ├── MultiText.json          ← raw ConfigDB multi-language blob
│   ├── sonataName.json         ← {slug: id} dict
│   ├── WeaponConf.json         ← ConfigDB dump (written by updater)
│   └── weapons.json
├── ja/
├── ko/
├── zh-Hans/
└── zh-Hant/
```

Supported language codes (from `languages.json`):
`de`, `en`, `es`, `fr`, `id`, `ja`, `ko`, `pt`, `ru`, `th`, `vi`, `zh-Hans`, `zh-Hant`.

Only `de`, `en`, `ja`, `ko`, `zh-Hans`, `zh-Hant` currently have local directories.

---

## Inventory of all access sites

### 1. Central data module — `scraping/data.py` (V1 legacy)

| File | Lines | What it does |
|------|-------|--------------|
| `scraping/data.py` | 22–56 | `loadData(language)` loads all 8 JSON files into module-level globals. Defaults to `'en'`. Called at import time: `loadData('en')`. |

**Module-level globals exposed:**
`itemsID`, `charactersID`, `weaponsID`, `echoesID`, `achievementsID`, `echoStats`, `definedText`, `sonataName`.

**Consumers (import from `scraping.data` or via `scraping.utils.common`):**

| Consumer | Symbols used |
|----------|-------------|
| `scraping/utils/common.py` | all IDs, `definedText` |
| `scraping/utils/__init__.py` | all IDs |
| `scraping/charactersScraper.py` | `charactersID`, `weaponsID`, `definedText` |
| `scraping/achievementsScraper.py` | `achievementsID`, `definedText` |
| `scraping/itemsScraper.py` | `itemsID` |
| `scraping/weaponsScraper.py` | `weaponsID`, `itemsID` |
| `scraping/processing/statsExtractor.py` | `echoStats` |
| `scraping/processing/echoesProcessor.py` | `echoesID`, `echoStats`, `sonataName` |
| `game/menu.py` | `definedText` (via `scraping.utils.common`) |
| `updater/databaseUpdater.py` | all IDs, `definedText`, `sonataName` (for post-update in-memory refresh) |

### 2. Central data module — `src/…/scraping/data.py` (V2)

| File | Lines | What it does |
|------|-------|--------------|
| `src/wuwa_inventory_kamera/scraping/data.py` | 26–66 | Identical shape to V1. `loadData(language)` defaults to `'en'`, auto-called at import time. |

**Consumers (import from `wuwa_inventory_kamera.scraping.data`):**

| Consumer | Symbols used |
|----------|-------------|
| `src/…/scraping/service/assemblers/echo_assembler.py` | `echoesID`, `echoStats`, `sonataName` |
| `src/…/scraping/service/assemblers/character_assembler.py` | `charactersID`, `weaponsID`, `definedText` |
| `src/…/scraping/service/assemblers/weapon_assembler.py` | `weaponsID`, `itemsID` |
| `src/…/scraping/service/assemblers/item_assembler.py` | `itemsID` |
| `src/…/game/navigation.py:578` | `definedText` (for `is_in_main_menu`) |

### 3. Direct file reads that bypass the central module

| File | Line(s) | Path expression | Data file |
|------|---------|-----------------|-----------|
| `src/…/game/navigation.py` | 370–373 | `Path('data') / 'en' / 'sonataName.json'` | `sonataName.json` — **hardcoded `'en'`** |
| `tools/update_sonata_templates/main.py` | 67 | `DATA_DIR / "en" / "sonataName.json"` | `sonataName.json` — **hardcoded `'en'`** |
| `tools/scrape_sonata_icons/main.py` | 59 | `data_dir / "en" / "sonataName.json"` | `sonataName.json` — **hardcoded `'en'`** |
| `nav-scripts/build-sonata-templates-from-filter.py` | 42 | `_REPO_ROOT / 'data' / _LANG / 'sonataName.json'` | `sonataName.json` — uses `--lang` arg ✓ |
| `properties/config.py` | 18–19 | `basePATH / 'data' / 'languages.json'` | `languages.json` |
| `updater/databaseUpdater.py` | 150–155 | `Path('data') / self.lang / filename` | All files (parameterized ✓) |

### 4. Hardcoded `definedText` key lookups

These reference opaque PrefabTextItem IDs to match localized game-UI strings:

| PrefabTextItem ID | Meaning | File(s) |
|-------------------|---------|---------|
| `PrefabTextItem_1547656443_Text` | "Terminal" (main menu label) | `game/menu.py:37`, `src/…/game/navigation.py:587` |
| `PrefabTextItem_3963945691_Text` | "Activated" (character status) | `scraping/charactersScraper.py:150,173`, `src/…/assemblers/character_assembler.py:172` |
| `PrefabTextItem_128820487_Text` | "Claim" (achievement status) | `scraping/achievementsScraper.py:19` |

These work correctly for any language **as long as** `loadData()` was called with the right language. The key IDs are stable across languages.

### 5. Hardcoded English strings used for matching

| String | Purpose | File | Line |
|--------|---------|------|------|
| `'terminal'` | Fallback when `definedText` is missing | `navigation.py:587` |
| `'filter'`, `'on/off'` | Detect "Filter On/Off" dropdown entry | `navigation.py:409` |
| `'Activated'` | (implied by `.lower()` comparison) | — (uses `definedText` correctly) |

The `'filter'` / `'on/off'` matching in `set_sonata_filter` is English-only — other languages will have completely different text for that dropdown entry.

### 6. `sonataName` type mismatch

`sonataName.json` is a **dict** (`{slug: id}`), but `scraping/data.py` declares `sonataName: list = []` and converts the dict to a **list of keys** via `list(obj)` when the default is a list.

- **V1/V2 scrapers** iterate `sonataName` as a list of slug strings for substring matching — this *works* but loses the ID values.
- **`navigation.py:370`** loads it directly as a dict to get both slugs and IDs for dropdown ordering — this is correct but bypasses the central module.
- **`build-sonata-templates-from-filter.py`** also loads directly as a dict.

---

## Problems

1. **Two copies of the data module** — `scraping/data.py` (V1) and `src/…/scraping/data.py` (V2) have the same shape but are separate codebases. Changes must be mirrored.

2. **No language coordination** — each consumer either hardcodes `'en'` or calls `loadData()` with its own language argument. Nothing ensures all modules see the same language.

3. **Direct file reads bypassing the central module** — `navigation.py`, tools, and nav-scripts load `sonataName.json` directly, constructing their own paths.

4. **`sonataName` exposed as the wrong type** — the dict→list conversion discards the ID values that callers like `set_sonata_filter` need.

5. **English-only string matching** — `set_sonata_filter` uses `'filter' in text` / `'on/off' in text` to detect the toggle entry, which only works in English.

6. **CWD-relative paths** — `Path('data') / …` breaks when the tool is run from a different working directory.

---

## Proposals

### A. Introduce a `GameData` singleton

Replace the two `scraping/data.py` modules + all ad-hoc file reads with one class:

```python
# src/wuwa_inventory_kamera/data.py  (new top-level location)

class GameData:
    _instance: GameData | None = None
    lang: str
    data_dir: Path

    # Typed accessors
    items: dict[str, ...]
    characters: dict[str, ...]
    weapons: dict[str, ...]
    echoes: dict[str, ...]
    achievements: dict[str, ...]
    echo_stats: dict[str, ...]
    defined_text: dict[str, str]
    sonata_names: dict[str, int]       # ← dict, not list

    @classmethod
    def instance(cls) -> GameData: ...

    @classmethod
    def init(cls, lang: str = 'en', data_dir: Path | None = None) -> GameData: ...

    def reload(self, lang: str | None = None) -> None: ...
```

- A single `GameData.init('de')` at startup sets the language for the whole process.
- Every consumer calls `GameData.instance()` to get the data.
- `sonata_names` stays a dict so callers can read both slugs and IDs.
- `data_dir` defaults to `Path(__file__).parents[N] / 'data'` (package-relative, not CWD-relative).

### B. Backward-compatible shims

Keep the existing `scraping.data` module globals but have them delegate:

```python
# scraping/data.py — becomes a thin shim
from wuwa_inventory_kamera.data import GameData as _GD

def loadData(language=None):
    _GD.init(language or 'en')
    d = _GD.instance()
    global itemsID, sonataName, ...
    itemsID.update(d.items)
    sonataName.clear()
    sonataName.extend(d.sonata_names)  # keeps list[str] for old callers
    ...
```

This lets V1 scrapers keep `from scraping.data import sonataName` without changes.

### C. Migrate `sonataName` to its true dict type under a new name

- `GameData.sonata_names` → `dict[str, int]`.
- Old `sonataName: list[str]` shim kept only in the V1 compatibility layer.
- V2 assemblers and `navigation.py` switch to `GameData.instance().sonata_names`.
- `set_sonata_filter` and nav-scripts stop doing their own `json.loads(…)`.

### D. Resolve the "Filter On/Off" English-only matching

The first item in the sonata dropdown is a toggle labelled differently per language. Options:

1. **Add a `definedText` key** — if the updater can discover the PrefabTextItem ID for "Filter On/Off", add it to `definedText.json` and reference it from `set_sonata_filter`.
2. **Position-based detection** — the toggle is always at position 0 in the dropdown. Instead of OCR-matching the text, recognise it by position after scrolling to the known top.
3. **Negative match** — if the text doesn't match any known sonata slug, assume it's the toggle.

Option 1 is the most robust if the key exists. Option 2 is a good fallback.

### E. Centralise path resolution

`GameData.__init__` should resolve the data directory relative to the package install location, not CWD:

```python
_DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent.parent / 'data'
```

All tools, nav-scripts, and scrapers use `GameData.instance().data_dir / …` instead of constructing paths themselves.

### F. Migration order

1. Create `src/wuwa_inventory_kamera/data.py` with `GameData`.
2. Wire `loadData()` in `src/…/scraping/data.py` to delegate to `GameData`.
3. Update V2 assemblers + `navigation.py` to use `GameData.instance()`.
4. Update nav-scripts and tools to use `GameData` (or accept `--lang` and pass it through).
5. Wire V1 `scraping/data.py` shim.
6. Add the "Filter On/Off" `definedText` key to the updater.
7. Remove direct `json.loads(Path('data')/…)` calls.

---

## Files that need changes (per proposal)

| File | Change |
|------|--------|
| `src/wuwa_inventory_kamera/data.py` | **New** — `GameData` class |
| `src/wuwa_inventory_kamera/scraping/data.py` | Delegate to `GameData`; keep globals as shims |
| `scraping/data.py` | Same delegation for V1 |
| `src/…/game/navigation.py` | Use `GameData` for `set_sonata_filter` and `is_in_main_menu` |
| `src/…/scraping/service/assemblers/*.py` | Import from `GameData` instead of `scraping.data` |
| `nav-scripts/build-sonata-templates-from-filter.py` | Use `GameData` or accept lang via `--lang` (already done) |
| `tools/update_sonata_templates/main.py` | Accept `--lang`; use `GameData` |
| `tools/scrape_sonata_icons/main.py` | Accept `--lang`; use `GameData` |
| `properties/config.py` | Load `languages.json` via `GameData.data_dir` |
| `updater/databaseUpdater.py` | Call `GameData.init(lang)` after updating files |
| `game/menu.py` | Import `definedText` from `GameData` shim |
