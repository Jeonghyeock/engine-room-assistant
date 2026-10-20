"""HTTP API — 화면이 쓰는 대화 스트림(SSE)과 알람·시나리오(설계 문서 6.5).

    uv run uvicorn app.api:app --port 8000 --timeout-graceful-shutdown 5

(종료 시간 제한이 없으면 Ctrl+C가 돌고 있는 차례가 끝날 때까지 기다린다 — 느린 GPU에서는 몇 분)

- POST /api/chat {conversation_id?, message} → text/event-stream
  첫 이벤트는 conversation {id, scenario}(새 대화의 번호를 화면이 알 수 있게), 이어서 step_start·step_end·token·citation,
  끝은 done 또는 error.
- GET /api/alarms · GET /api/scenarios · POST /api/scenarios/{id}/start(대화를 모두 지운다)

한 번에 한 차례만 돈다(GPU 하나에 모델 하나) — 돌고 있으면 409(동시에 들어오면 200 + 첫 이벤트 error busy).
화면이 스트림을 끊으면(중단 버튼) 그 차례는 되돌린다(run_turn의 finally). 다만 모델이 이미 답을 끝낸 뒤에 끊으면
그 차례는 받아들인 답으로 남는다. 대화는 이 프로세스 메모리에만 있다.

끊김 감지는 Starlette가 ASGI 2.3 서버(uvicorn 0.54)에서 http.disconnect를 듣고 응답 태스크를 취소하는 데 기댄다.
ASGI 2.4 서버로 바꾸면 Starlette는 끊김을 듣지 않으므로 중단을 다시 확인해야 한다(#20 리뷰).
"""

import asyncio
import dataclasses
import functools
import json
import logging
import uuid
from contextlib import aclosing, asynccontextmanager

import anyio
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ams.scenario import list_ids, load
from app.agent import Citation, Conversation, Done, Error, StepEnd, StepStart, Token, run_turn
from app.host import ToolHost
from app.llm import ChatOptions, stream_chat
from app.tools import ToolBox

log = logging.getLogger(__name__)

DEFAULT_SCENARIO = "S1"
EVENT_NAMES = {StepStart: "step_start", StepEnd: "step_end", Token: "token", Citation: "citation", Done: "done", Error: "error"}
BUSY = "다른 질문에 답하는 중이다 — 끝나거나 중단한 뒤에 다시 보낸다"
NO_TOOLS = "도구 서버가 열려 있지 않다 — 시나리오를 다시 고른다"
UNKNOWN = "모르는 대화다 — 시나리오를 바꾸면 대화가 지워진다"


class ChatRequest(BaseModel):
    conversation_id: str | None = None
    message: str = Field(min_length=1, pattern=r"\S")  # 공백뿐인 질문은 422


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def event_data(ev) -> dict:
    data = dataclasses.asdict(ev)
    if isinstance(ev, Done):
        data["context_near_limit"] = ev.context_near_limit  # 속성이라 asdict에 들어가지 않는다
    return data


async def pump(stream, queue: asyncio.Queue) -> None:
    """차례 하나의 이벤트를 큐로 옮긴다. 끝나면 None."""
    try:
        async with aclosing(stream):
            async for ev in stream:
                queue.put_nowait(ev)
    except Exception as e:  # 프로그램 오류 — 스트림이 말없이 끊기지 않게 화면에 알린다
        log.exception("차례를 처리하다 실패")
        queue.put_nowait(Error("internal", repr(e)))
    finally:
        queue.put_nowait(None)


@functools.cache
def _scenario_list() -> tuple[dict, ...]:
    return tuple({"id": s.id, "title": s.title, "summary": s.summary} for s in map(load, list_ids()))


def create_app(llm=stream_chat, box_factory=ToolBox, scenario: str = DEFAULT_SCENARIO, opts: ChatOptions | None = None) -> FastAPI:
    """llm·box_factory는 테스트와 E2E가 가짜로 바꿔 끼운다."""
    host = ToolHost(box_factory)
    conversations: dict[str, Conversation] = {}
    turn = asyncio.Lock()  # 대화 한 차례 또는 시나리오 바꾸기

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await host.start(scenario)
        try:
            yield
        finally:
            await host.stop()

    app = FastAPI(title="engine-room-assistant", lifespan=lifespan)
    app.state.host, app.state.conversations, app.state.turn = host, conversations, turn

    @app.post("/api/chat")
    async def chat(req: ChatRequest) -> StreamingResponse:
        cid = req.conversation_id or uuid.uuid4().hex
        if req.conversation_id is not None and cid not in conversations:
            raise HTTPException(404, UNKNOWN)
        if turn.locked():
            raise HTTPException(409, BUSY)
        if host.scenario is None:
            raise HTTPException(503, NO_TOOLS)

        async def events():
            if turn.locked():  # 위의 확인과 여기 사이에 다른 요청이 먼저 시작한 경우
                yield sse("error", {"code": "busy", "message": BUSY})
                return
            async with turn:  # 시나리오 바꾸기도 이 잠금을 잡는다 — 차례 도중에 도구 상자가 바뀌지 않는다
                if host.scenario is None:
                    yield sse("error", {"code": "no_tools", "message": NO_TOOLS})
                    return
                if req.conversation_id is not None and cid not in conversations:  # 확인한 뒤 시나리오를 바꾼 경우
                    yield sse("error", {"code": "unknown_conversation", "message": UNKNOWN})
                    return
                conv = conversations.setdefault(cid, Conversation())
                yield sse("conversation", {"id": cid, "scenario": host.scenario})
                queue: asyncio.Queue = asyncio.Queue()
                producer = asyncio.create_task(pump(run_turn(conv, req.message, host, opts, llm=llm), queue))
                last = None
                try:
                    while (ev := await queue.get()) is not None:
                        last = ev
                        yield sse(EVENT_NAMES[type(ev)], event_data(ev))
                    if not isinstance(last, (Done, Error)):  # 끝은 늘 done 또는 error(설계 6.5) — 차례가 Exception
                        # 아닌 예외(예: 안에서 생긴 취소)로 끝나면 pump가 오류 이벤트를 못 넣는다
                        yield sse("error", {"code": "internal", "message": "차례가 done 없이 끝났다"})
                finally:
                    # 끊기면(중단 버튼) Starlette가 이 태스크를 취소하고, 정리하는 await까지 거듭 취소한다. 차례를 여기서
                    # 돌리면 차례 안의 정리(모델 스트림 닫기 등)가 중간에 끊겼다(test_api). 진짜 Ollama로는 그래도 끊은
                    # 시각에 요청이 끝났지만(httpx가 소켓은 닫음), 정리 코드가 끝까지 도는 것을 운에 맡기지 않는다 —
                    # 차례는 따로 태스크에서 돌리고, 한 번만 취소한 뒤 정리가 끝나야 잠금을 푼다.
                    producer.cancel()
                    with anyio.CancelScope(shield=True):
                        await asyncio.wait([producer])
                    if not producer.cancelled() and producer.exception() is not None:
                        log.error("차례가 예외로 끝났다: %r", producer.exception())

        return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    @app.get("/api/alarms")
    async def alarms() -> dict:
        scenario = host.scenario  # 호출 전에 — 그사이 시나리오가 바뀌면 옛 알람에 새 이름을 붙이게 된다(#20 리뷰)
        res = await host.call("list_active_alarms", {})
        if not res.ok or host.scenario != scenario:
            raise HTTPException(503, res.error or "시나리오를 바꾸는 중이다")
        return {"scenario": scenario, **res.data}

    @app.get("/api/scenarios")
    async def scenarios() -> dict:
        return {
            "current": host.scenario,
            "scenarios": list(_scenario_list()),  # 시나리오를 펼치는 데 약 20ms — 한 번만
        }

    @app.post("/api/scenarios/{sid}/start")
    async def start_scenario(sid: str) -> dict:
        if sid not in list_ids():
            raise HTTPException(404, f"모르는 시나리오 {sid!r} — {', '.join(list_ids())} 중 하나")
        if turn.locked():
            raise HTTPException(409, BUSY)
        async with turn:
            conversations.clear()  # 대화는 그 시나리오의 알람·값을 근거로 했다 — 이어 쓰면 다른 배의 이야기가 된다
            try:
                await host.switch(sid)
            except Exception as e:
                raise HTTPException(503, f"시나리오 {sid}의 도구 서버를 띄우지 못했다: {e!r}") from e
        return {"current": host.scenario}

    return app


app = create_app()
