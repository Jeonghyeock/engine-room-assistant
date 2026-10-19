import asyncio

import pytest

from app.host import ToolHost
from app.tools import ToolResult

log: list[tuple] = []


class FakeBox:
    """여닫은 태스크와 순서를 적어 두는 가짜 상자."""

    fail_open: set[str] = set()

    def __init__(self, scenario: str):
        self.scenario = scenario

    async def __aenter__(self):
        if self.scenario in FakeBox.fail_open:
            raise RuntimeError(f"{self.scenario} 서버를 띄우지 못함")
        log.append(("open", self.scenario, asyncio.current_task()))
        return self

    async def __aexit__(self, *exc):
        log.append(("close", self.scenario, asyncio.current_task()))

    def ollama_tools(self):
        return [{"type": "function", "function": {"name": f"tool_{self.scenario}"}}]

    async def call(self, name, arguments):
        return ToolResult(True, data={"scenario": self.scenario, "name": name})


@pytest.fixture(autouse=True)
def _reset():
    log.clear()
    FakeBox.fail_open = set()


def test_one_task_opens_and_closes_every_box_and_calls_go_to_the_current_one():
    async def go():
        host = ToolHost(FakeBox)
        await host.start("S1")
        assert (await host.call("read_sensors", {})).data == {"scenario": "S1", "name": "read_sensors"}
        await host.switch("S3")
        assert host.scenario == "S3" and host.ollama_tools()[0]["function"]["name"] == "tool_S3"
        assert (await host.call("x", {})).data["scenario"] == "S3"
        await host.stop()
        return asyncio.current_task()

    caller = asyncio.run(go())
    assert [(a, s) for a, s, _ in log] == [("open", "S1"), ("close", "S1"), ("open", "S3"), ("close", "S3")]
    owners = {t for _, _, t in log}
    assert len(owners) == 1 and caller not in owners  # 요청 쪽이 아니라 주인 태스크 하나가 여닫는다(#8 리뷰)


def test_unknown_scenario_is_refused_without_closing_the_open_box():
    async def go():
        host = ToolHost(FakeBox)
        await host.start("S2")
        with pytest.raises(ValueError, match="모르는 시나리오"):
            await host.switch("S9")
        assert host.scenario == "S2" and (await host.call("x", {})).ok
        await host.stop()

    asyncio.run(go())
    assert [(a, s) for a, s, _ in log] == [("open", "S2"), ("close", "S2")]


def test_failed_open_reaches_the_caller_and_the_host_keeps_serving_later_switches():
    FakeBox.fail_open = {"S3"}

    async def go():
        host = ToolHost(FakeBox)
        await host.start("S1")
        with pytest.raises(RuntimeError, match="S3 서버를 띄우지 못함"):
            await host.switch("S3")
        res = await host.call("x", {})  # 열린 상자가 없다 — 모델이 읽을 오류로
        assert not res.ok and "열려 있지 않다" in res.error
        await host.switch("S2")
        assert (await host.call("x", {})).data["scenario"] == "S2"
        await host.stop()

    asyncio.run(go())
    assert [(a, s) for a, s, _ in log] == [("open", "S1"), ("close", "S1"), ("open", "S2"), ("close", "S2")]


def test_switch_before_start_is_an_error():
    async def go():
        with pytest.raises(RuntimeError, match="start"):
            await ToolHost(FakeBox).switch("S1")

    asyncio.run(go())
