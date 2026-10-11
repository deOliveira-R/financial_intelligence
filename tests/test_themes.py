from fin_intel import cot, energy, fx, macro, themes, universe


def test_theme_codes_exist_in_their_vocabularies():
    fred = set(macro.MACRO_SERIES) | {series for series, _ in fx.SERIES.values()}
    industries = {i.name for i in universe.INDUSTRIES}
    for t in themes.THEMES:
        assert t.universe_industry is None or t.universe_industry in industries, t.name
        assert set(t.cot) <= set(cot.MARKETS), t.name
        assert set(t.fred) <= fred, t.name
        assert set(t.eia) <= set(energy.ALIASES), t.name
        assert set(t.etfs) <= set(universe.ETFS), t.name
        assert t.universe_industry or t.sic, f"{t.name}: no way to find its companies"
        assert all(len(code) == 3 and code.isupper() for code in t.lobbying), t.name
        assert all(code.isdigit() for code in t.naics), t.name


def test_lookup():
    assert themes.get("defense").lobbying[0] == "DEF"
