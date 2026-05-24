"""
wuwa_inventory_kamera.scraping.scanning.character_workflow
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Scanning workflow for the resonator (character) screen.

Unlike the inventory scrapers (echoes, weapons) the character screen is
not a grid — it is a sidebar list of resonators.  The scanner:

1. Opens the resonator menu via the configured keybind.
2. Iterates through all characters in the right-side panel row-by-row,
   scrolling when the visible slot count for the current layout is
   exhausted.
3. For each resonator, captures and submits five sections:

   * **Section 0** — resonator overview (name + level)
   * **Section 1** — weapon panel (weapon name, level, rank)
   * **Section 2** — echoes (skipped; handled by echo scraper)
    * **Section 3** — skills panel (active skill levels + passive unlock states)
   * **Section 4** — resonance chain (button activation per node)

4. After section 0 the ``already_seen`` flag from the assembler is
    checked. Leading repeated names on a newly scrolled page are treated
    as overlap on the final page, so those slots are skipped and the scan
    stops only after that visible page is exhausted.

Navigation note
---------------
The right-side panel shows a layout-specific number of resonator slots at
a time (``visibleSlots`` using ``rightSide`` +
``offsets.rightSide.y * index``). After cycling through the visible slots
the list is scrolled by one page (``scroll.characters``). On the final
scroll, already scanned characters can still occupy the upper slots, so
the workflow skips repeated entries until it either finds new characters
or exhausts the page.
"""
from __future__ import annotations

import logging
import threading
from collections import defaultdict
from pathlib import Path
from typing import Callable

import numpy as np

from ...game.navigation import GameNavigator
from ...game.screen import capture_full, capture_region
from ..service.captures import CharCapture, CharResult
from ..service.ocr_service import OcrService
from .scan_state import ScanSession

logger = logging.getLogger(__name__)

# Default number of resonator slots visible in the right panel at once.
# Resolution-specific layouts can override this via characters.visibleSlots.
_DEFAULT_SLOTS_PER_PAGE = 7
_CHARACTER_SLOT_RETRY_WAIT_SECONDS = 1.1

# Post-click settle before capturing the chain button state.
_CHAIN_NODE_CAPTURE_WAIT_SECONDS = 0.2
_FIRST_CHAIN_NODE_CAPTURE_WAIT_SECONDS = 0.35
_SKILL_NODE_CAPTURE_WAIT_SECONDS = 0.15
_PASSIVE_SKILL_CAPTURE_WAIT_SECONDS = 0.15

# Sections handled (2 is skipped — echoes)
_SECTIONS = (0, 1, 3, 4)

_OVERVIEW_DEBUG_ROI_KEYS = {
    'name': 'characters.resonatorName',
    'level': 'characters.resonatorLevel',
}
_WEAPON_DEBUG_ROI_KEYS = {
    'weaponName': 'characters.weaponName',
    'weaponLevel': 'characters.weaponLevel',
    'weaponRank': 'characters.weaponRank',
}

SKILL_KEYS  = ['skill_0', 'skill_1', 'skill_2', 'skill_3', 'skill_4']
PASSIVE_SKILL_KEYS = ['stats0', 'stats1', 'inherent', 'stats3', 'stats4']
CHAIN_KEYS  = ['chain_0', 'chain_1', 'chain_2', 'chain_3', 'chain_4', 'chain_5']


def _passive_skill_crop_key(skill_key: str, tier: int) -> str:
    return f'passive_{skill_key}_{tier}'


class CharacterWorkflow:
    """
    Scanning workflow for the resonator panel.

    Parameters
    ----------
    nav:
        Game navigator.
    ocr_service:
        OCR service for assembling character data.
    session:
        Scan session for tracking progress (total_items is updated from
        the game).
    resonator_key:
        Keybind that opens the resonator panel (default: ``'c'``).
    stop_event:
        Optional :class:`~threading.Event`; scanning stops when set.
    save_raw:
        If set, raw screenshots are saved to this directory for offline
        reprocessing.
    write_debug:
        If set, write OCR debug crops and preprocessed/signature artifacts.
    """

    def __init__(
        self,
        nav: GameNavigator,
        ocr_service: OcrService,
        session: ScanSession,
        resonator_key: str = 'c',
        stop_event: threading.Event | None = None,
        save_raw: Path | None = None,
        write_debug: bool = False,
    ) -> None:
        self.nav = nav
        self.ocr = ocr_service
        self.session = session
        self._resonator_key = resonator_key
        self._stop_event = stop_event
        self.save_raw = save_raw
        self.write_debug = write_debug

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def run(self, on_progress: Callable | None = None) -> dict:
        """
        Execute the resonator scan.

        Returns
        -------
        dict
            ``{char_id: char_dict}`` mapping.
        """
        layout = self.nav.layout
        ctrl   = self.nav.ctrl
        ch     = layout.characters   # shorthand for character coordinate block

        def _click_character_slot(slot: int, wait: float = 0.7) -> None:
            ctrl.click(
                ch.rightSide.x,
                ch.rightSide.y + ch.offsets.rightSide.y * slot,
                wait=wait,
            )

        def _capture_overview(char_index: int) -> CharResult:
            full = capture_full(layout.width, layout.height, layout.monitor, gw=self.nav.gw)
            name_crop = full[
                int(ch.resonatorName.y) : int(ch.resonatorName.y + ch.resonatorName.h),
                int(ch.resonatorName.x) : int(ch.resonatorName.x + ch.resonatorName.w),
            ]
            level_crop = full[
                int(ch.resonatorLevel.y) : int(ch.resonatorLevel.y + ch.resonatorLevel.h),
                int(ch.resonatorLevel.x) : int(ch.resonatorLevel.x + ch.resonatorLevel.w),
            ]

            overview_crops = {'name': name_crop, 'level': level_crop}

            if self.save_raw:
                self._save_raw(char_index, 0, {'full': full})
            if self.write_debug:
                self._write_debug_artifacts(char_index, 0, overview_crops)

            sec0_cap = CharCapture(
                char_index=char_index,
                section=0,
                crops=overview_crops,
            )
            return self.ocr.submit(sec0_cap).result(timeout=30)

        # Open resonator panel
        ctrl.press_key(self._resonator_key, wait=2.0)
        _click_character_slot(0)

        # 
        ctrl.scroll(-1)
        ctrl.scroll(0.25)

        slots_per_page = int(getattr(ch, 'visibleSlots', _DEFAULT_SLOTS_PER_PAGE))

        results: dict = {}
        char_index = 0

        done = False

        while not done:
            page_started_with_seen_characters = False
            page_has_new_character = False

            for slot in range(slots_per_page):
                if self._stop_event and self._stop_event.is_set():
                    done = True
                    break

                if slot != 0 or char_index != 0:
                    _click_character_slot(slot)

                # The game can reopen the next resonator on the last viewed tab.
                # Always return to overview before capturing section 0.
                ctrl.click(ch.leftSide.x, ch.leftSide.y, wait=0.8)

                # --- Section 0: overview (name + level) ---
                sec0_result = _capture_overview(char_index)

                if sec0_result.fields.get('already_seen') and (slot != 0 or char_index != 0):
                    logger.warning(
                        'Character slot %d repeated a previously scanned resonator; retrying selection once',
                        slot,
                    )
                    _click_character_slot(slot, wait=_CHARACTER_SLOT_RETRY_WAIT_SECONDS)
                    ctrl.click(ch.leftSide.x, ch.leftSide.y, wait=0.8)
                    sec0_result = _capture_overview(char_index)

                if sec0_result.fields.get('already_seen'):
                    if not page_has_new_character:
                        if not page_started_with_seen_characters:
                            logger.info(
                                'Character page begins with already scanned entries at slot %d; '
                                'treating current view as the final page',
                                slot,
                            )
                        page_started_with_seen_characters = True
                        continue

                    logger.warning(
                        'Character slot %d repeated a previously scanned resonator after '
                        'new characters on the same page; skipping slot',
                        slot,
                    )
                    continue

                page_has_new_character = True

                # Navigate to weapon tab (left sidebar section 1)
                lx = ch.leftSide.x
                ly = ch.leftSide.y + ch.offsets.leftSide.y * 1
                ctrl.click(lx, ly, wait=0.8)

                # --- Section 1: weapon ---
                full = capture_full(layout.width, layout.height, layout.monitor, gw=self.nav.gw)
                wname_crop = full[
                    int(ch.weaponName.y) : int(ch.weaponName.y + ch.weaponName.h),
                    int(ch.weaponName.x) : int(ch.weaponName.x + ch.weaponName.w),
                ]
                wlevel_crop = full[
                    int(ch.weaponLevel.y) : int(ch.weaponLevel.y + ch.weaponLevel.h),
                    int(ch.weaponLevel.x) : int(ch.weaponLevel.x + ch.weaponLevel.w),
                ]
                wrank_crop = full[
                    int(ch.weaponRank.y) : int(ch.weaponRank.y + ch.weaponRank.h),
                    int(ch.weaponRank.x) : int(ch.weaponRank.x + ch.weaponRank.w),
                ]

                weapon_crops = {
                    'weaponName': wname_crop,
                    'weaponLevel': wlevel_crop,
                    'weaponRank': wrank_crop,
                }

                if self.save_raw:
                    self._save_raw(char_index, 1, {'full': full})
                if self.write_debug:
                    self._write_debug_artifacts(char_index, 1, weapon_crops)

                sec1_cap = CharCapture(
                    char_index=char_index,
                    section=1,
                    crops=weapon_crops,
                )
                self.ocr.submit(sec1_cap).result(timeout=30)

                # --- Section 3: skills ---
                # Navigate to the skills tab in the left sidebar, then open the skill tree.
                # Each primary skill node also has up to two passive unlock buttons above it.
                ctrl.click(ch.leftSide.x, ch.leftSide.y + ch.offsets.leftSide.y * 3, wait=0.8)
                ctrl.click(ch.skillClick.x, ch.skillClick.y, wait=0.5)
                skill_crops: dict[str, np.ndarray] = {}
                skill_step = getattr(getattr(ch.offsets, 'skillPosition', None), 'y', 0)
                skill_button = getattr(ch, 'skillButton', None)
                for idx, pos in enumerate(ch.skillPositions):
                    ctrl.click(pos.x, pos.y, wait=_SKILL_NODE_CAPTURE_WAIT_SECONDS)
                    level_shot = capture_region(self.nav.gw, ch.skillLevel)
                    skill_crops[SKILL_KEYS[idx]] = level_shot
                    if not skill_step or skill_button is None:
                        continue

                    passive_skill_key = PASSIVE_SKILL_KEYS[idx]
                    for tier in (1, 2):
                        ctrl.click(
                            pos.x,
                            pos.y - (skill_step * tier),
                            wait=_PASSIVE_SKILL_CAPTURE_WAIT_SECONDS,
                        )
                        skill_crops[_passive_skill_crop_key(passive_skill_key, tier)] = capture_region(
                            self.nav.gw,
                            skill_button,
                        )
                ctrl.press_key('esc', wait=0.3)

                if self.save_raw:
                    self._save_raw(char_index, 3, skill_crops)
                if self.write_debug:
                    self._write_debug_artifacts(char_index, 3, skill_crops)

                sec3_cap = CharCapture(
                    char_index=char_index,
                    section=3,
                    crops=skill_crops,
                )
                self.ocr.submit(sec3_cap).result(timeout=30)

                # --- Section 4: resonance chain ---
                # Navigate to the chain tab in the left sidebar, then open the chain detail
                ctrl.click(ch.leftSide.x, ch.leftSide.y + ch.offsets.leftSide.y * 4, wait=0.8)
                ctrl.click(ch.chainClick.x, ch.chainClick.y, wait=0.7)
                chain_crops: dict[str, np.ndarray] = {}
                for idx, pos in enumerate(ch.chainPositions):
                    wait = (
                        _FIRST_CHAIN_NODE_CAPTURE_WAIT_SECONDS
                        if idx == 0
                        else _CHAIN_NODE_CAPTURE_WAIT_SECONDS
                    )
                    ctrl.click(pos.x, pos.y, wait=wait)
                    btn_shot = capture_region(self.nav.gw, ch.chainButton)
                    chain_crops[CHAIN_KEYS[idx]] = btn_shot
                ctrl.press_key('esc', wait=0.3)

                if self.save_raw:
                    self._save_raw(char_index, 4, chain_crops)
                if self.write_debug:
                    self._write_debug_artifacts(char_index, 4, chain_crops)

                sec4_cap = CharCapture(
                    char_index=char_index,
                    section=4,
                    crops=chain_crops,
                )
                sec4_result: CharResult = self.ocr.submit(sec4_cap).result(timeout=30)

                # Collect the fully merged result (section 4 contains all fields)
                fields = sec4_result.fields
                char_id = fields.get('char_id') or fields.get('name', str(char_index))

                results[char_id] = self._build_output(fields)
                logger.info(
                    'Character %r (%s) scanned — index=%d',
                    char_id, fields.get('name'), char_index,
                )

                if on_progress:
                    on_progress(len(results), len(results))

                char_index += 1

            if not done:
                if page_started_with_seen_characters:
                    logger.info('Character final page complete — %d characters scanned', len(results))
                    done = True
                else:
                    # Scroll the character list to reveal next batch
                    self.nav.scroll_character_list(wait=0.5)

        ctrl.press_key('esc', wait=0.3)
        return results

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _build_output(fields: dict) -> dict:
        """
        Convert the merged assembler fields into the final export format,
        matching the structure produced by the legacy ``resonatorScraper``.
        """
        ASCENSION_LEVELS = [20, 40, 50, 60, 70, 80, 90]

        level = fields.get('level', 0)
        weapon_max = fields.get('weaponMaxLevel', 0)

        try:
            weapon_ascension = ASCENSION_LEVELS.index(weapon_max)
        except ValueError:
            weapon_ascension = 0

        # Skill levels from section 3
        raw_skills = fields.get('skills', {})
        skills_out: dict = defaultdict(int, {
            'normal':      raw_skills.get('skill_0', 1),
            'resonance':   raw_skills.get('skill_1', 1),
            'forte':       raw_skills.get('skill_2', 1),
            'liberation':  raw_skills.get('skill_3', 1),
            'intro':       raw_skills.get('skill_4', 1),
            'stats0':      raw_skills.get('stats0', 0),
            'stats1':      raw_skills.get('stats1', 0),
            'inherent':    raw_skills.get('inherent', 0),
            'stats3':      raw_skills.get('stats3', 0),
            'stats4':      raw_skills.get('stats4', 0),
        })

        # Chain count from section 4
        raw_chain = fields.get('chain', {})
        chain_count = sum(1 for v in raw_chain.values() if v)

        return {
            '_name': fields.get('name', ''),
            'level': level,
            'ascension': 0,   # ascension derived from level externally if needed
            'weapon': {
                'id':        fields.get('weaponId', fields.get('weaponName', '')),
                'level':     fields.get('weaponLevel', 1),
                'ascension': weapon_ascension,
                'rank':      fields.get('weaponRank', 1),
            },
            'echoes': {},
            'skills': dict(skills_out),
            'chain':  chain_count,
        }

    def _debug_base(self) -> Path:
        if self.save_raw is not None:
            return self.save_raw
        from ...config.app_config import app_config

        return Path(app_config.exportFolder) / self.session.session_id / 'raw'

    def _write_debug_artifacts(
        self,
        char_index: int,
        section: int,
        images: dict[str, np.ndarray],
    ) -> None:
        from ..service.echo_reprocess import _write_region_debug_artifacts

        roi_key_maps = {
            0: _OVERVIEW_DEBUG_ROI_KEYS,
            1: _WEAPON_DEBUG_ROI_KEYS,
            3: {
                _passive_skill_crop_key(skill_key, tier): 'characters.skillButton'
                for skill_key in PASSIVE_SKILL_KEYS
                for tier in (1, 2)
            },
        }
        debug_dir = (
            self._debug_base()
            / f'char_{char_index:04d}'
            / f'section_{section}'
            / 'debug'
        )
        for name, image in images.items():
            roi_key = roi_key_maps.get(section, {}).get(name, f'characters.{name}')
            _write_region_debug_artifacts(
                debug_dir,
                basename=name,
                roi_key=roi_key,
                raw_bgr=image,
                rarity=None,
            )

    # ── Raw image persistence ────────────────────────────────────────────

    def _save_raw(
        self,
        char_index: int,
        section: int,
        images: dict[str, np.ndarray],
    ) -> None:
        """Save raw screenshots/crops to disk for offline reprocessing."""
        import json
        import cv2

        assert self.save_raw is not None
        char_dir = self.save_raw / f'char_{char_index:04d}'
        section_dir = char_dir / f'section_{section}'
        section_dir.mkdir(parents=True, exist_ok=True)

        for name, img in images.items():
            cv2.imwrite(str(section_dir / f'{name}.png'), img)

        if section == 0:
            meta = {
                'char_index': char_index,
                'screen_width': self.nav.layout.width,
                'screen_height': self.nav.layout.height,
                'monitor': self.nav.layout.monitor,
            }
            with open(char_dir / 'meta.json', 'w') as f:
                json.dump(meta, f, indent=2)
