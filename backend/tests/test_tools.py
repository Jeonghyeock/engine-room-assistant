"""실제 MCP 서버 두 개를 자식 프로세스로 띄워 확인한다(CI에서도 돈다 — docs 서버는 색인이 없어도 뜬다).

서버를 띄우는 데 몇 초씩 걸려서, 같은 시나리오에서 볼 것은 한 번 띄워 몰아서 본다.
"""

import asyncio
import json

import pytest

from app.tools import ToolBox, _clean_schema


@pytest.fixture(scope="module")
def s2():
    async def go():
        async with ToolBox("S2") as box:
            return {
                "names": box.names,
                "tools": box.ollama_tools(),
                "read": await box.call("read_sensors", {"tags": ["ME_LO_PRESS_IN"]}),
                "bad_tag": await box.call("read_sensors", {"tags": ["LO_PRESS"]}),
                "bad_tool": await box.call("open_valve", {}),
                "search": await box.call("search_docs", {"query": "윤활유 압력 낮음", "source": "manual"}),
            }

    return asyncio.run(go())


def test_both_servers_tools_in_one_box(s2):
    assert sorted(s2["names"]) == ["get_trend", "list_active_alarms", "list_tags", "read_sensors", "search_docs"]
    assert all(t["type"] == "function" and t["function"]["parameters"]["type"] == "object" for t in s2["tools"])
    assert "title" not in json.dumps(s2["tools"])


def test_call_returns_the_servers_object(s2):
    r = s2["read"]
    assert r.ok and r.data["sensors"][0]["value"] == 1.62 and r.error is None


def test_tool_errors_come_back_as_text_for_the_model(s2):
    assert not s2["bad_tag"].ok and "비슷한 태그: ME_LO_PRESS_IN" in s2["bad_tag"].error
    assert not s2["bad_tool"].ok and "쓸 수 있는 도구" in s2["bad_tool"].error and "read_sensors" in s2["bad_tool"].error


def test_search_tool_answers_or_says_why_not(s2):
    r = s2["search"]
    # 색인·Ollama가 있으면 결과, 없으면(CI) 읽을 수 있는 이유
    assert (r.ok and len(r.data["results"]) == 5) or ("색인" in r.error or "Ollama" in r.error)


def test_scenario_argument_reaches_the_ams_server():
    async def go():
        async with ToolBox("S3") as box:
            return await box.call("list_active_alarms", {})

    assert {a["tag"] for a in asyncio.run(go()).data["alarms"]} == {"ME_JCW_TEMP_OUT"}


def test_unknown_scenario_fails_to_open():
    async def go():
        async with ToolBox("S9"):
            pass

    with pytest.raises(Exception):
        asyncio.run(go())


def test_clean_schema_drops_titles_only():
    s = {"title": "A", "type": "object", "properties": {"tag": {"title": "Tag", "type": "string", "description": "d"}}}
    assert _clean_schema(s) == {"type": "object", "properties": {"tag": {"type": "string", "description": "d"}}}
