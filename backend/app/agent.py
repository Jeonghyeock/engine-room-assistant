"""에이전트 루프 — 모델이 도구를 부르면 실행해 결과를 돌려주고, 답이 나올 때까지 되풀이한다.

    conv = Conversation()
    async for ev in run_turn(conv, "알람 분석해 줘", box):
        ...  # StepStart · StepEnd · Token · Citation · Done · Error (설계 문서 6.5의 이벤트)

프레임워크 없이 이 파일 하나에 다 있다.
- 한 번의 대화(Conversation)가 메시지 기록과 인용 번호표를 함께 든다. S2처럼 같은 대화에서
  이어 묻는 질문도 앞의 근거 번호를 그대로 쓴다.
- 검색 결과는 모델에게 넘기기 전에 [1], [2]… 번호를 붙인다(같은 조각은 같은 번호). 8B 모델은
  긴 id를 자주 틀리게 옮겨 적는다. 번호와 조각의 대응은 여기서 들고 있다.
- 답에 나온 번호가 이번 대화에서 실제로 검색된 조각인지 확인해 verified를 붙인다.
"""

import json
import re
import time
from collections.abc import AsyncIterator, Callable
from contextlib import aclosing
from dataclasses import dataclass, field
from typing import Any

from app.llm import ChatOptions, Done as LLMDone, TextDelta, ToolCalls, stream_chat
from app.tools import ToolResult

MAX_ROUNDS = 8  # 도구를 부르는 왕복 최대 횟수
MAX_TOOL_ERRORS = 3  # 도구 오류가 이만큼 이어지면 멈춘다(틀린 태그만 되풀이하는 경우)
# 문맥이 num_ctx를 넘으면 Ollama는 오래된 대화(앞쪽 질문·도구 결과)를 잘라낸 뒤 남은 길이를 보고한다.
# 그래서 보고된 길이는 num_ctx를 넘지 않는다 — 이만큼 가까우면 잘렸을 수 있다고 본다.
CONTEXT_LIMIT_RATIO = 0.9

SYSTEM_PROMPT = """너는 선박 기관실 당직 기관사를 돕는 알람 대응 어시스턴트다. 교육용 가상 선박이다.
알람이나 질문을 받으면 도구로 사실을 확인하고, 매뉴얼과 규정에서 근거를 찾아 조치를 권고한다.

규칙
1. 권고만 한다. 장비를 조작하거나 설정을 바꾸는 도구는 없고, 결정과 조작은 사람이 한다.
2. 수치는 도구 결과와 문서에 있는 것만 쓴다. 짐작한 수치를 쓰지 않는다.
3. 근거는 search_docs 결과의 번호로 문장 끝에 [1], [2]처럼 단다. 결과에 없는 번호를 만들지 않는다.
4. 정지·감속 같은 안전 장치를 우회하라고 권하지 않는다. 우회를 물으면 하면 안 되는 이유를 근거와 함께 말한다.
5. 값이 1분 사이에 물리적으로 어려울 만큼 바뀌었거나 측정 범위 끝에 붙어 있으면 센서 고장 가능성을 말하고 현장 계기로 확인하라고 권한다. 확인 전에는 실제 이상일 가능성도 열어 둔다.

순서
list_active_alarms로 무엇이 울리는지 보고 → read_sensors·get_trend로 관련 값과 변화를 확인하고 → search_docs(source="manual")로 매뉴얼 절차를 찾고 → 필요하면 search_docs(source="cfr")로 규정을 찾는다.

답 형식(한국어)
- 상황: 무엇이 언제부터 어떤 값인지
- 가능한 원인
- 권고 조치: 순서대로
각 문장의 근거를 [n]으로 단다."""


# ---- 이벤트(설계 문서 6.5) ----


@dataclass
class StepStart:
    id: int
    tool: str
    args: dict[str, Any]


@dataclass
class StepEnd:
    id: int
    ok: bool
    summary: str


@dataclass
class Token:
    text: str


@dataclass
class Citation:
    n: int | None
    chunk_id: str | None
    source: str | None
    locator: str
    title: str | None
    snippet: str | None
    verified: bool


@dataclass
class Done:
    tokens: int  # 이번 차례에 모델이 쓴 토큰 합
    tok_per_s: float
    elapsed_ms: int
    rounds: int  # 도구를 부른 왕복 수
    max_prompt_tokens: int  # 가장 컸던 문맥 길이(num_ctx를 넘지 않는다 — 위 CONTEXT_LIMIT_RATIO)
    num_ctx: int

    @property
    def context_near_limit(self) -> bool:
        return self.max_prompt_tokens >= self.num_ctx * CONTEXT_LIMIT_RATIO


@dataclass
class Error:
    code: str
    message: str


Event = StepStart | StepEnd | Token | Citation | Done | Error


# ---- 대화 ----


@dataclass
class Conversation:
    messages: list[dict] = field(default_factory=lambda: [{"role": "system", "content": SYSTEM_PROMPT}])
    chunks: dict[int, dict] = field(default_factory=dict)  # 번호 → 검색 조각
    _by_id: dict[str, int] = field(default_factory=dict)
    _steps: int = 0

    def number(self, results: list[dict]) -> list[dict]:
        """검색 조각에 이번 대화 안의 번호를 붙여, 모델이 볼 모양으로 돌려준다(id·점수는 빼고)."""
        out = []
        for r in results:
            n = self._by_id.get(r["id"])
            if n is None:
                n = len(self.chunks) + 1
                self._by_id[r["id"]] = n
                self.chunks[n] = r
            out.append({"n": n, "source": r["source"], "locator": r["locator"], "title": r["title"], "text": r["text"]})
        return out

    def next_step(self) -> int:
        self._steps += 1
        return self._steps


# ---- 인용 확인 ----

_NUM_GROUP = re.compile(r"\[(\d+(?:\s*[,，]\s*\d+)*)\]")
_LOCATOR = re.compile(r"§\s?\d+\.\d+-\d+(?:\([a-z0-9]+\))*|매뉴얼\s?\d+(?:\.\d+)*")


def find_citations(answer: str, conv: Conversation) -> list[Citation]:
    """답에 나온 [n]과 위치 표기(§62.35-50(d), 매뉴얼 4.2)를 모아 검색된 조각과 맞춰 본다.

    번호는 이번 대화에서 붙인 번호여야 verified. 위치 표기는 검색된 조각의 위치와 같으면 verified —
    도구 설명이 일반 MCP 클라이언트용으로 "위치로 근거를 밝혀라"라고 하므로 모델이 이렇게 쓰기도 한다.
    """
    out: list[Citation] = []
    seen: set[Any] = set()
    for group in _NUM_GROUP.findall(answer):
        for n in (int(x) for x in re.split(r"\s*[,，]\s*", group)):
            if n in seen:
                continue
            seen.add(n)
            c = conv.chunks.get(n)
            if c:
                out.append(Citation(n, c["id"], c["source"], c["locator"], c["title"], c["text"][:200], True))
            else:
                out.append(Citation(n, None, None, f"[{n}]", None, None, False))
    by_locator = {c["locator"].replace(" ", ""): (n, c) for n, c in conv.chunks.items()}
    for raw in _LOCATOR.findall(answer):
        key = raw.replace(" ", "")
        if key in seen:
            continue
        seen.add(key)
        hit = by_locator.get(key)
        if hit and hit[0] in seen:
            continue  # 같은 조각을 번호로 이미 인용했다
        if hit:
            n, c = hit
            out.append(Citation(n, c["id"], c["source"], c["locator"], c["title"], c["text"][:200], True))
        else:
            out.append(Citation(None, None, None, raw, None, None, False))
    return out


# ---- 도구 결과 → 모델에게 줄 글 ----


def _summary(name: str, res: ToolResult, numbered: list[dict] | None) -> str:
    if not res.ok:
        return (res.error or "오류")[:120]
    d = res.data or {}
    if name == "search_docs" and numbered is not None:
        ns = [r["n"] for r in numbered]
        return f"조각 {len(ns)}개 [{', '.join(map(str, ns))}]" if ns else "결과 없음"
    if name == "list_active_alarms":
        return f"알람 {len(d.get('alarms', []))}개"
    if name == "read_sensors":
        return ", ".join(f"{s['tag']} {s['value']}{s.get('unit', '')}" for s in d.get("sensors", []))
    if name == "get_trend":
        return f"{d.get('tag')} {d.get('window_minutes')}분: {d.get('first')} → {d.get('last')}"
    if name == "list_tags":
        return f"태그 {len(d.get('tags', []))}개"
    return "완료"


def _compact(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


class _Box:
    """에이전트가 도구 상자에서 쓰는 것(테스트에서 가짜로 바꿔 끼운다)."""

    def ollama_tools(self) -> list[dict]: ...
    async def call(self, name: str, arguments: dict) -> ToolResult: ...


LLM = Callable[[list[dict], list[dict], ChatOptions], AsyncIterator[Any]]


async def run_turn(
    conv: Conversation,
    user_text: str,
    box: _Box,
    opts: ChatOptions | None = None,
    llm: LLM = stream_chat,
) -> AsyncIterator[Event]:
    """사용자 질문 하나에 답할 때까지. 이벤트를 차례로 낸다."""
    opts = opts or ChatOptions()
    conv.messages.append({"role": "user", "content": user_text})
    started = time.perf_counter()
    rounds = tool_errors = completion_tokens = 0
    eval_seconds = 0.0
    max_prompt = 0
    tools = box.ollama_tools()

    while True:
        text: list[str] = []
        calls = []
        # 도구 호출은 줄마다 하나씩 온다 — Done까지 모은 뒤 실행한다. 쓰는 쪽이 중간에 그만 읽으면(4단계 중단 버튼)
        # aclosing이 모델 스트림을 닫아 Ollama가 생성을 멈춘다.
        async with aclosing(llm(conv.messages, tools, opts)) as stream:
            async for ev in stream:
                if isinstance(ev, TextDelta):
                    text.append(ev.text)
                    yield Token(ev.text)
                elif isinstance(ev, ToolCalls):
                    calls.extend(ev.calls)
                elif isinstance(ev, LLMDone):
                    completion_tokens += ev.completion_tokens
                    eval_seconds += ev.eval_seconds
                    max_prompt = max(max_prompt, ev.prompt_tokens)

        assistant: dict[str, Any] = {"role": "assistant", "content": "".join(text)}
        if calls:
            assistant["tool_calls"] = [{"function": {"name": c.name, "arguments": c.arguments}} for c in calls]
        conv.messages.append(assistant)

        if not calls:  # 답이 나왔다
            for c in find_citations(assistant["content"], conv):
                yield c
            yield Done(
                tokens=completion_tokens,
                tok_per_s=round(completion_tokens / eval_seconds, 1) if eval_seconds else 0.0,
                elapsed_ms=int((time.perf_counter() - started) * 1000),
                rounds=rounds,
                max_prompt_tokens=max_prompt,
                num_ctx=opts.num_ctx,
            )
            return

        rounds += 1
        if rounds > MAX_ROUNDS:
            yield Error("too_many_steps", f"도구를 {MAX_ROUNDS}번 넘게 불러 멈췄다.")
            return
        for call in calls:
            step = conv.next_step()
            yield StepStart(step, call.name, call.arguments)
            res = await box.call(call.name, call.arguments)
            numbered = conv.number(res.data["results"]) if res.ok and call.name == "search_docs" else None
            if res.ok:
                content = _compact({"results": numbered} if numbered is not None else res.data)
                tool_errors = 0
            else:
                content = _compact({"error": res.error})
                tool_errors += 1
            conv.messages.append({"role": "tool", "tool_name": call.name, "content": content})
            yield StepEnd(step, res.ok, _summary(call.name, res, numbered))
        if tool_errors >= MAX_TOOL_ERRORS:
            yield Error("tool_errors", f"도구 오류가 {tool_errors}번 이어져 멈췄다.")
            return
