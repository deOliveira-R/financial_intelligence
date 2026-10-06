from datetime import date, timedelta

import respx
from sqlalchemy import func, select

from fin_intel import carry, ingest, japan, releases
from fin_intel.models import (
    DailyBar,
    EconomicObservation,
    EconomicReleaseDate,
    EconomicSeries,
    EconomicVintage,
    Security,
)
from fin_intel.providers import MofProvider
from fin_intel.providers.mof import FLOWS, JGB_CURRENT, JGB_HISTORY
from fin_intel.rebuild import rebuild

JGB_ALL = b"""Interest Rate,,,,,,,,,,,,,,,(Unit : %)
Date,1Y,2Y,3Y,4Y,5Y,6Y,7Y,8Y,9Y,10Y,15Y,20Y,25Y,30Y,40Y
1974/9/24,10.327,9.362,8.83,8.515,8.348,8.29,8.24,8.121,8.127,-,-,-,-,-,-
2026/9/30,1.684,1.952,2.086,2.273,2.399,2.525,2.639,2.796,2.926,3.057,3.583,3.877,4.131,4.098,4.099
"""
JGB_MONTH = b"""Interest Rate (October 2026),,,,,,,,,,,,,,,(Unit : %)
Date,1Y,2Y,3Y,4Y,5Y,6Y,7Y,8Y,9Y,10Y,15Y,20Y,25Y,30Y,40Y
2026/10/1,1.668,1.939,2.077,2.274,2.407,2.534,2.657,2.82,2.952,3.092,3.62,3.91,4.154,4.122,4.125
,,,,,,,,,,,,,,,
"  If you cannot download the latest csv data, please clear the browser's cache.",,,
"""


def flows_csv(rows):
    head = "International Transactions in Securities (Weekly),,,\n期間,株式,,,\n"
    body = "".join(
        f'{period},"1,000 ","900 ",{eq} ,"5,000 ","6,000 ","{bonds}",0,0,0,0,{total},'
        f"1,1,{eq_in},1,1,{bonds_in},0,0,0,0,{total_in}\n"
        for period, eq, bonds, total, eq_in, bonds_in, total_in in rows
    )
    return (head + body + "（備考）,\n").encode("cp932")


FLOWS_CSV = flows_csv(
    [
        ("2025．12．21～12．27", 100, "-1,000", -900, 5, 6, 7),
        ("2025．12．28～2026．1．3", 200, "2,500", 2700, 5, 6, 7),
    ]
)

BOJ_PAGE = b"""<table><caption>Table : 2016</caption><thead><tr><th>Date of MPM</th></tr></thead>
<tbody>
<tr><td><a href="k1.pdf">Apr.&nbsp; 27 (Wed.),&nbsp; 28 (Thurs.) [PDF 19KB]</a></td><td>-</td></tr>
<tr><td><a href="k2.pdf">Oct. 31 (Mon.), Nov. 1 (Tues.) [PDF 57KB]</a></td><td>Nov. 2</td></tr>
</tbody></table>
<table><caption>Table : 2017</caption><tbody>
<tr class="x"><td>Mar. 15 (Wed.), 16 (Thurs.)</td><td>Jan. 27 (Wed.), 2018</td></tr>
<tr><td>To be announced</td></tr>
</tbody></table>"""


def test_parse_jgb_curve():
    series, obs = japan.parse_jgb(JGB_ALL)
    assert len(series) == 15
    first = {o["series_id"]: o["value"] for o in obs if o["date"] == date(1974, 9, 24)}
    assert first["JGB2Y"] == 9.362 and "JGB10Y" not in first  # "-": not issued yet
    assert {o["date"] for o in japan.parse_jgb(JGB_MONTH)[1]} == {date(2026, 10, 1)}


def test_parse_flows_dates_weeks_by_their_end():
    _, obs = japan.parse_flows(FLOWS_CSV)
    by = {(o["series_id"], o["date"]): o["value"] for o in obs}
    assert by[("MOF_OUT_BONDS", date(2025, 12, 27))] == -1000
    assert by[("MOF_OUT_BONDS", date(2026, 1, 3))] == 2500  # spans the new year
    assert by[("MOF_IN_TOTAL", date(2026, 1, 3))] == 7
    assert japan.available_on("MOF_OUT_BONDS", date(2026, 1, 3)) == date(2026, 1, 8)
    assert japan.resolve("jgb10y") == "JGB10Y" and japan.resolve("out_bonds") == "MOF_OUT_BONDS"


def test_parse_boj_meetings():
    assert releases.parse_boj(BOJ_PAGE) == [
        date(2016, 4, 28),
        date(2016, 11, 1),  # a meeting spanning two months: its last day
        date(2017, 3, 16),
    ]


def test_boj_pages_replace_only_their_years(session):
    session.add(EconomicSeries(id="X", source="fred"))
    releases.load_boj(session, BOJ_PAGE)
    current = b"<table><caption>Table : 2026</caption><tbody>"
    current += b"<tr><td>Jan. 22 (Thurs.), 23 (Fri.)</td></tr></tbody></table>"
    releases.load_boj(session, current)
    dates = session.scalars(
        select(EconomicReleaseDate.date).where(
            EconomicReleaseDate.release_id == releases.BOJ_RELEASE_ID
        )
    ).all()
    assert sorted(dates) == [
        date(2016, 4, 28),
        date(2016, 11, 1),
        date(2017, 3, 16),
        date(2026, 1, 23),
    ]


@respx.mock
def test_sync_japan_loads_history_once_and_rebuilds(session, raw_store):
    base = "https://www.mof.go.jp"
    history = respx.get(base + JGB_HISTORY).respond(content=JGB_ALL)
    respx.get(base + JGB_CURRENT).respond(content=JGB_MONTH)
    respx.get(base + FLOWS).respond(content=FLOWS_CSV)
    mof = MofProvider(raw_store=raw_store)
    assert ingest.sync_japan(session, mof) > 0
    assert history.call_count == 1  # nothing before this month yet: history fetched
    ingest.sync_japan(session, mof)
    assert history.call_count == 1  # history present: only this month and flows

    def count():
        return session.scalar(select(func.count()).select_from(EconomicObservation))

    before = count()
    with respx.mock:
        rebuild(session, raw_store, "economic")
    assert count() == before


def weekdays(end: date, n: int) -> list[date]:
    out, d = [], end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return out[::-1]


def test_gauge_flags_a_yen_rally_a_narrowing_and_a_meeting(session):
    days = weekdays(date(2026, 9, 30), 400)
    spy = Security(ticker="SPY", origin="sec", security_type="ETF")
    session.add(spy)
    session.flush()
    for d in days:
        session.add(DailyBar(security_id=spy.id, date=d, close=500.0, source="tiingo"))
    n = len(days)

    def fred(series_id, values):
        session.add(EconomicSeries(id=series_id, source="fred"))
        session.flush()
        for d, v in zip(days, values, strict=True):
            session.add(EconomicVintage(series_id=series_id, date=d, realtime_start=d, value=v))

    # Yen 6.7% stronger over the last month; US 2-year down 0.7 points in three months.
    fred("DEXJPUS", [150.0 + (i % 5) * 0.1 if i < n - 21 else 140.0 for i in range(n)])
    fred("DGS2", [4.0 if i < n - 63 else 3.3 for i in range(n)])
    fred("DGS10", [4.2] * n)
    fred("DGS3MO", [4.3] * n)
    fred("IR3TIB01JPM156N", [0.5] * n)
    session.add_all(
        EconomicSeries(id=s, source="mof") for s in ("JGB2Y", "JGB10Y", "MOF_OUT_BONDS")
    )
    session.flush()
    for d in days:
        session.add(EconomicObservation(series_id="JGB2Y", date=d, value=1.0))
        session.add(EconomicObservation(series_id="JGB10Y", date=d, value=1.5))
    for k, d in enumerate(dd for dd in days if dd.weekday() == 4):
        session.add(EconomicObservation(series_id="MOF_OUT_BONDS", date=d, value=1000.0 + k))
    page = b"<table><caption>Table : 2026</caption><tbody><tr><td>Oct. 2 (Fri.)</td></tr>"
    releases.load_boj(session, page + b"</tbody></table>")
    session.commit()

    g = carry.gauge(session)
    r = {x.name: x for x in g.readings}
    assert g.as_of == date(2026, 9, 30) and g.next_boj == date(2026, 10, 2)
    assert round(r["diff_2y_pct"].value, 2) == 2.3 and round(r["diff_3m_pct"].value, 2) == 3.8
    assert (
        round(r["usd_jpy"].change_1m, 3) == round(140 / 150.4 - 1, 3)
        or r["usd_jpy"].change_1m < -0.06
    )
    assert r["carry_to_risk"].value is not None
    assert r["vix"].value is None  # not loaded: reported empty, not an error
    text = " | ".join(g.flags)
    assert "yen rallied" in text and "narrowed" in text and "BOJ decision on 2026-10-02" in text
    # Point in time: a month earlier, before the rally, no rally flag.
    assert not any("yen rallied" in f for f in carry.gauge(session, date(2026, 8, 31)).flags)
