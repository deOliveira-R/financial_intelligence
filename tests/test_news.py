from datetime import date

import respx
from sqlalchemy import select

from fin_intel import news
from fin_intel.models import NewsVolume
from fin_intel.providers import GdeltProvider


def timeline(*points):
    return {
        "timeline": [
            {
                "series": "Article Count",
                "data": [
                    {"date": f"{d:%Y%m%d}T000000Z", "value": n, "norm": t} for d, n, t in points
                ],
            }
        ]
    }


@respx.mock
def test_sync_reads_past_years_once_and_refetches_edited_queries(session, raw_store, monkeypatch):
    monkeypatch.setattr(news, "FIRST_YEAR", 2025)
    monkeypatch.setattr(news, "QUERIES", {"drone_warfare": ("warfare", '"drone strike"')})
    route = respx.get("https://api.gdeltproject.org/api/v2/doc/doc")
    route.side_effect = lambda request: respx.MockResponse(
        200,
        json=timeline((date(int(request.url.params["startdatetime"][:4]), 3, 1), 30, 100_000)),
    )
    gdelt = GdeltProvider(raw_store=raw_store)
    today = date(2026, 6, 1)
    news.sync_query(session, gdelt, "drone_warfare", today)
    news.sync_query(session, gdelt, "drone_warfare", today)
    # 2025 once, 2026 (the current year) on both runs.
    years = [c.request.url.params["startdatetime"][:4] for c in route.calls]
    assert years == ["2025", "2026", "2026"]
    assert route.calls[0].request.url.params["query"] == '"drone strike"'
    assert route.calls[1].request.url.params["enddatetime"] == "20260601235959"
    rows = session.scalars(select(NewsVolume).order_by(NewsVolume.date)).all()
    assert [(r.date, r.articles, r.total) for r in rows] == [
        (date(2025, 3, 1), 30, 100_000),
        (date(2026, 3, 1), 30, 100_000),
    ]
    # An edited query refetches its past years.
    monkeypatch.setattr(news, "QUERIES", {"drone_warfare": ("warfare", '"drone attack"')})
    news.sync_query(session, gdelt, "drone_warfare", today)
    assert len(route.calls) == 5

    # The series: articles per million monitored, known the next day.
    days = [date(2025, 3, 1), date(2025, 3, 2)]
    assert news._spec(session, "drone_warfare", days, pit=True) == [None, 300.0]
    assert news._spec(session, "drone_warfare", days, pit=False) == [300.0, 300.0]
