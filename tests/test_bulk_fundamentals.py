import copy
import json
import zipfile

from conftest import COMPANY_FACTS
from sqlalchemy import func, select

from fin_intel import ingest
from fin_intel.models import Fact, Issuer, RawResponse
from fin_intel.rebuild import rebuild


def company(cik, accession_prefix):
    payload = copy.deepcopy(COMPANY_FACTS)
    payload["cik"], payload["entityName"] = cik, f"Company {cik}"
    for concept in payload["facts"]["us-gaap"].values():
        for facts in concept["units"].values():
            for f in facts:
                f["accn"] = f"{accession_prefix}-{f['accn']}"
    return payload


def write_zip(path, companies):
    with zipfile.ZipFile(path, "w") as archive:
        for cik, payload in companies.items():
            archive.writestr(f"CIK{cik:010d}.json", json.dumps(payload))
        archive.writestr("README.txt", "not a company")
    return path


def count(session, model):
    return session.scalar(select(func.count()).select_from(model))


def test_bulk_load_skips_unchanged_companies(session, raw_store, tmp_path):
    companies = {320193: company(320193, "A"), 789019: company(789019, "M")}
    path = write_zip(tmp_path / "bulk.zip", companies)

    assert ingest.load_bulk_company_facts(session, raw_store, path) == (2, 0)
    assert count(session, Fact) == 12 and count(session, Issuer) == 2
    raw = session.execute(select(RawResponse.dataset, RawResponse.key)).all()
    assert sorted(raw) == [("companyfacts", "320193"), ("companyfacts", "789019")]

    # The next night: nothing changed.
    assert ingest.load_bulk_company_facts(session, raw_store, path) == (0, 2)
    # Then one company files something new.
    companies[789019]["facts"]["us-gaap"]["Assets"]["units"]["USD"].append(
        {
            "end": "2025-09-27",
            "val": 400.0,
            "accn": "M-NEW",
            "form": "10-K",
            "filed": "2025-11-01",
            "fy": 2025,
            "fp": "FY",
        }
    )
    write_zip(path, companies)
    assert ingest.load_bulk_company_facts(session, raw_store, path) == (1, 1)
    assert count(session, Fact) == 13
    assert count(session, RawResponse) == 3


def test_bulk_load_only_tracked_issuers(session, raw_store, tmp_path):
    path = write_zip(tmp_path / "bulk.zip", {1: company(1, "X"), 2: company(2, "Y")})
    seen = []
    loaded = ingest.load_bulk_company_facts(
        session, raw_store, path, ciks={2}, progress=lambda n, total: seen.append((n, total))
    )
    assert loaded == (1, 0)
    assert session.scalars(select(Issuer.cik)).all() == [2]
    assert seen == [(1, 1)]


def test_bulk_loaded_facts_rebuild_from_raw(session, raw_store, tmp_path):
    path = write_zip(tmp_path / "bulk.zip", {320193: company(320193, "A")})
    ingest.load_bulk_company_facts(session, raw_store, path)
    before = session.execute(select(Fact.value, Fact.fiscal_year).order_by(Fact.value)).all()
    rebuild(session, raw_store, "fundamentals")
    assert (
        session.execute(select(Fact.value, Fact.fiscal_year).order_by(Fact.value)).all() == before
    )
