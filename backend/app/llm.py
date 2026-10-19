"""Ollama 대화 클라이언트 — /api/chat을 스트리밍으로 읽어 글 조각·도구 호출·마무리 통계를 차례로 낸다.

Ollama는 줄마다 JSON 하나(NDJSON)를 보낸다. 답 글자는 여러 줄에 나눠 오고, 도구 호출은 **한 줄에
하나씩**(여러 개면 여러 줄) 오며, 도구 호출 앞에 짧은 글이 먼저 오기도 한다. 마지막 줄(done)에
토큰 수와 걸린 시간이 온다. 그래서 쓰는 쪽은 Done이 올 때까지 도구 호출을 모은 뒤 실행한다.

    async with contextlib.aclosing(stream_chat(messages, tools, ChatOptions())) as events:
        async for ev in events:
            ...  # TextDelta | ToolCalls | Done

중간에 그만 읽을 때는 반드시 생성기를 닫는다(aclosing). 생성기를 잡아 둔 채 break만 하면 HTTP
연결이 열린 채로 남아 Ollama가 답을 끝까지 만들고, 그동안 다음 요청이 줄을 서서 기다린다.
"""

import json
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx


class LLMError(RuntimeError):
    """Ollama에 닿지 못했거나, 오류로 답했거나, 응답이 멈췄다."""


def _ollama_url() -> str:
    return os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")


# 줄과 줄 사이를 기다리는 시간. 처음 줄은 모델 적재(약 9초)와 문맥 읽기까지 기다려야 해서 넉넉히 두되,
# 다른 요청 뒤에 줄을 서 멈춘 경우를 10분씩 기다리지 않게 한다.
READ_TIMEOUT = 120


@dataclass
class ChatOptions:
    model: str = field(default_factory=lambda: os.environ.get("CHAT_MODEL", "qwen3:8b"))
    num_ctx: int = 8192
    think: bool = False  # qwen3의 생각 모드. 끄면 빠르다 — 평가에서 켠 것과 비교한다
    temperature: float = 0.0
    seed: int | None = None
    num_predict: int = 2048  # 한 번에 만들 최대 토큰. 생각 모드에서 끝없이 이어지지 않게
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
    # 이번 요청의 문맥 길이. 문맥이 num_ctx를 넘으면 Ollama는 오래된 대화(앞쪽 질문·도구 결과)를 잘라낸 뒤
    # 남은 길이를 보고한다 — 시스템 프롬프트는 남는다. 그래서 이 값은 num_ctx를 넘지 않고, 한도에 가까우면
    # 잘렸을 수 있다고 봐야 한다.
    prompt_tokens: int
    completion_tokens: int
    eval_seconds: float
    load_seconds: float
    reason: str = ""

    @property
    def tokens_per_second(self) -> float:
        return self.completion_tokens / self.eval_seconds if self.eval_seconds else 0.0


Event = TextDelta | ToolCalls | Done


def _request(messages: list[dict], tools: list[dict], opts: ChatOptions) -> dict:
    options: dict[str, Any] = {"num_ctx": opts.num_ctx, "temperature": opts.temperature, "num_predict": opts.num_predict}
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
    url = _ollama_url()
    client = client or httpx.AsyncClient(base_url=url, timeout=httpx.Timeout(READ_TIMEOUT, connect=5))
    try:
        async with client.stream("POST", "/api/chat", json=_request(messages, tools, opts)) as res:
            if res.status_code != 200:
                body = (await res.aread()).decode(errors="replace")[:300]
                raise LLMError(f"Ollama가 {res.status_code}로 답했다(모델 {opts.model!r}을 받아 두었는지 확인): {body}")
            async for line in res.aiter_lines():
                if not line.strip():
                    continue
                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError as e:
                    raise LLMError(f"Ollama 응답 줄을 읽지 못했다: {line[:120]!r}") from e
                for ev in parse_line(chunk):
                    yield ev
    except httpx.TimeoutException as e:
        raise LLMError(f"Ollama({url})가 {READ_TIMEOUT}초 동안 답하지 않았다 — 다른 요청이 모델을 붙들고 있을 수 있다: {e!r}") from e
    except httpx.HTTPError as e:
        raise LLMError(f"Ollama({url})에 연결하지 못했다: {e!r}") from e
    finally:
        if own:
            await client.aclose()
