"""에이전트 루프 — 가짜 모델(정해 둔 응답을 차례로 냄)과 가짜 도구 상자로 확인한다. Ollama가 필요 없다."""

import asyncio
import json
from contextlib import aclosing

from app.agent import (
    MAX_RECHECKS,
    MAX_ROUNDS,
    Citation,
    Conversation,
    Done,
    Error,
    StepEnd,
    StepStart,
    Token,
    find_citations,
    run_turn,
)
from app.llm import ChatOptions, Done as LLMDone, LLMError, TextDelta, ToolCall, ToolCalls
from app.tools import ToolResult

CHUNK_44 = {"id": "manual:매뉴얼 4.4", "source": "manual", "locator": "매뉴얼 4.4", "title": "4.4 저압 감속·정지와 우회 금지", "text": "윤활유 저압 정지는 우회하지 않는다.", "score": 0.8}
CHUNK_D = {"id": "cfr:§62.35-50(d)", "source": "cfr", "locator": "§62.35-50(d)", "title": "§62.35-50 …", "text": "(d) Override … must not be provided", "score": 0.6}


def calls(*pairs):
    return [ToolCalls([ToolCall(n, a) for n, a in pairs]), LLMDone(500, 20, 1.0, 0.0)]


def answer(text, prompt=900):
    return [TextDelta(t) for t in text.split(" ")[:1]] + [TextDelta(text[len(text.split(" ")[0]):])] + [LLMDone(prompt, 100, 2.0, 0.0)]


class FakeLLM:
    def __init__(self, *turns):
        self.turns = list(turns)
        self.seen = []  # 모델이 본 메시지(호출마다)

    async def __call__(self, messages, tools, opts):
        self.seen.append(json.loads(json.dumps(messages, ensure_ascii=False)))
        for ev in self.turns.pop(0):
            yield ev


class FakeBox:
    def __init__(self, results):
        self.results = results  # 도구 이름 → ToolResult 또는 그 목록(차례로)
        self.called = []

    def ollama_tools(self):
        return [{"type": "function", "function": {"name": n}} for n in self.results]

    async def call(self, name, arguments):
        self.called.append((name, arguments))
        r = self.results[name]
        return r.pop(0) if isinstance(r, list) else r


def run(conv, question, llm, box, opts=None):
    async def go():
        return [ev async for ev in run_turn(conv, question, box, opts or ChatOptions(), llm=llm)]

    return asyncio.run(go())


def test_tool_round_then_answer_with_numbered_citation():
    box = FakeBox({"list_active_alarms": ToolResult(True, {"alarms": [{"tag": "ME_LO_PRESS_IN"}]}), "search_docs": ToolResult(True, {"results": [CHUNK_44]})})
    llm = FakeLLM(
        calls(("list_active_alarms", {}), ("search_docs", {"query": "윤활유 저압", "source": "manual"})),
        answer("감속 상태에서 압력을 회복하는 것이 먼저다 [1]."),
    )
    conv = Conversation()
    events = run(conv, "알람 분석", llm, box)

    kinds = [type(e).__name__ for e in events]
    assert kinds == ["StepStart", "StepEnd", "StepStart", "StepEnd", "Token", "Token", "Citation", "Done"]
    assert events[3] == StepEnd(2, True, "조각 1개 [1]")
    cite = events[6]
    assert cite == Citation(1, "manual:매뉴얼 4.4", "manual", "매뉴얼 4.4", CHUNK_44["title"], CHUNK_44["text"], True)
    done = events[-1]
    assert done.rounds == 1 and done.tokens == 120 and done.max_prompt_tokens == 900

    # 모델은 번호만 보고 조각 id·점수는 보지 않는다
    tool_msg = llm.seen[1][-1]
    assert tool_msg["role"] == "tool" and tool_msg["tool_name"] == "search_docs"
    assert json.loads(tool_msg["content"]) == {"results": [{"n": 1, "source": "manual", "locator": "매뉴얼 4.4", "title": CHUNK_44["title"], "text": CHUNK_44["text"]}]}


def test_numbering_is_per_conversation_across_turns():
    box = FakeBox({"search_docs": [ToolResult(True, {"results": [CHUNK_44]}), ToolResult(True, {"results": [CHUNK_D, CHUNK_44]})]})
    conv = Conversation()
    run(conv, "첫 질문", FakeLLM(calls(("search_docs", {"query": "a"})), answer("답 [1].")), box)
    llm = FakeLLM(calls(("search_docs", {"query": "b"})), answer("우회하면 안 된다 [2], [1]."))
    events = run(conv, "우회해도 되나?", llm, box)

    # 같은 조각은 같은 번호, 새 조각은 다음 번호
    assert json.loads(llm.seen[1][-1]["content"])["results"][0]["n"] == 2
    assert json.loads(llm.seen[1][-1]["content"])["results"][1]["n"] == 1
    assert [(c.n, c.verified) for c in events if isinstance(c, Citation)] == [(2, True), (1, True)]
    # 앞 차례의 대화가 그대로 이어진다(시스템 + 질문·도구·답 + 새 질문)
    assert [m["role"] for m in llm.seen[0]][:2] == ["system", "user"] and llm.seen[0][-1]["content"] == "우회해도 되나?"


def test_answer_cut_by_num_predict_is_marked():
    llm = FakeLLM([TextDelta("길게 쓰다가"), LLMDone(1000, 2048, 60.0, 0.0, reason="length")])
    assert run(Conversation(), "?", llm, FakeBox({}))[-1].truncated


def test_preamble_and_answer_tokens_carry_their_round():
    box = FakeBox({"list_active_alarms": ToolResult(True, {"alarms": []})})
    llm = FakeLLM([TextDelta("확인해 보겠습니다."), ToolCalls([ToolCall("list_active_alarms", {})]), LLMDone(100, 5, 1.0, 0.0)], answer("알람 없음."))
    tokens = [e for e in run(Conversation(), "?", llm, box) if isinstance(e, Token)]
    assert tokens[0] == Token("확인해 보겠습니다.", 0) and {t.round for t in tokens[1:]} == {1}


def test_shared_locator_and_unusual_citation_forms():
    conv = Conversation()
    rows = [{**CHUNK_44, "id": f"manual:매뉴얼 2.1#{i}", "locator": "매뉴얼 2.1", "title": f"2.1 설정값 표 — 행{i}"} for i in (1, 2)]
    table = {**CHUNK_D, "id": "cfr:§62.35-50 표 1#3", "locator": "§62.35-50 표 1"}
    conv.number(rows + [CHUNK_D, table])  # [1][2] 표 행, [3] (d), [4] 표 1
    cites = find_citations("압력 [1]. 매뉴얼 2.1 참조. 【3】과 [2-3]. 46 CFR 62.35-50(d)(1), §62.35-50 표 1. 2026년 [2026], 매뉴얼 4장.", conv)
    assert [(c.n, c.locator, c.verified) for c in cites] == [
        (1, "매뉴얼 2.1", True),  # 매뉴얼 2.1 언급은 [1]과 같은 위치라 다시 내지 않는다
        (3, "§62.35-50(d)", True),  # 【3】, 그리고 (d)(1)은 검색된 (d) 안의 하위 문단
        (2, "매뉴얼 2.1", True),  # [2-3] 범위
        (4, "§62.35-50 표 1", True),
    ]  # [2026]과 "매뉴얼 4장"은 인용이 아니다


def test_citations_by_number_and_by_locator():
    conv = Conversation()
    conv.number([CHUNK_44, CHUNK_D])
    cites = find_citations("압력부터 [1][7]. 규정 §62.35-50(d)와 매뉴얼 4.4, 그리고 §62.30-1(b).", conv)
    assert [(c.n, c.locator, c.verified) for c in cites] == [
        (1, "매뉴얼 4.4", True),
        (7, "[7]", False),  # 검색된 적 없는 번호
        (2, "§62.35-50(d)", True),  # 번호 대신 위치로 쓴 인용도 검색된 조각이면 확인됨
        (None, "§62.30-1(b)", False),  # 검색된 적 없는 위치
    ]  # 매뉴얼 4.4는 [1]로 이미 인용해 한 번만


def test_tool_error_goes_back_to_the_model_and_it_retries():
    box = FakeBox({"read_sensors": [ToolResult(False, error="모르는 태그 'LO_PRESS'. 비슷한 태그: ME_LO_PRESS_IN."), ToolResult(True, {"sensors": [{"tag": "ME_LO_PRESS_IN", "value": 1.62, "unit": "bar"}]})]})
    llm = FakeLLM(calls(("read_sensors", {"tags": ["LO_PRESS"]})), calls(("read_sensors", {"tags": ["ME_LO_PRESS_IN"]})), answer("압력은 1.62 bar다."))
    events = run(Conversation(), "압력?", llm, box)
    assert [e.ok for e in events if isinstance(e, StepEnd)] == [False, True]
    assert "비슷한 태그" in json.loads(llm.seen[1][-1]["content"])["error"]
    assert isinstance(events[-1], Done) and events[-1].rounds == 2


def test_stops_after_three_failed_rounds_in_a_row():
    box = FakeBox({"read_sensors": ToolResult(False, error="모르는 태그")})
    llm = FakeLLM(*[calls(("read_sensors", {"tags": ["X"]}))] * 3)
    events = run(Conversation(), "?", llm, box)
    assert events[-1] == Error("tool_errors", "부른 도구가 모두 실패한 왕복이 3번 이어져 멈췄다.")


def test_several_failures_in_one_round_count_once_so_the_model_can_fix_them():
    """리뷰: 한 왕복에서 도구 3개가 모두 실패하면 모델이 오류를 보기도 전에 멈췄다."""
    box = FakeBox({"read_sensors": [ToolResult(False, error="모르는 태그")] * 3 + [ToolResult(True, {"sensors": []})]})
    llm = FakeLLM(calls(*[("read_sensors", {"tags": ["X"]})] * 3), calls(("read_sensors", {"tags": ["ME_RPM"]})), answer("답."))
    events = run(Conversation(), "?", llm, box)
    assert isinstance(events[-1], Done) and events[-1].rounds == 2
    assert sum(m["role"] == "tool" and "error" in m["content"] for m in llm.seen[1]) == 3  # 모델이 오류 셋을 봤다


def test_failed_turn_is_rolled_back_so_the_next_question_starts_clean():
    box = FakeBox({"list_tags": ToolResult(True, {"tags": []})})
    conv = Conversation()
    run(conv, "첫 질문", FakeLLM(*[calls(("list_tags", {}))] * (MAX_ROUNDS + 1)), box)
    assert [m["role"] for m in conv.messages] == ["system"]  # tool_calls만 남은 assistant 메시지 같은 것이 없다


def test_model_failure_becomes_an_error_event():
    async def broken(messages, tools, opts):
        raise LLMError("Ollama(http://x)가 120초 동안 답하지 않았다")
        yield  # noqa — 생성기로 만들기 위함

    conv = Conversation()
    events = run(conv, "?", broken, FakeBox({}))
    assert events == [Error("llm", "Ollama(http://x)가 120초 동안 답하지 않았다")]
    assert [m["role"] for m in conv.messages] == ["system"]


def test_stops_after_too_many_rounds():
    box = FakeBox({"list_tags": ToolResult(True, {"tags": []})})
    llm = FakeLLM(*[calls(("list_tags", {}))] * (MAX_ROUNDS + 1))
    events = run(Conversation(), "?", llm, box)
    assert isinstance(events[-1], Error) and events[-1].code == "too_many_steps"
    assert sum(isinstance(e, StepStart) for e in events) == MAX_ROUNDS


def test_context_near_limit_is_reported():
    events = run(Conversation(), "?", FakeLLM(answer("답.", prompt=7500)), FakeBox({}), ChatOptions(num_ctx=8192))
    assert events[-1].context_near_limit
    events = run(Conversation(), "?", FakeLLM(answer("답.", prompt=3000)), FakeBox({}), ChatOptions(num_ctx=8192))
    assert not events[-1].context_near_limit


def test_tokens_stream_before_the_answer_is_complete():
    events = run(Conversation(), "?", FakeLLM(answer("압력이 낮다 원인은 필터다.")), FakeBox({}))
    assert "".join(e.text for e in events if isinstance(e, Token)) == "압력이 낮다 원인은 필터다."


def test_stopping_early_closes_the_model_stream():
    """화면의 중단 버튼처럼 에이전트를 중간에 그만 읽으면 모델 스트림도 닫혀야 Ollama가 생성을 멈춘다."""
    closed = []

    async def llm(messages, tools, opts):
        try:
            yield TextDelta("한 ")
            yield TextDelta("글자씩")
            yield LLMDone(10, 2, 1.0, 0.0)
        finally:
            closed.append(True)

    async def go():
        async with aclosing(run_turn(Conversation(), "?", FakeBox({}), ChatOptions(), llm=llm)) as events:
            async for ev in events:
                break  # 첫 글자만 받고 중단

    asyncio.run(go())
    assert closed == [True]


def test_guard_sends_an_unsearched_answer_back_to_search():
    """평가 기준선에서 실제로 나온 모양 — 검색 없이 [1]을 붙인 답을 받지 않고 검색하게 한다."""
    box = FakeBox({"list_active_alarms": ToolResult(True, {"alarms": []}), "search_docs": ToolResult(True, {"results": [CHUNK_44]})})
    llm = FakeLLM(
        calls(("list_active_alarms", {})),
        answer("필터를 점검한다 [1]."),  # 검색 없이 지어 붙인 번호
        calls(("search_docs", {"query": "윤활유 압력 낮음", "source": "manual"})),
        answer("감속 상태에서 압력부터 회복한다 [1]."),
    )
    events = run(Conversation(), "알람 분석", llm, box)
    steps = [(e.tool, e.args) for e in events if isinstance(e, StepStart)]
    assert steps[1] == ("check_citations", {"unverified": ["[1]"]})
    assert "(자동 검사)" in llm.seen[2][-1]["content"]  # 모델이 되돌림 이유를 봤다
    assert [(c.n, c.verified) for c in events if isinstance(c, Citation)] == [(1, True)]  # 되돌린 답의 인용은 내지 않는다
    assert events[-1].rechecks == 1


def test_guard_gives_up_after_max_rechecks_and_reports_the_answer():
    llm = FakeLLM(*[answer("지어낸 근거 [3].")] * (MAX_RECHECKS + 1))
    events = run(Conversation(), "?", llm, FakeBox({}))
    assert events[-1].rechecks == MAX_RECHECKS
    assert [(c.n, c.verified) for c in events if isinstance(c, Citation)] == [(3, False)]  # 평가가 잡도록 그대로 낸다


def test_guard_can_be_turned_off_for_comparison():
    async def go():
        return [ev async for ev in run_turn(Conversation(), "?", FakeBox({}), ChatOptions(), llm=FakeLLM(answer("근거 [1].")), guard=False)]
    events = asyncio.run(go())
    assert not any(isinstance(e, StepStart) for e in events) and events[-1].rechecks == 0


def test_answer_without_citations_is_not_sent_back():
    events = run(Conversation(), "현재 압력은?", FakeLLM(answer("1.62 bar다.")), FakeBox({}))
    assert isinstance(events[-1], Done) and events[-1].rechecks == 0


def test_prompt_version_is_chosen_per_conversation():
    from app.prompts import PROMPTS

    assert Conversation(prompt="v1").messages[0]["content"] == PROMPTS["v1"]
    assert Conversation().messages[0]["content"] == PROMPTS["v2"]


def test_rejected_draft_and_note_leave_the_history_once_the_answer_is_accepted():
    """리뷰: 남겨 두면 다음 질문에서 모델이 "(자동 검사) 다시 답하라"를 다시 읽고 앞 답을 되풀이했다."""
    box = FakeBox({"search_docs": ToolResult(True, {"results": [CHUNK_44]})})
    llm = FakeLLM(answer("지어낸 [1]."), calls(("search_docs", {"query": "q"})), answer("근거 있는 답 [1]."))
    conv = Conversation()
    run(conv, "질문", llm, box)
    contents = [m["content"] for m in conv.messages]
    assert not any("(자동 검사)" in c or c == "지어낸 [1]." for c in contents)
    assert contents[-1] == "근거 있는 답 [1]." and [m["role"] for m in conv.messages] == ["system", "user", "assistant", "tool", "assistant"]


def test_recheck_does_not_count_as_a_tool_round():
    """리뷰: 되돌림이 왕복으로 세지면 7번째 왕복의 초안을 되돌린 뒤 검색하자마자 too_many_steps로 차례 전체가 사라졌다."""
    box = FakeBox({"list_tags": ToolResult(True, {"tags": []}), "search_docs": ToolResult(True, {"results": [CHUNK_44]})})
    llm = FakeLLM(*[calls(("list_tags", {}))] * (MAX_ROUNDS - 1), answer("지어낸 [1]."), calls(("search_docs", {"query": "q"})), answer("답 [1]."))
    events = run(Conversation(), "?", llm, box)
    done = events[-1]
    assert isinstance(done, Done) and done.rounds == MAX_ROUNDS and done.rechecks == 1 and done.model_calls == MAX_ROUNDS + 2


def test_token_round_counts_model_calls_so_a_redo_is_separate_from_its_draft():
    llm = FakeLLM(answer("지어낸 [3]."), answer("근거 없이 다시 [3]."), answer("마지막 [3]."))
    tokens = [e for e in run(Conversation(), "?", llm, FakeBox({})) if isinstance(e, Token)]
    assert sorted({t.round for t in tokens}) == [0, 1, 2]


def test_partial_read_reaches_the_model_and_the_step_summary():
    """일부만 읽힌 결과도 성공이고, 모르는 태그와 비슷한 태그가 모델에게 그대로 간다."""
    data = {"sensors": [{"tag": "ME_LO_PRESS_IN", "value": 1.62, "unit": "bar"}], "unknown": [{"tag": "LO_TANK_LEVEL", "similar": ["LO_SUMP_LEVEL"]}]}
    box = FakeBox({"read_sensors": ToolResult(True, data)})
    llm = FakeLLM(calls(("read_sensors", {"tags": ["ME_LO_PRESS_IN", "LO_TANK_LEVEL"]})), answer("압력 1.62 bar."))
    events = run(Conversation(), "?", llm, box)
    assert [e.summary for e in events if isinstance(e, StepEnd)] == ["ME_LO_PRESS_IN 1.62bar · 모르는 태그 LO_TANK_LEVEL"]
    assert json.loads(llm.seen[1][-1]["content"])["unknown"] == [{"tag": "LO_TANK_LEVEL", "similar": ["LO_SUMP_LEVEL"]}]
