"""
wuwa_inventory_kamera.scraping.service.assemblers.item_assembler
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Parses OCR tokens for one item grid cell into a structured dict.

The item info region contains a multi-line OCR result:
  line 0 — item name
  line 1 — (optional) flavour text or empty
  line 2 — item count (the last non-empty line)

Assembly is simple (one lookup + one integer parse) and never requires retry.
"""
from __future__ import annotations

import logging
import re

from wuwa_inventory_kamera.scraping.ocr._types import OcrResult
from wuwa_inventory_kamera.scraping.ocr import tokens_to_lines
from wuwa_inventory_kamera.scraping.service.captures import ItemCapture, ItemResult

logger = logging.getLogger(__name__)

_DIGITS_RE = re.compile(r'\d[\d,]*')


def _get_data():
    from wuwa_inventory_kamera.scraping.data import itemsID
    return itemsID


class ItemAssembler:
    """
    Assembles one :class:`~...captures.ItemCapture` into an
    :class:`~...captures.ItemResult`.
    """

    def assemble(
        self,
        capture: ItemCapture,
        info_tokens: list[OcrResult],
    ) -> ItemResult:
        """
        Parameters
        ----------
        capture:
            The originating :class:`ItemCapture`.
        info_tokens:
            OCR tokens from the combined name + count region.
        """
        itemsID = _get_data()
        idx = capture.index

        lines = [ln.lower().strip() for ln in tokens_to_lines(info_tokens, divisor='')]
        non_empty = [ln for ln in lines if ln]

        name_text = non_empty[0] if non_empty else ''

        # Count is on the last non-empty line (after the name)
        count_text = non_empty[-1] if len(non_empty) > 1 else ''
        m = _DIGITS_RE.search(count_text)
        count = int(m.group().replace(',', '')) if m else 0

        # Item lookup
        item_id: str | None = itemsID.get(name_text)
        if item_id is None:
            from difflib import get_close_matches
            close = get_close_matches(name_text, itemsID, n=1, cutoff=0.8)
            if close:
                logger.info('Item %d — fuzzy-resolved %r → %r', idx, name_text, close[0])
                item_id = itemsID[close[0]]
                name_text = close[0]
            else:
                logger.warning('Item %d — name %r not recognised.', idx, name_text)

        return ItemResult(
            index=idx,
            name=name_text,
            item_id=item_id,
            count=count,
        )
