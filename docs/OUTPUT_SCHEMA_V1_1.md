# Output Schema v1.1

## Scope

This document describes the current output schema used by the main branch.

Version label:

- `v1.1` = the current schema family built around a canonical
  `scan_result.json` session document plus legacy-named standalone convenience
  exports.

## Session Layout

- Export root: the configured export directory or CLI `--output-dir`.
- Session directory: `YYYY-MM-DD_HH-MM-SS`.
- Canonical CLI artifact: `scan_result.json`.
- Optional raw capture directory: `raw/` when raw saving is enabled.
- Standalone exports are only written when their payload is non-empty.

Current standalone filenames:

- `inventory_wuwainventorykamera.json`
- `echoes_wuwainventorykamera.json`
- `weapons_wuwainventorykamera.json`
- `devItems_wuwainventorykamera.json`
- `resources_wuwainventorykamera.json`
- `characters_wuwainventorykamera.json`
- `achievements_wuwainventorykamera.json`

Not currently emitted as standalone files:

- `shell`

The `shell` section exists inside `scan_result.json` and is also folded into the
aggregated `inventory` export.

## Notation

```text
TimestampString = string formatted as YYYY-MM-DD_HH-MM-SS
int             = JSON number without a fractional part
normalized-key  = lowercase canonical lookup key with spaces removed
stat-value      = int | float | string
ScraperError    = { error: string }
```

## Canonical Session File: scan_result.json

```text
ScanResult = {
  date: TimestampString,
  cancelled?: true,
  echoes?: EchoExport | ScraperError,
  weapons?: WeaponExport | ScraperError,
  inventory?: InventoryExport,
  devItems?: ItemRow[] | ScraperError,
  resources?: ItemRow[] | ScraperError,
  characters?: CharacterExport | ScraperError,
  achievements?: AchievementExport | ScraperError,
  shell?: ShellExport | ScraperError
}

AchievementExport = int[]

InventoryExport = {
  [itemId: string]: int
}

ShellExport = {
  "2": int
}
```

Semantics:

- `date` is always present.
- A scraper key is present only when that scraper was requested.
- `cancelled` is present only when the user stopped the scan early.
- If a requested scraper raises an exception after the session starts, that
  scraper section is serialized as `{ "error": "..." }`.
- `inventory` is a serializer-built aggregate of any successful `devItems`,
  `resources`, and `shell` payloads present in the in-memory result.
- If the session fails before scan output exists, the CLI exits without writing
  `scan_result.json`.
- The `shell` object keeps the v1 shell item id convention and therefore uses
  key `"2"`.

Example:

```json
{
  "date": "2026-05-29_11-00-00",
  "cancelled": true,
  "inventory": {
    "2": 123456
  },
  "echoes": [
    {
      "310000010": {
        "echo_key": "bellbornegeochelone",
        "level": 25,
        "tuneLv": 5,
        "sonata": "moonlitclouds",
        "sonata_key": "moonlitclouds",
        "rarity": 5,
        "stats": {
          "main": {
            "Healing Bonus": "26.4%"
          },
          "sub": {
            "Crit Rate": "8.4%",
            "ATK%": "9.4%"
          }
        },
        "_equipped": "shorekeeper",
        "_scanIndex": 7,
        "_monsterId": 310000010,
        "_cost": 1
      }
    }
  ],
  "achievements": [9001],
  "shell": {
    "2": 123456
  }
}
```

## Standalone Convenience Exports

Each standalone file uses the same payload type as the corresponding section in
`scan_result.json`.

| File | Top-level type |
| --- | --- |
| `inventory_wuwainventorykamera.json` | `InventoryExport` |
| `echoes_wuwainventorykamera.json` | `EchoExport` |
| `weapons_wuwainventorykamera.json` | `WeaponExport` |
| `devItems_wuwainventorykamera.json` | `ItemRow[]` |
| `resources_wuwainventorykamera.json` | `ItemRow[]` |
| `characters_wuwainventorykamera.json` | `CharacterExport` |
| `achievements_wuwainventorykamera.json` | `AchievementExport` |

## Inventory Export

```text
InventoryExport = {
  [itemId: string]: int
}
```

Semantics:

- Restores the v1 inventory container shape for compatibility.
- Contains item quantities keyed by serialized item id.
- Includes shell currency under item id `"2"` when shell data is available.
- May represent a partial inventory when only a subset of item-like scrapers was
  requested.

Example:

```json
{
  "2": 123456,
  "10800": 3
}
```

## Item Rows

```text
ItemRow = {
  id: int,
  item_key: normalized-key,
  count: int
}
```

Semantics:

- Used by both `devItems` and `resources`.
- These are additive convenience exports alongside the restored
  `inventory_wuwainventorykamera.json` map.
- `item_key` is the canonical normalized lookup key and is always emitted for
  recognized item rows.

Example:

```json
[
  {
    "id": 10800,
    "item_key": "resonancepotion",
    "count": 3
  }
]
```

## Weapon Export

```text
WeaponExport = WeaponEntry[]

WeaponEntry = {
  [weaponId: string]: WeaponRecord
}

WeaponRecord = {
  level: int,
  ascension: int,
  rank: int,
  weapon_key?: normalized-key,
  maxLevel?: int,
  _equipped?: normalized-key
}
```

Semantics:

- Restores the v1 array-of-singletons container shape.
- `ascension` restores the v1 weapon-ascension field.
- `weapon_key` and `maxLevel` are additive convenience fields.
- `_equipped` stores the normalized character key when equipped text was
  parsed successfully.

Example:

```json
[
  {
    "21010074": {
      "level": 90,
      "ascension": 6,
      "rank": 1,
      "weapon_key": "emeraldofgenesis",
      "maxLevel": 90,
      "_equipped": "shorekeeper"
    }
  }
]
```

## Character Export

```text
CharacterExport = {
  [characterRef: string]: CharacterRecord
}

CharacterRecord = {
  _name: normalized-key,
  character_key: normalized-key,
  level: int,
  ascension: int,
  weapon: {
    id: int | string,
    weapon_key: normalized-key,
    level: int,
    ascension: int,
    rank: int
  },
  echoes: {},
  skills: {
    normal: int,
    resonance: int,
    forte: int,
    liberation: int,
    intro: int,
    stats0: int,
    stats1: int,
    inherent: int,
    stats3: int,
    stats4: int
  },
  chain: int
}
```

Semantics:

- The outer key is usually the numeric character id serialized as a JSON member
  name.
- If id resolution fails, the outer key falls back to the normalized character
  key.
- `_name` and `character_key` are currently the same normalized value.
- Embedded `weapon.id` is usually numeric, but may fall back to the normalized
  weapon key if weapon id resolution fails.
- `echoes` remains an empty object in the current writer.

Example:

```json
{
  "1105": {
    "_name": "shorekeeper",
    "character_key": "shorekeeper",
    "level": 90,
    "ascension": 6,
    "weapon": {
      "id": 21010074,
      "weapon_key": "emeraldofgenesis",
      "level": 90,
      "ascension": 6,
      "rank": 1
    },
    "echoes": {},
    "skills": {
      "normal": 10,
      "resonance": 10,
      "forte": 10,
      "liberation": 10,
      "intro": 10,
      "stats0": 2,
      "stats1": 2,
      "inherent": 2,
      "stats3": 2,
      "stats4": 2
    },
    "chain": 2
  }
}
```

## Echo Export

```text
EchoExport = EchoEntry[]

EchoEntry = {
  [echoId: string]: EchoRecord
}

EchoRecord = {
  echo_key: normalized-key,
  level: int,
  tuneLv: int,
  sonata: normalized-key,
  sonata_key: normalized-key,
  rarity: int,
  stats: {
    main: OrderedStatMap,
    sub: OrderedStatMap
  },
  _equipped?: normalized-key,
  _scanIndex?: int,
  _monsterId?: int,
  _cost?: int
}

OrderedStatMap = {
  [statName: string]: stat-value
}
```

Semantics:

- v1.1 echoes still use the legacy array-of-singletons container shape.
- `echo_key` is the canonical normalized echo lookup key.
- `sonata` and `sonata_key` are currently the same normalized sonata key.
- `stats.main` and `stats.sub` use JSON object member order to preserve the
  in-game stat order.
- `_equipped`, `_scanIndex`, `_monsterId`, and `_cost` are service-added
  metadata fields and may be absent.

Example:

```json
[
  {
    "310000010": {
      "echo_key": "bellbornegeochelone",
      "level": 25,
      "tuneLv": 5,
      "sonata": "moonlitclouds",
      "sonata_key": "moonlitclouds",
      "rarity": 5,
      "stats": {
        "main": {
          "Healing Bonus": "26.4%",
          "ATK": 150
        },
        "sub": {
          "Crit Rate": "8.4%",
          "ATK%": "9.4%",
          "HP%": "7.1%"
        }
      },
      "_equipped": "shorekeeper",
      "_scanIndex": 7,
      "_monsterId": 310000010,
      "_cost": 1
    }
  }
]
```

## Achievements and Shell

```text
AchievementExport = int[]

ShellExport = {
  "2": int
}
```

Semantics:

- `achievements` contains completed achievement ids as JSON integers.
- `shell` records the shell currency count under item id `"2"`.
- `achievements` is emitted both inside `scan_result.json` and as
  `achievements_wuwainventorykamera.json`.
- `shell` is emitted only inside `scan_result.json`, and its value is also
  folded into the aggregated `inventory` export.

## Compatibility Notes

- `scan_result.json` is the canonical current session artifact.
- Standalone exports remain available for UI save flow and reprocessing.
- Compared with v1, v1.1 keeps the legacy inventory and weapon container
  shapes for compatibility, restores standalone achievements, and keeps
  `devItems` and `resources` as additive convenience exports.
- Compared with v1, v1.1 adds canonical key fields such as `item_key`,
  `weapon_key`, `character_key`, `echo_key`, and `sonata_key`.
- Compared with v1, v1.1 also adds optional equipped and scan metadata to echo
  and weapon outputs.