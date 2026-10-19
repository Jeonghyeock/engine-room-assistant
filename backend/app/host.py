"""도구 상자 주인 — 웹 서버에서 ToolBox를 태스크 하나가 열고 닫는다.

    host = ToolHost()
    await host.start("S2")          # 주인 태스크가 상자를 연다
    await host.call("read_sensors", {...})
    await host.switch("S3")         # 주인이 상자를 닫고 S3로 다시 연다
    await host.stop()

MCP 클라이언트(anyio)는 상자를 연 태스크에서 닫아야 한다(#8 리뷰). 웹 요청은 저마다 다른 태스크에서 오므로,
여닫기는 주인 태스크 하나가 맡고 요청은 call()만 부른다. 에이전트에게는 ToolBox와 같은 모양이다(ollama_tools·call).
"""

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from ams.scenario import list_ids
from app.tools import ToolBox, ToolResult

log = logging.getLogger(__name__)


class ToolHost:
    def __init__(self, factory: Callable[[str], Any] = ToolBox):
        self._factory = factory  # 시나리오 → async with로 여는 상자(테스트에서 가짜로 바꿔 끼운다)
        self._box: Any = None
        self._want: str | None = None  # 주인이 열어야 할 시나리오. None이면 닫고 끝낸다
        self._changed = asyncio.Event()  # 요청 → 주인: 시나리오가 바뀌었다
        self._ready = asyncio.Event()  # 주인 → 요청: 상자를 열었다(또는 열지 못했다)
        self._error: BaseException | None = None
        self._switching = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self.scenario: str | None = None

    async def start(self, scenario: str) -> None:
        self._task = asyncio.create_task(self._run(), name="tool-host")
        await self.switch(scenario)

    async def switch(self, scenario: str) -> None:
        """시나리오를 바꾸고 새 상자가 열릴 때까지 기다린다. 열지 못하면 그 예외를 그대로 올린다."""
        if scenario not in list_ids():  # 주인에게 넘기기 전에 — 잘못된 이름으로 지금 상자를 닫지 않게
            raise ValueError(f"모르는 시나리오 {scenario!r} — {', '.join(list_ids())} 중 하나")
        if self._task is None or self._task.done():
            raise RuntimeError("도구 상자 주인이 돌고 있지 않다 — start()를 먼저 부른다")
        async with self._switching:
            self._ready.clear()
            self._error = None
            self._want = scenario
            self._changed.set()
            await self._ready.wait()
            if self._error is not None:
                raise self._error

    async def stop(self) -> None:
        if self._task is None:
            return
        self._want = None
        self._changed.set()
        await self._task
        self._task = None

    async def _run(self) -> None:
        while True:
            await self._changed.wait()
            self._changed.clear()
            scenario = self._want
            if scenario is None:
                return
            opened = False
            try:
                async with self._factory(scenario) as box:
                    opened = True
                    self._box, self.scenario = box, scenario
                    self._ready.set()
                    await self._changed.wait()  # 다음 바꾸기(또는 멈춤)까지 열어 둔다 — 닫기도 이 태스크에서
            except Exception as e:
                if opened:  # 닫다가 실패 — 기다리는 요청은 다음 시나리오를 기다리므로 알리지 않고 남기기만 한다
                    log.warning("도구 상자(%s)를 닫다가 실패: %r", scenario, e)
                else:  # 열지 못함 — 기다리는 요청에게 넘기고, 다음 바꾸기를 기다린다
                    self._error = e
                    self._ready.set()
            finally:
                self._box = None
                self.scenario = None

    # ---- 에이전트가 쓰는 모양(app.agent._Box) ----

    def ollama_tools(self) -> list[dict]:
        if self._box is None:
            raise RuntimeError("열린 도구 상자가 없다")
        return self._box.ollama_tools()

    async def call(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        if self._box is None:
            return ToolResult(False, error="도구 상자가 열려 있지 않다(시나리오를 바꾸는 중일 수 있다).")
        return await self._box.call(name, arguments)
