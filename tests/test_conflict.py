import gzip
from datetime import date

import pytest
import respx
from sqlalchemy import select

from fin_intel import conflict
from fin_intel.config import get_settings
from fin_intel.models import ConflictEvent, RawResponse
from fin_intel.providers import AcledProvider
from fin_intel.providers.errors import NotConfiguredError


def event(n, day="2026-03-05", sub="Air/drone strike", fatalities="2"):
    return {
        "event_id_cnty": f"UKR{n}",
        "event_date": day,
        "disorder_type": "Political violence",
        "event_type": "Explosions/Remote violence",
        "sub_event_type": sub,
        "actor1": "Military Forces of Russia (2000-)",
        "actor2": "Civilians (Ukraine)",
        "interaction": "17",
        "country": "Ukraine",
        "iso": "804",
        "admin1": "Kharkiv",
        "location": "Kharkiv",
        "latitude": "49.99",
        "longitude": "36.23",
        "fatalities": fatalities,
        "notes": "On 5 March 2026, Russian forces struck Kharkiv with Shahed drones.",
        "timestamp": "1773000000",
    }


@pytest.fixture
def account(monkeypatch):
    monkeypatch.setenv("FI_ACLED_EMAIL", "someone@example.com")
    monkeypatch.setenv("FI_ACLED_PASSWORD", "secret-password")
    get_settings.cache_clear()


@respx.mock
def test_sync_month_pages_and_keeps_the_password_out_of_the_raw_store(
    session, raw_store, account, monkeypatch
):
    monkeypatch.setattr("fin_intel.providers.acled.PAGE", 2)
    monkeypatch.setattr(conflict, "PAGE", 2)
    token = respx.post("https://acleddata.com/oauth/token").respond(
        json={"access_token": "tok", "refresh_token": "r"}
    )
    read = respx.get("https://acleddata.com/api/acled/read")
    read.side_effect = [
        respx.MockResponse(200, json={"success": True, "data": [event(1), event(2)]}),
        respx.MockResponse(200, json={"success": True, "data": [event(3, sub="Shelling")]}),
    ]
    acled = AcledProvider(raw_store=raw_store)
    assert conflict.sync_month(session, acled, date(2026, 3, 1)) == 3
    assert token.call_count == 1  # one login, reused across pages
    first = read.calls[0].request
    assert first.headers["Authorization"] == "Bearer tok"
    assert first.url.params["event_date"] == "2026-03-01|2026-03-31"
    assert [c.request.url.params["page"] for c in read.calls] == ["1", "2"]
    recorded = session.scalars(select(RawResponse.provider)).all()
    assert set(recorded) == {"acled"} and len(recorded) == 2  # the two pages, not the login
    for body in raw_store.root.rglob("*.gz"):
        assert b"secret-password" not in gzip.decompress(body.read_bytes())
    drone = session.get(ConflictEvent, "UKR1")
    assert drone.sub_event_type == "Air/drone strike" and drone.fatalities == 2
    assert drone.iso == 804 and drone.updated.year == 2026

    # Revisions overwrite the event.
    read.side_effect = [
        respx.MockResponse(200, json={"success": True, "data": [event(1, fatalities="5")]})
    ]
    conflict.sync_month(session, acled, date(2026, 3, 1))
    session.refresh(drone)
    assert drone.fatalities == 5


def test_months_read_once_except_recent(session, monkeypatch):
    from fin_intel.models import SyncState

    monkeypatch.setattr(conflict, "START", date(2026, 1, 1))
    session.add_all(
        SyncState(
            provider="acled",
            dataset="events",
            key=k,
            last_attempt=f"{k}-28T00:00:00Z",
            last_success=f"{k}-28T00:00:00Z",
        )
        for k in ("2026-01", "2026-02", "2026-03")
    )
    session.commit()
    # Today is May: January and February are done; March, April and May are recent.
    assert conflict.wanted(session, date(2026, 5, 10)) == [
        date(2026, 3, 1),
        date(2026, 4, 1),
        date(2026, 5, 1),
    ]
    assert conflict.available_on(date(2026, 3, 5)) == date(2026, 3, 12)


def test_login_needs_the_account():
    with pytest.raises(NotConfiguredError):
        AcledProvider().headers()
