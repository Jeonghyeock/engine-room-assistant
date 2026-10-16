import asyncio
import sys
from pathlib import Path

from mcp import Client, StdioServerParameters

from ams.scenario import load
from ams.system import AlarmMonitoringSystem
from mcp_servers.ams_server import create_server

BACKEND = Path(__file__).resolve().parents[1]


def call(server, tool, args=None):
    async def go():
        async with Client(server) as c:
            return await c.call_tool(tool, args or {})

    return asyncio.run(go())


def test_every_tool_is_read_only():
    async def go():
        async with Client(create_server("S1")) as c:
            return (await c.list_tools()).tools

    tools = asyncio.run(go())
    assert {t.name for t in tools} == {"list_active_alarms", "list_tags", "read_sensors", "get_trend"}
    for t in tools:
        assert t.annotations.read_only_hint is True and t.annotations.destructive_hint is False, t.name


def test_alarms_pass_through_unchanged():
    r = call(create_server("S2"), "list_active_alarms")
    assert not r.is_error
    assert r.structured_content == {"alarms": AlarmMonitoringSystem(load("S2")).active_alarms()}
    assert len(r.content) == 1  # 결과는 글 조각 하나(목록이어도)


def test_unknown_tag_comes_back_as_readable_error():
    r = call(create_server("S2"), "read_sensors", {"tags": ["LO_PRESS"]})
    assert r.is_error
    assert "비슷한 태그: ME_LO_PRESS_IN" in r.content[0].text


def test_unknown_system_is_an_error_not_an_empty_list():
    r = call(create_server("S1"), "list_tags", {"system": "주기관"})
    assert r.is_error and "계통 코드 ME, LO, JCW, FO, GE" in r.content[0].text


def test_tool_descriptions_are_dedented_and_system_is_a_plain_string():
    async def go():
        async with Client(create_server("S1")) as c:
            return {t.name: t for t in (await c.list_tools()).tools}

    tools = asyncio.run(go())
    assert all("\n        " not in t.description for t in tools.values())
    # anyOf(string|null) 없이 문자열 하나 — Ollama 도구 정의로 옮기기 쉽게
    assert tools["list_tags"].input_schema["properties"]["system"]["type"] == "string"


def test_trend_window_is_bounded_by_schema():
    r = call(create_server("S3"), "get_trend", {"tag": "ME_JCW_TEMP_OUT", "minutes": 99})
    assert r.is_error


def test_stdio_launch_uses_the_scenario_argument():
    """실제로 쓰는 방식 — 자식 프로세스로 띄우고 stdin/stdout으로 주고받는다."""
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcp_servers.ams_server", "--scenario", "S3"],
        cwd=str(BACKEND),
    )
    r = call(params, "list_active_alarms")
    alarms = r.structured_content["alarms"]
    assert [(a["tag"], a["level"]) for a in alarms] == [("ME_JCW_TEMP_OUT", "감속"), ("ME_JCW_TEMP_OUT", "경보")]


def test_bad_scenario_from_env_is_rejected_cleanly():
    import os
    import subprocess

    env = {**os.environ, "AMS_SCENARIO": "S9", "PYTHONUTF8": "1"}
    p = subprocess.run([sys.executable, "-m", "mcp_servers.ams_server"], cwd=BACKEND, env=env, capture_output=True, text=True, timeout=60)
    assert p.returncode == 2 and "모르는 시나리오" in p.stderr and "Traceback" not in p.stderr
