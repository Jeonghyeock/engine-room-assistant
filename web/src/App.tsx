import { useEffect, useRef, useState } from "react";
import {
  ApiError,
  getAlarms,
  getScenarios,
  startScenario,
  streamChat,
  type Alarm,
  type ChatEvent,
  type Citation,
  type DoneInfo,
  type Scenario,
} from "./api";

const ANALYZE_ALL = "지금 울리는 알람을 분석하고 조치를 권고해 줘";

type Step = { id: number; tool: string; args: Record<string, unknown>; auto: boolean; ok?: boolean; summary?: string };

type Turn = {
  question: string;
  steps: Step[];
  rounds: string[]; // 모델 호출마다 쓴 글 — 마지막이 답, 앞은 도구를 부르기 전의 말이나 근거 검사로 되돌린 초안
  citations: Citation[];
  done?: DoneInfo;
  error?: string;
  status: "streaming" | "done" | "stopped" | "error";
};

function applyEvent(t: Turn, e: ChatEvent): Turn {
  switch (e.event) {
    case "step_start":
      return { ...t, steps: [...t.steps, { ...e.data }] };
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
      return { ...t, done: e.data, status: "done" };
    case "error":
      return { ...t, error: `${e.data.code}: ${e.data.message}`, status: "error" };
    default:
      return t;
  }
}

function stepLabel(s: Step): string {
  if (s.tool === "check_citations") return "근거 검사"; // 무엇을 되돌렸는지는 step_end의 요약이 말한다
  const args = Object.entries(s.args)
    .map(([k, v]) => `${k}=${JSON.stringify(v)}`)
    .join(", ");
  return `${s.auto ? "(자동) " : ""}${s.tool}(${args})`;
}

/** 답의 [n]을 누르면 근거 패널에서 그 조각을 연다. */
function AnswerText({ text, citations, onCite }: { text: string; citations: Citation[]; onCite: (n: number) => void }) {
  const parts = text.split(/(\[\d+\])/g);
  return (
    <p className="answer">
      {parts.map((p, i) => {
        const m = /^\[(\d+)\]$/.exec(p);
        if (!m) return <span key={i}>{p}</span>;
        const n = Number(m[1]);
        const c = citations.find((c) => c.n === n);
        const cls = c === undefined ? "cite" : c.verified ? "cite verified" : "cite unverified";
        return (
          <button key={i} className={cls} onClick={() => onCite(n)} title={c ? c.locator : "근거 정보를 기다리는 중"}>
            {p}
          </button>
        );
      })}
    </p>
  );
}

function TurnView({ turn, onCite }: { turn: Turn; onCite: (n: number) => void }) {
  const last = turn.rounds.length - 1;
  const earlier = turn.rounds.slice(0, Math.max(last, 0)).filter((r) => r?.trim());
  const answer = last >= 0 ? (turn.rounds[last] ?? "") : "";
  const unverified = turn.citations.filter((c) => !c.verified);
  return (
    <div className="turn">
      <div className="question">{turn.question}</div>
      {turn.steps.length > 0 && (
        <ul className="steps">
          {turn.steps.map((s) => (
            <li key={s.id} className={s.ok === undefined ? "running" : s.ok ? "ok" : "fail"}>
              <span className="mark">{s.ok === undefined ? "…" : s.ok ? "✓" : "✗"}</span> {stepLabel(s)}
              {s.summary && <span className="summary"> → {s.summary}</span>}
            </li>
          ))}
        </ul>
      )}
      {earlier.length > 0 && (
        <details className="earlier">
          <summary>도구 전 말·되돌린 초안 {earlier.length}개</summary>
          {earlier.map((r, i) => (
            <p key={i}>{r}</p>
          ))}
        </details>
      )}
      {answer && <AnswerText text={answer} citations={turn.citations} onCite={onCite} />}
      {unverified.length > 0 && (
        <div className="warn">⚠ 이번 대화에서 검색된 적 없는 근거: {unverified.map((c) => c.locator).join(", ")}</div>
      )}
      {turn.status === "streaming" && <div className="status">답하는 중…</div>}
      {turn.status === "stopped" && <div className="status">중단함 — 이 질문은 대화 기록에 남지 않는다.</div>}
      {turn.error && <div className="error">✗ {turn.error}</div>}
      {turn.done && (
        <div className="meta">
          {turn.done.tokens} 토큰 · {turn.done.tok_per_s} tok/s · {(turn.done.elapsed_ms / 1000).toFixed(1)}초 · 문맥 {turn.done.max_prompt_tokens}/
          {turn.done.num_ctx}
          {turn.done.context_near_limit && <span className="warn"> ⚠ 문맥이 한도에 가깝다 — 오래된 대화가 잘렸을 수 있다</span>}
          {turn.done.truncated && <span className="warn"> ⚠ 답이 길이 한도에서 잘렸다</span>}
        </div>
      )}
    </div>
  );
}

export default function App() {
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  const [current, setCurrent] = useState<string>("");
  const [alarms, setAlarms] = useState<Alarm[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [selected, setSelected] = useState<number | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const busy = turns.at(-1)?.status === "streaming";

  const loadAlarms = () =>
    getAlarms()
      .then((r) => setAlarms(r.alarms))
      .catch((e) => setNotice(`알람을 읽지 못했다: ${e.message}`));

  useEffect(() => {
    getScenarios()
      .then((r) => {
        setScenarios(r.scenarios);
        setCurrent(r.current);
      })
      .catch((e) => setNotice(`백엔드에 연결하지 못했다: ${e.message}`));
    loadAlarms();
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" }); // 새 Chromium은 Promise를 돌려준다 — 효과 함수가 그것을 돌려주면 React가 정리 함수로 부르다 깨진다
  }, [turns]);

  const update = (fn: (t: Turn) => Turn) => setTurns((ts) => [...ts.slice(0, -1), fn(ts[ts.length - 1])]);

  async function ask(question: string) {
    if (!question.trim() || busy) return;
    setInput("");
    setNotice(null);
    setTurns((ts) => [...ts, { question, steps: [], rounds: [], citations: [], status: "streaming" }]);
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    try {
      await streamChat({ conversation_id: conversationId, message: question }, ctrl.signal, (e) => {
        if (e.event === "conversation") setConversationId(e.data.id);
        else update((t) => applyEvent(t, e));
      });
      update((t) => (t.status === "streaming" ? { ...t, status: "error", error: "스트림이 done 없이 끝났다" } : t));
    } catch (e) {
      if (ctrl.signal.aborted) update((t) => ({ ...t, status: "stopped" }));
      else update((t) => ({ ...t, status: "error", error: e instanceof ApiError ? `${e.status} ${e.message}` : String(e) }));
    } finally {
      abortRef.current = null;
    }
  }

  async function switchScenario(id: string) {
    if (busy) return;
    try {
      const r = await startScenario(id);
      setCurrent(r.current);
      setConversationId(null);
      setTurns([]);
      setSelected(null);
      await loadAlarms();
    } catch (e) {
      setNotice(`시나리오를 바꾸지 못했다: ${(e as Error).message}`);
    }
  }

  // 같은 대화 안에서 번호는 같은 조각이다(백엔드가 대화마다 번호를 붙인다)
  const citations = new Map<number, Citation>();
  for (const t of turns) for (const c of t.citations) if (c.n !== null && c.verified) citations.set(c.n, c);

  return (
    <div className="app">
      <header>
        <h1>기관실 알람 어시스턴트</h1>
        <span className="disclaimer">가상 선박·가상 매뉴얼 · 권고만 한다 — 판단과 조작은 사람이</span>
      </header>
      <aside className="left">
        <label>
          시나리오{" "}
          <select value={current} onChange={(e) => switchScenario(e.target.value)} disabled={busy}>
            {scenarios.map((s) => (
              <option key={s.id} value={s.id}>
                {s.id} — {s.title}
              </option>
            ))}
          </select>
        </label>
        <h2>울리는 알람</h2>
        <ul className="alarms">
          {alarms.map((a) => (
            <li key={a.tag}>
              <button onClick={() => ask(`${a.name} 알람을 분석하고 조치를 권고해 줘`)} disabled={busy}>
                <span className={`level level-${a.level}`}>{a.level}</span> {a.name}
                <span className="value">
                  {a.value} {a.unit} ({a.limit}){a.minutes_ago !== null && ` · ${a.minutes_ago}분 전`}
                </span>
              </button>
            </li>
          ))}
        </ul>
        <button className="all" onClick={() => ask(ANALYZE_ALL)} disabled={busy || alarms.length === 0}>
          알람 전체 분석
        </button>
      </aside>
      <main>
        <div className="turns">
          {turns.map((t, i) => (
            <TurnView key={i} turn={t} onCite={setSelected} />
          ))}
          {turns.length === 0 && <p className="empty">왼쪽 알람을 누르거나 질문을 입력한다.</p>}
          <div ref={bottomRef} />
        </div>
        {notice && <div className="error">{notice}</div>}
        <form
          onSubmit={(e) => {
            e.preventDefault();
            ask(input);
          }}
        >
          <input value={input} onChange={(e) => setInput(e.target.value)} placeholder="질문 (예: 트립을 잠시 우회해도 되나요?)" disabled={busy} />
          {busy ? (
            <button type="button" className="stop" onClick={() => abortRef.current?.abort()}>
              중단
            </button>
          ) : (
            <button type="submit" disabled={!input.trim()}>
              보내기
            </button>
          )}
        </form>
      </main>
      <aside className="right">
        <h2>근거</h2>
        {citations.size === 0 && <p className="empty">답의 [n]을 누르면 원문 조각이 여기 나온다.</p>}
        <ul className="sources">
          {[...citations.values()]
            .sort((a, b) => (a.n ?? 0) - (b.n ?? 0))
            .map((c) => (
              <li key={c.n} className={c.n === selected ? "selected" : ""} onClick={() => setSelected(c.n)}>
                <div className="loc">
                  [{c.n}] {c.locator}
                </div>
                <div className="title">{c.title}</div>
                {c.n === selected && <div className="snippet">{c.snippet}</div>}
              </li>
            ))}
        </ul>
      </aside>
    </div>
  );
}
