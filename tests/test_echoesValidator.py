"""
tests.test_echoesValidator
~~~~~~~~~~~~~~~~~~~~~~~~~~

Test suite for scraping.processing.echoesValidator.

Run with:
    pytest tests/test_echoesValidator.py -v
"""

from __future__ import annotations

import pytest

from wuwa_inventory_kamera.scraping.processing.echoesValidator import infer_cost, validate_echo_stats


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _stats(main: dict, sub: dict) -> dict:
    return {'main': main, 'sub': sub}


# Pre-built valid stat sets — all values taken directly from YAML spec tiers.

VALID_COST1_LV25 = _stats(
    main={'atk%': 18.0, 'hp': 2280},
    sub={'cr%': 9.9, 'cd%': 21.0, 'atk': 60, 'def': 70, 'hp%': 11.6},
)

VALID_COST3_LV25 = _stats(
    main={'er%': 32.0, 'atk': 100},
    sub={'cr%': 9.9, 'cd%': 21.0, 'atk': 60, 'def': 70, 'hp%': 11.6},
)

VALID_COST4_LV25 = _stats(
    main={'cr%': 22.0, 'atk': 150},
    sub={'atk%': 10.1, 'hp': 470, 'def%': 9.0, 'cd%': 15.0, 'er%': 8.4},
)


# ---------------------------------------------------------------------------
# Valid echoes
# ---------------------------------------------------------------------------

class TestValidEchoes:

    def test_cost1_level0_no_subs(self):
        """Level-0 cost-1 echo with no substats is valid."""
        r = validate_echo_stats(1, 0, 5, _stats({'atk%': 3.6, 'hp': 456}, {}))
        assert r.valid
        assert not r.errors

    def test_cost1_level25_max_subs(self):
        r = validate_echo_stats(1, 25, 5, VALID_COST1_LV25)
        assert r.valid
        assert not r.errors

    def test_cost3_level25_max_subs(self):
        r = validate_echo_stats(3, 25, 5, VALID_COST3_LV25)
        assert r.valid
        assert not r.errors

    def test_cost4_level25_max_subs(self):
        r = validate_echo_stats(4, 25, 5, VALID_COST4_LV25)
        assert r.valid
        assert not r.errors

    def test_cost4_level5_one_sub(self):
        """Level 5 allows exactly 1 substat."""
        r = validate_echo_stats(4, 5, 5, _stats(
            {'cr%': 7.9, 'atk': 59},
            {'cd%': 21.0},
        ))
        assert r.valid
        assert not r.errors

    def test_cost3_level10_two_subs(self):
        """Level 10 allows exactly 2 substats."""
        r = validate_echo_stats(3, 10, 5, _stats(
            {'er%': 16.6, 'atk': 52},
            {'cr%': 9.9, 'cd%': 21.0},
        ))
        assert r.valid
        assert not r.errors

    def test_dual_flat_pct_hp_allowed(self):
        """hp (flat) + hp% in subs is allowed — hp is in _DUAL_FLAT_PCT."""
        r = validate_echo_stats(1, 25, 5, _stats(
            {'atk%': 18.0, 'hp': 2280},
            {'hp': 470, 'hp%': 11.6, 'cr%': 9.9, 'cd%': 21.0, 'atk': 60},
        ))
        assert r.valid
        assert not r.errors

    def test_dual_flat_pct_atk_allowed(self):
        """atk (flat) + atk% in subs is allowed — atk is in _DUAL_FLAT_PCT."""
        r = validate_echo_stats(1, 25, 5, _stats(
            {'hp%': 22.8, 'hp': 2280},
            {'atk': 60, 'atk%': 11.6, 'cr%': 9.9, 'cd%': 21.0, 'def': 70},
        ))
        assert r.valid
        assert not r.errors

    def test_dual_flat_pct_def_allowed(self):
        """def (flat) + def% in subs is allowed — def is in _DUAL_FLAT_PCT."""
        r = validate_echo_stats(1, 25, 5, _stats(
            {'atk%': 18.0, 'hp': 2280},
            {'def': 70, 'def%': 14.7, 'cr%': 9.9, 'cd%': 21.0, 'hp': 580},
        ))
        assert r.valid
        assert not r.errors


# ---------------------------------------------------------------------------
# OCR artefacts (the reported bug class)
# ---------------------------------------------------------------------------

class TestOCRArtefacts:

    def test_leading_percent_unknown_name(self):
        """
        "Crit. Rate 9.9%" OCR'd as ("Crit. Rate", "%66").

        _extractStats stores `stat_value.endswith('%')` → False for "%66",
        so int("%66") raises ValueError and the raw string is stored as-is.
        The stat name has no "%" appended, giving sub = {'cr': '%66'}.
        'cr' (without %) is not a valid substat name → validator must reject.
        """
        stats = _stats(
            {'cr%': 22.0, 'atk': 150},
            {'cr': '%66', 'cd%': 21.0},
        )
        r = validate_echo_stats(4, 25, 5, stats)
        assert not r.valid
        assert any('cr' in e for e in r.errors)

    def test_leading_percent_known_name(self):
        """
        A non-numeric string stored under a valid substat key must be rejected.
        e.g. 'hp': '%66' — name is valid but value is unparseable.
        """
        stats = _stats(
            {'cr%': 22.0, 'atk': 150},
            {'hp': '%66'},
        )
        r = validate_echo_stats(4, 5, 5, stats)
        assert not r.valid
        assert any("'hp'" in e and 'not a valid number' in e for e in r.errors)

    def test_non_numeric_rolled_main_value(self):
        """A non-numeric string in the rolled main stat value is rejected."""
        stats = _stats(
            {'cr%': '%99', 'atk': 150},
            {},
        )
        r = validate_echo_stats(4, 25, 5, stats)
        assert not r.valid
        assert any('not a valid number' in e for e in r.errors)

    def test_non_numeric_fixed_main_value(self):
        """A non-numeric string in the fixed main stat value is rejected."""
        stats = _stats(
            {'cr%': 22.0, 'atk': '%abc'},
            {},
        )
        r = validate_echo_stats(4, 25, 5, stats)
        assert not r.valid
        assert any('not a valid number' in e for e in r.errors)


# ---------------------------------------------------------------------------
# Invalid stat names
# ---------------------------------------------------------------------------

class TestInvalidStatNames:

    def test_unknown_substat_name(self):
        stats = _stats(
            {'cr%': 22.0, 'atk': 150},
            {'unknownstat': 42.0},
        )
        r = validate_echo_stats(4, 5, 5, stats)
        assert not r.valid
        assert any('unknownstat' in e for e in r.errors)

    def test_er_pct_not_valid_rolled_main_for_cost4(self):
        """er% is a valid rolled main only for cost 3, not cost 4."""
        stats = _stats(
            {'er%': 32.0, 'atk': 150},
            {},
        )
        r = validate_echo_stats(4, 25, 5, stats)
        assert not r.valid
        assert any("er%" in e for e in r.errors)

    def test_cr_pct_not_valid_rolled_main_for_cost1(self):
        """cr% is a valid rolled main only for cost 4, not cost 1."""
        stats = _stats(
            {'cr%': 22.0, 'hp': 2280},
            {},
        )
        r = validate_echo_stats(1, 25, 5, stats)
        assert not r.valid
        assert any("cr%" in e for e in r.errors)

    def test_element_dmg_not_valid_rolled_main_for_cost4(self):
        """Elemental damage % is only valid as rolled main for cost 3."""
        for elem in ('fusion%', 'havoc%', 'spectro%', 'electro%', 'aero%', 'glacio%'):
            stats = _stats({'atk': 150, elem: 30.0}, {})
            r = validate_echo_stats(4, 25, 5, stats)
            assert not r.valid, f"{elem} should not be valid for cost 4"
            assert any(elem in e for e in r.errors), f"expected error mentioning {elem}"


# ---------------------------------------------------------------------------
# Wrong / missing main stats
# ---------------------------------------------------------------------------

class TestMainStats:

    def test_wrong_fixed_main_cost1(self):
        """Cost-1 fixed main must be 'hp'."""
        stats = _stats({'atk%': 18.0, 'atk': 2280}, {})
        r = validate_echo_stats(1, 25, 5, stats)
        assert not r.valid
        assert any('hp' in e for e in r.errors)

    def test_wrong_fixed_main_cost3(self):
        """Cost-3 fixed main must be 'atk'."""
        stats = _stats({'er%': 32.0, 'hp': 100}, {})
        r = validate_echo_stats(3, 25, 5, stats)
        assert not r.valid
        assert any('atk' in e for e in r.errors)

    def test_missing_fixed_main(self):
        """Only rolled main, no fixed main."""
        stats = _stats({'cr%': 22.0}, {})
        r = validate_echo_stats(4, 25, 5, stats)
        assert not r.valid
        assert any('atk' in e for e in r.errors)

    def test_only_fixed_main(self):
        """Only fixed main, no rolled main → not exactly 2 main stats."""
        stats = _stats({'atk': 150}, {})
        r = validate_echo_stats(4, 25, 5, stats)
        assert not r.valid

    def test_empty_main_stats(self):
        stats = {'main': {}, 'sub': {}}
        r = validate_echo_stats(4, 25, 5, stats)
        assert not r.valid


# ---------------------------------------------------------------------------
# Substat count
# ---------------------------------------------------------------------------

class TestSubstatCount:

    def test_no_subs_at_level0_ok(self):
        r = validate_echo_stats(4, 0, 5, _stats({'cr%': 22.0, 'atk': 150}, {}))
        assert r.valid

    def test_one_sub_at_level0_rejected(self):
        """Level 0 allows 0 substats."""
        stats = _stats({'cr%': 22.0, 'atk': 150}, {'cd%': 21.0})
        r = validate_echo_stats(4, 0, 5, stats)
        assert not r.valid
        assert any('Too many substats' in e for e in r.errors)

    def test_two_subs_at_level5_rejected(self):
        """Level 5 allows 1 substat max."""
        stats = _stats({'cr%': 22.0, 'atk': 150}, {'cd%': 21.0, 'atk%': 10.1})
        r = validate_echo_stats(4, 5, 5, stats)
        assert not r.valid
        assert any('Too many substats' in e for e in r.errors)

    def test_max_subs_at_each_breakpoint(self):
        """Exactly max substats at each level breakpoint is valid."""
        # max_substats_by_level: [0]*5 + [1]*5 + [2]*5 + [3]*5 + [4]*5 + [5]
        all_subs = {'cr%': 9.9, 'cd%': 21.0, 'atk': 60, 'def': 70, 'hp%': 11.6}
        cases = [(0, 0), (5, 1), (10, 2), (15, 3), (20, 4), (25, 5)]
        for level, count in cases:
            sub = dict(list(all_subs.items())[:count])
            r = validate_echo_stats(4, level, 5, _stats({'cr%': 22.0, 'atk': 150}, sub))
            assert r.valid, f"level={level}, count={count}: {r.errors}"

    def test_fewer_subs_than_max_is_warning(self):
        """Fewer substats than the level maximum produces a warning (not an error)."""
        # At level 25 max is 5; providing only 2 should warn.
        sub = {'cr%': 9.9, 'cd%': 21.0}
        r = validate_echo_stats(4, 25, 5, _stats({'cr%': 22.0, 'atk': 150}, sub))
        assert r.valid   # still a valid echo — just suspicious
        assert any('Fewer substats' in w for w in r.warnings)

    def test_zero_subs_at_level_zero_no_warning(self):
        """Level-0 echo with no substats should produce no count warning."""
        r = validate_echo_stats(4, 0, 5, _stats({'cr%': 4.4, 'atk': 30}, {}))
        assert r.valid
        assert not any('substats' in w for w in r.warnings)


# ---------------------------------------------------------------------------
# Duplicate substats
# ---------------------------------------------------------------------------

class TestDuplicateSubstats:

    def test_flat_pct_not_allowed_for_non_dual(self):
        """
        er% has no flat form in the spec, but testing the dual-check path:
        cr% appears both in rolled main and as a substat is allowed (different
        scopes). Two % entries of the same base in subs is not possible via
        normal Python dict (deduplication), so we test the symmetric flat case
        via a known non-DUAL stat that appears as flat in sub while its % is
        also in sub — but such a stat doesn't exist in the spec.

        Instead, confirm that DUAL stats do NOT trigger the error (covered by
        TestValidEchoes.test_dual_flat_pct_hp_allowed) while a truly unknown
        stat combination is caught by the name check first.
        """
        # cr% in subs alongside cr% in rolled main is valid (main ≠ sub).
        r = validate_echo_stats(4, 25, 5, _stats(
            {'cr%': 22.0, 'atk': 150},
            {'cr%': 9.9, 'cd%': 21.0, 'atk': 60, 'def': 70, 'hp%': 11.6},
        ))
        assert r.valid  # cr% in main and cr% in sub is fine


# ---------------------------------------------------------------------------
# Value range checks (warnings and errors)
# ---------------------------------------------------------------------------

class TestValueChecks:

    def test_main_stat_value_exact_no_issues(self):
        # Use level-0 spec values so the 0-substat empty dict does not
        # trigger the fewer-substats warning (max at level 0 is 0).
        r = validate_echo_stats(4, 0, 5, _stats({'cr%': 4.4, 'atk': 30}, {}))
        assert r.valid
        assert not r.warnings
        assert not r.errors

    def test_main_stat_value_off_by_one_tenth_is_warning(self):
        """cr% at level 25 should be 22.0; 22.1 is off by 0.1 → warning only."""
        stats = _stats({'cr%': 22.1, 'atk': 150}, {})
        r = validate_echo_stats(4, 25, 5, stats)
        assert r.valid       # warnings do not invalidate
        assert r.warnings

    def test_main_stat_value_way_off_is_warning(self):
        """Value checks on main stats produce warnings regardless of magnitude."""
        stats = _stats({'cr%': 5.0, 'atk': 150}, {})
        r = validate_echo_stats(4, 25, 5, stats)
        assert r.valid
        assert r.warnings

    def test_substat_on_valid_tier_no_issue(self):
        # Use level-5 spec values: max substats at level 5 is 1, matching the
        # single substat provided so the fewer-substats warning is not raised.
        r = validate_echo_stats(4, 5, 5, _stats(
            {'cr%': 7.9, 'atk': 54},
            {'cd%': 21.0},
        ))
        assert r.valid
        assert not r.warnings
        assert not r.errors

    def test_substat_close_to_tier_is_warning(self):
        """A substat value within 0.1 of a tier is a warning, not an error."""
        # cd% valid tiers include 21.0; 21.09 is off by ~0.09 (< _FLOAT_TOL=0.1).
        # Note: 21.1 looks right but 21.1 - 21.0 = 0.100...14 in IEEE 754,
        # which exceeds the tolerance and produces an error instead of a warning.
        stats = _stats({'cr%': 22.0, 'atk': 150}, {'cd%': 21.09})
        r = validate_echo_stats(4, 25, 5, stats)
        assert r.valid
        assert r.warnings

    def test_substat_far_from_all_tiers_is_error(self):
        """A substat value far from every tier is an error."""
        # cd% valid tiers: [12.6, 13.8, 15.0, ...] — 5.0 is far from all
        stats = _stats({'cr%': 22.0, 'atk': 150}, {'cd%': 5.0})
        r = validate_echo_stats(4, 5, 5, stats)
        assert not r.valid
        assert any("cd%" in e for e in r.errors)

    def test_flat_substat_on_valid_tier_no_issue(self):
        # atk valid tiers: [30, 40, 50, 60]. Use level-5 spec values so the
        # single substat matches the expected max at level 5 (=1).
        r = validate_echo_stats(4, 5, 5, _stats(
            {'cr%': 7.9, 'atk': 54},
            {'atk': 60},
        ))
        assert r.valid
        assert not r.warnings


# ---------------------------------------------------------------------------
# infer_cost
# ---------------------------------------------------------------------------

class TestInferCost:

    def test_cost1_from_hp_fixed(self):
        assert infer_cost(_stats({'atk%': 18.0, 'hp': 2280}, {})) == 1

    def test_cost3_from_er_rolled(self):
        assert infer_cost(_stats({'er%': 32.0, 'atk': 100}, {})) == 3

    def test_cost3_from_elemental_damage(self):
        for elem in ('fusion%', 'havoc%', 'spectro%', 'electro%', 'aero%', 'glacio%'):
            result = infer_cost(_stats({elem: 30.0, 'atk': 100}, {}))
            assert result == 3, f"Expected 3 for {elem}, got {result}"

    def test_cost4_from_cr(self):
        assert infer_cost(_stats({'cr%': 22.0, 'atk': 150}, {})) == 4

    def test_cost4_from_cd(self):
        assert infer_cost(_stats({'cd%': 44.0, 'atk': 150}, {})) == 4

    def test_cost4_from_healing(self):
        assert infer_cost(_stats({'healing%': 26.4, 'atk': 150}, {})) == 4

    def test_ambiguous_atk_pct_rolled_returns_none(self):
        """atk%/hp%/def% are valid for both cost 3 and 4 → ambiguous."""
        for stat in ('atk%', 'hp%', 'def%'):
            result = infer_cost(_stats({stat: 30.0, 'atk': 100}, {}))
            assert result is None, f"Expected None for {stat}, got {result}"

    def test_empty_spec_returns_none(self, monkeypatch):
        import wuwa_inventory_kamera.scraping.processing.echoesValidator as v
        monkeypatch.setattr(v, '_SPEC', {})
        assert infer_cost(_stats({'cr%': 22.0, 'atk': 150}, {})) is None


# ---------------------------------------------------------------------------
# Pre-conditions / edge cases
# ---------------------------------------------------------------------------

class TestPreconditions:

    def test_unsupported_rarity_gives_warning_not_error(self):
        """Rarity 4 is not in the spec — should warn and return early, not error."""
        r = validate_echo_stats(4, 25, 4, VALID_COST4_LV25)
        assert r.valid
        assert r.warnings

    def test_invalid_cost_2_is_error(self):
        r = validate_echo_stats(2, 25, 5, VALID_COST4_LV25)
        assert not r.valid
        assert any('cost' in e.lower() or '2' in e for e in r.errors)

    def test_level_26_is_error(self):
        r = validate_echo_stats(4, 26, 5, VALID_COST4_LV25)
        assert not r.valid
        assert any('26' in e for e in r.errors)

    def test_level_negative_is_error(self):
        r = validate_echo_stats(4, -1, 5, VALID_COST4_LV25)
        assert not r.valid
        assert any('-1' in e for e in r.errors)

    def test_empty_stats_dict_errors(self):
        """Completely empty stats produce main-stat structure errors."""
        r = validate_echo_stats(4, 25, 5, {'main': {}, 'sub': {}})
        assert not r.valid

    def test_missing_spec_gives_warning(self, monkeypatch):
        """When the YAML spec is not loaded, validate_echo_stats warns and returns."""
        import wuwa_inventory_kamera.scraping.processing.echoesValidator as v
        monkeypatch.setattr(v, '_SPEC', {})
        r = validate_echo_stats(4, 25, 5, VALID_COST4_LV25)
        assert r.valid   # no spec → no errors, just a warning
        assert r.warnings
