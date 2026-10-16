import asyncio

import httpx
import pytest
from mcp import Client

from mcp_servers.docs_server import create_server
from rag import store

ROW = {"id": "manual:매뉴얼 4.2", "source": "manual", "locator": "매뉴얼 4.2", "title": "4.2 입구 압력 낮음", "text": "…", "page": 3, "score": 0.74}


def call(server, args):
    async def go():
        async with Client(server) as c:
            return await c.call_tool("search_docs", args)

    return asyncio.run(go())


def test_tool_is_read_only_and_source_is_an_enum():
    async def go():
        async with Client(create_server(lambda *a, **k: [])) as c:
            return (await c.list_tools()).tools

    (tool,) = asyncio.run(go())
    assert tool.name == "search_docs" and tool.annotations.read_only_hint is True
    # null 없이 열거형 하나 — Ollama 도구 정의로 옮기기 쉽게
    assert tool.input_schema["properties"]["source"]["enum"] == ["all", "manual", "cfr"]
    assert "\n        " not in tool.description


def test_passes_arguments_and_returns_only_the_public_fields():
    seen = {}

    def fake(query, k, source):
        seen.update(query=query, k=k, source=source)
        return [{**ROW, "internal": "버림"}]

    r = call(create_server(fake), {"query": "윤활유 압력", "source": "manual"})
    assert seen == {"query": "윤활유 압력", "k": 5, "source": "manual"}  # 조각 수는 서버가 정한다
    assert r.structured_content == {"results": [ROW]}


def test_all_means_no_source_filter():
    seen = {}
    call(create_server(lambda q, k, source: seen.update(source=source) or []), {"query": "x"})
    assert seen == {"source": None}


def test_unknown_source_is_rejected_by_schema():
    assert call(create_server(lambda *a, **k: []), {"query": "x", "source": "web"}).is_error


@pytest.mark.parametrize(
    "exc, text",
    [
        (store.IndexUnavailableError("없음"), "색인이 없거나 손상"),
        (httpx.ConnectError("refused"), "연결하지 못해"),
        # 모델을 안 받아 둔 Ollama는 404로 답한다 — 연결 실패와 다르게 알려야 고칠 곳을 안다
        (httpx.HTTPStatusError("404", request=httpx.Request("POST", "http://x"), response=httpx.Response(404)), "오류 404"),
    ],
)
def test_environment_failures_become_readable_errors(exc, text):
    def broken(*a, **k):
        raise exc

    r = call(create_server(broken), {"query": "x"})
    assert r.is_error and text in r.content[0].text


def test_missing_index_fails_without_leaving_an_empty_folder(tmp_path, monkeypatch):
    missing = tmp_path / "index"
    monkeypatch.setattr(store, "INDEX_DIR", missing)
    with pytest.raises(store.IndexUnavailableError):
        store.search("x")
    assert not missing.exists()


def test_empty_index_folder_is_reported_as_damaged(tmp_path, monkeypatch):
    (tmp_path / "chunks.lance").mkdir()  # 폴더만 있고 표가 없다
    monkeypatch.setattr(store, "INDEX_DIR", tmp_path)
    with pytest.raises(store.IndexUnavailableError, match="손상"):
        store.search("x")


def test_rebuilt_index_is_seen_without_restarting(tmp_path, monkeypatch):
    """오래 도는 서버가 옛 색인을 붙들고 있지 않는다 — 다른 프로세스가 다시 만들면 다음 검색부터 새 색인."""
    monkeypatch.setattr(store, "INDEX_DIR", tmp_path)
    row = {"id": "a", "source": "manual", "locator": "매뉴얼 1.1", "title": "t", "text": "x", "page": 1, "vector": [1.0] + [0.0] * 1023}
    store.write([row])
    assert store.all_locators() == {"매뉴얼 1.1"}
    store.write([{**row, "locator": "매뉴얼 9.9"}])
    assert store.all_locators() == {"매뉴얼 9.9"}


def test_cfr_results_have_no_page():
    cfr_row = {**ROW, "id": "cfr:§62.30-1(b)", "source": "cfr", "locator": "§62.30-1(b)", "page": 0}
    (out,) = call(create_server(lambda *a, **k: [cfr_row]), {"query": "x"}).structured_content["results"]
    assert "page" not in out


def _index_and_ollama_ready() -> bool:
    try:
        store._table()
        httpx.get("http://127.0.0.1:11434/api/version", timeout=1).raise_for_status()
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _index_and_ollama_ready(), reason="색인(data/index)과 Ollama가 있어야 한다 — CI에서는 건너뜀")
def test_real_search_finds_failsafe_clause_in_english():
    r = call(create_server(), {"query": "failsafe behaviour of alarm systems", "source": "cfr"})
    assert "§62.30-1(b)" in [h["locator"] for h in r.structured_content["results"]][:3]
