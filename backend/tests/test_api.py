import asyncio
import json

import httpx
import uvicorn

from app.api import create_app
from app.llm import Done as LLMDone, TextDelta, ToolCall, ToolCalls
from app.tools import ToolResult

CHUNK_44 = {"id": "manual:매뉴얼 4.4", "source": "manual", "locator": "매뉴얼 4.4", "title": "4.4 저압 감속·정지와 우회 금지", "text": "윤활유 저압 정지는 우회하지 않는다.", "score": 0.8}


class FakeToolBox:
    """시나리오마다 알람 하나를 돌려주는 가짜 도구 상자(ToolHost가 async with로 연다)."""

    def __init__(self, scenario):
        self.scenario = scenario
        self.called = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        pass

    def ollama_tools(self):
        return [{"type": "function", "function": {"name": n}} for n in ("list_active_alarms", "search_docs")]

    async def call(self, name, arguments):
        self.called.append((name, arguments))
        if name == "list_active_alarms":
            return ToolResult(True, {"alarms": [{"tag": f"TAG_{self.scenario}", "name": f"{self.scenario} 알람"}]})
        return ToolResult(True, {"results": [CHUNK_44]})


class BrokenS3(FakeToolBox):
    async def __aenter__(self):
        if self.scenario == "S3":
            raise RuntimeError("S3 서버를 띄우지 못함")
        return self


class ScriptLLM:
    """첫 호출은 검색, 둘째 호출은 [1]을 인용한 답 — 차례마다 이 둘을 되풀이한다."""

    def __init__(self):
        self.calls = 0

    async def __call__(self, messages, tools, opts):
        self.calls += 1
        if self.calls % 2:
            yield ToolCalls([ToolCall("list_active_alarms", {}), ToolCall("search_docs", {"query": "윤활유 저압"})])
            yield LLMDone(500, 20, 1.0, 0.0)
        else:
            yield TextDelta("우회하지 않는다 ")
            yield TextDelta("[1].")
            yield LLMDone(900, 100, 2.0, 0.0)


def parse_sse(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        out.append((lines["event"], json.loads(lines["data"])))
    return out


async def with_client(app, fn):
    async with app.router.lifespan_context(app):  # ASGITransport는 lifespan을 돌리지 않는다
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            return await fn(c)


def test_chat_streams_the_contract_in_order_and_the_follow_up_is_searched_first():
    app = create_app(llm=ScriptLLM(), box_factory=FakeToolBox, scenario="S2")

    async def go(c):
        first = parse_sse((await c.post("/api/chat", json={"message": "알람을 분석해 줘"})).text)
        cid = first[0][1]["id"]
        second = parse_sse((await c.post("/api/chat", json={"conversation_id": cid, "message": "우회해도 되나요?"})).text)
        return first, second

    first, second = asyncio.run(with_client(app, go))
    names = [e for e, _ in first]
    assert names[0] == "conversation" and first[0][1]["scenario"] == "S2"
    assert names[1:] == ["step_start", "step_end", "step_start", "step_end", "token", "token", "citation", "done"]
    assert first[1][1] == {"id": 1, "tool": "list_active_alarms", "args": {}, "auto": False}
    assert first[5][1] == {"text": "우회하지 않는다 ", "round": 1}
    cite = first[7][1]
    assert cite["n"] == 1 and cite["locator"] == "매뉴얼 4.4" and cite["verified"] is True
    done = first[8][1]
    assert done["context_near_limit"] is False and done["rounds"] == 1  # 속성도 실어 보낸다
    # 같은 대화의 이어지는 질문 — 에이전트가 먼저 검색한다(auto)
    assert second[0][1]["id"] == first[0][1]["id"]
    auto = second[1][1]
    assert second[1][0] == "step_start" and auto["tool"] == "search_docs" and auto["auto"] is True
    assert "S2 알람" in auto["args"]["query"]


def test_unknown_conversation_busy_turn_and_empty_message_are_refused():
    app = create_app(llm=ScriptLLM(), box_factory=FakeToolBox, scenario="S1")

    async def go(c):
        unknown = await c.post("/api/chat", json={"conversation_id": "nope", "message": "q"})
        empty = await c.post("/api/chat", json={"message": ""})
        async with app.state.turn:  # 다른 차례가 도는 중
            busy = await c.post("/api/chat", json={"message": "q"})
            busy_switch = await c.post("/api/scenarios/S2/start")
        return unknown, empty, busy, busy_switch

    unknown, empty, busy, busy_switch = asyncio.run(with_client(app, go))
    assert unknown.status_code == 404 and empty.status_code == 422
    assert busy.status_code == 409 and busy_switch.status_code == 409


def test_switching_scenario_reopens_tools_and_forgets_conversations():
    app = create_app(llm=ScriptLLM(), box_factory=FakeToolBox, scenario="S1")

    async def go(c):
        cid = parse_sse((await c.post("/api/chat", json={"message": "q"})).text)[0][1]["id"]
        listed = (await c.get("/api/scenarios")).json()
        before = (await c.get("/api/alarms")).json()
        switched = await c.post("/api/scenarios/S3/start")
        unknown = await c.post("/api/scenarios/S9/start")
        after = (await c.get("/api/alarms")).json()
        old = await c.post("/api/chat", json={"conversation_id": cid, "message": "q"})
        return listed, before, switched, unknown, after, old

    listed, before, switched, unknown, after, old = asyncio.run(with_client(app, go))
    assert listed["current"] == "S1" and [s["id"] for s in listed["scenarios"]] == ["S1", "S2", "S3"]
    assert all(s["title"] for s in listed["scenarios"])
    assert before["scenario"] == "S1" and before["alarms"][0]["tag"] == "TAG_S1"
    assert switched.json() == {"current": "S3"} and unknown.status_code == 404
    assert after["alarms"][0]["tag"] == "TAG_S3"
    assert old.status_code == 404  # 바꾸기 전 대화는 지워졌다


def test_without_a_tool_box_no_turn_starts_until_a_scenario_opens():
    """바꾸기에 실패하면 도구 상자 없이 남는다(app.host) — 그때는 차례를 시작하지 않는다(#19 리뷰)."""
    app = create_app(llm=ScriptLLM(), box_factory=BrokenS3, scenario="S1")

    async def go(c):
        failed = await c.post("/api/scenarios/S3/start")
        refused = await c.post("/api/chat", json={"message": "q"})
        await c.post("/api/scenarios/S2/start")
        ok = parse_sse((await c.post("/api/chat", json={"message": "q"})).text)
        return failed, refused, ok

    failed, refused, ok = asyncio.run(with_client(app, go))
    assert failed.status_code == 503 and "S3 서버를 띄우지 못함" in failed.json()["detail"]
    assert refused.status_code == 503
    assert ok[0][1]["scenario"] == "S2" and ok[-1][0] == "done"


class SlowLLM:
    """글을 천천히 흘린다. 정리(finally)에 await가 있어, 끊긴 뒤에도 정리가 끝까지 도는지 볼 수 있다
    (진짜 클라이언트는 여기서 Ollama 연결을 닫는다 — 못 닫으면 Ollama가 계속 생성한다)."""

    def __init__(self):
        self.cleaned = False

    async def __call__(self, messages, tools, opts):
        try:
            for _ in range(200):
                yield TextDelta("글 ")
                await asyncio.sleep(0.02)
            yield LLMDone(900, 100, 2.0, 0.0)
        finally:
            await asyncio.sleep(0.01)
            self.cleaned = True


def test_closing_the_stream_rolls_the_turn_back_and_frees_the_server():
    """중단 버튼: 화면이 스트림을 끊으면 그 차례는 되돌아가고, 다음 질문을 받을 수 있다(진짜 uvicorn으로)."""
    llm = SlowLLM()
    app = create_app(llm=llm, box_factory=FakeToolBox, scenario="S2")

    async def go():
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))
        serving = asyncio.create_task(server.serve())
        while not server.started:
            await asyncio.sleep(0.05)
        port = server.servers[0].sockets[0].getsockname()[1]
        try:
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=10) as c:
                cid = None
                async with c.stream("POST", "/api/chat", json={"message": "알람을 분석해 줘"}) as res:
                    async for line in res.aiter_lines():
                        if line.startswith("data: ") and cid is None:
                            cid = json.loads(line[6:])["id"]
                        if line == "event: token":
                            break  # 글이 흐르기 시작하자마자 끊는다
                for _ in range(100):  # 서버가 끊김을 알아채고 차례를 정리할 때까지
                    if not app.state.turn.locked():
                        break
                    await asyncio.sleep(0.05)
                conv = app.state.conversations[cid]
                roles, answered = [m["role"] for m in conv.messages], conv.answered
                async with c.stream("POST", "/api/chat", json={"conversation_id": cid, "message": "다시"}) as again:
                    status = again.status_code  # 머리만 보고 끊는다
                return roles, answered, status
        finally:
            server.should_exit = True
            await serving

    roles, answered, again = asyncio.run(go())
    assert roles == ["system"] and not answered  # 끊긴 차례는 기록에 남지 않는다 → 다음 질문은 첫 질문으로 다룬다
    assert llm.cleaned  # 모델 스트림의 정리가 끝까지 돌았다
    assert again == 200  # 잠금이 풀려 다음 질문을 받는다
