"""명령줄에서 에이전트와 대화한다. 질문을 여러 개 주면 같은 대화에서 차례로 묻는다.

    uv run python -m app.cli --scenario S2 "알람을 분석해 줘" "트립을 잠시 우회하고 항해를 계속해도 되나요?"
"""

import argparse
import asyncio
import sys

from ams.scenario import list_ids
from app.agent import Citation, Conversation, Done, Error, StepEnd, StepStart, Token, run_turn
from app.llm import ChatOptions
from app.tools import ToolBox


async def main(scenario: str, questions: list[str], opts: ChatOptions) -> int:
    conv = Conversation()
    async with ToolBox(scenario) as box:
        for q in questions:
            print(f"\n## 질문: {q}\n", flush=True)
            last_round = 0
            async for ev in run_turn(conv, q, box, opts):
                match ev:
                    case StepStart():
                        print(f"  → {ev.tool}({ev.args})", flush=True)
                    case StepEnd():
                        print(f"    {'✓' if ev.ok else '✗'} {ev.summary}", flush=True)
                    case Token():
                        if ev.round != last_round:  # 도구를 부르기 전의 짧은 말과 답을 줄로 가른다
                            print(flush=True)
                            last_round = ev.round
                        print(ev.text, end="", flush=True)
                    case Citation() if ev.verified:
                        print(f"\n  [{ev.n}] {ev.locator} — {ev.title}", end="")
                    case Citation():
                        print(f"\n  {ev.locator} — ⚠ 이번 대화에서 검색된 적 없는 근거", end="")
                    case Done():
                        warn = " ⚠ 문맥이 한도에 가까움" if ev.context_near_limit else ""
                        print(
                            f"\n\n  ({ev.rounds}번 도구 왕복, {ev.tokens} 토큰, {ev.tok_per_s} tok/s, "
                            f"{ev.elapsed_ms / 1000:.1f}초, 문맥 최대 {ev.max_prompt_tokens}/{ev.num_ctx}{warn})"
                        )
                    case Error():
                        print(f"\n  ✗ {ev.code}: {ev.message}")
                        return 1
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="engine-room-assistant agent CLI")
    ap.add_argument("questions", nargs="+")
    ap.add_argument("--scenario", default="S1", choices=list_ids())
    ap.add_argument("--think", action="store_true", help="qwen3 생각 모드")
    ap.add_argument("--num-ctx", type=int, default=8192)
    ap.add_argument("--seed", type=int)
    a = ap.parse_args()
    sys.exit(asyncio.run(main(a.scenario, a.questions, ChatOptions(think=a.think, num_ctx=a.num_ctx, seed=a.seed))))
