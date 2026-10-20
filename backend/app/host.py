"""도구 상자 주인 — 웹 서버에서 ToolBox를 태스크 하나가 열고 닫는다.

    host = ToolHost()
    await host.start("S2")          # 주인 태스크가 상자를 연다
    await host.call("read_sensors", {...})
    await host.switch("S3")         # 주인이 상자를 닫고 S3로 다시 연다
    await host.stop()

MCP 클라이언트(anyio)는 상자를 연 태스크에서 닫아야 한다(#8 리뷰). 웹 요청은 저마다 다른 태스크에서 오므로,
여닫기는 주인 태스크 하나가 맡고 요청은 call()만 부른다. 에이전트에게는 ToolBox와 같은 모양이다(ollama_tools·call).

바꾸기·멈추기는 요청마다 Future를 만들어 큐 하나로 주인에게 보낸다. 주인은 받은 차례대로 처리하고 그 요청의
Future에만 결과를 넣는다 — 기다리던 쪽이 그만둔 요청이 다른 요청의 결과를 가로채지 않게(#19 리뷰).

정상 종료는 stop()이다. 주인 태스크 자체가 취소되면(이벤트 루프 정리 등) 열린 상자는 이 태스크에서 닫지만,
닫는 도중에 취소가 오면 닫기가 끊길 수 있다 — 다른 태스크로 감싸 끝까지 닫으면 "연 태스크에서 닫기"가 깨진다.
"""

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from ams.scenario import list_ids
from app.tools import ToolBox, ToolResult

log = logging.getLogger(__name__)

# 상자 하나를 여는 데 기다리는 시간. 실제로는 자식 프로세스 둘을 띄우는 데 1~3초 — 서버가 응답하지 않으면
# 주인이 영원히 막혀 그 뒤의 바꾸기·멈추기도 끝나지 않는다(#19 리뷰).
OPEN_TIMEOUT = 30.0


def _settle(fut: asyncio.Future, error: BaseException | None = None) -> None:
    if fut.done():  # 기다리던 쪽이 그만뒀다(취소)
        return
    if error is None:
        fut.set_result(None)
    else:
        fut.set_exception(error)


class ToolHost:
    def __init__(self, factory: Callable[[str], Any] = ToolBox):
        self._factory = factory  # 시나리오 → async with로 여는 상자(테스트에서 가짜로 바꿔 끼운다)
        self._box: Any = None
        self._requests: asyncio.Queue[tuple[str | None, asyncio.Future]] = asyncio.Queue()  # (시나리오 또는 None=멈춤, 결과)
        self._task: asyncio.Task | None = None
        self.scenario: str | None = None

    async def start(self, scenario: str) -> None:
        if self._task is not None and not self._task.done():
            raise RuntimeError("도구 상자 주인이 이미 돌고 있다")
        self._task = asyncio.create_task(self._run(), name="tool-host")
        await self.switch(scenario)

    async def switch(self, scenario: str) -> None:
        """시나리오를 바꾸고 새 상자가 열릴 때까지 기다린다. 열지 못하면 그 예외를 올리고, 상자 없이 남는다."""
        if scenario not in list_ids():  # 주인에게 넘기기 전에 — 잘못된 이름으로 지금 상자를 닫지 않게
            raise ValueError(f"모르는 시나리오 {scenario!r} — {', '.join(list_ids())} 중 하나")
        await self._ask(scenario)

    async def stop(self) -> None:
        task = self._task
        if task is None:
            return
        if task.done():  # 이미 끝난 주인(예상 못한 예외·취소) — 기다리던 요청은 그때 이미 깨웠다
            self._task = None
            if not task.cancelled() and task.exception() is not None:
                log.error("도구 상자 주인이 예외로 끝나 있었다: %r", task.exception())
            return
        self._requests.put_nowait((None, asyncio.get_running_loop().create_future()))
        try:
            await asyncio.shield(task)  # 부른 쪽이 취소돼도 주인은 상자를 끝까지 닫는다
        finally:
            if task.done():
                self._task = None

    async def _ask(self, scenario: str | None) -> None:
        if self._task is None or self._task.done():
            raise RuntimeError("도구 상자 주인이 돌고 있지 않다")
        fut = asyncio.get_running_loop().create_future()
        self._requests.put_nowait((scenario, fut))
        try:
            await asyncio.wait({fut, self._task}, return_when=asyncio.FIRST_COMPLETED)
        except asyncio.CancelledError:
            fut.cancel()  # 그만둔 요청 — 주인이 아직 집어 들지 않았으면 건너뛴다
            raise
        if not fut.done():
            raise RuntimeError("도구 상자 주인이 멈췄다")
        fut.result()  # 열지 못했으면 그 예외

    async def _next(self) -> tuple[str | None, asyncio.Future]:
        while True:
            request = await self._requests.get()
            if not request[1].cancelled():  # 기다리던 쪽이 그만둔 요청은 건너뛴다 — 지금 상자를 닫지 않는다
                return request

    async def _run(self) -> None:
        try:
            request = await self._next()
            while True:
                scenario, fut = request
                if scenario is None:
                    _settle(fut)
                    return
                request = await self._serve(scenario, fut) or await self._next()
        finally:
            # 멈춤·예상 못한 예외·취소 — 줄 서 있던 요청이 영원히 기다리지 않게 모두 끝낸다
            while not self._requests.empty():
                _settle(self._requests.get_nowait()[1], RuntimeError("도구 상자 주인이 멈췄다"))

    async def _serve(self, scenario: str, fut: asyncio.Future) -> tuple[str | None, asyncio.Future] | None:
        """상자 하나를 열어 다음 요청이 올 때까지 둔 뒤 닫는다(이 태스크에서). 다음 요청을 돌려준다. 열지 못하면 None."""
        cm = self._factory(scenario)
        try:
            async with asyncio.timeout(OPEN_TIMEOUT):
                box = await cm.__aenter__()
        except Exception as e:
            _settle(fut, e)
            return None
        try:
            self._box, self.scenario = box, scenario
            _settle(fut)
            return await self._next()  # 다음 바꾸기(또는 멈춤)까지 열어 둔다
        finally:
            self._box = self.scenario = None  # 닫기 전에 — 닫는 중인 상자로 call이 가지 않게
            try:
                await cm.__aexit__(None, None, None)
            except Exception as e:  # 닫다가 실패 — 다음 시나리오는 열어야 하므로 기록만 남긴다
                log.warning("도구 상자(%s)를 닫다가 실패: %r", scenario, e)

    # ---- 에이전트가 쓰는 모양(app.agent._Box) ----

    def ollama_tools(self) -> list[dict]:
        if self._box is None:
            raise RuntimeError("열린 도구 상자가 없다")
        return self._box.ollama_tools()

    async def call(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        if self._box is None:
            return ToolResult(False, error="도구 상자가 열려 있지 않다(시나리오를 바꾸는 중이거나 열지 못했다).")
        return await self._box.call(name, arguments)
