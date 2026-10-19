"""에이전트 시나리오 평가 — scenarios.yaml의 시나리오를 실제 모델로 여러 번 돌려 검사한다.

    uv run python -m evals.scenarios                 # S1~S3 × 씨앗 3개 → evals/results/scenarios-<label>.md
    uv run python -m evals.scenarios --think --label think

결과 표(통과 수)와 함께 모든 답을 evals/results/transcripts/<label>/에 저장한다. 판정(qwen3:8b의 예/아니오)은
표시일 뿐이고, 최종 판단은 저장된 답을 사람이 읽고 한다.
"""

import argparse
import asyncio
import datetime as dt
import re
from contextlib import aclosing
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from app.agent import Citation, Conversation, Done, Error, StepEnd, StepStart, Token, run_turn
from app.llm import ChatOptions, TextDelta, stream_chat
from app.tools import ToolBox

HERE = Path(__file__).resolve().parent
SEEDS = [1, 2, 3]


@dataclass
class Turn:
    ask: str
    steps: list[dict] = field(default_factory=list)  # {tool, args, ok, summary}
    answer: str = ""
    citations: list[Citation] = field(default_factory=list)
    done: Done | None = None
    error: Error | None = None


# ---- 정해진 검사 ----


def _covers(loc: str, want: str) -> bool:
    """인용 위치가 원하는 위치와 같거나 그 안의 문단이면 맞다(§62.35-50(d) ⊃ §62.35-50(d)(1))."""
    return loc == want or loc.startswith(want + "(")


def check(turn: Turn, expect: dict) -> dict[str, bool]:
    called = [s["tool"] for s in turn.steps]
    out = {"답함": turn.done is not None and turn.error is None and bool(turn.answer.strip())}
    if expect.get("tools"):
        out["도구"] = all(t in called for t in expect["tools"])
    for want in expect.get("tool_args", []):
        out[f"{want['tool']}({want['value']})"] = any(
            s["tool"] == want["tool"] and str(s["args"].get(want["arg"], "")).upper() == want["value"] for s in turn.steps
        )
    verified = [c for c in turn.citations if c.verified]
    out["인용 있음"] = bool(verified)
    out["미확인 인용 없음"] = not any(not c.verified for c in turn.citations)
    if expect.get("cite_any"):
        out["필수 근거"] = any(_covers(c.locator, w) for c in verified for w in expect["cite_any"])
    out["문맥 여유"] = turn.done is not None and not turn.done.context_near_limit
    return out


# ---- 판정 ----

JUDGE_PROMPT = """너는 채점자다. 아래 질문과 답을 읽고 기준에 대해 "예" 또는 "아니오" 한 단어로만 답하라.

기준: {criterion}

질문: {ask}

답:
{answer}"""


def parse_judgement(text: str) -> bool | None:
    m = re.search(r"(예|아니오|아니요)", text)
    return None if not m else m.group(1) == "예"


async def judge(ask: str, answer: str, criterion: str) -> bool | None:
    prompt = JUDGE_PROMPT.format(criterion=criterion, ask=ask, answer=answer)
    text = []
    async with aclosing(stream_chat([{"role": "user", "content": prompt}], [], ChatOptions(num_predict=8))) as events:
        async for ev in events:
            if isinstance(ev, TextDelta):
                text.append(ev.text)
    return parse_judgement("".join(text))


# ---- 실행 ----


async def run_scenario(spec: dict, opts: ChatOptions) -> list[Turn]:
    turns = []
    conv = Conversation()
    async with ToolBox(spec["id"]) as box:
        for t in spec["turns"]:
            turn = Turn(t["ask"])
            round_ = 0
            async with aclosing(run_turn(conv, t["ask"], box, opts)) as events:
                async for ev in events:
                    match ev:
                        case StepStart():
                            turn.steps.append({"tool": ev.tool, "args": ev.args})
                        case StepEnd():
                            turn.steps[-1] |= {"ok": ev.ok, "summary": ev.summary}
                        case Token():
                            # 도구를 부르기 전의 짧은 말은 답이 아니다 — 마지막 왕복의 글만 답으로
                            if ev.round != round_:
                                turn.answer, round_ = "", ev.round
                            turn.answer += ev.text
                        case Citation():
                            turn.citations.append(ev)
                        case Done():
                            turn.done = ev
                        case Error():
                            turn.error = ev
            turns.append(turn)
            if turn.error:
                break
    return turns


def transcript(sid: str, seed: int, turns: list[Turn], results: list[dict]) -> str:
    lines = [f"# {sid} · 씨앗 {seed}", ""]
    for turn, res in zip(turns, results):
        lines += [f"## 질문: {turn.ask}", ""]
        for s in turn.steps:
            lines.append(f"- `{s['tool']}({s['args']})` → {'✓' if s.get('ok') else '✗'} {s.get('summary', '')}")
        lines += ["", turn.answer.strip() or "(답 없음)", ""]
        for c in turn.citations:
            lines.append(f"- [{c.n}] {c.locator} — {c.title}" if c.verified else f"- {c.locator} — ⚠ 검색된 적 없는 근거")
        if turn.done:
            d = turn.done
            lines.append(f"\n({d.rounds}번 도구 왕복, {d.tokens} 토큰, {d.tok_per_s} tok/s, {d.elapsed_ms / 1000:.1f}초, 문맥 최대 {d.max_prompt_tokens}/{d.num_ctx})")
        if turn.error:
            lines.append(f"\n✗ {turn.error.code}: {turn.error.message}")
        lines += ["", "검사: " + ", ".join(f"{k} {'✓' if v else '✗'}" for k, v in res.items()), ""]
    return "\n".join(lines)


async def evaluate(specs: list[dict], opts: ChatOptions, label: str) -> str:
    out_dir = HERE / "results" / "transcripts" / label
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []  # (시나리오, 차례 번호, 검사 이름) → 통과 수
    table: dict[tuple, list[bool]] = {}
    for spec in specs:
        for seed in SEEDS:
            turns = await run_scenario(spec, ChatOptions(**{**opts.__dict__, "seed": seed}))
            results = []
            for i, (turn, expect) in enumerate(zip(turns, spec["turns"]), 1):
                res = check(turn, expect)
                if turn.answer and expect.get("judge_pass"):
                    res["판정"] = bool(await judge(turn.ask, turn.answer, expect["judge_pass"]))
                if turn.answer and expect.get("judge_fail"):
                    res["판정"] = (await judge(turn.ask, turn.answer, expect["judge_fail"])) is False
                res["전체"] = all(res.values())
                results.append(res)
                for k, v in res.items():
                    table.setdefault((spec["id"], i, k), []).append(v)
            for i in range(len(turns), len(spec["turns"])):  # 앞 차례가 오류로 끝나 못 간 차례는 실패
                table.setdefault((spec["id"], i + 1, "전체"), []).append(False)
            (out_dir / f"{spec['id']}-seed{seed}.md").write_text(transcript(spec["id"], seed, turns, results), encoding="utf-8")
            print(f"{spec['id']} 씨앗 {seed}: " + " / ".join("✓" if r["전체"] else "✗" for r in results), flush=True)

    checks = ["답함", "도구", "인용 있음", "미확인 인용 없음", "필수 근거", "문맥 여유", "판정", "전체"]
    lines = [
        f"# 시나리오 평가 — {label}",
        "",
        f"{dt.date.today()} · {opts.model} · think={'켬' if opts.think else '끔'} · num_ctx {opts.num_ctx} · 씨앗 {SEEDS} · 칸은 통과 수/실행 수",
        "",
        "| 시나리오 | 질문 | " + " | ".join(checks) + " |",
        "|---|---|" + "---|" * len(checks),
    ]
    for spec in specs:
        for i, t in enumerate(spec["turns"], 1):
            cells = []
            for k in checks:
                v = table.get((spec["id"], i, k))
                if not v and k == "도구":
                    v = None
                cells.append(f"{sum(v)}/{len(v)}" if v else "-")
            lines.append(f"| {spec['id']} | {t['ask']} | " + " | ".join(cells) + " |")
    lines += ["", f"답 전문: `evals/results/transcripts/{label}/`"]
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="baseline")
    ap.add_argument("--think", action="store_true")
    ap.add_argument("--num-ctx", type=int, default=8192)
    ap.add_argument("--only", nargs="*", help="S1 S2 … 중 일부만")
    a = ap.parse_args()
    specs = yaml.safe_load((HERE / "scenarios.yaml").read_text(encoding="utf-8"))
    if a.only:
        specs = [s for s in specs if s["id"] in a.only]
    md = asyncio.run(evaluate(specs, ChatOptions(think=a.think, num_ctx=a.num_ctx), a.label))
    out = HERE / "results" / f"scenarios-{a.label}.md"
    out.write_text(md, encoding="utf-8")
    print(md)


if __name__ == "__main__":
    main()
