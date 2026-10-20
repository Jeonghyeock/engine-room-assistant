import asyncio
import logging

import anyio
import pytest

from app import host as host_module
from app.host import ToolHost
from app.tools import ToolResult

log: list[tuple] = []


class FakeBox:
    """여닫은 태스크와 순서를 적어 두는 가짜 상자. MCP 클라이언트처럼 anyio CancelScope를 연 채로 있어,
    다른 태스크에서 닫으면 anyio가 RuntimeError를 낸다(#8 리뷰의 조건을 anyio가 직접 검사)."""

    delay: dict[str, float] = {}  # 여는 데 걸리는 시간
    close_delay: dict[str, float] = {}  # 닫는 데 걸리는 시간
    fail_new: set[str] = set()  # 만들기(생성자)부터 실패
    fail_open: set[str] = set()
    fail_close: set[str] = set()

    def __init__(self, scenario: str):
        if scenario in FakeBox.fail_new:
            raise RuntimeError(f"{scenario} 상자를 만들지 못함")
        self.scenario = scenario
        self._scope = anyio.CancelScope()

    async def __aenter__(self):
        await asyncio.sleep(FakeBox.delay.get(self.scenario, 0))
        if self.scenario in FakeBox.fail_open:
            raise RuntimeError(f"{self.scenario} 서버를 띄우지 못함")
        self._scope.__enter__()
        log.append(("open", self.scenario, asyncio.current_task()))
        return self

    async def __aexit__(self, *exc):
        await asyncio.sleep(FakeBox.close_delay.get(self.scenario, 0))  # 닫는 데도 틈이 있다
        self._scope.__exit__(None, None, None)
        log.append(("close", self.scenario, asyncio.current_task()))
        if self.scenario in FakeBox.fail_close:
            raise RuntimeError(f"{self.scenario} 서버를 닫다가 실패")

    def ollama_tools(self):
        return [{"type": "function", "function": {"name": f"tool_{self.scenario}"}}]

    async def call(self, name, arguments):
        return ToolResult(True, data={"scenario": self.scenario, "name": name})


@pytest.fixture(autouse=True)
def _reset():
    log.clear()
    FakeBox.delay, FakeBox.close_delay = {}, {}
    FakeBox.fail_new, FakeBox.fail_open, FakeBox.fail_close = set(), set(), set()


def opened_closed():
    return [(a, s) for a, s, _ in log]


async def until(cond):
    """시간이 아니라 조건을 기다린다(Windows 타이머 해상도는 약 15ms라 sleep(0.01)로 시점을 맞출 수 없다)."""
    while not cond():
        await asyncio.sleep(0)


def run(coro, timeout=5):
    """테스트가 멈추면 실패로 — 영원히 기다리는 길을 잡으려는 테스트들이다."""

    async def go():
        async with asyncio.timeout(timeout):
            return await coro

    return asyncio.run(go())


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

    caller = run(go())
    assert opened_closed() == [("open", "S1"), ("close", "S1"), ("open", "S3"), ("close", "S3")]
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

    run(go())
    assert opened_closed() == [("open", "S2"), ("close", "S2")]


def test_failed_open_reaches_the_caller_and_the_host_keeps_serving_later_switches():
    FakeBox.fail_open = {"S3"}

    async def go():
        host = ToolHost(FakeBox)
        await host.start("S1")
        with pytest.raises(RuntimeError, match="S3 서버를 띄우지 못함"):
            await host.switch("S3")
        res = await host.call("x", {})  # 앞 시나리오로 되돌리지 않는다 — 상자 없이 남고, 모델이 읽을 오류로
        assert not res.ok and "열려 있지 않다" in res.error and host.scenario is None
        await host.switch("S2")
        assert (await host.call("x", {})).data["scenario"] == "S2"
        await host.stop()

    run(go())
    assert opened_closed() == [("open", "S1"), ("close", "S1"), ("open", "S2"), ("close", "S2")]


def test_an_abandoned_switch_does_not_answer_the_next_one():
    """#19 리뷰: S2를 기다리다 그만둔 뒤 S3를 부르면, S2가 열린 신호에 깨어 S2인 채로 돌아왔다."""
    FakeBox.delay = {"S2": 0.1}

    async def go():
        host = ToolHost(FakeBox)
        await host.start("S1")
        abandoned = asyncio.create_task(host.switch("S2"))
        await until(lambda: ("close", "S1") in opened_closed())  # 주인이 S2를 여는 중
        abandoned.cancel()
        await host.switch("S3")
        state = host.scenario, (await host.call("x", {})).data["scenario"]
        await host.stop()
        return state

    assert run(go()) == ("S3", "S3")
    assert opened_closed() == [("open", "S1"), ("close", "S1"), ("open", "S2"), ("close", "S2"), ("open", "S3"), ("close", "S3")]


def test_a_switch_behind_stop_fails_instead_of_waiting_forever_and_stop_finishes():
    """#19 리뷰: 멈춤 뒤에 줄 선 바꾸기가 영원히 기다렸고, 바꾸기가 멈춤을 덮어써 stop()이 끝나지 않았다."""
    FakeBox.delay = {"S2": 0.1}

    async def go():
        host = ToolHost(FakeBox)
        await host.start("S1")
        first = asyncio.create_task(host.switch("S2"))
        await until(lambda: ("close", "S1") in opened_closed())  # 주인이 S2를 여는 중
        stopping = asyncio.create_task(host.stop())
        await asyncio.sleep(0)
        late = asyncio.create_task(host.switch("S3"))
        await stopping
        await first  # 멈춤 앞에 줄 섰던 바꾸기는 끝났다
        with pytest.raises(RuntimeError, match="멈췄다|돌고 있지 않다"):
            await late

    run(go())
    assert opened_closed() == [("open", "S1"), ("close", "S1"), ("open", "S2"), ("close", "S2")]


def test_failed_close_is_logged_and_the_next_scenario_still_opens(caplog):
    FakeBox.fail_close = {"S1"}

    async def go():
        host = ToolHost(FakeBox)
        await host.start("S1")
        await host.switch("S2")
        scenario = host.scenario
        await host.stop()
        return scenario

    with caplog.at_level(logging.WARNING, logger="app.host"):
        assert run(go()) == "S2"
    assert "S1 서버를 닫다가 실패" in caplog.text


def test_open_that_hangs_times_out(monkeypatch):
    monkeypatch.setattr(host_module, "OPEN_TIMEOUT", 0.05)
    FakeBox.delay = {"S2": 10}

    async def go():
        host = ToolHost(FakeBox)
        await host.start("S1")
        with pytest.raises(TimeoutError, match="S2.*열지 못했다"):
            await host.switch("S2")
        await host.switch("S3")
        scenario = host.scenario
        await host.stop()
        return scenario

    assert run(go()) == "S3"


def test_owner_cancelled_mid_open_wakes_the_waiter_and_stop_returns():
    FakeBox.delay = {"S2": 0.1}

    async def go():
        host = ToolHost(FakeBox)
        await host.start("S1")
        waiting = asyncio.create_task(host.switch("S2"))
        await until(lambda: ("close", "S1") in opened_closed())  # 주인이 S2를 여는 중
        host._task.cancel()  # 서버 종료 등으로 주인이 취소됨
        with pytest.raises(RuntimeError, match="멈췄다"):
            await waiting
        await host.stop()  # 이미 끝난 주인 — 예외 없이 돌아온다

    run(go())
    assert opened_closed() == [("open", "S1"), ("close", "S1")]  # S1은 주인 태스크에서 닫혔다(anyio가 검사)


def test_start_twice_and_switch_before_start_are_errors():
    async def go():
        with pytest.raises(RuntimeError, match="돌고 있지 않다"):
            await ToolHost(FakeBox).switch("S1")
        host = ToolHost(FakeBox)
        with pytest.raises(ValueError):
            await host.start("S9")  # 주인을 띄우기 전에 거절 — 다음 start가 "이미 돌고 있다"로 막히지 않는다
        await host.start("S1")
        with pytest.raises(RuntimeError, match="이미 돌고"):
            await host.start("S2")
        await host.stop()

    run(go())


def test_a_request_cancelled_while_queued_is_skipped():
    """S2를 여는 동안 줄 선 S3 요청을 그만두면, 주인은 그 요청을 건너뛰고 S2를 그대로 둔다(#19 리뷰 2차)."""
    FakeBox.delay = {"S2": 0.1}

    async def go():
        host = ToolHost(FakeBox)
        await host.start("S1")
        opening = asyncio.create_task(host.switch("S2"))
        await until(lambda: ("close", "S1") in opened_closed())  # 주인이 S2를 여는 중
        queued = asyncio.create_task(host.switch("S3"))
        await asyncio.sleep(0)  # 줄에 선다
        queued.cancel()
        await opening
        await until(lambda: host._requests.empty())  # 주인이 그 요청을 집어 들어 보고 건너뛰었다
        state = host.scenario
        await host.stop()
        return state

    assert run(go()) == "S2"
    assert ("open", "S3") not in opened_closed()


def test_stop_finishes_closing_even_if_its_caller_is_cancelled():
    FakeBox.close_delay = {"S1": 0.1}

    async def go():
        host = ToolHost(FakeBox)
        await host.start("S1")
        stopping = asyncio.create_task(host.stop())
        await until(lambda: host._box is None)  # 닫기 시작
        stopping.cancel()
        await until(lambda: ("close", "S1") in opened_closed())  # 주인은 취소되지 않고 끝까지 닫는다(멈추면 run의 시간 제한)
        await host.stop()  # 끝난 주인 — 돌아온다
        assert host._task is None

    run(go())


def test_calls_during_closing_do_not_reach_the_closing_box():
    FakeBox.close_delay = {"S1": 0.1}

    async def go():
        host = ToolHost(FakeBox)
        await host.start("S1")
        switching = asyncio.create_task(host.switch("S2"))
        await until(lambda: host._box is None)
        assert ("close", "S1") not in opened_closed()  # 아직 닫는 중
        res = await host.call("x", {})
        await switching
        await host.stop()
        return res

    res = run(go())
    assert not res.ok and "열려 있지 않다" in res.error


def test_a_box_that_cannot_be_made_is_reported_like_a_failed_open(caplog):
    FakeBox.fail_new = {"S2"}

    async def go():
        host = ToolHost(FakeBox)
        await host.start("S1")
        with pytest.raises(RuntimeError, match="S2 상자를 만들지 못함"):
            await host.switch("S2")
        await host.switch("S3")  # 주인은 살아 있다
        state = host.scenario
        await host.stop()
        return state

    with caplog.at_level(logging.WARNING, logger="app.host"):
        assert run(go()) == "S3"
    assert "S2" in caplog.text and "열지 못했다" in caplog.text
