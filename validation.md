For validation purposes, the valid data space is quite constrained.

The OCR trace looks like this:

```json
{
  "scan_index": 42,
  "decision": "accepted",
  "card": {
    "raw_lines": ["pufferfish", "level", "25"],
    "name_raw": "pufferfish",
    "name_normalized": "pufferfish",
    "rarity": 5,
    "level_text": "25",
    "level": 25
  },
  "stats": {
    "raw_names_ocr": ["atk", "hp", "crit", "rate", "critdmg"],
    "matched_names": ["atk", "hp", "critrate", "critdmg"],
    "raw_values_ocr": ["18.0%", "2280", "7.5%", "15.0%"]
  },
  "sonata": {
    "raw_ocr": "frostspawned...",
    "matched": "freezing frost"
  }
}
```

The first row in matched_names and raw_values_ocr is the rolled main stat of the echo. Depending on rarity, cost and level, only a small number of names are possible here, and for that name, only a single value.

The second row in matched_names and raw_values_ocr is the fixed main stat. Depending on cost, only a single name is possible. The value depends on rarity and level.

All further rows are substats and have a different semantic.
There are 0 to 5 substat rolls. Only level-25 echos can have 5 substats, only >=level-20 echos can have 4 substats and so on.

No substat can be rolled twice (However, within the substat rows, "atk", "hp", "def" can occur twice, once with a flat value and once with a percentage). The value depends on the substat type and can be one of eight possible values (one out of four for flat atk and flat def).

The following YAML describes the valid value space:

```yaml
mainstat:
  - rarity: 5
    slotCost:
    - cost: 1
        # 1-cost rolls only have atk%, hp%, and def% as possible mains
        #   - given is stat value at level 0 and level 25 (rarity 5 only)
        #   - intermediate levels are simply equally spaced between level 0 and level 25, something like stat_at_0+(level/25)*(stat_at_max-stat_at_min)
        rolls:
        - "atk%": [ 3.6, 18.0 ]
        - "hp%": [ 4.5, 22.8 ]
        - "def%": [ 3.6, 18.0 ]
        fixed:
        - "hp": [ 456, 2280 ]
    - cost: 3
        # given is stat value at level 0 and level 25 (rarity 5 only)
        rolls:
        - "atk%": [ 6.0, 30.0 ]
        - "hp%": [ 6.0, 30.0 ]
        - "def%": [ 7.6, 38.0 ]
        - "er%": [ 6.4, 32.0 ]
        - "fusion%": [ 6.0, 30 ]
        - "havoc%": [ 6.0, 30 ]
        - "spectro%": [ 6.0, 30 ]
        - "electro%": [ 6.0, 30 ]
        - "aero%": [ 6.0, 30 ]
        - "glacio%": [ 6.0, 30 ]
        fixed:
        - "atk": [ 20, 100 ]
    - cost: 4
        # given is stat value at level 0 and level 25 (rarity 5 only)
        rolls:
        - "atk%": [ 6.6, 33.0 ]
        - "hp%": [ 6.6, 33.0 ]
        - "def%": [ 8.3, 41.8 ]
        - "cr%": [ 4.4, 22.0 ]
        - "cd%": [ 8.8, 44.0 ]
        - "healing%": [ 5.2, 26.4 ]
        fixed:
        - "atk": [ 30, 150 ]
substat:
  - rarity: 5
    # possible roll values (either 4 tiers or 8 tiers)
    # which tier a substat has when rolled is random
    rolls:
        "cr%": [6.3, 6.9, 7.5, 8.1, 8.7, 9.3, 9.9, 10.5]
        "cd%": [12.6, 13.8, 15.0, 16.2, 17.4, 18.6, 19.8, 21.0]
        "atk%": [6.4, 7.1, 7.9, 8.6, 9.4, 10.1, 10.9, 11.6]
        "er%": [6.8, 7.6, 8.4, 9.2, 10.0, 10.8, 11.6, 12.4]
        "skillDmg%": [6.4, 7.1, 7.9, 8.6, 9.4, 10.1, 10.9, 11.6]
        "liberationDmg%": [6.4, 7.1, 7.9, 8.6, 9.4, 10.1, 10.9, 11.6]
        "basicAttack%": [6.4, 7.1, 7.9, 8.6, 9.4, 10.1, 10.9, 11.6]
        "heavyAttack%": [6.4, 7.1, 7.9, 8.6, 9.4, 10.1, 10.9, 11.6]
        "def%": [8.1, 9.0, 10.0, 10.9, 11.8, 12.8, 13.8, 14.7]
        "hp%": [6.4, 7.1, 7.9, 8.6, 9.4, 10.1, 10.9, 11.6]
        "hp": [320, 360, 390, 430, 470, 510, 540, 580]
        "atk": [30, 40, 50, 60]
        "def": [40, 50, 60, 70]
```