# Localization Data Plan

## Goals

- Keep locale-independent identifiers in exports, caches, and internal result models as English normalized keys.
- Show real localized strings in the UI without changing persisted identifiers.
- Let OCR and validation run against the selected in-game language.
- Make updater outputs explicit about which files are canonical and which are locale-specific.
- Remove the current coupling where one JSON file simultaneously acts as identifier source, display source, and OCR lookup table.

## Status On 2026-05-28

The architecture described below is no longer only aspirational. The repository already has the new generated contract in place, and the remaining work is now mostly about export cleanup, broader OCR localization, and reducing the temporary compatibility layer.

Implemented so far:

- `BaseDataUpdater` generates `data/catalog/` and `data/locale/<lang>/` outputs and can bootstrap them when those generated files are missing.
- Non-English updater runs bootstrap the English canonical catalog first so canonical keys remain English-derived.
- The inventory viewer metadata resolver already prefers generated catalog plus locale data.
- OCR/runtime consumers for echo names, equipped-character names, and sonata filter matching already use generated locale data.
- Shared compatibility globals in `scraping.data` now rebuild themselves directly from generated catalog plus locale data, without reading legacy `data/<lang>/*.json` compatibility files.
- Runtime callers have been migrated to cache-specific compatibility getters, so they no longer depend on updater-emitted legacy compatibility bundles.
- The updater no longer emits legacy compatibility files under `data/<lang>/` for items, weapons, characters, echoes, achievements, stats, defined text, or sonatas.
- Raw updater inputs and updater state now live under `data/raw/<lang>/` instead of reusing `data/<lang>/`.

Still incomplete:

- Export schemas still need a deliberate cleanup pass so canonical keys are explicit and unambiguous.
- OCR coverage is still only partially localized; more menu text and region-specific checks need to stop assuming English text.

## Current Mismatch

The main architectural split now exists and the legacy generated compatibility files are no longer part of the intended contract.

The remaining mismatch is narrower:

- In-memory compatibility getters still expose old lookup shapes for transitional runtime callers, even though those shapes are now synthesized from generated data instead of loaded from disk.
- Export payloads still mix ids, canonical names, and display-oriented fields in ways that are not yet explicit.

## Recommended Model

Split game data into three layers.

| Layer | Scope | Primary key | Purpose |
|---|---|---|---|
| Canonical catalog | locale-independent | English normalized key | Stable identifiers, joins, export metadata |
| Locale pack | per language | canonical key or stable text id | Display strings, normalized OCR aliases, localized labels |
| Raw updater input | per language | upstream format | Downloaded source files and debugging only |

### Canonical identifiers

Use English normalized strings as the public canonical keys for entity-like game data:

- echoes
- characters
- items
- weapons
- achievements
- sonatas

For stats, keep the existing short internal codes as canonical keys:

- `hp`
- `atk`
- `cr`
- `cd`
- `er`
- element codes, etc.

For UI text such as `definedText`, keep the stable upstream text IDs as the keys. Those IDs are already locale-independent.

## Target Data Layout

Current generated layout target:

```text
data/
    languages.json
    catalog/
        achievements.json
        characters.json
        echoes.json
        items.json
        sonatas.json
        stats.json
        weapons.json
    locale/
        en/
            achievements.json
            characters.json
            definedText.json
            echoes.json
            sonatas.json
            stats.json
            lookup/
                achievements.json
                characters.json
                echoes.json
                sonatas.json
                stats.json
        ja/
            ...
    raw/
        en/
            MultiText.json
            ItemInfo.json
            WeaponConf.json
        ja/
            ...
```

Current status:

- `data/catalog/` and `data/locale/` are already implemented and should be treated as the generated source of truth.
- Raw updater inputs now live under `data/raw/<lang>/` and are no longer mixed into the legacy per-language generated-output layout.

## File Contracts

### 1. Canonical catalogs

Canonical catalogs should be keyed by English normalized identifier and should contain only locale-independent fields.

Example: `data/catalog/echoes.json`

```json
{
    "vanguardjunrock": {
        "id": 310000010,
        "text_key": "MonsterInfo_310000010_Name"
    }
}
```

Example: `data/catalog/items.json`

```json
{
    "unionexp": {
        "id": 1,
        "text_key": "ItemInfo_1_Name",
        "image": "IconA/T_IconA_AccountExp_UI.png"
    }
}
```

Locale-independent metadata such as image paths, rarity, sort order, or stable config ids belongs in the catalog.

### 2. Locale packs

Locale packs should be keyed by canonical identifier and should contain the strings the user should see or the OCR stack should match.

Example: `data/locale/ja/echoes.json`

```json
{
    "vanguardjunrock": {
        "display_name": "先鋒岩塊",
        "normalized": "先鋒岩塊",
        "aliases": ["先鋒岩塊"]
    }
}
```

Example: `data/locale/en/echoes.json`

```json
{
    "vanguardjunrock": {
        "display_name": "Vanguard Junrock",
        "normalized": "vanguardjunrock",
        "aliases": ["vanguardjunrock"]
    }
}
```

This keeps the key stable across all locales while still letting each locale provide its own display string and OCR alias set.

### 3. OCR lookup files

Generate reverse lookup files for exact matching and cheap runtime loading.

Example: `data/locale/ja/lookup/echoes.json`

```json
{
    "先鋒岩塊": "vanguardjunrock"
}
```

These files are not the source of truth. They are derived indexes built from the locale pack.

## Result JSON Contract

Result JSONs should persist canonical keys, not locale-specific display text.

Recommended rules:

- Echo records store a canonical echo key.
- Character records store a canonical character key.
- Sonata/set references store a canonical sonata key.
- Stat names stay on canonical stat codes.
- Numeric game ids can still be exported when useful, but they are secondary join fields, not the primary human-facing identifier.

When touching export schemas, prefer explicit field names such as:

- `echo_key`
- `character_key`
- `sonata_key`
- `main_stat_key`
- `substat_key`

If existing result schemas already expose a normalized-name field, phase 1 can keep that field but must redefine it as a canonical key and stop localizing it.

Display strings should be resolved at read time by the UI, not embedded as the authoritative identifier in exported data.

## Updater Responsibilities

The updater should stop generating identifier files directly from the active locale. Instead it should build data in two passes.

### Pass A: build canonical catalogs from English

English remains the source for canonical keys.

For each entity type:

1. Read the English source data.
2. Derive the English normalized key.
3. Store a canonical record keyed by that English normalized key.
4. Persist the stable join field needed to reconnect every locale to that same record.

Preferred join fields:

- entities backed by `MultiText`: upstream text key such as `MonsterInfo_310000010_Name`
- items and weapons: config id plus source text key
- stats: existing internal stat code
- `definedText`: keep the existing PrefabTextItem id

### Pass B: build locale packs from each selected language

For each locale:

1. Load the canonical catalog.
2. Resolve the locale-specific string using the stored join field.
3. Generate localized display records keyed by the canonical key.
4. Generate normalized alias lists and reverse lookup indexes.

### Updater validation rules

The updater should fail loudly or emit a clear report when it sees:

- a missing locale string for a canonical entry
- duplicate normalized aliases that collide inside one locale
- a locale record that cannot be joined back to a canonical entry
- a canonical entity that has no English source string

## Runtime Consumption

Converge on one runtime resolver or repository that loads the canonical catalog plus one locale pack and exposes three distinct views:

- canonical metadata for exports and joins
- localized display strings for UI rendering
- reverse lookups and alias lists for OCR matching

That resolver can sit behind existing modules during migration, but those modules should stop treating one flat `name -> id` dict as the only game-data shape.

Current status:

- `ui.inventory_models` already resolves display names from generated catalog plus locale data.
- Some OCR and navigation helpers already use generated locale packs directly.
- `scraping.data` now acts as a compatibility shim over generated outputs when legacy files are absent.
- The remaining gap is consistency: not every consumer has been routed through the same shared abstraction yet.

### UI usage

The inventory viewer should stop prettifying identifiers as a fallback display strategy.

Instead it should:

1. read the canonical key from the result document
2. resolve the display string from the active locale pack
3. fall back to English locale data if the chosen locale is incomplete

### OCR usage

OCR should read from locale lookup files or locale-pack alias tables, not from canonical catalogs.

This is especially important for:

- echo names
- equipped character names
- sonata names
- localized stat names
- localized menu text

### `definedText`

`definedText.json` already has the right key shape: stable upstream ids. Keep that model and continue preferring text-id lookups over hardcoded English strings whenever possible.

## Migration Plan

### Phase 1: generated data contract

Status: mostly complete

- Keep `data/catalog/` as the canonical generated contract.
- Keep `data/locale/<lang>/` plus locale lookup indexes as the localized generated contract.
- Keep raw updater source caches isolated under `data/raw/<lang>/`.
- Preserve tests that confirm canonical keys stay identical across `en` and at least one non-English locale.
- Preserve updater bootstrapping so `wuwa-app` and `cli/update_data.py` can regenerate missing generated outputs from already-downloaded sources.

### Phase 2: runtime resolver

Status: in progress

- Consolidate on a single resolver or shared helper surface that merges catalog plus locale data.
- Keep the inventory viewer on generated metadata and use it as the pattern for other consumers.
- Continue switching OCR helper code and navigation helpers to generated locale data.
- Keep legacy globals or shims only as adapters over the new data model.
- Remove remaining direct reads of `data/<lang>/*.json` from runtime code and tools.

### Phase 3: export cleanup

Status: not started in a coordinated way

- Update assemblers and exporters to persist canonical keys only.
- Add explicit `*_key` fields where current names are ambiguous.
- Stop relying on localized labels in result payloads.
- Add round-trip tests proving a non-English game locale still exports English canonical identifiers.

### Phase 4: compatibility cleanup

Status: blocked on phase 2 and phase 3 completion

- Remove consumers of the old `normalized localized name -> id` files.
- Deprecate or delete the old generated lookup shape.

## OCR Roadmap

### Stage 1: localized string lookup

- Use exact lookup from generated `data/locale/<lang>/lookup/*.json`.
- Keep fuzzy matching only as a fallback after canonical lookup fails.
- Build OCR allowlists from locale-pack strings and aliases, not from canonical identifiers.

### Stage 2: eliminate English-only runtime matching

- Replace hardcoded English text checks with `definedText` ids where available.
- Where a stable text id is not available, use position-based or structure-based matching instead of English substrings.

### Stage 3: validate region specs for non-English UI

- Confirm text regions remain wide enough for longer localized strings.
- Confirm signature and preprocessing behavior remains stable for non-Latin scripts and different stroke densities.
- Scope any locale-sensitive OCR caches by locale when the preprocessed image or allowlist depends on language.

## Tests And Validation

Minimum coverage for this migration:

- updater tests that compare canonical catalogs across multiple locales
- updater tests that detect alias collisions within one locale
- UI tests that show localized labels instead of prettified slugs
- OCR tests that resolve a localized token back to the same canonical key exported in English mode
- round-trip tests where a non-English game locale still produces result JSONs with English canonical identifiers

Coverage already in place:

- updater tests for generated-output bootstrapping and English-first canonical catalog creation
- inventory-model tests for generated localized metadata resolution
- OCR and navigation tests for generated locale consumers and localized sonata matching
- shared-loader tests that verify generated fallback behavior when compatibility files are missing

Notable remaining gaps:

- alias-collision validation coverage inside non-English locale generation
- end-to-end export tests that assert explicit canonical `*_key` fields
- broader OCR/menu-text validation outside the currently migrated name-matching flows

## Recommended Decisions

These choices keep the plan coherent and reduce future churn.

- Use `data/catalog/` plus `data/locale/` as the generated contract.
- Use `data/raw/<lang>/` for downloaded updater source caches and updater state.
- Keep English display strings in `data/locale/en/`, not in the catalog.
- Materialize lookup JSONs instead of building them ad hoc at runtime so tests can assert the exact output.
- Prefer explicit `*_key` export fields when a schema is already being touched.
- Treat the current `data/<lang>/*.json` name-to-id files as temporary compatibility artifacts, not as the long-term contract.
