"""Ollama 대화 클라이언트 — /api/chat을 스트리밍으로 읽어 글 조각·도구 호출·마무리 통계를 차례로 낸다.

Ollama는 줄마다 JSON 하나(NDJSON)를 보낸다. 답 글자는 여러 줄에 나눠 오고, 도구 호출은 한 줄에
목록으로 오며, 마지막 줄(done)에 토큰 수와 걸린 시간이 온다.

    async for ev in stream_chat(messages, tools, ChatOptions()):
        ...  # TextDelta | ToolCalls | Done
"""

import json
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")


class LLMError(RuntimeError):
    """Ollama에 닿지 못했거나 오류로 답했다."""


@dataclass
class ChatOptions:
    model: str = os.environ.get("CHAT_MODEL", "qwen3:8b")
    num_ctx: int = 8192
    think: bool = False  # qwen3의 생각 모드. 끄면 빠르다 — 평가에서 켠 것과 비교한다
    temperature: float = 0.0
    seed: int | None = None
    keep_alive: str = "10m"  # 모델을 올려 두는 시간. 처음 올리는 데 약 9초가 걸린다


@dataclass
class TextDelta:
    text: str


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any]


@dataclass
class ToolCalls:
    calls: list[ToolCall]


@dataclass
class Done:
    prompt_tokens: int  # 이번 요청에 들어간 문맥 길이 — num_ctx를 넘으면 Ollama가 앞부분을 조용히 버린다
    completion_tokens: int
    eval_seconds: float
    load_seconds: float
    reason: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def tokens_per_second(self) -> float:
        return self.completion_tokens / self.eval_seconds if self.eval_seconds else 0.0


Event = TextDelta | ToolCalls | Done


def _request(messages: list[dict], tools: list[dict], opts: ChatOptions) -> dict:
    options: dict[str, Any] = {"num_ctx": opts.num_ctx, "temperature": opts.temperature}
    if opts.seed is not None:
        options["seed"] = opts.seed
    return {
        "model": opts.model,
        "messages": messages,
        "tools": tools,
        "stream": True,
        "think": opts.think,
        "keep_alive": opts.keep_alive,
        "options": options,
    }


def parse_line(chunk: dict) -> list[Event]:
    """Ollama 스트림 한 줄 → 이벤트들. 생각 모드의 생각 글(thinking)은 답이 아니므로 내지 않는다."""
    if "error" in chunk:
        raise LLMError(f"Ollama 오류: {chunk['error']}")
    out: list[Event] = []
    msg = chunk.get("message") or {}
    if msg.get("content"):
        out.append(TextDelta(msg["content"]))
    if msg.get("tool_calls"):
        out.append(ToolCalls([ToolCall(c["function"]["name"], c["function"].get("arguments") or {}) for c in msg["tool_calls"]]))
    if chunk.get("done"):
        out.append(
            Done(
                prompt_tokens=chunk.get("prompt_eval_count", 0),
                completion_tokens=chunk.get("eval_count", 0),
                eval_seconds=chunk.get("eval_duration", 0) / 1e9,
                load_seconds=chunk.get("load_duration", 0) / 1e9,
                reason=chunk.get("done_reason", ""),
            )
        )
    return out


async def stream_chat(
    messages: list[dict],
    tools: list[dict],
    opts: ChatOptions,
    client: httpx.AsyncClient | None = None,
) -> AsyncIterator[Event]:
    own = client is None
    client = client or httpx.AsyncClient(base_url=OLLAMA_URL, timeout=httpx.Timeout(600, connect=5))
    try:
        async with client.stream("POST", "/api/chat", json=_request(messages, tools, opts)) as res:
            if res.status_code != 200:
                body = (await res.aread()).decode(errors="replace")[:300]
                raise LLMError(f"Ollama가 {res.status_code}로 답했다(모델 {opts.model!r}을 받아 두었는지 확인): {body}")
            async for line in res.aiter_lines():
                if line.strip():
                    for ev in parse_line(json.loads(line)):
                        yield ev
    except httpx.HTTPError as e:
        raise LLMError(f"Ollama({OLLAMA_URL})에 연결하지 못했다: {e!r}") from e
    finally:
        if own:
            await client.aclose()
