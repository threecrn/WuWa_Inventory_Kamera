# Output Schema v1 Compatibility Plan

## Goal

Make current exported JSON backward compatible with v1 by restoring v1 file
shapes and value semantics, while keeping new metadata only as additive fields.

Compatibility rule:

- keep existing v1 value types and meanings unchanged
- add fields only when they are additive to an existing object
- do not add fallback logic for already-written v1.1 session files

## Serializer-First Approach

Introduce one serialization layer between in-memory scan results and on-disk JSON.
Keep OCR and workflow outputs unchanged in memory where practical, and convert only
at export time.

Primary writer entry points:

- `src/wuwa_inventory_kamera/cli/scan.py`
- `src/wuwa_inventory_kamera/ui/home.py`
- `src/wuwa_inventory_kamera/cli/reprocess.py`
- `src/wuwa_inventory_kamera/scraping/utils/common.py`

## Required Output Changes

### Weapons

- restore the v1 array-of-singletons container
- restore nested `ascension` in weapon export objects
- keep `weapon_key`, `maxLevel`, and `_equipped` as additive fields

Owning inputs:

- `src/wuwa_inventory_kamera/scraping/service/assemblers/weapon_assembler.py`

### Inventory Items

- restore `inventory_wuwainventorykamera.json` as an id-to-count map
- aggregate `devItems`, `resources`, and `shell` into the inventory map when present
- keep `devItems_wuwainventorykamera.json` and `resources_wuwainventorykamera.json`
  as additive convenience exports

Owning inputs:

- `src/wuwa_inventory_kamera/scraping/service/assemblers/weapon_assembler.py`
- `src/wuwa_inventory_kamera/scraping/scanning/shell_workflow.py`

### Achievements

- restore numeric achievement ids in exported payloads
- restore `achievements_wuwainventorykamera.json`

Owning inputs:

- `src/wuwa_inventory_kamera/scraping/scanning/achievement_workflow.py`

### Characters and Echoes

- keep current payloads where they already remain v1-compatible with additive fields

Owning inputs:

- `src/wuwa_inventory_kamera/scraping/service/assemblers/echo_assembler.py`
- `src/wuwa_inventory_kamera/scraping/scanning/character_workflow.py`
- `src/wuwa_inventory_kamera/scraping/service/character_reprocess.py`

## Session File Strategy

`scan_result.json` remains the canonical session artifact, but its section payloads
should be serialized through the same compatibility layer.

Planned session sections:

- keep `date` and optional `cancelled`
- serialize `echoes`, `weapons`, `characters`, `achievements`, and `shell`
- add `inventory` as the aggregated v1-compatible inventory map
- keep `devItems` and `resources` only as additive convenience sections

## Viewer Changes

Update the result viewer to read the new export contract written by the serializer:

- support `inventory_wuwainventorykamera.json`
- support `achievements_wuwainventorykamera.json`
- support v1 weapon arrays in both standalone files and `scan_result.json`
- treat `inventory` as the primary item section in session files when present

Owning file:

- `src/wuwa_inventory_kamera/ui/inventory_models.py`

## Validation

Focused suites to update and run:

- `tests/test_reprocess_cli.py`
- `tests/test_scan_debug_workflows.py`
- `tests/test_inventory_models.py`
- `tests/test_equipped_output.py`
- `tests/test_character_reprocess.py`

## Execution Order

1. Add the serializer module and route all writer entry points through it.
2. Update standalone export writing for UI save and reprocess.
3. Update `scan_result.json` serialization.
4. Update viewer loading for inventory, achievements, and v1 weapon shapes.
5. Update focused tests and docs.