# Localization Data Plan

## Goals

- Keep locale-independent identifiers in exports, caches, and internal result models as English normalized keys.
- Show real localized strings in the UI without changing persisted identifiers.
- Let OCR and validation run against the selected in-game language.
- Make updater outputs explicit about which files are canonical and which are locale-specific.
- Remove the current coupling where one JSON file simultaneously acts as identifier source, display source, and OCR lookup table.

## Current Mismatch

Today the updater still generates several files as localized lookup dicts keyed by normalized display text:

- `characters.json`
- `echoes.json`
- `achievements.json`
- `sonataName.json`
- `echoStats.json`

That happens to work for English because the English normalized display string is also the identifier we want to keep in result JSONs. It breaks down for non-English support because the key space would then change with the chosen locale.

The same mismatch now shows up in multiple places:

- The updater emits localized normalized-name keys instead of stable canonical keys.
- The inventory viewer currently prettifies identifiers because it does not have a proper localized-display layer.
- OCR helpers build allowlists from those localized-key files, so runtime matching is coupled to whichever locale-specific JSON shape happens to exist.

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

Preferred end state:

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

If moving raw files into `data/raw/` creates too much churn, phase 1 can keep the current raw-file locations and introduce only `data/catalog/` and `data/locale/`.

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

Introduce one runtime resolver or repository that loads the canonical catalog plus one locale pack and exposes three distinct views:

- canonical metadata for exports and joins
- localized display strings for UI rendering
- reverse lookups and alias lists for OCR matching

That resolver can sit behind existing modules during migration, but those modules should stop treating one flat `name -> id` dict as the only game-data shape.

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

### Phase 1: updater-first

- Add `data/catalog/` outputs.
- Add `data/locale/<lang>/` outputs.
- Keep existing `data/<lang>/*.json` lookup files as compatibility artifacts for now.
- Add tests that confirm canonical keys stay identical across `en` and at least one non-English locale.

### Phase 2: runtime resolver

- Add a single resolver that merges catalog plus locale pack.
- Switch UI consumers first.
- Switch OCR helper code next.
- Keep legacy globals or shims only as adapters over the new data model.

### Phase 3: export cleanup

- Update assemblers and exporters to persist canonical keys only.
- Add explicit `*_key` fields where current names are ambiguous.
- Stop relying on localized labels in result payloads.

### Phase 4: compatibility cleanup

- Remove consumers of the old `normalized localized name -> id` files.
- Deprecate or delete the old generated lookup shape.
- Optionally move raw updater inputs into a dedicated `data/raw/<lang>/` tree.

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

## Recommended Decisions

These choices keep the plan coherent and reduce future churn.

- Use `data/catalog/` plus `data/locale/` as the generated contract.
- Keep English display strings in `data/locale/en/`, not in the catalog.
- Materialize lookup JSONs instead of building them ad hoc at runtime so tests can assert the exact output.
- Prefer explicit `*_key` export fields when a schema is already being touched.
- Treat the current `data/<lang>/*.json` name-to-id files as temporary compatibility outputs, not as the long-term contract.
