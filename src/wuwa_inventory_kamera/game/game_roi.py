"""
wuwa_inventory_kamera.game.game_roi
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

UI coordinate tables for the supported game resolutions.

This is a verbatim migration of ``game/gameROI.py`` from the legacy package
root into the new ``wuwa_inventory_kamera`` package so that the game layer
has no dependency on the project root being on ``sys.path``.

``COORDINATES`` is a nested dict keyed by ``(aspect_w, aspect_h)`` then
``(width, height)``.  All leaf values are :class:`Coordinates` objects.
Only 1920x1080 and 1920x1200 are supported.
"""
from __future__ import annotations

from wuwa_inventory_kamera.game.utils.geometry import reduce_ratio


class Coordinates:
    def __init__(self, x: int | float = 0, y: int | float = 0, w: int | float = 0, h: int | float = 0):
        self.x = x
        self.y = y
        self.w = w
        self.h = h

    def __repr__(self):
        return f"Coordinates(x={self.x}, y={self.y}, w={self.w}, h={self.h})"

    def __reduce__(self):
        return (self.__class__, (self.x, self.y, self.w, self.h))


# ---------------------------------------------------------------------------
# COORDINATES — master UI-element lookup table
# ---------------------------------------------------------------------------
#
# Schema
# ~~~~~~
# The dict is keyed in three levels:
#
#   COORDINATES[(aspect_w, aspect_h)][(width, height)][region_key]
#
# 1. *Aspect ratio* — reduced integer pair, e.g. ``(16, 9)``.
# 2. *Resolution* — exact pixel dimensions of the game viewport.
# 3. *Region key* — a string naming the UI area (may nest further).
#
# Every leaf value is a ``Coordinates(x, y, w, h)`` instance.
# The meaning of the four fields depends on context:
#
# * **ROI rectangle** (has all four fields):
#   ``x, y`` = top-left corner in game-viewport pixels.
#   ``w, h`` = width and height of the region to capture / crop.
#
# * **Click target** (only ``x, y`` set; ``w, h`` default to 0):
#   ``x, y`` = the point to click, relative to the game viewport origin.
#
# * **Scroll / offset value** (only one axis set):
#   ``y`` (or ``x``) = a scalar passed to the scroll or spacing logic.
#   Negative ``y`` = scroll upward; positive = scroll downward.
#   For page offsets, ``x`` and ``y`` are the horizontal and vertical
#   gap between adjacent grid cells.
#
# Region keys
# ~~~~~~~~~~~
# terminal          ROI   — "Terminal" label in the main menu (used to
#                           detect whether the game is on the main screen).
#
# shell             ROI   — Sonata/shell icon area in the top bar.
#
# offsets
#   page            off   — (x, y) pixel gap between adjacent grid cells
#                           in any inventory grid.
#
# scroll
#   page            scr   — Mouse-wheel delta to scroll one full page of
#                           the inventory grid (negative = up).
#   characters      scr   — Mouse-wheel delta to scroll the character
#                           sidebar list.
#   sonata          scr   — Mouse-wheel delta to scroll the echo detail
#                           panel down to reveal the sonata section.
#
# scrapers
#   weapons         click — Center of the Weapons tab button in the left
#   echoes          click   sidebar of the inventory screen.
#   devItems        click
#   resources       click
#
# items
#   start           ROI   — First grid cell (row 0, col 0) position + size.
#                           Used together with ``offsets.page`` to compute
#                           every cell's position.
#   info            ROI   — Item info panel (name + basic stats).
#   description     ROI   — Extended item description panel.
#
# weapons
#   page            ROI   — Item-count / page-count text region (e.g. "48/2").
#   start           ROI   — First grid cell, same role as ``items.start``.
#   name            ROI   — Weapon name text in the detail panel.
#   value           ROI   — Weapon base-ATK value text.
#   level           ROI   — Weapon level text (e.g. "Lv.90/90").
#   rank            ROI   — Weapon rank (refinement) text.
#
# echoes
#   page            ROI   — Item-count text region.
#   start           ROI   — First grid cell.
#   echoCard        ROI   — Echo card header (name + cost + element icon).#   echoName        ROI   — Echo name text line in the card header (turquoise text,
#                           colour-filtered before OCR).#   sonata          ROI   — Sonata effect region (captured after scrolling).
#   sonataIcon      ROI   — Sonata icon on the Echo portrait in the sonata region.
#   mouseMovement   click — Position to hover the cursor before scrolling
#                           the echo detail panel (ensures scroll targets
#                           the correct pane).
#   fullStatsName   ROI   — Left column of the stat list (stat labels).
#   fullStatsValue  ROI   — Right column of the stat list (stat numbers).
#   sort
#     button        click — Sort-order dropdown trigger button.
#     items[]       click — List of click targets for each dropdown option
#                           (index 0 = topmost = "Sort by Level").
#
# achievements
#   status          ROI   — Achievement completion status text.
#   searchBar       click — Search input field.
#   searchButton    click — Search submit button.
#   achievementsButton click — Button to open achievements panel.
#   achievementsTab click — Tab selector inside the achievements screen.
#
# characters
#   offsets
#     leftSide      off   — Vertical spacing between entries in the left
#                           character sidebar.
#     rightSide     off   — Vertical spacing between entries on the right
#                           info panel.
#     skillPosition off   — Vertical spacing between skill slots.
#   leftSide        click — First character entry in the left sidebar.
#   rightSide       click — First entry on the right-side info panel.
#   resonatorName   ROI   — Character name text.
#   resonatorLevel  ROI   — Character level text.
#   weaponName      ROI   — Equipped weapon name.
#   weaponLevel     ROI   — Equipped weapon level.
#   weaponRank      ROI   — Equipped weapon rank/refinement.
#   skillClick      click — Skill tree open button.
#   skillLevel      ROI   — Skill level text.
#   skillButton     ROI   — "Skills" tab button.
#   chainClick      click — Resonance chain open button.
#   chainButton     ROI   — "Chain" tab button.
#   skillPositions[]  click — Centers of the 5 skill nodes on the skill tree.
#   chainPositions[]  click — Centers of the 6 chain nodes on the chain screen.
# ---------------------------------------------------------------------------

COORDINATES = {
    reduce_ratio(16, 9): {
        (1920, 1080): {
            "terminal": Coordinates(140, 40, 150, 40),
            "shell": Coordinates(1255, 38, 165, 50),
            "offsets": {
                "page": Coordinates(16, 24)
            },
            "scroll": {
                "page": Coordinates(y=-32.25),
                "characters": Coordinates(y=-56),
                "sonata": Coordinates(y=70)
            },
            "scrapers": {
                "weapons": Coordinates(81.5, 191.5),
                "echoes": Coordinates(81.5, 326.5), 
                "devItems": Coordinates(81.5, 596.5),
                "resources": Coordinates(81.5, 731.5),
            },
            "items": {
                "start": Coordinates(205, 122, 151, 181),
                "rarityColorPick": Coordinates(1313, 423), 
                "info": Coordinates(1296, 114, 558, 278),
                "description": Coordinates(1296, 114, 558, 820)
            },
            "weapons": {
                "page": Coordinates(102, 50, 400, 40),
                "start": Coordinates(176, 135, 151, 181),
                "rarityColorPick": Coordinates(1313, 423), 
                "name": Coordinates(1328, 116, 525, 55),
                "value": Coordinates(1655, 320, 190, 40),
                "level": Coordinates(1660, 235, 180, 45),
                "equipped": Coordinates(1364, 933, 480, 39),
                #"rank": Coordinates(1300, 530, 115, 50),
                # Sort dropdown — same button as echoes; 3 options at 72 px spacing.
                # Options top→bottom: Sort by Rarity / Sort by Level / Sort by Amount.
                "sort": {
                    "button": Coordinates(431, 963),
                    "items": [
                        Coordinates(431, 775),   # 0 – Sort by Rarity
                        Coordinates(431, 847),   # 1 – Sort by Level
                        Coordinates(431, 919),   # 2 – Sort by Amount
                    ],
                },
            },
            "echoes": {
                "page": Coordinates(200, 50, 130, 40),
                "start": Coordinates(205, 122, 151, 181),
                "echoCard": Coordinates(1296, 114, 558, 170),
                "echoName": Coordinates(1316, 125, 544, 40), # NEW-UI: Echo name
                "level": Coordinates(1330, 198, 58, 30), # NEW-UI: Echo level has moved into the echo card header.
                # NEW-UI: Rarity is indicated by the colored area below the echo card portrait:
                #  rarity 5: (1.00,0.98,0.69) = "gold"
                #  rarity 4: (0.91,0.63,1.00) = "purple"
                #  rarity 3: (0.60,0.60,1.00) = "blue"
                #  rarity 2: (0.60,1.00,0.60) = "green"
                "rarityColorPick": Coordinates(1313, 423), 
                #"sonataIcon": Coordinates(1441, 317, 23, 24), # Sonata icon within the sonata region.
                "sonataIcon": {
                    "radius": 14.5,
                    "level_XX": { # if level is 10 or above, the sonata icon shifts slightly to the left to make room for the double digit level badge.
                        "circle": Coordinates(14.5, 14.5),     # Circle parameters for the sonata icon (for matching).
                        "icon": Coordinates(1396, 198, 29, 29), # NEW-UI: Sonata icon has moved to the echo card header.
                    },
                    "level_X":  { # if level is below 10, the sonata icon is slightly further to the right
                        "circle": Coordinates(14.5, 14.5),     # Circle parameters for the sonata icon (for matching).
                        "icon": Coordinates(1379, 198, 29, 29), # NEW-UI: Sonata icon has moved to the echo card header.
                    },
                },
                "sonata": Coordinates(1298, 397, 554, 467),
                "mouseMovement": Coordinates(1576.5, 665.5),
                "fullStatsName": Coordinates(1367, 455, 378, 398),
                "fullStatsValue": Coordinates(1742, 455, 115, 398),
                # Sort dropdown (opens upward from the button).
                # Measured from screenshots/screenshot_inventory_echoes_sort_drop_down.png
                # at 1920x1080.  button = trigger that opens the dropdown;
                # items[N] = center of the N-th option (0 = topmost/farthest from button).
                # Options top→bottom: Sort by Level / Rarity / Time Added / Tuning Status /
                # Show Discarded First.  Spacing = 72 px; selected item (Time Added) at y=775.
                "sort": {
                    "button": Coordinates(431, 963),
                    "items": [
                        Coordinates(431, 631),   # 0 – Sort by Level
                        Coordinates(431, 703),   # 1 – Sort by Rarity
                        Coordinates(431, 775),   # 2 – Sort by Time Added  (selected in ref screenshot)
                        Coordinates(431, 847),   # 3 – Sort by Tuning Status
                        Coordinates(431, 919),   # 4 – Show Discarded First
                    ],
                },
                "filter":{
                    "button": Coordinates(227, 978), # position of the "Filter by Sonata" menu button
                    "sonata": {
                        "dropdown": Coordinates(1322, 468, 347, 45), # position of the "Filter by Sonata" dropdown in the echo filter panel
                        "item_positions": [ # positions of the 6 options in the dropdown, in order.  Option 0 = "All Sonatas".
                             Coordinates(1540, 560), # position 0
                             Coordinates(1540, 640), # position 1
                        ],
                        "item_names": [ # 
                             Coordinates(1322, 544, 347, 40), # position 0
                             Coordinates(1322, 620, 347, 40), # position 1
                             Coordinates(1322, 696, 347, 40), # position 2
                             Coordinates(1322, 772, 347, 40), # position 3
                             Coordinates(1322, 848, 347, 40), # position 4
                             Coordinates(1322, 924, 347, 40), # position 5
                        ],
                        "item_amounts": [
                             Coordinates(1670, 544, 70, 40), # position 0
                             Coordinates(1670, 620, 70, 40), # position 1
                             Coordinates(1670, 696, 70, 40), # position 2
                             Coordinates(1670, 772, 70, 40), # position 3
                             Coordinates(1670, 848, 70, 40), # position 4
                             Coordinates(1670, 924, 70, 40), # position 5
                        ],
                        "bottom_offset_item_names": Coordinates(y=38), # if scrolling to the bottom, how are item name positions affected?
                        "scroll": Coordinates(y=2.91), # scroll delta to move the filter dropdown by one option (positive = down)
                    }

                }
            },
            "achievements": {
                "status": Coordinates(1579, 230, 256, 65),
                "searchBar": Coordinates(388, 149),
                "searchButton": Coordinates(629, 149),
                "achievementsButton": Coordinates(1674, 790),
                "achievementsTab": Coordinates(835, 570),
            },
            "characters": {
                "offsets": {
                    "leftSide": Coordinates(y=136),
                    "rightSide": Coordinates(y=106),
                    "skillPosition": Coordinates(y=255)
                },
                "leftSide": Coordinates(82, 191),
                "rightSide": Coordinates(1814, 203.50),
                "resonatorName": Coordinates(250, 110, 280, 50),
                "resonatorLevel": Coordinates(180, 200, 135, 80),
                "weaponName": Coordinates(257, 126, 273, 34),
                "weaponLevel": Coordinates(255, 160, 110, 35),
                "weaponRank": Coordinates(175, 355, 95, 35),
                "skillClick": Coordinates(460.5, 903),
                "skillLevel": Coordinates(390, 100, 70, 40),
                "skillButton": Coordinates(200, 980, 120, 35),
                "chainClick": Coordinates(1265, 135),
                "chainButton": Coordinates(342, 964, 110, 32),
                "skillPositions": [
                    Coordinates(755, 905),
                    Coordinates(985, 765),
                    Coordinates(1260, 705),
                    Coordinates(1535, 765),
                    Coordinates(1760, 905)
                ],
                "chainPositions": [
                    Coordinates(1395, 140),
                    Coordinates(1565, 305),
                    Coordinates(1640, 535),
                    Coordinates(1565, 765),
                    Coordinates(1400, 935),
                    Coordinates(1170, 995)
                ]
            }
        },
    },
    reduce_ratio(16, 10): {
        (1920, 1200): {
            # Supported 16:10 layout measurements.
            "terminal": Coordinates(142.9, 36.6, 171.4, 45.7),
            "shell": Coordinates(1257.1, 40.0, 165.7, 45.7),
            "offsets": {
                "page": Coordinates(18.3, 27.4)
            },
            "scroll": {
                "page": Coordinates(y=-32.25),
                "characters": Coordinates(y=-56),
                "sonata": Coordinates(y=70)
            },
            "scrapers": {
                "weapons": Coordinates(81.7, 190.9),
                "echoes": Coordinates(81.7, 325.7),
                "devItems": Coordinates(81.7, 595.4),
                "resources": Coordinates(81.7, 730.3),
            },
            "items": {
                "start": Coordinates(205.7, 118.9, 148.6, 185.1),
                "rarityColorPick": Coordinates(1313, 482), 
                "info": Coordinates(1298.3, 176.0, 554.3, 274.3),
                "description": Coordinates(1298.3, 176.0, 554.3, 817.1)
            },
            "weapons": {
                "page": Coordinates(102, 52, 400, 45),
                "start": Coordinates(176, 135, 148.6, 185.1),
                "rarityColorPick": Coordinates(1313, 482), 
                "name": Coordinates(1328, 173.7, 525, 57.1),
                "value": Coordinates(1634.3, 377.1, 217.1, 45.7),
                "level": Coordinates(1329, 299, 218, 38),
                "equipped": Coordinates(1364, 933, 480, 39),
                "rank": Coordinates(1331, 635, 96, 42),
                "sort": {
                    "button": Coordinates(431, 1110),
                    "items": [
                        Coordinates(431,  900),  # 0 – Sort by Rarity
                        Coordinates(431,  980),  # 1 – Sort by Level
                        Coordinates(431, 1060),  # 2 – Sort by Amount
                    ],
                },
            },
            "echoes": {
                "page": Coordinates(200.0, 45.7, 148.6, 45.7),
                "start": Coordinates(205.7, 118.9, 148.6, 185.1),
                "rarityColorPick": Coordinates(1313, 482), 
                "echoName": Coordinates(1316, 187, 544, 40), # measured
                "echoCard": Coordinates(1298.3, 173.7, 555.4, 173.7),
                "level": Coordinates(1330, 260, 58, 30), # measured
                "sonataIcon": {
                    "radius": 14.0,
                    "level_XX": { # if level is 10 or above, the sonata icon shifts slightly to the left to make room for the double digit level badge.
                        "circle": Coordinates(14.0, 14.0),     # Circle parameters for the sonata icon (for matching).
                        "icon": Coordinates(1396, 259, 28, 28), # NEW-UI: Sonata icon has moved to the echo card header.
                    },
                    "level_X":  { # if level is below 10, the sonata icon is slightly further to the right
                        "circle": Coordinates(14.0, 14.0),     # Circle parameters for the sonata icon (for matching).
                        "icon": Coordinates(1379, 259, 28, 28), # NEW-UI: Sonata icon has moved to the echo card header.
                    },
                },
                "sonata": Coordinates(1297.1, 457.1, 555.4, 466.3),
                "mouseMovement": Coordinates(1801.7, 760.6),
                "fullStatsName": Coordinates(1367, 521, 378, 398),  # measured
                "fullStatsValue": Coordinates(1742, 521, 115, 398), # measured
                "sort": {
                    "button": Coordinates(431, 1111), # measured
                    "items": [
                        Coordinates(430.9, 742),   # 0 – Sort by Level
                        Coordinates(430.9, 821),   # 1 – Sort by Rarity
                        Coordinates(430.9, 902),   # 2 – Sort by Time Added
                        Coordinates(430.9, 982),   # 3 – Sort by Tuning Status
                        Coordinates(430.9, 1062),  # 4 – Show Discarded First
                    ],
                },
                "filter": {
                    "button": Coordinates(227.0, 978.1),
                    "sonata": {
                        "dropdown": Coordinates(1322.1, 468.0, 347.0, 45.0),
                        "item_positions": [
                            Coordinates(1540.0, 560.0),   # position 0
                            Coordinates(1540.0, 640.0),   # position 1
                        ],
                        "item_names": [
                            Coordinates(1322.1, 544.0, 347.0, 40.0),   # position 0
                            Coordinates(1322.1, 620.0, 347.0, 40.0),   # position 1
                            Coordinates(1322.1, 696.0, 347.0, 40.0),   # position 2
                            Coordinates(1322.1, 772.0, 347.0, 40.0),   # position 3
                            Coordinates(1322.1, 848.0, 347.0, 40.0),   # position 4
                            Coordinates(1322.1, 924.0, 347.0, 40.0),   # position 5
                        ],
                        "item_amounts": [
                            Coordinates(1669.9, 544.0, 69.9, 40.0),    # position 0
                            Coordinates(1669.9, 620.0, 69.9, 40.0),    # position 1
                            Coordinates(1669.9, 696.0, 69.9, 40.0),    # position 2
                            Coordinates(1669.9, 772.0, 69.9, 40.0),    # position 3
                            Coordinates(1669.9, 848.0, 69.9, 40.0),    # position 4
                            Coordinates(1669.9, 924.0, 69.9, 40.0),    # position 5
                        ],
                        "bottom_offset_item_names": Coordinates(y=38.0),
                        "scroll": Coordinates(y=2.91),
                    }
                }
            },
            "achievements": {
                "status": Coordinates(1804.6, 225.1, 292.6, 74.3),
                "searchBar": Coordinates(443.4, 147.4),
                "searchButton": Coordinates(628.6, 147.4),
                "achievementsButton": Coordinates(1674.3, 788.6),
                "achievementsTab": Coordinates(840.0, 651.4),
            },
            "characters": {
                "offsets": {
                    "leftSide": Coordinates(y=136.0),
                    "rightSide": Coordinates(y=106.9),
                    "skillPosition": Coordinates(y=251.4)
                },
                "leftSide": Coordinates(77.7, 191.4),
                "rightSide": Coordinates(1813.1, 202.9),
                "resonatorName": Coordinates(251.4, 116.6, 320.0, 57.1),
                "resonatorLevel": Coordinates(182.9, 205.7, 154.3, 91.4),
                "weaponName": Coordinates(257.1, 134.9, 274.3, 38.9),
                "weaponLevel": Coordinates(245.7, 171.4, 125.7, 40.0),
                "weaponRank": Coordinates(163.4, 365.7, 106.3, 40.0),
                "skillClick": Coordinates(460.6, 965.7),
                "skillLevel": Coordinates(388.6, 108.6, 80.0, 45.7),
                "skillButton": Coordinates(194.3, 1085.7, 137.1, 40.0),
                "chainClick": Coordinates(1267.4, 198.9),
                "chainButton": Coordinates(333.7, 1069.7, 125.7, 36.6),
                "skillPositions": [
                    Coordinates(754.3, 962.3),
                    Coordinates(987.4, 825.1),
                    Coordinates(1260.6, 762.3),
                    Coordinates(1533.7, 825.1),
                    Coordinates(1765.7, 962.3)
                ],
                "chainPositions": [
                    Coordinates(1398.9, 201.1),
                    Coordinates(1564.6, 364.6),
                    Coordinates(1627.4, 593.1),
                    Coordinates(1564.6, 827.4),
                    Coordinates(1398.9, 987.4),
                    Coordinates(1170.3, 1050.3)
                ]
            }
        }
    },
}
