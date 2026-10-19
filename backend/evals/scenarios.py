"""에이전트 시나리오 평가 — scenarios.yaml의 시나리오를 실제 모델로 여러 번 돌려 검사한다.

    uv run python -m evals.scenarios --label 이름              # S1~S3 × 씨앗 3개 → evals/results/scenarios-이름.md
    uv run python -m evals.scenarios --label think --think

- "전체"는 정해진 검사(도구·인용·근거·문맥)만으로 정한다.
- 판정(qwen3:8b의 예/아니오, 기준 하나에 질문 하나)은 따로 칸을 둔 표시일 뿐이다. 판정 원문까지 모든 답을
  evals/results/transcripts/<label>/에 저장하고, 최종 판단은 사람이 그 답을 읽고 한다.
- 씨앗이 뜻을 가지려면 temperature가 0보다 커야 한다(0이면 씨앗과 상관없이 같은 답이 나온다). 평가는 qwen3가
  권하는 비생각 모드 값 0.7로 돌린다.
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
from app.prompts import DEFAULT as DEFAULT_PROMPT
from app.tools import ToolBox

HERE = Path(__file__).resolve().parent
SEEDS = [1, 2, 3, 4, 5]  # 3번은 칸마다 흔들림이 컸다(PR #12 리뷰) — 결론은 경향으로 읽는다
EVAL_TEMPERATURE = 0.7
CHECKS = ["답함", "도구", "도구 인자", "인용 있음", "미확인 인용 없음", "필수 근거", "전제 오류 없음", "문맥 여유", "잘리지 않음"]


@dataclass
class Turn:
    ask: str
    steps: list[dict] = field(default_factory=list)  # {tool, args, ok, summary}
    answer: str = ""
    citations: list[Citation] = field(default_factory=list)
    done: Done | None = None
    error: Error | None = None
    judgements: list[dict] = field(default_factory=list)  # {criterion, want, raw, verdict}
    drafts: list[str] = field(default_factory=list)  # 근거 검사로 되돌린 초안


# ---- 정해진 검사 ----


def _covers(loc: str, want: str) -> bool:
    """인용 위치가 원하는 위치와 같거나 그 안의 문단이면 맞다(§62.35-50(d) ⊃ §62.35-50(d)(1))."""
    return loc == want or loc.startswith(want + "(")


def _arg_ok(args: dict, want: dict) -> bool:
    """인자 하나가 기대와 맞는지. value: 같아야 함(대소문자 무시). where: 여러 인자가 모두 같아야 함.
    min: 숫자가 이상이어야 함 — 인자를 안 줬으면 도구 기본값(default)으로 본다."""
    if "where" in want and any(str(args.get(k, "")).upper() != str(v).upper() for k, v in want["where"].items()):
        return False
    if "value" in want and str(args.get(want["arg"], "")).upper() != str(want["value"]).upper():
        return False
    if "min" in want:
        v = args.get(want["arg"], want.get("default"))
        if not isinstance(v, (int, float)) or v < want["min"]:
            return False
    return True


def check(turn: Turn, expect: dict) -> dict[str, bool]:
    """정해진 검사. 기대하지 않은 항목은 통과로 둔다(표에서 칸이 빠지지 않게)."""
    called = [s["tool"] for s in turn.steps]
    verified = [c for c in turn.citations if c.verified]
    return {
        "답함": turn.done is not None and turn.error is None and bool(turn.answer.strip()),
        "도구": all(t in called for t in expect.get("tools", [])) and (not expect.get("tools_any") or any(t in called for t in expect["tools_any"])),
        "도구 인자": all(any(s["tool"] == w["tool"] and _arg_ok(s["args"], w) for s in turn.steps) for w in expect.get("tool_args", [])),
        "인용 있음": bool(verified),
        "미확인 인용 없음": not any(not c.verified for c in turn.citations),
        "필수 근거": not expect.get("cite_any") or any(_covers(c.locator, w) for c in verified for w in expect["cite_any"]),
        # 일어나지 않은 일을 일어났다고 말하면 실패(예: 경보 단계인데 "트립이 발생했다"). 판정 모델은 이 기준을 거듭 틀려
        # 정해진 문형으로 본다 — 과거형 단정만 잡으므로 "내려가면 정지한다" 같은 조건문은 통과한다.
        "전제 오류 없음": not any(re.search(p, turn.answer) for p in expect.get("forbid", [])),
        "문맥 여유": turn.done is not None and not turn.done.context_near_limit,
        "잘리지 않음": turn.done is not None and not turn.done.truncated,
    }


def not_reached() -> dict[str, bool]:
    """앞 차례가 오류로 끝나 못 간 차례 — 모든 칸을 실패로."""
    return {k: False for k in CHECKS} | {"전체": False, "판정": False}


# ---- 판정 ----

JUDGE_PROMPT = """너는 채점자다. 아래 질문과 답을 읽고, 기준에 대해 "예" 또는 "아니오" 한 단어로만 답하라.

기준: {criterion}

질문: {ask}

답:
{answer}"""


def parse_judgement(text: str) -> bool | None:
    """답의 맨 앞만 본다 — "예외", "예를 들어" 같은 말 속의 '예'를 판정으로 읽지 않게."""
    head = text.strip().lstrip("\"'*「『 ")
    if head.startswith(("아니오", "아니요")):
        return False
    if head.startswith("예") and not head.startswith(("예외", "예를", "예시", "예상")):
        return True
    return None


async def judge(ask: str, answer: str, criterion: str) -> tuple[bool | None, str]:
    prompt = JUDGE_PROMPT.format(criterion=criterion, ask=ask, answer=answer)
    text = []
    async with aclosing(stream_chat([{"role": "user", "content": prompt}], [], ChatOptions(num_predict=8))) as events:
        async for ev in events:
            if isinstance(ev, TextDelta):
                text.append(ev.text)
    raw = "".join(text)
    return parse_judgement(raw), raw


def judged_ok(turn: Turn) -> bool:
    """판정을 못 읽었으면(None) 실패로 센다."""
    return all(j["verdict"] is j["want"] for j in turn.judgements)


# ---- 실행 ----


async def run_scenario(
    spec: dict, opts: ChatOptions, prompt: str = DEFAULT_PROMPT, guard: bool = True, follow_up_search: bool = True
) -> list[Turn]:
    turns = []
    conv = Conversation(prompt=prompt)
    async with ToolBox(spec["id"]) as box:
        for t in spec["turns"]:
            turn = Turn(t["ask"])
            round_ = 0
            async with aclosing(run_turn(conv, t["ask"], box, opts, guard=guard, follow_up_search=follow_up_search)) as events:
                async for ev in events:
                    match ev:
                        case StepStart():
                            if ev.tool == "check_citations":  # 지금까지의 글은 되돌린 초안
                                turn.drafts.append(turn.answer)
                            turn.steps.append({"tool": ev.tool, "args": ev.args, "auto": ev.auto})
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
                            if round_ != ev.model_calls - 1:  # 마지막 호출에 글이 없었다 — 앞 호출의 짧은 말을 답으로 두지 않는다
                                turn.answer = ""
                        case Error():
                            turn.error = ev
            turns.append(turn)
            if turn.error:
                break
    return turns


async def judge_turn(turn: Turn, expect: dict) -> None:
    if not turn.answer.strip():
        return
    for criterion in expect.get("judge_pass", []):
        verdict, raw = await judge(turn.ask, turn.answer, criterion)
        turn.judgements.append({"criterion": criterion, "want": True, "raw": raw, "verdict": verdict})
    for criterion in expect.get("judge_fail", []):
        verdict, raw = await judge(turn.ask, turn.answer, criterion)
        turn.judgements.append({"criterion": criterion, "want": False, "raw": raw, "verdict": verdict})


def transcript(sid: str, seed: int, turns: list[Turn], results: list[dict]) -> str:
    lines = [f"# {sid} · 씨앗 {seed}", ""]
    for turn, res in zip(turns, results):
        lines += [f"## 질문: {turn.ask}", ""]
        for s in turn.steps:
            lines.append(f"- {'(자동) ' if s.get('auto') else ''}`{s['tool']}({s['args']})` → {'✓' if s.get('ok') else '✗'} {s.get('summary', '')}")
        for i, d in enumerate(turn.drafts, 1):
            lines += ["", f"<details><summary>근거 검사로 되돌린 초안 {i}</summary>", "", d.strip(), "", "</details>"]
        lines += ["", turn.answer.strip() or "(답 없음)", ""]
        for c in turn.citations:
            lines.append(f"- [{c.n}] {c.locator} — {c.title}" if c.verified else f"- {c.locator} — ⚠ 검색된 적 없는 근거")
        if turn.done:
            d = turn.done
            lines.append(f"\n({d.rounds}번 도구 왕복, 근거 검사로 {d.rechecks}번 되돌림, {d.tokens} 토큰, {d.tok_per_s} tok/s, {d.elapsed_ms / 1000:.1f}초, 문맥 최대 {d.max_prompt_tokens}/{d.num_ctx})")
        if turn.error:
            lines.append(f"\n✗ {turn.error.code}: {turn.error.message}")
        lines += ["", "검사: " + ", ".join(f"{k} {'✓' if v else '✗'}" for k, v in res.items()), ""]
        for j in turn.judgements:
            mark = "✓" if j["verdict"] is j["want"] else "✗"
            lines.append(f"- 판정 {mark} {j['criterion']} → 원문 {j['raw'].strip()!r} (바라는 답: {'예' if j['want'] else '아니오'})")
        lines.append("")
    return "\n".join(lines)


async def evaluate(
    specs: list[dict], opts: ChatOptions, label: str, prompt: str = DEFAULT_PROMPT, guard: bool = True, follow_up_search: bool = True
) -> str:
    out_dir = HERE / "results" / "transcripts" / label
    out_dir.mkdir(parents=True, exist_ok=True)
    table: dict[tuple, list[bool]] = {}  # (시나리오, 차례 번호, 칸) → 실행마다 통과 여부
    for spec in specs:
        for seed in SEEDS:
            turns = await run_scenario(spec, ChatOptions(**{**opts.__dict__, "seed": seed}), prompt, guard, follow_up_search)
            results = []
            for turn, expect in zip(turns, spec["turns"]):
                await judge_turn(turn, expect)
                res = check(turn, expect)
                res["전체"] = all(res.values())  # 정해진 검사만
                res["판정"] = bool(turn.answer.strip()) and judged_ok(turn)
                results.append(res)
            results += [not_reached() for _ in range(len(turns), len(spec["turns"]))]
            for i, res in enumerate(results, 1):
                for k, v in res.items():
                    table.setdefault((spec["id"], i, k), []).append(v)
            (out_dir / f"{spec['id']}-seed{seed}.md").write_text(transcript(spec["id"], seed, turns, results), encoding="utf-8")
            print(f"{spec['id']} 씨앗 {seed}: " + " / ".join(("✓" if r["전체"] else "✗") + ("" if r["판정"] else "(판정✗)") for r in results), flush=True)

    cols = CHECKS + ["전체", "판정"]
    lines = [
        f"# 시나리오 평가 — {label}",
        "",
        f"{dt.date.today()} · {opts.model} · 프롬프트 {prompt} · 근거 검사 {'켬' if guard else '끔'} · 이어지는 질문 자동 검색 {'켬' if follow_up_search else '끔'} · temperature {opts.temperature} · think={'켬' if opts.think else '끔'} · num_ctx {opts.num_ctx} · 씨앗 {SEEDS}",
        "",
        "칸은 통과 수/실행 수. **전체 = 정해진 검사만**(판정은 표시 — 답 전문의 판정 원문과 함께 사람이 읽고 판단).",
        "",
        "| 시나리오 | 질문 | " + " | ".join(cols) + " |",
        "|---|---|" + "---|" * len(cols),
    ]
    for spec in specs:
        for i, t in enumerate(spec["turns"], 1):
            cells = [f"{sum(v)}/{len(v)}" for v in (table[(spec["id"], i, k)] for k in cols)]
            lines.append(f"| {spec['id']} | {t['ask']} | " + " | ".join(cells) + " |")
    lines += ["", f"답 전문: `evals/results/transcripts/{label}/`"]
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default=dt.datetime.now().strftime("%Y%m%d-%H%M"), help="결과 이름(기본: 지금 시각)")
    ap.add_argument("--overwrite", action="store_true", help="같은 이름의 결과를 덮어쓴다")
    ap.add_argument("--think", action="store_true")
    ap.add_argument("--temperature", type=float, default=EVAL_TEMPERATURE)
    ap.add_argument("--num-ctx", type=int, default=8192)
    ap.add_argument("--only", nargs="*", help="S1 S2 … 중 일부만")
    ap.add_argument("--prompt", default=DEFAULT_PROMPT, help="시스템 프롬프트 판본(app.prompts) — v1이 기준선")
    ap.add_argument("--no-guard", action="store_true", help="근거 검사를 끈다(기준선과 비교용)")
    ap.add_argument("--no-follow-up-search", action="store_true", help="이어지는 질문의 자동 검색을 끈다(비교용)")
    a = ap.parse_args()
    out = HERE / "results" / f"scenarios-{a.label}.md"
    if out.exists() and not a.overwrite:
        ap.error(f"{out.name}이 이미 있다 — 다른 --label을 쓰거나 --overwrite")
    specs = yaml.safe_load((HERE / "scenarios.yaml").read_text(encoding="utf-8"))
    if a.only:
        specs = [s for s in specs if s["id"] in a.only]
    md = asyncio.run(evaluate(specs, ChatOptions(think=a.think, temperature=a.temperature, num_ctx=a.num_ctx), a.label, a.prompt, not a.no_guard, not a.no_follow_up_search))
    out.write_text(md, encoding="utf-8")
    print(md)


if __name__ == "__main__":
    main()
