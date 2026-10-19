import asyncio
import json

import httpx
import pytest

from app.llm import ChatOptions, Done, LLMError, TextDelta, ToolCall, ToolCalls, stream_chat

# Ollama 0.40의 실제 스트림 모양(직접 받아 본 것)
TOOL_TURN = [
    {"message": {"role": "assistant", "content": "", "tool_calls": [
        {"id": "call_1", "function": {"index": 0, "name": "read_sensors", "arguments": {"tags": ["ME_LO_PRESS_IN"]}}},
        {"id": "call_2", "function": {"index": 1, "name": "get_trend", "arguments": {"tag": "LO_FILTER_DP"}}},
    ]}, "done": False},
    {"message": {"role": "assistant", "content": ""}, "done": True, "done_reason": "stop",
     "prompt_eval_count": 157, "eval_count": 25, "eval_duration": 852_570_000, "load_duration": 9_312_636_700},
]
TEXT_TURN = [
    {"message": {"role": "assistant", "content": "압력은 "}, "done": False},
    {"message": {"role": "assistant", "thinking": "생각 글은 내보내지 않는다"}, "done": False},
    {"message": {"role": "assistant", "content": "1.62 bar입니다."}, "done": False},
    {"message": {"role": "assistant", "content": ""}, "done": True, "done_reason": "stop",
     "prompt_eval_count": 229, "eval_count": 23, "eval_duration": 769_000_000, "load_duration": 0},
]


def run(lines, status=200, seen=None):
    def handler(req: httpx.Request):
        if seen is not None:
            seen.update(json.loads(req.content))
        body = "\n".join(json.dumps(x, ensure_ascii=False) for x in lines) + "\n"
        return httpx.Response(status, content=body.encode())

    async def go():
        client = httpx.AsyncClient(base_url="http://ollama", transport=httpx.MockTransport(handler))
        async with client:
            return [ev async for ev in stream_chat([{"role": "user", "content": "q"}], [], ChatOptions(seed=1), client)]

    return asyncio.run(go())


def test_tool_calls_arrive_as_one_event_with_every_call():
    events = run(TOOL_TURN)
    assert events[0] == ToolCalls([ToolCall("read_sensors", {"tags": ["ME_LO_PRESS_IN"]}), ToolCall("get_trend", {"tag": "LO_FILTER_DP"})])
    done = events[-1]
    assert isinstance(done, Done) and done.prompt_tokens == 157 and round(done.load_seconds, 1) == 9.3


def test_text_streams_in_pieces_and_thinking_is_dropped():
    events = run(TEXT_TURN)
    assert [e.text for e in events if isinstance(e, TextDelta)] == ["압력은 ", "1.62 bar입니다."]
    assert round(events[-1].tokens_per_second, 1) == 29.9


def test_request_carries_options():
    seen = {}
    run(TEXT_TURN, seen=seen)
    assert seen["stream"] is True and seen["think"] is False and seen["keep_alive"] == "10m"
    assert seen["options"] == {"num_ctx": 8192, "temperature": 0.0, "seed": 1}


def test_http_error_names_the_model():
    with pytest.raises(LLMError, match="404.*qwen3:8b"):
        run([{"error": "model not found"}], status=404)


def test_error_line_in_stream_is_raised():
    with pytest.raises(LLMError, match="Ollama 오류"):
        run([{"error": "out of memory"}])
