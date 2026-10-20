// 백엔드 API(설계 문서 6.5, backend/app/api.py)의 모양과 부르는 함수.
import { SseParser } from "./sse";

export type Alarm = {
  tag: string;
  name: string;
  level: string;
  value: number;
  unit: string;
  limit: string;
  since: string | null;
  minutes_ago: number | null;
};

export type Scenario = { id: string; title: string; summary: string };

export type Citation = {
  n: number | null;
  chunk_id: string | null;
  source: string | null;
  locator: string;
  title: string | null;
  snippet: string | null;
  verified: boolean;
};

export type DoneInfo = {
  tokens: number;
  tok_per_s: number;
  elapsed_ms: number;
  rounds: number;
  rechecks: number;
  max_prompt_tokens: number;
  num_ctx: number;
  truncated: boolean;
  context_near_limit: boolean;
};

export type ChatEvent =
  | { event: "conversation"; data: { id: string; scenario: string } }
  | { event: "step_start"; data: { id: number; tool: string; args: Record<string, unknown>; auto: boolean } }
  | { event: "step_end"; data: { id: number; ok: boolean; summary: string } }
  | { event: "token"; data: { text: string; round: number } }
  | { event: "citation"; data: Citation }
  | { event: "done"; data: DoneInfo }
  | { event: "error"; data: { code: string; message: string } };

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function failure(res: Response): Promise<ApiError> {
  const body = await res.json().catch(() => null);
  const detail = body?.detail;
  return new ApiError(res.status, typeof detail === "string" ? detail : JSON.stringify(detail ?? res.statusText));
}

async function getJson<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  if (!res.ok) throw await failure(res);
  return res.json();
}

export const getAlarms = () => getJson<{ scenario: string; alarms: Alarm[] }>("/api/alarms");
export const getScenarios = () => getJson<{ current: string; scenarios: Scenario[] }>("/api/scenarios");
export const startScenario = (id: string) => getJson<{ current: string }>(`/api/scenarios/${id}/start`, { method: "POST" });

/** 질문 하나의 이벤트 스트림을 끝까지 읽는다. signal을 끊으면(중단 버튼) 서버는 그 차례를 되돌린다. */
export async function streamChat(
  body: { conversation_id: string | null; message: string },
  signal: AbortSignal,
  onEvent: (e: ChatEvent) => void,
): Promise<void> {
  const res = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok || !res.body) throw await failure(res);
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  const parser = new SseParser();
  for (;;) {
    const { value, done } = await reader.read();
    if (done) return;
    for (const e of parser.push(value)) onEvent(e as ChatEvent);
  }
}
