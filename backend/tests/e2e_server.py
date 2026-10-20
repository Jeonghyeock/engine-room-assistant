"""E2E용 서버 — 진짜 API(app.api)에 대본대로 천천히 답하는 가짜 모델과 가짜 도구 상자를 붙인다.

    uv run python -m tests.e2e_server --port 8010

화면(web/e2e)이 이 서버에 붙어 진짜 SSE로 돈다. Ollama·색인이 없는 CI에서도 돌게 모델과 도구만 가짜다.
"""

import argparse
import asyncio

import uvicorn

from app.api import create_app
from app.llm import Done, TextDelta, ToolCall, ToolCalls
from app.tools import ToolResult

CHUNK = {
    "id": "manual:매뉴얼 3.2",
    "source": "manual",
    "locator": "매뉴얼 3.2",
    "title": "3.2 한 실린더만 높을 때",
    "text": "한 실린더의 배기가스 온도가 나머지 실린더 평균보다 50 °C 이상 높으면, 그 실린더의 연소에 문제가 있다고 본다.",
    "score": 0.8,
}
ANSWER = (
    "- 가능한 원인: 한 실린더만 높으면 연료 분사 밸브 불량을 먼저 의심한다 [1].\n"
    "- 권고 조치: 연료 분사 밸브를 점검하고, 다른 실린더와 과급기 입구 온도를 비교한다. "
    "확인 전에는 부하를 급히 바꾸지 않고, 현장 온도계로 값을 다시 본다 [1]."
)
FOLLOW_UP = "결론: 우회하지 않는다 [1]. 원인을 먼저 확인한다."


class E2EBox:
    def __init__(self, scenario: str):
        self.scenario = scenario

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        pass

    def ollama_tools(self):
        return [{"type": "function", "function": {"name": n}} for n in ("list_active_alarms", "search_docs")]

    async def call(self, name, arguments):
        if name == "list_active_alarms":
            alarm = {"tag": "ME_EXH_TEMP_CYL3", "name": "3번 실린더 배기가스 온도", "level": "경보", "value": 462.6,
                     "unit": "°C", "limit": "≥ 450", "since": "2026-10-16T14:27+09:00", "minutes_ago": 3}
            return ToolResult(True, {"alarms": [alarm]})
        return ToolResult(True, {"results": [CHUNK]})


class ScriptLLM:
    """새 질문이면 도구부터 부르고, 도구 결과를 받았으면 [1]을 인용해 글을 천천히 흘린다."""

    def __init__(self, delay: float):
        self.delay = delay

    async def __call__(self, messages, tools, opts):
        last = messages[-1]
        if last["role"] == "user":
            await asyncio.sleep(self.delay)
            yield ToolCalls([ToolCall("list_active_alarms", {}), ToolCall("search_docs", {"query": "한 실린더만 배기가스 온도가 높을 때 할 일"})])
            yield Done(1200, 40, 1.0, 0.0)
            return
        follow_up = sum(m["role"] == "user" for m in messages) > 1
        text = FOLLOW_UP if follow_up else ANSWER
        for i in range(0, len(text), 3):
            await asyncio.sleep(self.delay)
            yield TextDelta(text[i : i + 3])
        yield Done(3000, 120, 4.0, 0.0)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8010)
    ap.add_argument("--delay", type=float, default=0.05, help="글 조각 사이 간격(초)")
    a = ap.parse_args()
    uvicorn.run(create_app(llm=ScriptLLM(a.delay), box_factory=E2EBox, scenario="S1"), host="127.0.0.1", port=a.port, log_level="warning")
