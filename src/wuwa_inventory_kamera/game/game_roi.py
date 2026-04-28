"""
wuwa_inventory_kamera.game.game_roi
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

UI coordinate tables for every known game resolution.

This is a verbatim migration of ``game/gameROI.py`` from the legacy package
root into the new ``wuwa_inventory_kamera`` package so that the game layer
has no dependency on the project root being on ``sys.path``.

``COORDINATES`` is a nested dict keyed by ``(aspect_w, aspect_h)`` then
``(width, height)``.  All leaf values are :class:`Coordinates` objects.
Unknown resolutions are handled by :class:`~.screen_info.ScreenInfo` via
nearest-resolution scaling.
"""
from __future__ import annotations


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
#   echoCard        ROI   — Echo card header (name + cost + element icon).
#   sonata          ROI   — Sonata effect region (captured after scrolling).
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
    (16, 9): {
        (1920, 1080): {
            "terminal": Coordinates(140, 40, 150, 40),
            "shell": Coordinates(1255, 38, 165, 50),
            "offsets": {
                "page": Coordinates(16, 24)
            },
            "scroll": {
                "page": Coordinates(y=-31.25),
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
                "info": Coordinates(1296, 114, 558, 278),
                "description": Coordinates(1296, 114, 558, 820)
            },
            "weapons": {
                "page": Coordinates(200, 50, 130, 40),
                "start": Coordinates(205, 122, 151, 181),
                "name": Coordinates(1305, 116, 545, 55),
                "value": Coordinates(1655, 320, 190, 40),
                "level": Coordinates(1660, 235, 180, 45),
                "rank": Coordinates(1300, 530, 115, 50)
            },
            "echoes": {
                "page": Coordinates(200, 50, 130, 40),
                "start": Coordinates(205, 122, 151, 181),
                "echoCard": Coordinates(1296, 114, 558, 170),
                "level": Coordinates(1777, 251, 58, 30),
                "sonataIcon": Coordinates(1441, 317, 23, 24), # Sonata icon within the sonata region.
                "sonataIconCircle": {
                    "circle": Coordinates(12.55, 12.65), # Circle parameters for the sonata icon (for matching).
                    "radius": 11.5,                      # Circle radius of the sonata icon (for matching).
                },
                "sonata": Coordinates(1298, 397, 554, 467),
                "mouseMovement": Coordinates(1576.5, 665.5),
                "fullStatsName": Coordinates(1380, 430, 360, 380),
                "fullStatsValue": Coordinates(1740, 430, 100, 380),
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
        }
    },
    (16, 10): {
        (1920, 1200): {
            "echoes": {
                "sonataIcon": Coordinates(1441, 377, 23, 23) # Sonata icon within the sonata region
            },
        },
        (1680, 1050): {
            "terminal": Coordinates(125, 32, 150, 40),
            "shell": Coordinates(1100, 35, 145, 40),
            "offsets": {
                "page": Coordinates(16, 24),
                "characters": Coordinates(y=-56),
                "sonata": Coordinates(y=70),
            },
            "scroll": {
                "page": Coordinates(y=-31.70),
                "characters": Coordinates(y=-56),
                "sonata": Coordinates(y=70)
            },
            "scrapers": {
                "weapons": Coordinates(71.5, 167),
                "echoes": Coordinates(71.5, 285),
                "devItems": Coordinates(71.5, 521),
                "resources": Coordinates(71.5, 639),
            },
            "items": {
                "start": Coordinates(180, 104, 130, 162),
                "info": Coordinates(1136, 154, 485, 240),
                "description": Coordinates(1136, 154, 485, 715)
            },
            "weapons": {
                "page": Coordinates(175, 40, 130, 40),
                "start": Coordinates(180, 104, 130, 162),
                "name": Coordinates(1140, 152, 480, 50),
                "value": Coordinates(1430, 330, 190, 40),
                "level": Coordinates(1435, 255, 180, 45),
                "rank": Coordinates(1135, 510, 100, 50)
            },
            "echoes": {
                "page": Coordinates(175, 40, 130, 40),
                "start": Coordinates(180, 104, 130, 162),
                "echoCard": Coordinates(1136, 152, 486, 152),
                "sonata": Coordinates(1135, 400, 486, 408),
                "sonataIcon": Coordinates(1261, 330, 20, 20), # Scaled from 1920x1200 (×0.875)
                "mouseMovement": Coordinates(1576.5, 665.5),
                "fullStatsName": Coordinates(1200, 420, 320, 380),
                "fullStatsValue": Coordinates(1510, 420, 100, 380),
                # Sort dropdown – proportionally scaled from 1920x1080 measurements.
                "sort": {
                    "button": Coordinates(377, 936),
                    "items": [
                        Coordinates(377, 614),   # 0 – Sort by Level
                        Coordinates(377, 683),   # 1 – Sort by Rarity
                        Coordinates(377, 754),   # 2 – Sort by Time Added
                        Coordinates(377, 824),   # 3 – Sort by Tuning Status
                        Coordinates(377, 894),   # 4 – Sort Discarded First
                    ],
                },
            },
            "achievements": {
                "status": Coordinates(1579, 197, 256, 65),
                "searchBar": Coordinates(388, 129),
                "searchButton": Coordinates(550, 129),
                "achievementsButton": Coordinates(1465, 690),
                "achievementsTab": Coordinates(735, 570),
            },
            "characters": {
                "offsets": {
                    "leftSide": Coordinates(y=119),
                    "rightSide": Coordinates(y=93.5),
                    "skillPosition": Coordinates(y=220)
                },
                "leftSide": Coordinates(68, 167.5),
                "rightSide": Coordinates(1586.5, 177.5),
                "resonatorName": Coordinates(220, 102, 280, 50),
                "resonatorLevel": Coordinates(160, 180, 135, 80),
                "weaponName": Coordinates(225, 118, 240, 34),
                "weaponLevel": Coordinates(215, 150, 110, 35),
                "weaponRank": Coordinates(143, 320, 93, 35),
                "skillClick": Coordinates(403, 845),
                "skillLevel": Coordinates(340, 95, 70, 40),
                "skillButton": Coordinates(170, 950, 120, 35),
                "chainClick": Coordinates(1109, 174),
                "chainButton": Coordinates(292, 936, 110, 32),
                "skillPositions": [
                    Coordinates(660, 842),
                    Coordinates(864, 722),
                    Coordinates(1103, 667),
                    Coordinates(1342, 722),
                    Coordinates(1545, 842)
                ],
                "chainPositions": [
                    Coordinates(1224, 176),
                    Coordinates(1369, 319),
                    Coordinates(1424, 519),
                    Coordinates(1369, 724),
                    Coordinates(1224, 864),
                    Coordinates(1024, 919)
                ]
            }
        }
    },
}
