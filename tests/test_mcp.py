import asyncio
import json
from datetime import date

import pytest
from conftest import TEST_DATABASE_URL
from mcp.server.mcpserver.exceptions import ToolError

from fin_intel.db import get_engine
from fin_intel.mcp_server import server
from fin_intel.models import Bill, GraphEdge, GraphNode, Issuer, Security


@pytest.fixture
def db(engine, session, monkeypatch, tmp_path):
    """The tools open their own sessions on the configured database: point it at the test
    one."""
    monkeypatch.setenv("FI_DATABASE_URL", TEST_DATABASE_URL)
    get_engine.cache_clear()
    session.add(Issuer(cik=1, name="Aero"))
    session.flush()
    session.add_all(
        [
            Security(ticker="AERO", cik=1),
            Bill(
                id="119-hr-1",
                congress=119,
                type="hr",
                number=1,
                introduced=date(2026, 6, 2),
                title="Munitions Act",
                policy_area="Health",
            ),
            GraphNode(key="theme:defense", kind="theme", label="Defense"),
            GraphNode(key="theme:critical_minerals", kind="theme", label="Minerals"),
            GraphEdge(
                src="theme:critical_minerals",
                dst="theme:defense",
                kind="supplies",
                source="bea",
                buyer_share=0.05,
                supplier_share=0.1,
            ),
        ]
    )
    session.commit()
    yield
    get_engine().dispose()
    get_engine.cache_clear()


def call(name, **arguments):
    result = asyncio.run(server.call_tool(name, arguments))
    assert not result.is_error
    return [json.loads(c.text) for c in result.content]


def test_tools_are_read_only_and_described():
    tools = asyncio.run(server.list_tools())
    assert {t.name for t in tools} >= {
        "company_dossier",
        "theme_dossier",
        "timeline",
        "supply_chain",
        "search_filings",
        "timeseries",
    }
    assert all(t.annotations.read_only_hint and t.description for t in tools)


def test_calls(db):
    themes = call("list_themes")
    assert {"name": "defense", "label": "Defense and aerospace"} in themes
    (walk,) = call("supply_chain", node="defense", direction="upstream", hops=1)
    assert walk["upstream"][0]["node"] == "theme:critical_minerals"
    events = call("timeline", theme="defense", kinds=["bill"], start="2026-06-01", end="2026-06-30")
    assert events[0]["ref"] == "bills:119-hr-1"
    (dossier,) = call("company_dossier", ticker="AERO", as_of="2026-06-30")
    assert dossier["identity"]["ticker"] == "AERO"


def test_errors_reach_the_agent(db):
    with pytest.raises(ToolError, match="no graph node"):
        asyncio.run(server.call_tool("supply_chain", {"node": "nope"}))
    with pytest.raises(ToolError, match="unknown company"):
        asyncio.run(server.call_tool("company_dossier", {"ticker": "NOPE"}))
