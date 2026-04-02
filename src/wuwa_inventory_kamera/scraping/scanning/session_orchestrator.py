"""
wuwa_inventory_kamera.scraping.scanning.session_orchestrator
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Top-level orchestrator that runs a complete scanning session — echoes,
weapons, items, characters, achievements, shell — using the new game
manipulation layer.

This replaces ``scraping.scraperManager.scrapers()`` and can be driven
from either the CLI or the Qt UI.

Key differences from the V1 orchestrator:

* **No multiprocessing** — the new architecture uses a single process
  with a background OcrService thread.  The game interaction thread
  submits captures; the OcrService thread does GPU work.
* **Configurable scraper list** — callers pass the list of scraper names
  to enable.
* **Progress callback** — a simple ``(step: str, scanned: int, total: int)``
  callback replaces the Qt signal.
* **Return value** — returns a structured dict of results instead of
  writing to a global ``INVENTORY``.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from wuwa_inventory_kamera.game.input_controller import InputController
from wuwa_inventory_kamera.game.navigation import GameNavigator, InventoryTab, SortOrder
from wuwa_inventory_kamera.game.screen import GameWindow
from wuwa_inventory_kamera.game.stop_signal import StopSignal
from wuwa_inventory_kamera.scraping.scanning.scan_state import ScanSession
from wuwa_inventory_kamera.scraping.service.ocr_service import OcrService

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[str, int, int], None]


def _noop_progress(step: str, scanned: int, total: int) -> None:
    pass


class SessionOrchestrator:
    """
    Runs a complete inventory scanning session.

    Parameters
    ----------
    scrapers:
        List of scraper names to run, e.g.
        ``['echoes', 'weapons', 'devItems', 'resources']``.
    ocr_providers:
        ONNX providers for the OcrService.
    min_rarity / min_level:
        Quality thresholds forwarded to the OcrService assemblers.
    sort_order:
        Sort order to set before scanning.
    save_raw:
        If set, raw screenshots are saved under this directory.
    inventory_key:
        Keybind to open the inventory.
    on_progress:
        Progress callback ``(step, scanned, total)``.
    """

    def __init__(
        self,
        scrapers: list[str],
        ocr_providers: list[str] | None = None,
        min_rarity: int = 1,
        min_level: int = 0,
        sort_order: SortOrder | None = None,
        save_raw: Path | None = None,
        inventory_key: str = 'b',
        on_progress: ProgressCallback | None = None,
        *,
        windowed: bool = False,
    ) -> None:
        self.scrapers = scrapers
        self.ocr_providers = ocr_providers
        self.min_rarity = min_rarity
        self.min_level = min_level
        self.sort_order = sort_order
        self.save_raw = save_raw
        self.inventory_key = inventory_key
        self.on_progress = on_progress or _noop_progress
        self.windowed = windowed

    def run(self) -> dict[str, Any]:
        """
        Execute the scanning session.

        Returns
        -------
        dict
            ``{'date': ..., 'echoes': [...], 'weapons': [...], ...}``
            When the user pressed Enter to stop early the dict also
            contains ``'cancelled': True``.
        """
        session_id = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        result: dict[str, Any] = {'date': session_id}

        # Discover game window
        gw = GameWindow(windowed=self.windowed)
        if not gw.found:
            logger.error('Game window not found')
            return {'error': 'Game window not found', **result}

        if self.windowed:
            gw.check_minimum_size()
            w, h = gw.size
            logger.info('Windowed mode: client area %dx%d', w, h)

        gw.activate()
        time.sleep(0.5)

        # Build controller + navigator
        get_origin = (lambda: gw.client_origin) if self.windowed else None
        ctrl = InputController(gw.monitor_index, get_origin=get_origin)
        nav = GameNavigator(ctrl, gw, inventory_key=self.inventory_key)

        # Check we're in the main menu
        if not nav.is_in_main_menu():
            logger.error('Game is not on the main menu')
            return {'error': 'Not on main menu', **result}

        # Start the Enter-key stop signal watcher
        stop = StopSignal()

        # Start OcrService
        with OcrService(
            providers=self.ocr_providers,
            min_rarity=self.min_rarity,
            min_level=self.min_level,
        ) as ocr_service:

            for scraper_name in self.scrapers:
                if stop.is_set():
                    logger.info('Scan cancelled by user — skipping remaining scrapers')
                    result['cancelled'] = True
                    break

                logger.info('Running scraper: %s', scraper_name)
                # Esc to reset before each scraper (matches V1 behaviour)
                ctrl.press_key('esc', wait=0.5)

                try:
                    scraper_result = self._run_scraper(
                        scraper_name, nav, ocr_service, session_id, stop.event,
                    )
                    result[scraper_name] = scraper_result
                except Exception:
                    logger.exception('Scraper %s failed', scraper_name)
                    result[scraper_name] = {'error': f'{scraper_name} failed'}

            # Final Esc
            ctrl.press_key('esc')

        stop.stop()  # clean shutdown of the polling thread

        if stop.is_set() and 'cancelled' not in result:
            result['cancelled'] = True

        return result

    def _run_scraper(
        self,
        name: str,
        nav: GameNavigator,
        ocr_service: OcrService,
        session_id: str,
        stop_event: threading.Event,
    ) -> Any:
        """Dispatch to the appropriate workflow."""
        from wuwa_inventory_kamera.game.navigation import InventoryTab

        if name == 'echoes':
            return self._run_echoes(nav, ocr_service, session_id, stop_event)
        elif name == 'weapons':
            return self._run_weapons(nav, ocr_service, session_id, InventoryTab.WEAPONS, stop_event)
        elif name in ('devItems', 'resources'):
            tab = InventoryTab.DEV_ITEMS if name == 'devItems' else InventoryTab.RESOURCES
            return self._run_weapons(nav, ocr_service, session_id, tab, stop_event)
        else:
            logger.warning('Scraper %r not yet implemented in v2', name)
            return {'error': f'{name} not yet implemented'}

    def _run_echoes(
        self, nav: GameNavigator, ocr_service: OcrService, session_id: str,
        stop_event: threading.Event,
    ) -> list[dict]:
        from wuwa_inventory_kamera.scraping.scanning.echo_workflow import EchoWorkflow

        session = ScanSession(
            total_items=0,  # placeholder; workflow reads from game
            sort_order=self.sort_order or SortOrder.TIME_ADDED,
            session_id=session_id,
        )

        raw_path = self.save_raw / session_id / 'raw' if self.save_raw else None

        wf = EchoWorkflow(
            nav=nav,
            ocr_service=ocr_service,
            session=session,
            sort_order=self.sort_order,
            save_raw=raw_path,
            stop_event=stop_event,
        )

        def _on_progress(scanned: int, total: int) -> None:
            self.on_progress('echoes', scanned, total)

        return wf.run(on_progress=_on_progress)

    def _run_weapons(
        self,
        nav: GameNavigator,
        ocr_service: OcrService,
        session_id: str,
        tab: 'InventoryTab',
        stop_event: threading.Event,
    ) -> list[dict]:
        from wuwa_inventory_kamera.scraping.scanning.weapon_workflow import WeaponWorkflow

        session = ScanSession(
            total_items=0,
            sort_order=self.sort_order or SortOrder.TIME_ADDED,
            session_id=session_id,
        )

        wf = WeaponWorkflow(
            nav=nav,
            ocr_service=ocr_service,
            session=session,
            tab=tab,
            sort_order=self.sort_order,
            stop_event=stop_event,
        )

        def _on_progress(scanned: int, total: int) -> None:
            self.on_progress(tab.value, scanned, total)

        return wf.run(on_progress=_on_progress)
