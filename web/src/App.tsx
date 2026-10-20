import { useEffect, useRef, useState } from "react";
import { ApiError, getAlarms, getScenarios, startScenario, streamChat, type Alarm, type Citation, type Scenario } from "./api";
import { splitInline } from "./inline";
import { applyEvent, newTurn, rolledBack, split, type Step, type Turn } from "./turn";

const ANALYZE_ALL = "지금 울리는 알람을 분석하고 조치를 권고해 줘";

function stepLabel(s: Step): string {
  if (s.tool === "check_citations") return "근거 검사"; // 무엇을 되돌렸는지는 step_end의 요약이 말한다
  const args = Object.entries(s.args)
    .map(([k, v]) => `${k}=${JSON.stringify(v)}`)
    .join(", ");
  return `${s.auto ? "(자동) " : ""}${s.tool}(${args})`;
}

/** 답의 [n]을 누르면 근거 패널에서 그 조각을 연다. 되돌린 차례의 [n]은 버튼으로 만들지 않는다 —
 *  서버가 되돌린 번호는 다음 질문에서 다른 조각에 다시 붙을 수 있다. */
function AnswerText({ text, citations, live, onCite }: { text: string; citations: Citation[]; live: boolean; onCite: (n: number) => void }) {
  return (
    <p className="answer">
      {splitInline(text).map((p, i) => {
        if (p.kind === "bold") return <strong key={i}>{p.value}</strong>;
        if (p.kind === "text" || !live) return <span key={i}>{p.value}</span>;
        const c = citations.find((c) => c.n === p.n);
        const cls = c === undefined ? "cite" : c.verified ? "cite verified" : "cite unverified";
        return (
          <button key={i} className={cls} onClick={() => onCite(p.n)} title={c ? c.locator : "근거 정보를 기다리는 중"}>
            {p.value}
          </button>
        );
      })}
    </p>
  );
}

function TurnView({ turn, onCite }: { turn: Turn; onCite: (n: number) => void }) {
  const { earlier, answer } = split(turn);
  const unverified = turn.citations.filter((c) => !c.verified);
  const back = rolledBack(turn);
  return (
    <div className="turn">
      <div className="question">{turn.question}</div>
      {turn.steps.length > 0 && (
        <ul className="steps">
          {turn.steps.map((s) => (
            <li key={s.id} className={s.ok === undefined ? "running" : s.ok ? "ok" : "fail"}>
              <span className="mark">{s.ok === undefined ? (back ? "–" : "…") : s.ok ? "✓" : "✗"}</span> {stepLabel(s)}
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
      {answer && <AnswerText text={answer} citations={turn.citations} live={!back} onCite={onCite} />}
      {unverified.length > 0 && (
        <div className="warn">⚠ 이번 대화에서 검색된 적 없는 근거: {unverified.map((c) => c.locator).join(", ")}</div>
      )}
      {turn.status === "streaming" && <div className="status">답하는 중…</div>}
      {turn.error && <div className="error">✗ {turn.error}</div>}
      {back && <div className="status">{turn.status === "stopped" ? "중단함" : "끝까지 답하지 못함"} — 이 질문은 대화 기록에 남지 않는다.</div>}
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
  const [notices, setNotices] = useState<string[]>([]);
  const [switching, setSwitching] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const streaming = turns.at(-1)?.status === "streaming";
  const busy = streaming || switching;

  const notify = (msg: string) => setNotices((ns) => [...ns, msg]);

  async function refresh() {
    try {
      const r = await getScenarios();
      setScenarios(r.scenarios);
      setCurrent(r.current);
    } catch (e) {
      notify(`백엔드에 연결하지 못했다: ${(e as Error).message}`);
    }
    try {
      setAlarms((await getAlarms()).alarms);
    } catch (e) {
      notify(`알람을 읽지 못했다: ${(e as Error).message}`);
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" }); // 새 Chromium은 Promise를 돌려준다 — 효과 함수가 그것을 돌려주면 React가 정리 함수로 부르다 깨진다
  }, [turns]);

  // 마지막 차례만 바꾼다. 그사이 대화가 비었으면(시나리오 바꾸기) 그대로 둔다.
  const update = (fn: (t: Turn) => Turn) => setTurns((ts) => (ts.length === 0 ? ts : [...ts.slice(0, -1), fn(ts[ts.length - 1])]));

  function newConversation() {
    setConversationId(null);
    setTurns([]);
    setSelected(null);
  }

  async function ask(question: string) {
    if (!question.trim() || busy) return;
    setInput("");
    setNotices([]);
    setTurns((ts) => [...ts, newTurn(question)]);
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    try {
      await streamChat({ conversation_id: conversationId, message: question }, ctrl.signal, (e) => {
        if (e.event === "conversation") setConversationId(e.data.id);
        else update((t) => applyEvent(t, e));
      });
      update((t) => (t.status === "streaming" ? { ...t, status: "error", error: "스트림이 done 없이 끝났다" } : t));
    } catch (e) {
      if (ctrl.signal.aborted) {
        update((t) => (t.status === "streaming" ? { ...t, status: "stopped" } : t));
      } else if (e instanceof ApiError && e.status === 404) {
        // 서버가 이 대화를 모른다(다른 곳에서 시나리오를 바꿨거나 서버가 다시 떴다) — 새 대화로
        newConversation();
        notify("서버에 이 대화가 없다(시나리오가 바뀌었거나 서버가 다시 시작됨) — 새 대화로 다시 물어 주세요.");
        await refresh();
      } else {
        ctrl.abort(); // 읽다가 실패했으면 스트림을 끊어 서버가 그 차례를 정리하게 한다
        const msg = e instanceof ApiError ? `${e.status} ${e.message}` : String(e);
        update((t) => (t.status === "streaming" ? { ...t, status: "error", error: msg } : t));
      }
    } finally {
      abortRef.current = null;
    }
  }

  async function switchScenario(id: string) {
    if (busy) return;
    setSwitching(true);
    try {
      const r = await startScenario(id);
      setCurrent(r.current);
      newConversation();
      setNotices([]);
      setAlarms((await getAlarms()).alarms);
    } catch (e) {
      notify(`시나리오를 바꾸지 못했다: ${(e as Error).message}`);
    } finally {
      setSwitching(false);
    }
  }

  // 같은 대화 안에서 번호는 같은 조각이다(백엔드가 대화마다 번호를 붙인다). 되돌린 차례의 인용은 넣지 않는다.
  const citations = new Map<number, Citation>();
  for (const t of turns) if (!rolledBack(t)) for (const c of t.citations) if (c.n !== null && c.verified) citations.set(c.n, c);

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
            // 한 태그가 경보와 감속을 함께 넘으면 두 줄로 온다 — 태그만으로는 겹친다(#21 리뷰)
            <li key={`${a.tag}:${a.level}`}>
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
        {notices.map((n, i) => (
          <div key={i} className="error">
            {n}
          </div>
        ))}
        <form
          onSubmit={(e) => {
            e.preventDefault();
            ask(input);
          }}
        >
          <input aria-label="질문" value={input} onChange={(e) => setInput(e.target.value)} placeholder="질문 (예: 트립을 잠시 우회해도 되나요?)" disabled={busy} />
          {streaming ? (
            <button type="button" className="stop" onClick={() => abortRef.current?.abort()}>
              중단
            </button>
          ) : (
            <button type="submit" disabled={!input.trim() || switching}>
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
              <li key={c.n} className={c.n === selected ? "selected" : ""}>
                <button className="source" onClick={() => setSelected(c.n)} aria-expanded={c.n === selected}>
                  <span className="loc">
                    [{c.n}] {c.locator}
                  </span>
                  <span className="title">{c.title}</span>
                </button>
                {c.n === selected && <div className="snippet">{c.snippet}</div>}
              </li>
            ))}
        </ul>
      </aside>
    </div>
  );
}
