"""
wuwa_inventory_kamera.scraping.service.assemblers.achievement_assembler
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Assembler for :class:`~...captures.AchievementCapture`.

Each capture carries a single ``status`` crop.  The assembler OCR-reads
the text and checks whether the achievement has been completed (claimed)
by comparing against the localised "claim" / "activated" string from
``definedText``.

The legacy scraper checked for two indicators of completion:

* The button text matches the defined text key
  ``PrefabTextItem_128820487_Text`` (which corresponds to the "claim"
  action text, meaning the reward is ready to collect — i.e. completed).
* Or the status text contains ``'/'`` (numeric progress like "3/3").

Both mean the achievement is done.  We replicate this logic here.
"""
from __future__ import annotations

import logging

from ...ocr import tokens_to_string
from ..captures import AchievementCapture, AchievementResult

logger = logging.getLogger(__name__)


def _get_defined_text() -> dict:
    from ...data import ensureDataLoaded, definedText

    ensureDataLoaded()
    return definedText


class AchievementAssembler:
    """
    Stateless assembler for achievement status crops.

    One instance should be created per scanning session.
    """

    def assemble(
        self,
        capture: AchievementCapture,
        status_tokens: list,
    ) -> AchievementResult:
        """
        Determine whether the achievement is completed.

        Parameters
        ----------
        capture:
            The originating :class:`AchievementCapture`.
        status_tokens:
            OCR token list for the ``status`` crop.

        Returns
        -------
        AchievementResult
        """
        defined_text = _get_defined_text()
        # The key used in the legacy scraper for the claim-button label
        claim_text = defined_text.get(
            'PrefabTextItem_128820487_Text',
            defined_text.get('claim', 'claim'),
        ).lower()

        status_text = tokens_to_string(status_tokens, divisor=' ').lower().strip()

        # Achievement is done when the button says "claim" or shows numeric progress
        completed = (status_text == claim_text) or ('/' in status_text)

        logger.debug(
            'Achievement %r — status=%r completed=%s',
            capture.achievement_name, status_text, completed,
        )

        return AchievementResult(
            achievement_name=capture.achievement_name,
            achievement_id=capture.achievement_id,
            completed=completed,
        )
