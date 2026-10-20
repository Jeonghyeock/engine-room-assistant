import { describe, expect, it } from "vitest";
import type { ChatEvent, DoneInfo } from "./api";
import { applyEvent, newTurn, split } from "./turn";

const done = (model_calls: number): ChatEvent => ({
  event: "done",
  data: { model_calls, tokens: 1, tok_per_s: 1, elapsed_ms: 1, rounds: 1, rechecks: 0, max_prompt_tokens: 1, num_ctx: 8192, truncated: false, context_near_limit: false } as DoneInfo,
});
const token = (round: number, text: string): ChatEvent => ({ event: "token", data: { round, text } });
const step = (id: number, tool: string): ChatEvent => ({ event: "step_start", data: { id, tool, args: {}, auto: false } });
const play = (...events: ChatEvent[]) => events.reduce(applyEvent, newTurn("q"));

describe("답 자리(split)", () => {
  it("근거 검사가 시작되면 되돌린 초안은 곧바로 답 자리에서 내려간다(#21 리뷰 GUARD)", () => {
    const t = play(token(0, "초안: 우회해도 된다 [9]."), step(1, "check_citations"));
    expect(split(t)).toEqual({ earlier: ["초안: 우회해도 된다 [9]."], answer: "" });
    expect(split(applyEvent(t, token(1, "우회하지 않는다 [1].")))).toEqual({ earlier: ["초안: 우회해도 된다 [9]."], answer: "우회하지 않는다 [1]." });
  });

  it("도구를 부르는 동안 도구 전 말은 답 자리에 없다(PRE)", () => {
    const t = play(token(0, "우선 확인하겠다."), step(1, "list_active_alarms"));
    expect(split(t).answer).toBe("");
  });

  it("마지막 모델 호출이 빈 글이면 답은 비어 있다 — 도구 전 말을 답으로 올리지 않는다(EMPTY)", () => {
    const t = play(token(0, "우선 확인하겠다 [7]."), step(1, "search_docs"), done(2));
    expect(split(t)).toEqual({ earlier: ["우선 확인하겠다 [7]."], answer: "" });
  });

  it("도구 없이 바로 답하면 그 글이 답이다", () => {
    expect(split(play(token(0, "결론: "), token(0, "안 된다."), done(1)))).toEqual({ earlier: [], answer: "결론: 안 된다." });
  });
});
