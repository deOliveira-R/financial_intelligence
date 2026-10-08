from datetime import date

import httpx
import respx
from sqlalchemy import func, select

from fin_intel import ingest, presidential
from fin_intel.models import PresidentialDocument, PresidentialText
from fin_intel.providers import FederalRegisterProvider
from fin_intel.rebuild import rebuild

TEXT_URL = "https://www.federalregister.gov/documents/full_text/text/2025/04/15/2025-06063.txt"
BODY = b"""<html><head><title>FR</title></head><body><pre>
[FR Doc No: 2025-06063]
                Proclamation 10908 of March 26, 2025
Adjusting Imports of Automobiles and Automobile Parts Into the United States
By the President: under section 232 of the Trade Expansion Act of 1962 &amp; other laws,
a tariff of 25 percent on imported automobiles ...
</pre></body></html>"""


def listing(kind):
    if kind != "proclamation":
        return {"count": 0, "results": []}
    return {
        "count": 1,
        "results": [
            {
                "document_number": "2025-06063",
                "presidential_document_type": "proclamation",
                "proclamation_number": "10908",
                "executive_order_number": None,
                "title": "Adjusting Imports of Automobiles and Automobile Parts",
                "president": {"name": "Donald Trump"},
                "signing_date": "2025-03-26",
                "publication_date": "2025-04-03",
                "disposition_notes": "Amended by: Proclamation 10925",
                "raw_text_url": TEXT_URL,
            }
        ],
    }


@respx.mock
def test_sync_search_and_rebuild(session, raw_store):
    respx.get("https://www.federalregister.gov/api/v1/documents.json").mock(
        side_effect=lambda r: httpx.Response(
            200, json=listing(r.url.params["conditions[presidential_document_type]"])
        )
    )
    text = respx.get(TEXT_URL).respond(content=BODY)
    provider = FederalRegisterProvider(raw_store=raw_store)
    assert ingest.sync_presidential_year(session, provider, 2025) == 2
    ingest.sync_presidential_year(session, provider, 2025)
    assert text.call_count == 1  # stored text isn't fetched again

    doc = session.get(PresidentialDocument, "2025-06063")
    assert (doc.kind, doc.number, doc.signed) == ("proclamation", "10908", date(2025, 3, 26))
    stored = session.get(PresidentialText, "2025-06063").text
    assert stored.startswith("[FR Doc No: 2025-06063]") and "&amp;" not in stored

    hits = presidential.search(session, '"section 232" automobiles')
    assert [h.number for h in hits] == ["10908"] and "[Automobiles]" in hits[0].snippet
    assert presidential.search(session, "automobiles", kind="executive_order") == []

    def count():
        return session.scalar(select(func.count()).select_from(PresidentialText))

    with respx.mock:
        rebuild(session, raw_store, "presidential")
    assert count() == 1 and len(presidential.search(session, "tariff")) == 1
