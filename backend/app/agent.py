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
from app.prompts import DEFAULT as DEFAULT_PROMPT, PROMPTS
from app.tools import ToolResult

MAX_ROUNDS = 8  # 도구를 부르는 왕복 최대 횟수
# 부른 도구가 모두 실패한 왕복이 이만큼 이어지면 멈춘다(틀린 태그만 되풀이하는 경우). read_sensors가 일부만 읽은 것은
# 값을 받았으니 성공으로 센다 — 틀린 태그를 계속 섞어 불러도 그때마다 값은 받으며, 끝은 MAX_ROUNDS가 정한다.
MAX_FAILED_ROUNDS = 3
MAX_CITE_NUMBER = 99  # 이보다 큰 [숫자]는 인용으로 보지 않는다([2026] 같은 연도·값)
MAX_RECHECKS = 2  # 근거 검사로 답을 되돌리는 최대 횟수

RECHECK_NOTE = (
    "(자동 검사) 방금 답의 근거 {cited}는 이번 대화에서 검색된 적이 없다. "
    "search_docs로 매뉴얼(필요하면 규정)을 찾은 뒤, 검색 결과의 번호로만 근거를 달아 다시 답하라."
)
SEEN = "(앞에서 받은 조각 — 본문은 위의 검색 결과에 있다)"
# 문맥이 num_ctx를 넘으면 Ollama는 오래된 대화(앞쪽 질문·도구 결과)를 잘라낸 뒤 남은 길이를 보고한다.
# 그래서 보고된 길이는 num_ctx를 넘지 않는다 — 이만큼 가까우면 잘렸을 수 있다고 본다.
CONTEXT_LIMIT_RATIO = 0.9


# ---- 이벤트(설계 문서 6.5) ----


@dataclass
class StepStart:
    id: int
    tool: str
    args: dict[str, Any]
    auto: bool = False  # 모델이 아니라 에이전트가 스스로 부른 단계(이어지는 질문의 자동 검색)


@dataclass
class StepEnd:
    id: int
    ok: bool
    summary: str


@dataclass
class Token:
    text: str
    round: int  # 이 차례에서 몇 번째 모델 호출의 글인지(0부터). 도구 전 짧은 말·되돌린 초안과 마지막 답을 화면이 가를 수 있게


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
    rounds: int  # 도구를 부른 왕복 수(근거 검사로 되돌린 것은 세지 않는다 — rechecks)
    max_prompt_tokens: int  # 가장 컸던 문맥 길이(num_ctx를 넘지 않는다 — 위 CONTEXT_LIMIT_RATIO)
    num_ctx: int
    truncated: bool = False  # 답이 num_predict에서 잘렸다
    rechecks: int = 0  # 근거 검사로 답을 되돌린 횟수
    model_calls: int = 0  # 이 차례에서 모델을 부른 횟수 — 마지막 답의 Token.round는 model_calls - 1

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
    prompt: str = DEFAULT_PROMPT  # 시스템 프롬프트 판본(app.prompts)
    messages: list[dict] = field(default_factory=list)
    chunks: dict[int, dict] = field(default_factory=dict)  # 번호 → 검색 조각
    alarms: list[str] = field(default_factory=list)  # 마지막으로 본 알람 이름 — 이어지는 질문의 검색어에 붙인다
    _by_id: dict[str, int] = field(default_factory=dict, init=False, repr=False, compare=False)
    _steps: int = field(default=0, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.messages:
            self.messages = [{"role": "system", "content": PROMPTS[self.prompt]}]

    def number(self, results: list[dict]) -> list[dict]:
        """검색 조각에 이번 대화 안의 번호를 붙여, 모델이 볼 모양으로 돌려준다(id·점수는 빼고).
        이미 번호를 받은 조각은 본문을 다시 넣지 않는다 — 본문은 앞의 검색 결과에 있다. 이어지는 질문의 자동 검색은
        대개 앞에서 받은 조각을 다시 가져와(5개 중 4개), 질문 하나에 약 1.7k 토큰씩 늘어 셋째 질문에서 한도 근처였다."""
        out = []
        for r in results:
            n = self._by_id.get(r["id"])
            if n is not None:
                out.append({"n": n, "source": r["source"], "locator": r["locator"], "title": r["title"], "text": SEEN})
                continue
            n = len(self.chunks) + 1
            self._by_id[r["id"]] = n
            self.chunks[n] = r
            out.append({"n": n, "source": r["source"], "locator": r["locator"], "title": r["title"], "text": r["text"]})
        return out

    def rollback(self, messages_len: int, chunks_len: int) -> None:
        """실패하거나 중단된 차례를 되돌린다 — 그 차례의 메시지와, 그 차례에 번호를 붙인 검색 조각까지.
        조각이 남으면 기록에 없는 조각을 다음 차례에 인용해도 확인됨이 된다(리뷰)."""
        del self.messages[messages_len:]
        for n in [n for n in self.chunks if n > chunks_len]:
            self._by_id.pop(self.chunks.pop(n)["id"], None)

    @property
    def answered(self) -> bool:
        """앞 차례에 받아들인 답이 있는가 — 있으면 지금 질문은 이어지는 질문이다."""
        return any(m["role"] == "assistant" and not m.get("tool_calls") for m in self.messages)

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
        return (res.error or "오류")[:200]
    d = res.data or {}
    if name == "search_docs" and numbered is not None:
        ns = [r["n"] for r in numbered]
        return f"조각 {len(ns)}개 [{', '.join(map(str, ns))}]" if ns else "결과 없음"
    if name == "list_active_alarms":
        return f"알람 {len(d.get('alarms', []))}개"
    if name == "read_sensors":
        read = ", ".join(f"{s['tag']} {s['value']}{s.get('unit', '')}" for s in d.get("sensors", []))
        unknown = d.get("unknown", [])
        return read + (f" · 모르는 태그 {', '.join(u['tag'] for u in unknown)}" if unknown else "")
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
    guard: bool = True,
    follow_up_search: bool = True,
) -> AsyncIterator[Event]:
    """사용자 질문 하나에 답할 때까지. 이벤트를 차례로 낸다.

    guard: 근거 검사. 답에 이번 대화에서 검색된 적 없는 근거가 있으면 그 답을 받지 않고, 먼저 검색하라고
    되돌린다(최대 MAX_RECHECKS번). 되돌림은 check_citations 단계로 드러나 화면에서도 보인다.
    follow_up_search: 이어지는 질문이면 모델을 부르기 전에 에이전트가 "질문 + 지금 알람 이름"으로 먼저 검색해
    결과를 대화에 넣는다. 8B 모델은 프롬프트(v3·v4)로 일러도, 검색을 강제해도(#14) 이어지는 질문에서 의미 있는
    검색을 스스로 시작하지 않았다 — 첫 차례에서 찾은 조각에 기대 답했다.
    """
    opts = opts or ChatOptions()
    turn_start = len(conv.messages)  # 이 차례가 실패하거나 중단되면 여기까지 되돌려 다음 질문이 깨진 기록을 이어받지 않게
    chunks_start = len(conv.chunks)
    follow_up = conv.answered
    conv.messages.append({"role": "user", "content": user_text})
    started = time.perf_counter()
    rounds = failed_rounds = completion_tokens = rechecks = model_calls = 0
    # 되돌린 초안과 자동 검사 메시지. 답이 받아들여지면 기록에서 지운다 — 남겨 두면 다음 질문에서 모델이
    # "(자동 검사) 다시 답하라"를 다시 읽고 앞 답을 되풀이한다(리뷰에서 S2 우회 질문 3번 모두).
    transient: list[dict] = []
    eval_seconds = 0.0
    max_prompt = 0
    reason = ""
    tools = box.ollama_tools()

    def fail(code: str, message: str) -> Error:
        return Error(code, message)  # 되돌리기는 아래 finally가 한다

    accepted = False  # 답을 받아들였는가 — 아니면(실패·중단) finally에서 이 차례를 되돌린다

    try:
        if follow_up and follow_up_search:
            # 질문만으로는 "트립을 우회해도 되나?"처럼 무엇의 트립인지 빠져 있다 — 지금 알람 이름을 붙여 검색한다.
            query = user_text + (f" (지금 알람: {', '.join(conv.alarms)})" if conv.alarms else "")
            step = conv.next_step()
            yield StepStart(step, "search_docs", {"query": query}, auto=True)
            res = await box.call("search_docs", {"query": query})
            if res.ok:
                numbered = conv.number(res.data["results"])
                # 모델이 직접 부른 것처럼 호출과 결과를 함께 넣는다(호출 없이 결과만 있으면 템플릿에서 어색하다)
                conv.messages.append({"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "search_docs", "arguments": {"query": query}}}]})
                conv.messages.append({"role": "tool", "tool_name": "search_docs", "content": _compact({"results": numbered})})
                yield StepEnd(step, True, "자동 검색 · " + _summary("search_docs", res, numbered))
            else:  # 검색이 안 되면 그냥 모델에게 맡긴다
                yield StepEnd(step, False, "자동 검색 실패 · " + (res.error or "")[:150])

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
                            yield Token(ev.text, model_calls)
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
            model_calls += 1

            assistant: dict[str, Any] = {"role": "assistant", "content": "".join(text)}
            if calls:
                assistant["tool_calls"] = [{"function": {"name": c.name, "arguments": c.arguments}} for c in calls]
            conv.messages.append(assistant)

            if not calls:  # 답이 나왔다
                cites = find_citations(assistant["content"], conv)
                unverified = [c.locator for c in cites if not c.verified]
                if guard and unverified and rechecks < MAX_RECHECKS:
                    rechecks += 1
                    step = conv.next_step()
                    # 도구가 아니라 근거 검사를 단계로 보여 주는 가짜 단계다(설계 문서 6.4·6.5)
                    yield StepStart(step, "check_citations", {"unverified": unverified})
                    note = {"role": "user", "content": RECHECK_NOTE.format(cited=", ".join(unverified))}
                    conv.messages.append(note)
                    transient += [assistant, note]
                    yield StepEnd(step, False, f"검색된 적 없는 근거 {', '.join(unverified)} — 검색한 뒤 다시 답하게 함")
                    continue
                conv.messages[:] = [m for m in conv.messages if not any(m is t for t in transient)]
                accepted = True
                for c in cites:
                    yield c
                yield Done(
                    tokens=completion_tokens,
                    tok_per_s=round(completion_tokens / eval_seconds, 1) if eval_seconds else 0.0,
                    elapsed_ms=int((time.perf_counter() - started) * 1000),
                    rounds=rounds,
                    max_prompt_tokens=max_prompt,
                    num_ctx=opts.num_ctx,
                    truncated=reason == "length",
                    rechecks=rechecks,
                    model_calls=model_calls,
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
                if res.ok and call.name == "list_active_alarms":
                    conv.alarms = [a.get("name") or a.get("tag", "") for a in (res.data or {}).get("alarms", [])]
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
    finally:
        # 실패(Error)했거나, 쓰는 쪽이 중간에 그만 읽었으면(4단계 중단 버튼 — 생성기가 닫힘) 이 차례를 되돌린다.
        # 근거 검사 중에 멈추면 되돌린 초안이 기록에 남아 다음 질문이 "이어지는 질문"으로 잘못 갈렸다(리뷰).
        if not accepted:
            conv.rollback(turn_start, chunks_start)
