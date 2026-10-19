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

from app.llm import ChatOptions, Done as LLMDone, LLMError, TextDelta, ToolCalls, stream_chat
from app.tools import ToolResult

MAX_ROUNDS = 8  # 도구를 부르는 왕복 최대 횟수
MAX_FAILED_ROUNDS = 3  # 부른 도구가 모두 실패한 왕복이 이만큼 이어지면 멈춘다(틀린 태그만 되풀이하는 경우)
MAX_CITE_NUMBER = 99  # 이보다 큰 [숫자]는 인용으로 보지 않는다([2026] 같은 연도·값)
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
    round: int  # 몇 번째 모델 호출의 글인지(0부터). 도구를 부르기 전에 하는 짧은 말과 마지막 답을 화면이 가를 수 있게


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
    truncated: bool = False  # 답이 num_predict에서 잘렸다

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
    _by_id: dict[str, int] = field(default_factory=dict, init=False, repr=False, compare=False)
    _steps: int = field(default=0, init=False, repr=False, compare=False)

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

_NUM_GROUP = re.compile(r"[\[【](\d+(?:\s*[-–~]\s*\d+)?(?:\s*[,，]\s*\d+(?:\s*[-–~]\s*\d+)?)*)[\]】]")
# §62.35-50(d), § 62.35-50(d)(1), 46 CFR 62.35-50(d), §62.35-50 표 1, 매뉴얼 4.2 — "매뉴얼 4장"처럼 장만 쓴 것은 보지 않는다
_LOCATOR = re.compile(r"(?:§\s?|CFR\s+)(\d+\.\d+-\d+(?:\([a-z0-9]+\))*(?:\s?표\s?\d+)?)|매뉴얼\s?(\d+\.\d+)")


def _numbers(group: str) -> list[int]:
    out: list[int] = []
    for part in re.split(r"\s*[,，]\s*", group):
        if m := re.fullmatch(r"(\d+)\s*[-–~]\s*(\d+)", part):
            a, b = int(m[1]), int(m[2])
            if a <= b <= a + 10:
                out.extend(range(a, b + 1))
        else:
            out.append(int(part))
    return [n for n in out if 1 <= n <= MAX_CITE_NUMBER]


def _key(locator: str) -> str:
    return locator.replace(" ", "")


def find_citations(answer: str, conv: Conversation) -> list[Citation]:
    """답에 나온 [n]과 위치 표기(§62.35-50(d), 매뉴얼 4.2)를 모아 검색된 조각과 맞춰 본다.

    번호는 이번 대화에서 붙인 번호여야 verified. 위치 표기는 검색된 조각의 위치이거나, 그 안의 하위 문단이거나,
    검색된 문단을 품은 조이면 verified — 도구 설명이 일반 MCP 클라이언트용으로 "위치로 근거를 밝혀라"라고 하므로
    모델이 이렇게 쓰기도 한다. 한 위치를 여러 조각이 함께 쓰면(설정값 표의 행들) 그중 하나라도 이미 인용했으면
    다시 내지 않는다.
    """
    out: list[Citation] = []
    cited: set[int] = set()

    def from_chunk(n: int) -> Citation:
        c = conv.chunks[n]
        cited.add(n)
        return Citation(n, c["id"], c["source"], c["locator"], c["title"], c["text"][:200], True)

    for group in _NUM_GROUP.findall(answer):
        for n in _numbers(group):
            if n in cited:
                continue
            if n in conv.chunks:
                out.append(from_chunk(n))
            else:
                cited.add(n)
                out.append(Citation(n, None, None, f"[{n}]", None, None, False))

    by_locator: dict[str, list[int]] = {}
    for n, c in conv.chunks.items():
        by_locator.setdefault(_key(c["locator"]), []).append(n)
    seen: set[str] = set()
    for m in _LOCATOR.finditer(answer):
        loc = f"§{m[1]}" if m[1] else f"매뉴얼 {m[2]}"
        key = _key(loc)
        if key in seen:
            continue
        seen.add(key)
        ns = by_locator.get(key) or next(
            (v for k, v in by_locator.items() if key.startswith(k + "(") or k.startswith(key + "(") or k.startswith(key + "표")),
            None,
        )
        if ns is None:
            out.append(Citation(None, None, None, loc, None, None, False))
        elif not cited.intersection(ns):
            out.append(from_chunk(min(ns)))
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
    turn_start = len(conv.messages)  # 이 차례가 실패하면 여기까지 되돌려 다음 질문이 깨진 기록을 이어받지 않게
    conv.messages.append({"role": "user", "content": user_text})
    started = time.perf_counter()
    rounds = failed_rounds = completion_tokens = 0
    eval_seconds = 0.0
    max_prompt = 0
    reason = ""
    tools = box.ollama_tools()

    def fail(code: str, message: str) -> Error:
        del conv.messages[turn_start:]
        return Error(code, message)

    while True:
        text: list[str] = []
        calls = []
        # 도구 호출은 줄마다 하나씩 온다 — Done까지 모은 뒤 실행한다. 쓰는 쪽이 중간에 그만 읽으면(4단계 중단 버튼)
        # aclosing이 모델 스트림을 닫아 Ollama가 생성을 멈춘다.
        try:
            async with aclosing(llm(conv.messages, tools, opts)) as stream:
                async for ev in stream:
                    if isinstance(ev, TextDelta):
                        text.append(ev.text)
                        yield Token(ev.text, rounds)
                    elif isinstance(ev, ToolCalls):
                        calls.extend(ev.calls)
                    elif isinstance(ev, LLMDone):
                        completion_tokens += ev.completion_tokens
                        eval_seconds += ev.eval_seconds
                        max_prompt = max(max_prompt, ev.prompt_tokens)
                        reason = ev.reason
        except LLMError as e:
            yield fail("llm", str(e))
            return

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
                truncated=reason == "length",
            )
            return

        rounds += 1
        if rounds > MAX_ROUNDS:
            yield fail("too_many_steps", f"도구를 {MAX_ROUNDS}번 넘게 불러 멈췄다.")
            return
        all_failed = True
        for call in calls:
            step = conv.next_step()
            yield StepStart(step, call.name, call.arguments)
            res = await box.call(call.name, call.arguments)
            numbered = conv.number(res.data["results"]) if res.ok and call.name == "search_docs" else None
            if res.ok:
                content = _compact({"results": numbered} if numbered is not None else res.data)
                all_failed = False
            else:
                content = _compact({"error": res.error})
            conv.messages.append({"role": "tool", "tool_name": call.name, "content": content})
            yield StepEnd(step, res.ok, _summary(call.name, res, numbered))
        # 한 왕복에서 여러 도구가 실패해도 한 번으로 센다 — 모델이 오류를 보고 고칠 기회를 왕복마다 준다
        failed_rounds = failed_rounds + 1 if all_failed else 0
        if failed_rounds >= MAX_FAILED_ROUNDS:
            yield fail("tool_errors", f"부른 도구가 모두 실패한 왕복이 {failed_rounds}번 이어져 멈췄다.")
            return
