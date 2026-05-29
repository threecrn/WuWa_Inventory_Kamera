# Output Schema v1

## Scope

This document describes the original standalone export format emitted by the
original author's master branch.

Version label:

- `v1` = the original timestamped JSON exports written by the legacy UI scan
  flow.

This schema is reconstructed from the original writer and scraper code, not
from the README examples alone.

## Session Layout

- Export root: the configured export directory.
- Session directory: `YYYY-MM-DD_HH-MM-SS`.
- Files are only written when their payload is non-empty.
- There is no canonical `scan_result.json` session document in v1.

Actual v1 filenames:

- `inventory_wuwainventorykamera.json`
- `characters_wuwainventorykamera.json`
- `weapons_wuwainventorykamera.json`
- `echoes_wuwainventorykamera.json`
- `achievements_wuwainventorykamera.json`

Important documentation note:

- The original README showed unsuffixed sample names such as
  `inventory.json`, `weapons.json`, and `echoes.json` and included `_comment`
  helper members.
- The actual writer did not emit those unsuffixed names.
- The actual writer did not emit `_comment` members.

## Notation

```text
TimestampString = string formatted as YYYY-MM-DD_HH-MM-SS
int             = JSON number without a fractional part
id-key          = JSON object member name whose semantic value is an integer id
normalized-key  = lowercase lookup key with spaces removed
stat-value      = int | float | string
```

## File Set

| File | Top-level type | Notes |
| --- | --- | --- |
| `inventory_wuwainventorykamera.json` | object | Item id to quantity map |
| `characters_wuwainventorykamera.json` | object | Character map keyed by character id or OCR fallback name |
| `weapons_wuwainventorykamera.json` | array | Array of singleton weapon objects |
| `echoes_wuwainventorykamera.json` | array | Array of singleton echo objects |
| `achievements_wuwainventorykamera.json` | array | Completed achievement ids |

## inventory_wuwainventorykamera.json

```text
InventoryExport = {
  [itemId: id-key]: int
}
```

Semantics:

- Keys are JSON object member names, so numeric ids are serialized as strings.
- Values are owned quantities.
- Shell currency uses item id `2` when present.
- Unrecognized items are not serialized into this file; they are routed to the
  failure-review flow instead.

Example:

```json
{
  "2": 123456,
  "10800": 3
}
```

## characters_wuwainventorykamera.json

```text
CharacterExport = {
  [characterRef: string]: CharacterRecord
}

CharacterRecord = {
  level: int,
  ascension: int,
  weapon: {
    id: int | string,
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

- The outer key is usually the character id, serialized as a JSON object key.
- If OCR could not resolve the character id, the outer key falls back to the
  normalized OCR name.
- Rover had a hard-coded id override of `1502`.
- `weapon.id` is usually a numeric weapon id, but may fall back to the
  normalized OCR name when weapon id lookup fails.
- `echoes` is always an empty object in v1 because character echo scraping was
  explicitly skipped.
- v1 does not include `_name` or `character_key` members.

Example:

```json
{
  "1205": {
    "level": 90,
    "ascension": 6,
    "weapon": {
      "id": 21020064,
      "level": 80,
      "ascension": 5,
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
    "chain": 0
  }
}
```

## weapons_wuwainventorykamera.json

```text
WeaponExport = WeaponEntry[]

WeaponEntry = {
  [weaponId: id-key]: {
    level: int,
    ascension: int,
    rank: int
  }
}
```

Semantics:

- This is an array of singleton objects, not one large map.
- Each singleton object is keyed by the weapon id serialized as a JSON member
  name.
- Only recognized weapons are emitted.
- Weapons below the configured rarity or minimum level are filtered out.
- The original README suggested unknown weapon names might be emitted as free
  text, but the writer rejected unrecognized weapons instead.

Example:

```json
[
  {
    "21030016": {
      "level": 50,
      "ascension": 2,
      "rank": 1
    }
  }
]
```

## echoes_wuwainventorykamera.json

```text
EchoExport = EchoEntry[]

EchoEntry = {
  [echoId: id-key]: EchoRecord
}

EchoRecord = {
  level: int,
  tuneLv: int,
  sonata: normalized-key,
  rarity: int,
  stats: {
    main: StatMap,
    sub: StatMap
  }
}

StatMap = {
  [statName: string]: stat-value
}
```

Semantics:

- This is an array of singleton objects, not one large map.
- Each singleton object is keyed by the echo id serialized as a JSON member
  name.
- Only recognized echoes are emitted.
- `sonata` is a normalized lowercase key.
- Percentage stats use a `%` suffix in the stat name, for example `"cr%"`.
- `stats.main` and `stats.sub` are ordinary JSON objects. Their member order
  follows OCR row order because no explicit order metadata exists in v1.
- v1 does not include `echo_key`, `sonata_key`, `_equipped`, `_scanIndex`,
  `_monsterId`, or `_cost`.

Example:

```json
[
  {
    "340000070": {
      "level": 25,
      "tuneLv": 5,
      "sonata": "havoceclipse",
      "rarity": 5,
      "stats": {
        "main": {
          "cr%": 22.0,
          "atk": 150
        },
        "sub": {
          "atk": 40,
          "def": 50,
          "hp": 470,
          "basicAttack%": 8.6
        }
      }
    }
  }
]
```

## achievements_wuwainventorykamera.json

```text
AchievementExport = int[]
```

Semantics:

- The array contains completed achievement ids.
- The ids are serialized as JSON numbers.
- v1 had no dedicated README schema for this file even though the writer
  emitted it.

Example:

```json
[9001, 9002, 9010]
```

## Compatibility Notes

- v1 is a standalone-file schema only.
- The UI wrote only non-empty files for a session.
- Item quantities lived in `inventory_wuwainventorykamera.json`; there were no
  dedicated `devItems` or `resources` standalone files.
- Character records had no additive canonical key fields such as `_name`,
  `character_key`, `weapon_key`, `echo_key`, or `sonata_key`.