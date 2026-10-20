// 질문 하나(차례)의 화면 상태와, 이벤트를 받아 바꾸는 순수 함수.
import type { ChatEvent, Citation, DoneInfo } from "./api";

export type Step = { id: number; tool: string; args: Record<string, unknown>; auto: boolean; ok?: boolean; summary?: string };

export type Turn = {
  question: string;
  steps: Step[];
  rounds: string[]; // 모델 호출(token.round)마다 쓴 글
  settled: number; // 이 앞의 round는 답이 아니다 — 도구를 부르기 전의 말이거나 근거 검사로 되돌린 초안
  citations: Citation[];
  done?: DoneInfo;
  error?: string;
  status: "streaming" | "done" | "stopped" | "error";
};

export const newTurn = (question: string): Turn => ({ question, steps: [], rounds: [], settled: 0, citations: [], status: "streaming" });

export function applyEvent(t: Turn, e: ChatEvent): Turn {
  switch (e.event) {
    case "step_start":
      // 단계가 시작되면 지금까지의 글은 답이 아니다(도구 전 말, 또는 근거 검사가 되돌린 초안)
      return { ...t, steps: [...t.steps, { ...e.data }], settled: t.rounds.length };
    case "step_end":
      return { ...t, steps: t.steps.map((s) => (s.id === e.data.id ? { ...s, ok: e.data.ok, summary: e.data.summary } : s)) };
    case "token": {
      const rounds = [...t.rounds];
      rounds[e.data.round] = (rounds[e.data.round] ?? "") + e.data.text;
      return { ...t, rounds };
    }
    case "citation":
      return { ...t, citations: [...t.citations, e.data] };
    case "done":
      // 마지막 답은 마지막 모델 호출의 글이다(설계 6.5) — 그 호출이 빈 글이어도 앞의 글을 답으로 올리지 않는다
      return { ...t, done: e.data, status: "done", settled: Math.max(e.data.model_calls - 1, 0) };
    case "error":
      return { ...t, error: `${e.data.code}: ${e.data.message}`, status: "error" };
    default:
      return t;
  }
}

/** 답 자리의 글과, 접어 둘 앞의 글. */
export function split(t: Turn): { earlier: string[]; answer: string } {
  const earlier = t.rounds.slice(0, t.settled).filter((r) => r?.trim());
  const answer = t.rounds.slice(t.settled).join("");
  return { earlier, answer };
}

/** 서버가 이 차례를 되돌렸는가 — 끝까지 답하지 못했으면(중단·오류) 기록에 남지 않는다(백엔드 run_turn). */
export const rolledBack = (t: Turn) => t.status === "stopped" || t.status === "error";
