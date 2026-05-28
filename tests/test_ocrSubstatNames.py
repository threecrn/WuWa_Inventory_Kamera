from wuwa_inventory_kamera.scraping.processing.stats_extractor import _matchStats


def test_match_stats_merges_wrapped_substat_name_tokens() -> None:
    tokens = [
        'resonance liberation',
        'dmg bonus',
        'basic attack',
        'dmg bonus',
        'crit',
        'rate',
        'hp',
        'noise',
    ]

    assert _matchStats(tokens) == [
        'resonanceliberationdmgbonus',
        'basicattackdmgbonus',
        'critrate',
        'hp',
    ]