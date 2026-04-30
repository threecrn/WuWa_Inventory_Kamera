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
                "rank": Coordinates(1300, 530, 115, 50),
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
                "echoName": Coordinates(1316,125,544,40), # NEW-UI: Echo name
                "level": Coordinates(1330, 198, 58, 30), # NEW-UI: Echo level has moved into the echo card header.
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
        },
        (2560, 1440): {
            # All values scaled from (1920, 1080) by factor 4/3.
            # Exception: scroll values are mouse-wheel notch counts and are
            # resolution-independent — they match the 1920x1080 values exactly.
            "terminal": Coordinates(186.7, 53.3, 200.0, 53.3),
            "shell": Coordinates(1673.3, 50.7, 220.0, 66.7),
            "offsets": {
                "page": Coordinates(21.3, 32.0)
            },
            "scroll": {
                "page": Coordinates(y=-31.25),
                "characters": Coordinates(y=-56),
                "sonata": Coordinates(y=70)
            },
            "scrapers": {
                "weapons": Coordinates(108.7, 255.3),
                "echoes": Coordinates(108.7, 435.3),
                "devItems": Coordinates(108.7, 795.3),
                "resources": Coordinates(108.7, 975.3),
            },
            "items": {
                "start": Coordinates(273.3, 162.7, 201.3, 241.3),
                "info": Coordinates(1728.0, 152.0, 744.0, 370.7),
                "description": Coordinates(1728.0, 152.0, 744.0, 1093.3)
            },
            "weapons": {
                "page": Coordinates(266.7, 66.7, 173.3, 53.3),
                "start": Coordinates(273.3, 162.7, 201.3, 241.3),
                "name": Coordinates(1740.0, 154.7, 726.7, 73.3),
                "value": Coordinates(2206.7, 426.7, 253.3, 53.3),
                "level": Coordinates(2213.3, 313.3, 240.0, 60.0),
                "rank": Coordinates(1733.3, 706.7, 153.3, 66.7),
                "sort": {
                    "button": Coordinates(574.7, 1284.0),
                    "items": [
                        Coordinates(574.7, 1033.3),   # 0 – Sort by Rarity
                        Coordinates(574.7, 1129.3),   # 1 – Sort by Level
                        Coordinates(574.7, 1225.3),   # 2 – Sort by Amount
                    ],
                },
            },
            "echoes": {
                "page": Coordinates(266.7, 66.7, 173.3, 53.3),
                "start": Coordinates(273.3, 162.7, 201.3, 241.3),
                "echoCard": Coordinates(1728.0, 152.0, 744.0, 226.7),
                "echoName": Coordinates(1754.7, 166.7, 725.3, 53.3), # Scaled from 1920x1080 (×4/3)
                "level": Coordinates(2369.3, 334.7, 77.3, 40.0),
                "sonataIcon": Coordinates(1921.3, 422.7, 30.7, 32.0),
                "sonataIconCircle": {
                    "circle": Coordinates(16.7, 16.9),
                    "radius": 15.33,
                },
                "sonata": Coordinates(1730.7, 529.3, 738.7, 622.7),
                "mouseMovement": Coordinates(2102.0, 887.3),
                "fullStatsName": Coordinates(1840.0, 573.3, 480.0, 506.7),
                "fullStatsValue": Coordinates(2320.0, 573.3, 133.3, 506.7),
                "sort": {
                    "button": Coordinates(574.7, 1284.0),
                    "items": [
                        Coordinates(574.7, 841.3),    # 0 – Sort by Level
                        Coordinates(574.7, 937.3),    # 1 – Sort by Rarity
                        Coordinates(574.7, 1033.3),   # 2 – Sort by Time Added
                        Coordinates(574.7, 1129.3),   # 3 – Sort by Tuning Status
                        Coordinates(574.7, 1225.3),   # 4 – Show Discarded First
                    ],
                },
                "filter": {
                    "button": Coordinates(302.7, 1304.0),
                    "sonata": {
                        "dropdown": Coordinates(1762.7, 624.0, 462.7, 60.0),
                        "item_positions": [
                            Coordinates(2053.3, 746.7),   # position 0
                            Coordinates(2053.3, 853.3),   # position 1
                        ],
                        "item_names": [
                            Coordinates(1762.7, 725.3, 462.7, 53.3),   # position 0
                            Coordinates(1762.7, 826.7, 462.7, 53.3),   # position 1
                            Coordinates(1762.7, 928.0, 462.7, 53.3),   # position 2
                            Coordinates(1762.7, 1029.3, 462.7, 53.3),  # position 3
                            Coordinates(1762.7, 1130.7, 462.7, 53.3),  # position 4
                            Coordinates(1762.7, 1232.0, 462.7, 53.3),  # position 5
                        ],
                        "item_amounts": [
                            Coordinates(2226.7, 725.3, 93.3, 53.3),    # position 0
                            Coordinates(2226.7, 826.7, 93.3, 53.3),    # position 1
                            Coordinates(2226.7, 928.0, 93.3, 53.3),    # position 2
                            Coordinates(2226.7, 1029.3, 93.3, 53.3),   # position 3
                            Coordinates(2226.7, 1130.7, 93.3, 53.3),   # position 4
                            Coordinates(2226.7, 1232.0, 93.3, 53.3),   # position 5
                        ],
                        "bottom_offset_item_names": Coordinates(y=50.7),
                        "scroll": Coordinates(y=2.91),
                    }
                }
            },
            "achievements": {
                "status": Coordinates(2105.3, 306.7, 341.3, 86.7),
                "searchBar": Coordinates(517.3, 198.7),
                "searchButton": Coordinates(838.7, 198.7),
                "achievementsButton": Coordinates(2232.0, 1053.3),
                "achievementsTab": Coordinates(1113.3, 760.0),
            },
            "characters": {
                "offsets": {
                    "leftSide": Coordinates(y=181.3),
                    "rightSide": Coordinates(y=141.3),
                    "skillPosition": Coordinates(y=340.0)
                },
                "leftSide": Coordinates(109.3, 254.7),
                "rightSide": Coordinates(2418.7, 271.3),
                "resonatorName": Coordinates(333.3, 146.7, 373.3, 66.7),
                "resonatorLevel": Coordinates(240.0, 266.7, 180.0, 106.7),
                "weaponName": Coordinates(342.7, 168.0, 364.0, 45.3),
                "weaponLevel": Coordinates(340.0, 213.3, 146.7, 46.7),
                "weaponRank": Coordinates(233.3, 473.3, 126.7, 46.7),
                "skillClick": Coordinates(614.0, 1204.0),
                "skillLevel": Coordinates(520.0, 133.3, 93.3, 53.3),
                "skillButton": Coordinates(266.7, 1306.7, 160.0, 46.7),
                "chainClick": Coordinates(1686.7, 180.0),
                "chainButton": Coordinates(456.0, 1285.3, 146.7, 42.7),
                "skillPositions": [
                    Coordinates(1006.7, 1206.7),
                    Coordinates(1313.3, 1020.0),
                    Coordinates(1680.0, 940.0),
                    Coordinates(2046.7, 1020.0),
                    Coordinates(2346.7, 1206.7)
                ],
                "chainPositions": [
                    Coordinates(1860.0, 186.7),
                    Coordinates(2086.7, 406.7),
                    Coordinates(2186.7, 713.3),
                    Coordinates(2086.7, 1020.0),
                    Coordinates(1866.7, 1246.7),
                    Coordinates(1560.0, 1326.7)
                ]
            }
        },
    },
    (16, 10): {
        (1920, 1200): {
            # All values scaled from (1680, 1050) by factor 8/7.
            # Exception: scroll values are mouse-wheel notch counts and are
            # resolution-independent — they match the 1680x1050 values exactly.
            "terminal": Coordinates(142.9, 36.6, 171.4, 45.7),
            "shell": Coordinates(1257.1, 40.0, 165.7, 45.7),
            "offsets": {
                "page": Coordinates(18.3, 27.4)
            },
            "scroll": {
                "page": Coordinates(y=-31.70),
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
                "info": Coordinates(1298.3, 176.0, 554.3, 274.3),
                "description": Coordinates(1298.3, 176.0, 554.3, 817.1)
            },
            "weapons": {
                "page": Coordinates(200.0, 45.7, 148.6, 45.7),
                "start": Coordinates(205.7, 118.9, 148.6, 185.1),
                "name": Coordinates(1302.9, 173.7, 548.6, 57.1),
                "value": Coordinates(1634.3, 377.1, 217.1, 45.7),
                "level": Coordinates(1640.0, 291.4, 205.7, 51.4),
                "rank": Coordinates(1297.1, 582.9, 114.3, 57.1),
                "sort": {
                    "button": Coordinates(430.9, 1069.7),
                    "items": [
                        Coordinates(430.9, 861.7),   # 0 – Sort by Rarity
                        Coordinates(430.9, 941.7),   # 1 – Sort by Level
                        Coordinates(430.9, 1021.7),  # 2 – Sort by Amount
                    ],
                },
            },
            "echoes": {
                "page": Coordinates(200.0, 45.7, 148.6, 45.7),
                "start": Coordinates(205.7, 118.9, 148.6, 185.1),
                "echoCard": Coordinates(1298.3, 173.7, 555.4, 173.7),
                "level": Coordinates(1777.0, 251.0, 58.1, 29.9),
                "sonataIcon": Coordinates(1441.1, 377.1, 22.9, 22.9),
                "sonataIconCircle": {
                    "circle": Coordinates(12.55, 12.65),
                    "radius": 11.5,
                },
                "sonata": Coordinates(1297.1, 457.1, 555.4, 466.3),
                "mouseMovement": Coordinates(1801.7, 760.6),
                "fullStatsName": Coordinates(1371.4, 480.0, 365.7, 434.3),
                "fullStatsValue": Coordinates(1725.7, 480.0, 114.3, 434.3),
                "sort": {
                    "button": Coordinates(430.9, 1069.7),
                    "items": [
                        Coordinates(430.9, 701.7),   # 0 – Sort by Level
                        Coordinates(430.9, 780.6),   # 1 – Sort by Rarity
                        Coordinates(430.9, 861.7),   # 2 – Sort by Time Added
                        Coordinates(430.9, 941.7),   # 3 – Sort by Tuning Status
                        Coordinates(430.9, 1021.7),  # 4 – Show Discarded First
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
                "rank": Coordinates(1135, 510, 100, 50),
                # Sort dropdown – proportionally scaled from 1920x1080 measurements.
                "sort": {
                    "button": Coordinates(377, 936),
                    "items": [
                        Coordinates(377, 754),   # 0 – Sort by Rarity
                        Coordinates(377, 824),   # 1 – Sort by Level
                        Coordinates(377, 894),   # 2 – Sort by Amount
                    ],
                },
            },
            "echoes": {
                "page": Coordinates(175, 40, 130, 40),
                "start": Coordinates(180, 104, 130, 162),
                "echoCard": Coordinates(1136, 152, 486, 152),
                "level": Coordinates(1554.9, 219.6, 50.8, 26.2),  # Estimated from 1920x1080 × 7/8
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
                # Filter section – estimated from 1920x1080 × 7/8.
                "filter": {
                    "button": Coordinates(198.6, 855.8),
                    "sonata": {
                        "dropdown": Coordinates(1156.8, 409.5, 303.6, 39.4),
                        "item_positions": [
                            Coordinates(1347.5, 490.0),   # position 0
                            Coordinates(1347.5, 560.0),   # position 1
                        ],
                        "item_names": [
                            Coordinates(1156.8, 476.0, 303.6, 35.0),   # position 0
                            Coordinates(1156.8, 542.5, 303.6, 35.0),   # position 1
                            Coordinates(1156.8, 609.0, 303.6, 35.0),   # position 2
                            Coordinates(1156.8, 675.5, 303.6, 35.0),   # position 3
                            Coordinates(1156.8, 742.0, 303.6, 35.0),   # position 4
                            Coordinates(1156.8, 808.5, 303.6, 35.0),   # position 5
                        ],
                        "item_amounts": [
                            Coordinates(1461.2, 476.0, 61.2, 35.0),    # position 0
                            Coordinates(1461.2, 542.5, 61.2, 35.0),    # position 1
                            Coordinates(1461.2, 609.0, 61.2, 35.0),    # position 2
                            Coordinates(1461.2, 675.5, 61.2, 35.0),    # position 3
                            Coordinates(1461.2, 742.0, 61.2, 35.0),    # position 4
                            Coordinates(1461.2, 808.5, 61.2, 35.0),    # position 5
                        ],
                        "bottom_offset_item_names": Coordinates(y=33.25),
                        "scroll": Coordinates(y=2.55),
                    }
                }
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
