# Inventory Result Viewer Plan

## Goal

Repurpose the app's `Inventory` tab into a real inspector for scan result JSON files.

The current tab is not a general result viewer. It is a legacy item-count view that only works for `inventory_wuwainventorykamera.json` style files. The app now produces several different result formats, so the UI needs a schema-aware, read-only viewer instead of a single quantity grid.

## Current State

### What exists today

- `src/wuwa_inventory_kamera/ui/main_window.py` always exposes an `Inventory` tab.
- `src/wuwa_inventory_kamera/ui/inventory.py` provides that tab.
- The tab can load a `.json` file, but it still assumes the JSON is a dict of `item_id -> quantity`.
- For display metadata it only consults `itemsID` from `src/wuwa_inventory_kamera/scraping/data.py`.
- It builds cards with an image, a display name, and a quantity label.

### What the current implementation actually supports

The current implementation still matches the legacy inventory export shape that older sessions may contain:

- `inventory_wuwainventorykamera.json`
- shape: `{ "2": 12345, "10800": 3 }`
- display metadata source: `data/<lang>/items.json`

The app no longer produces that file, and the viewer does not need to support it.

### What it does not support

The rest of the app now writes heterogeneous scan outputs:

| File | Current shape | Produced by |
| --- | --- | --- |
| `echoes_wuwainventorykamera.json` | `list[dict[str, dict]]` | UI save flow and `wuwa-reprocess` |
| `weapons_wuwainventorykamera.json` | `list[dict]` | UI save flow and `wuwa-reprocess` |
| `characters_wuwainventorykamera.json` | `dict[str, dict]` | UI save flow and `wuwa-reprocess` |
| `inventory_wuwainventorykamera.json` | `dict[str, int]` | Unsupported legacy export |
| `scan_result.json` | session-level dict with multiple sections | `wuwa-scan` CLI |

The current `Inventory` tab does not detect which schema was loaded, and it does not present the non-legacy files meaningfully.

### Concrete mismatch examples

- Echo exports are a list of entries shaped like `{ "310000010": { ... } }`.
- Weapon exports are a list of rows like `{ "id": 21010074, "level": 40, "maxLevel": 90, "rank": 2 }`.
- Dev-item and resource exports reuse the weapon/item assembler and produce rows like `{ "id": 10800, "count": 3 }`.
- Character exports are keyed by character id and contain nested `weapon`, `skills`, and `chain` data.
- CLI session exports can also include `achievements` and `shell` results.

If one of those files is opened in the current tab, the UI still tries to treat each top-level key as an item id from `items.json`. That means non-legacy files are still displayed incorrectly, even though the write-back corruption path is gone.

## Data And Asset Inventory

### Metadata already available

- `data/<lang>/items.json` contains `id`, `name`, and `image`.
- `data/<lang>/weapons.json` contains `id`, `name`, `rarity`, and `image`.
- `data/<lang>/characters.json` currently contains only `name -> id`.
- `data/<lang>/echoes.json` currently contains only `name -> id`.
- `data/<lang>/achievements.json` currently contains only `name -> id`.
- `data/<lang>/sonataName.json` plus `assets/IconS/` already support sonata-set icon matching.

### Asset gaps discovered during inspection

- The asset updater in `src/wuwa_inventory_kamera/updater/assets.py` only downloads `assets/IconS/`.
- The workspace currently has no `assets/IconWeapon/` tree, even though `weapons.json` image paths point there.
- Several asset folders already exist (`IconA`, `IconC`, `IconMout`, `IconMst`, `IconTask`, etc.) but are empty in this checkout.
- That means the viewer cannot assume local thumbnails are present even for items and weapons.

### Implication

The viewer must be designed to work without local art:

- name-first rendering
- optional thumbnails
- graceful placeholders when an asset path is missing

Icons should improve the experience, not block the feature.

## Recommended Product Direction

### Primary purpose

Make the `Inventory` tab a read-only inspector for saved scan results.

That is the safest first step because:

- legacy inventory is now a compatibility input, not an active editing workflow
- exported scan results are structured records, not a single editable quantity map
- editing support would require schema-specific validation per result type

Manual editing is out of scope for this viewer plan, including legacy inventory quantity correction inside this tab.

### Inputs the viewer should support

Support both of these entry points:

1. Open a single JSON file.
2. Open a session folder and enumerate known result files inside it.

For single-file loading, detect the schema from both filename and JSON shape.

For session-folder loading, prefer this order:

1. `scan_result.json`
2. known standalone exports such as `echoes_wuwainventorykamera.json`
3. raw/debug folder links as secondary context

Current implementation target for Milestone 1:

- support `Open file`
- support `Open session`
- prefer `scan_result.json` when present in the selected session folder
- otherwise aggregate the known standalone exports into one read-only document view
- use a simple section selector when one document exposes multiple result sections
- keep quick `Reload` and `Open folder` actions on the active source
- filter the active section with a simple text search
- show a lightweight details pane for the selected row

### Result types to support in the first full implementation

- echoes exports
- weapons exports
- dev items exports
- resources exports
- characters exports
- session-level `scan_result.json`

### Result types to defer unless already cheap

- achievements as a dedicated standalone export
- shell as a dedicated standalone export
- write-back editing for non-legacy files

The viewer should still display achievements and shell when they are present inside `scan_result.json`.

## Proposed Architecture

## 1. Add a schema-normalization layer

Create a small UI-facing model layer, for example:

- `src/wuwa_inventory_kamera/ui/inventory_models.py`
- or `src/wuwa_inventory_kamera/ui/result_viewer/`

Suggested responsibilities:

- detect document type from filename and payload shape
- load one file or one session folder into a typed document model
- normalize heterogeneous JSON into section/row objects the UI can render consistently
- keep all schema-specific logic out of the QWidget code

Suggested normalized concepts:

- `InventoryDocument`
- `InventorySection`
- `InventoryRow`
- `InventoryRowDetails`
- `AssetRef`

Suggested document kinds:

- `echoes_export`
- `weapons_export`
- `items_export`
- `characters_export`
- `scan_session`
- `unsupported_legacy`
- `unknown`

## 2. Replace the current single-grid widget with a real viewer

Recommended layout:

- top toolbar: Open File, Open Session Folder, Reload, Open Containing Folder
- left pane: section list (`Echoes`, `Weapons`, `Dev Items`, `Resources`, `Characters`, `Achievements`, `Shell`)
- center pane: result table or card list for the selected section
- right pane: detail view for the selected record

Suggested viewer behavior:

- global search within the current section
- schema-specific filters and sorting
- no Save button for non-legacy documents in phase 1
- explicit empty/error states for unknown or malformed files

## 3. Use schema-specific adapters

Adapters should convert each source format into display rows.

### Unsupported legacy inventory

Input:

- `dict[item_id, quantity]`

Behavior:

- detect it explicitly
- show a clear unsupported message
- do not try to coerce it into the new viewer model

### Echo adapter

Input:

- `list[{echo_id: {...}}]`

Display columns:

- echo name
- rarity
- level
- tune level
- sonata
- cost
- equipped character
- validation/debug flags when present

Details panel:

- main stat
- substats
- raw internal metadata such as `_scanIndex`, `_monsterId`, `_cost`

### Weapon adapter

Input:

- `list[{id, level, maxLevel, rank, _equipped?}]`

Display columns:

- weapon name
- rarity
- level
- max level
- rank
- equipped character

### Dev-item/resource adapter

Input:

- `list[{id, count}]`

Display columns:

- item name
- count
- item id

### Character adapter

Input:

- `dict[char_id, {...}]`

Display columns:

- character name
- level
- ascension
- weapon name
- weapon level
- weapon rank
- chain count

Details panel:

- skills block
- weapon block
- future hook for equipped echo summary if that data becomes available

### Session adapter

Input:

- CLI `scan_result.json`

Behavior:

- expose each populated top-level section as a viewer section
- show session metadata (`date`, `cancelled`)
- allow opening sibling standalone exports when both exist

## 4. Separate metadata resolution from raw viewer logic

Add a metadata resolver layer that maps ids and names to display information.

Suggested responsibilities:

- resolve item names and icons from `items.json`
- resolve weapon names, rarity, and icons from `weapons.json`
- resolve character names from `characters.json`
- resolve echo names from `echoes.json`
- resolve achievement names from `achievements.json`
- provide placeholder labels when a lookup fails

Do not force the viewer code to know where every JSON file lives.

## 5. Add robust asset fallback handling

The current `ItemCard.setupImage()` path assumes the file exists. The full viewer should instead:

- try local asset path if present
- fall back to a generated placeholder tile when missing
- keep text readable when no image exists
- log missing assets once per path instead of spamming

This should apply to all supported result types.

## Asset Strategy

### Phase 1: do not block on missing icons

Ship the result viewer with text-first rows and placeholders.

This avoids delaying the feature on the asset pipeline.

### Phase 2: enrich metadata for weapons, characters, and echoes

Recommended work items:

1. Extend updater hooks in `src/wuwa_inventory_kamera/updater/database.py`.
2. Decide whether to enrich existing files (`characters.json`, `echoes.json`) or add new metadata files such as `character_meta.json` and `echo_meta.json`.
3. Add an asset resolver/downloader that can materialize the paths referenced by the metadata.

Recommended separation:

- data updater builds normalized metadata
- asset updater downloads or refreshes image files
- UI only consumes the resolved metadata and local paths

### Likely asset-source options

Possible sources that fit the current architecture:

- additional official extracted game data if available locally or from the upstream data source
- new downloaded metadata files that include icon paths for characters and echoes
- curated community/wiki assets as a fallback when official sources are not available

Selection criteria:

- stable ids that match current export ids
- automation-friendly updates
- low risk of name drift across languages

### Specific gaps to solve

- weapon icons: metadata exists, but the referenced asset tree is not currently downloaded
- character icons: ids exist, but no icon metadata is generated today
- echo icons: ids exist, but no viewer-facing icon metadata is generated today
- achievement icons: probably optional for the first viewer milestone

## Implementation Steps

### Milestone 0: lock the scope

1. Define the tab as a result viewer first, not an editor.
2. Decide whether `scan_result.json` is a first-class input in the UI.
3. Remove the legacy manual inventory editor from the app.

### Milestone 1: replace the unsafe legacy-only loader

1. Add schema detection.
2. Add typed adapters for the existing export formats.
3. Remove Save and inline quantity editing from the tab.
4. Add explicit unknown-format and malformed-file messages.

Exit criteria:

- every currently produced export file can be opened without corrupting it
- the tab shows useful text for each supported file even when images are missing

### Milestone 2: build a usable inspection UI

1. Add section navigation.
2. Add table/card views by section type.
3. Add search, sort, and filter controls.
4. Add a details pane for nested structures.
5. Add open-session-folder support.

Exit criteria:

- the user can inspect all major export types from one session without manually editing JSON

### Milestone 3: enrich metadata and assets

1. Add richer character metadata.
2. Add richer echo metadata.
3. Add weapon icon asset download support.
4. Add placeholder-to-real-icon resolution without changing viewer contracts.

Exit criteria:

- the viewer can show thumbnails for the result types where metadata/assets exist
- missing assets degrade cleanly

## Testing Plan

### Add adapter tests

Create fixtures for:

- `inventory_wuwainventorykamera.json`
- `echoes_wuwainventorykamera.json`
- `weapons_wuwainventorykamera.json`
- `characters_wuwainventorykamera.json`
- `scan_result.json`

Test:

- schema detection
- row normalization
- nested details extraction
- missing metadata behavior
- unknown file behavior

### Add UI smoke tests

At minimum:

- load each supported file type into the widget
- confirm the widget does not crash when icons are missing
- confirm section switching works for session documents

### Reuse existing coverage as baseline

Current tests already pin some export-save contracts:

- `tests/test_scan_debug_workflows.py`
- `session_tests/conftest.py`

Those tests are a good starting point for JSON fixtures, but they do not currently test the Inventory tab UI.

## Open Questions

1. Should the tab open a session folder by default instead of a single file?
2. Should `scan_result.json` become the canonical viewer input, with standalone exports treated as convenience files?
3. Should characters and echoes get richer metadata by expanding `characters.json` and `echoes.json`, or by adding separate metadata files?
4. Which asset source is acceptable for character and echo thumbnails?
5. Should legacy inventory files remain supported as read-only compatibility inputs once the viewer handles all current export formats?

## Recommended Next Step

Implement Milestone 1 first:

- add schema detection
- normalize the existing export formats
- keep the Inventory tab read-only with no write-back path

That delivers immediate value, removes the current corruption risk, and keeps the asset-source decision decoupled from the first usable viewer.