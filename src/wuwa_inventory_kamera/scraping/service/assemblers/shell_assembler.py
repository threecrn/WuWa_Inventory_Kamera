"""
wuwa_inventory_kamera.scraping.service.assemblers.shell_assembler
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Assembler for :class:`~...captures.ShellCapture`.

The shell HUD region contains a plain decimal number (the amount of Shell
currency the player owns).  The assembler concatenates all OCR tokens,
strips non-digit characters, and converts the result to an integer.

If OCR produces no digits (empty region, bad frame, etc.) the amount is
set to 0 and a warning is emitted.
"""
from __future__ import annotations

import logging

from ...ocr import tokens_to_string
from ..captures import ShellCapture, ShellResult

logger = logging.getLogger(__name__)


class ShellAssembler:
    """Stateless assembler for the shell-currency HUD crop."""

    def assemble(
        self,
        capture: ShellCapture,
        amount_tokens: list,
    ) -> ShellResult:
        """
        Parse the shell amount from OCR tokens.

        Parameters
        ----------
        capture:
            The originating :class:`ShellCapture` (unused beyond logging).
        amount_tokens:
            OCR token list for the ``amount`` crop.

        Returns
        -------
        ShellResult
        """
        raw = tokens_to_string(amount_tokens, divisor='').strip()
        digits = ''.join(ch for ch in raw if ch.isdigit())

        if digits:
            amount = int(digits)
        else:
            logger.warning('ShellAssembler — no digits found in OCR output %r; defaulting to 0', raw)
            amount = 0

        logger.debug('ShellAssembler — raw=%r amount=%d', raw, amount)
        return ShellResult(amount=amount)
