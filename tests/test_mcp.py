import asyncio

from plainontology.mcp_server import build_server
from plainontology.textformat import load


def _call(server, name, args=None):
    result = asyncio.run(server.call_tool(name, args or {}))
    if result.structured_content is not None and "result" not in result.structured_content:
        return result.structured_content
    return "\n".join(block.text for block in result.content)


def _tools(server):
    return {t.name for t in asyncio.run(server.list_tools())}


def test_read_tools(examples):
    server = build_server(examples / "library.onto")
    assert _tools(server) == {"list_concepts", "describe_concept", "search", "validate_record", "drift"}
    assert "Book: A title the library holds" in _call(server, "list_concepts")
    described = _call(server, "describe_concept", {"name": "book"})
    assert "concept Book" in described and "Loan.book -> Book" in described
    assert "Inherited:" not in described
    assert _call(server, "search", {"query": "checkout"}).startswith("Loan")
    assert "0 change(s) from the sources" in _call(server, "drift")


def test_validate_record_tool(examples):
    server = build_server(examples / "library.onto")
    ok = _call(server, "validate_record", {"concept": "ReserveBook", "record": {"book_id": 1, "member": "C-1"}})
    assert ok == {"ok": True, "issues": []}
    bad = _call(server, "validate_record", {"concept": "ReserveBook", "record": {"book_id": "x"}})
    assert bad["ok"] is False and len(bad["issues"]) == 2


def test_proposals_are_opt_in_and_tagged(examples):
    path = examples / "library.onto"
    assert "propose" not in _tools(build_server(path))
    server = build_server(path, allow_proposals=True)
    msg = _call(server, "propose", {"concept": "Loan", "line": 'synonym "Borrowing"', "reason": "seen in chat logs"})
    assert msg == 'Proposed for Loan: synonym "Borrowing" [proposed: seen in chat logs]'
    assert load(path).concepts["Loan"].statements[-1].is_proposed
    assert _call(server, "propose", {"concept": "Loan", "line": "not valid"}).startswith("Not added")
    assert _call(server, "propose", {"concept": "Bad name", "line": 'synonym "x"'}).startswith("Not added")
