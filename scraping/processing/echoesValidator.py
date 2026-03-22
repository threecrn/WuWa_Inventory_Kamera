"""
scraping.processing.echoesValidator
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Validates a parsed echo stats dict against the known valid value space
defined in ``echo_stats_valid_values.yaml``.

This module has **no project-level imports** — only stdlib + PyYAML — so it
works in both the GUI context and the standalone ``reprocess.py`` CLI without
any stub injection.

Public API
----------
validate_echo_stats(cost, level, rarity, stats) -> ValidationResult
    Full validation: main stat names, main stat values, substat names,
    substat values, substat count, and duplication rules.

infer_cost(stats) -> int | None
    Best-effort cost inference from the stats dict when the caller does not
    have cost information readily available.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Tolerance used when comparing OCR-read values against expected values.
# Values for percentage stats are displayed with 1 decimal place; the
# minimum tier step for any percentage substat is ~0.7, so a tolerance of
# 0.4 always resolves to the correct tier while covering OCR rounding up
# to ±0.4.  Integer (flat) stats use a separate tolerance.
_FLOAT_TOL: float = 0.4
_INT_TOL: float = 0.5

# Fixed main stat name per slot cost (cost → stat name in the stats dict).
_FIXED_MAIN: dict[int, str] = {1: 'hp', 3: 'atk', 4: 'atk'}

# Flat stats that are allowed to appear as BOTH a flat value and a
# percentage value within the same echo's substat list.
_DUAL_FLAT_PCT: frozenset[str] = frozenset({'hp', 'atk', 'def'})

# Supported rarities (only rarity-5 data is currently in the YAML).
_SUPPORTED_RARITIES: frozenset[int] = frozenset({5})

# Valid slot costs.
_VALID_COSTS: frozenset[int] = frozenset({1, 3, 4})

# ---------------------------------------------------------------------------
# YAML spec — loaded once at module import
# ---------------------------------------------------------------------------

def _load_spec() -> dict:
    yaml_path = Path(__file__).parent / 'echo_stats_valid_values.yaml'
    try:
        with open(yaml_path, encoding='utf-8') as fh:
            return yaml.safe_load(fh)
    except FileNotFoundError:
        logger.error(
            "echo_stats_valid_values.yaml not found at %s — validation disabled.", yaml_path
        )
        return {}
    except yaml.YAMLError as exc:
        logger.error("Failed to parse echo_stats_valid_values.yaml: %s", exc)
        return {}


_SPEC: dict = _load_spec()


# ---------------------------------------------------------------------------
# Return type
# ---------------------------------------------------------------------------

@dataclass
class ValidationResult:
    """
    Result of a single echo stats validation pass.

    Attributes
    ----------
    valid : bool
        ``True`` when no structural *errors* were found.  Warnings do not
        affect this flag — they indicate possible OCR inaccuracies that may
        be worth reviewing but do not invalidate the echo.
    errors : list[str]
        Structural issues: unknown stat names, wrong fixed main, duplicate
        substats for stats that do not allow flat+percentage pairs, too many
        substats for the given level.
    warnings : list[str]
        Value-level discrepancies: a numeric value does not match the
        expected value from the game formula (within the display tolerance of
        ±0.1 for percentage stats, exact for integer stats).
    """

    valid: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add_error(self, msg: str) -> None:
        self.errors.append(msg)
        self.valid = False

    def add_warning(self, msg: str) -> None:
        self.warnings.append(msg)

    def __bool__(self) -> bool:
        return self.valid


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _is_float_stat(name: str) -> bool:
    """Return True when *name* ends with ``%`` (percentage stat)."""
    return name.endswith('%')


def _parse_value(raw: str | float | int) -> float:
    """
    Coerce *raw* to a plain Python float.

    Accepts the string form read by the OCR (``"18.0%"`` or ``"2280"``) or
    an already-parsed numeric value.
    """
    if isinstance(raw, (int, float)):
        return float(raw)
    raw = str(raw).strip()
    if raw.endswith('%'):
        raw = raw[:-1]
    try:
        return float(raw)
    except ValueError:
        return float('nan')


def _check_value(
    result: ValidationResult,
    label: str,
    actual: float,
    expected: float,
    is_float: bool,
) -> None:
    """Append a warning to *result* when *actual* deviates from *expected*."""
    tol = _FLOAT_TOL if is_float else _INT_TOL
    if abs(actual - expected) > tol:
        result.add_warning(
            f"{label}: value {actual} differs from expected {expected} "
            f"(tolerance ±{tol})"
        )


def _check_substat_value(
    result: ValidationResult,
    name: str,
    actual: float,
    valid_tiers: list[float],
) -> None:
    """
    Check whether *actual* is (close to) one of the *valid_tiers*.

    A value within ``_FLOAT_TOL`` of the nearest tier becomes a warning;
    a value farther away becomes an error.
    """
    if not valid_tiers:
        return
    closest = min(valid_tiers, key=lambda v: abs(v - actual))
    diff = abs(closest - actual)
    tol = _FLOAT_TOL if _is_float_stat(name) else _INT_TOL
    if diff == 0.0:
        return  # exact match
    if diff <= tol:
        result.add_warning(
            f"Substat '{name}': value {actual} is close to tier {closest} "
            f"but not exact (diff {diff:.3f}) — possible OCR rounding"
        )
    else:
        result.add_error(
            f"Substat '{name}': value {actual} does not match any valid tier "
            f"{valid_tiers} (nearest: {closest}, diff {diff:.3f})"
        )


def _validate_numeric(result: ValidationResult, label: str, raw) -> bool:
    """
    Return ``True`` when *raw* can be interpreted as a plain number.

    Adds an error to *result* and returns ``False`` for values that are
    non-numeric strings — for example the OCR artefact ``"%66"`` produced
    when a ``%`` character is split to a separate token and ends up prepended
    to the following digits instead of marking the entire value as a percentage.
    """
    if math.isnan(_parse_value(raw)):
        result.add_error(
            f"{label}: value {raw!r} is not a valid number — likely an OCR artefact."
        )
        return False
    return True


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def infer_cost(stats: dict) -> int | None:
    """
    Best-effort inference of slot cost from the parsed stats dict.

    The fixed main stat is deterministic per cost (``hp`` → 1, ``atk`` → 3
    or 4).  When cost is ambiguous (atk can be cost-3 or cost-4), the rolled
    main stat is used to disambiguate: cost-4 exclusive stats are ``cr%``,
    ``cd%``, ``healing%``; cost-3 exclusive stats are ``er%`` and all
    element damage stats.

    Parameters
    ----------
    stats : dict
        Parsed stats dict with ``'main'`` and ``'sub'`` keys.

    Returns
    -------
    int | None
        Inferred cost (1, 3, or 4), or ``None`` if the cost cannot be
        determined from the available data.
    """
    if not _SPEC:
        return None

    main: dict = stats.get('main', {})

    if 'hp' in main and not _is_float_stat('hp'):
        return 1  # cost-1 fixed main is integer hp

    if 'atk' in main and not _is_float_stat('atk'):
        # Distinguish cost 3 vs 4 by the rolled main stat.
        cost4_exclusive = {'cr%', 'cd%', 'healing%'}
        cost3_exclusive = {'er%', 'fusion%', 'havoc%', 'spectro%', 'electro%', 'aero%', 'glacio%'}
        for name in main:
            if name in cost4_exclusive:
                return 4
            if name in cost3_exclusive:
                return 3
        # "atk%", "hp%", "def%" are valid for both 3 and 4 — cannot tell.
        return None

    return None


def expected_sub_count(level: int) -> int:
    """Return the expected number of substats for an echo at *level*.

    Reads ``max_substats_by_level`` from the validation spec.  Returns 0
    when the spec is not loaded or *level* is out of range.
    """
    tbl = _SPEC.get('max_substats_by_level', [])
    if tbl and 0 <= level < len(tbl):
        return tbl[level]
    return 0


def validate_echo_stats(
    cost: int,
    level: int,
    rarity: int,
    stats: dict,
) -> ValidationResult:
    """
    Validate a parsed echo stats dict against the known valid value space.

    Parameters
    ----------
    cost : int
        Slot cost (1, 3, or 4).  Use :func:`infer_cost` when the cost is not
        available directly.
    level : int
        Echo level (0–25).
    rarity : int
        Echo rarity (currently only rarity 5 is supported).
    stats : dict
        Parsed stats dict as produced by ``_extractStats`` / ``_buildEcho``::

            {
                'main': {'cr%': 22.0, 'atk': 150},
                'sub':  {'atk': 40, 'def': 50, 'hp': 470, 'basicAttack%': 8.6},
            }

    Returns
    -------
    ValidationResult
        ``.valid`` is ``True`` when no structural errors were found.
        Warnings indicate value-level discrepancies (potential OCR errors).
    """
    result = ValidationResult()

    if not _SPEC:
        result.add_warning("Validation spec not loaded — skipping all checks.")
        return result

    # -----------------------------------------------------------------------
    # Pre-condition checks
    # -----------------------------------------------------------------------
    if rarity not in _SUPPORTED_RARITIES:
        result.add_warning(
            f"Rarity {rarity} is not in the validation spec "
            f"(supported: {sorted(_SUPPORTED_RARITIES)}) — skipping value checks."
        )
        return result

    if cost not in _VALID_COSTS:
        result.add_error(f"Unknown slot cost {cost!r} (valid: {sorted(_VALID_COSTS)}).")
        return result

    if not (0 <= level <= 25):
        result.add_error(f"Level {level} is out of range 0–25.")
        return result

    spec_rolled  = _SPEC.get('mainstats', {}).get('rolled', {}).get(cost, {})
    spec_fixed   = _SPEC.get('mainstats', {}).get('fixed',  {}).get(cost, {})
    spec_subs    = _SPEC.get('substats', {})
    max_subs_tbl = _SPEC.get('max_substats_by_level', [])

    main: dict = stats.get('main', {})
    sub:  dict = stats.get('sub',  {})

    # -----------------------------------------------------------------------
    # Main stats: structure
    # -----------------------------------------------------------------------
    if len(main) != 2:
        result.add_error(
            f"Expected exactly 2 main stats (rolled + fixed), got {len(main)}: "
            f"{list(main.keys())}"
        )

    # Fixed main stat
    fixed_name = _FIXED_MAIN.get(cost)
    if fixed_name is None:
        result.add_error(f"No fixed main stat defined for cost {cost}.")
    elif fixed_name not in main:
        result.add_error(
            f"Fixed main stat '{fixed_name}' (expected for cost {cost}) "
            f"not found in main stats {list(main.keys())}."
        )
    else:
        fixed_spec_values: list = spec_fixed.get(fixed_name, [])
        if _validate_numeric(result, f"Fixed main '{fixed_name}'", main[fixed_name]):
            if fixed_spec_values and level < len(fixed_spec_values):
                expected_fixed = fixed_spec_values[level]
                _check_value(
                    result,
                    f"Fixed main '{fixed_name}'",
                    _parse_value(main[fixed_name]),
                    float(expected_fixed),
                    is_float=_is_float_stat(fixed_name),
                )

    # Rolled main stat
    rolled_candidates = [k for k in main if k != fixed_name]
    if not rolled_candidates:
        result.add_error("No rolled main stat found in main stats.")
    elif len(rolled_candidates) > 1:
        result.add_error(
            f"Multiple rolled main stat candidates found: {rolled_candidates} "
            f"(expected exactly one besides the fixed '{fixed_name}')."
        )
    else:
        rolled_name = rolled_candidates[0]
        if rolled_name not in spec_rolled:
            result.add_error(
                f"Rolled main stat '{rolled_name}' is not valid for cost {cost}. "
                f"Valid options: {sorted(spec_rolled.keys())}."
            )
        else:
            if _validate_numeric(result, f"Rolled main '{rolled_name}'", main[rolled_name]):
                rolled_spec_values: list = spec_rolled[rolled_name]
                if rolled_spec_values and level < len(rolled_spec_values):
                    expected_rolled = rolled_spec_values[level]
                    _check_value(
                        result,
                        f"Rolled main '{rolled_name}'",
                        _parse_value(main[rolled_name]),
                        float(expected_rolled),
                        is_float=_is_float_stat(rolled_name),
                    )

    # -----------------------------------------------------------------------
    # Substats: count
    # -----------------------------------------------------------------------
    if max_subs_tbl and level < len(max_subs_tbl):
        max_subs = max_subs_tbl[level]
        if len(sub) > max_subs:
            result.add_error(
                f"Too many substats: {len(sub)} found, maximum at level {level} "
                f"is {max_subs}."
            )
        elif max_subs > 0 and len(sub) < max_subs:
            result.add_warning(
                f"Suspicious substat count: {len(sub)} found but {max_subs} expected "
                f"at level {level} — the echo should have been upgraded to "
                f"{max_subs} substat(s) by now. Possible missing OCR line(s) "
                f"or player skipped upgrade."
            )

    # -----------------------------------------------------------------------
    # Substats: names, values, and duplication rules
    # -----------------------------------------------------------------------
    seen_base_flat: set[str] = set()   # base names seen as flat values
    seen_base_pct:  set[str] = set()   # base names seen as percentage values

    for name, value in sub.items():
        # --- Name validity ---
        if name not in spec_subs:
            result.add_error(
                f"Unknown substat '{name}'. "
                f"Valid substats: {sorted(spec_subs.keys())}."
            )
            continue

        # --- Value parseability (OCR artefact guard) ---
        actual_value = _parse_value(value)
        if math.isnan(actual_value):
            result.add_error(
                f"Substat '{name}': value {value!r} is not a valid number — "
                "likely an OCR artefact."
            )
            continue

        # --- Duplicate / flat+pct rules ---
        base = name[:-1] if name.endswith('%') else name
        is_pct = name.endswith('%')

        if is_pct:
            if base in seen_base_pct:
                result.add_error(
                    f"Substat '{name}' appears more than once as a percentage value."
                )
            seen_base_pct.add(base)
            if base in seen_base_flat and base not in _DUAL_FLAT_PCT:
                result.add_error(
                    f"Substat '{base}' appears as both flat and percentage, "
                    f"which is only allowed for {sorted(_DUAL_FLAT_PCT)}."
                )
        else:
            if base in seen_base_flat:
                result.add_error(
                    f"Substat '{name}' appears more than once as a flat value."
                )
            seen_base_flat.add(base)
            if base in seen_base_pct and base not in _DUAL_FLAT_PCT:
                result.add_error(
                    f"Substat '{base}' appears as both flat and percentage, "
                    f"which is only allowed for {sorted(_DUAL_FLAT_PCT)}."
                )

        # --- Value validity ---
        valid_tiers: list = spec_subs[name]
        _check_substat_value(result, name, actual_value, [float(t) for t in valid_tiers])

    return result
