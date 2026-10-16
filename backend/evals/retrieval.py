"""검색 평가 — questions.yaml의 질문마다 상위 k개에 정답 위치가 들어 있는지 잰다.

- recall@k: 질문마다 (찾은 정답 위치 수 / 정답 위치 수)의 평균
- 첫 정답 순위: 상위 k개 중 처음으로 정답이 나온 순위(없으면 -)

    uv run python -m evals.retrieval            # 표를 출력하고 evals/results/에 저장
"""

import argparse
import datetime as dt
import time
from pathlib import Path

import yaml

from rag import store

HERE = Path(__file__).resolve().parent


def covers(loc: str, gold: str, index_locs: set[str]) -> bool:
    """색인 조각의 위치 loc이 정답 위치 gold를 맞힌 것으로 볼지.

    - 같으면 맞다.
    - loc이 gold보다 큰 단위면 맞다: 문단 조각 §62.25-20(b)는 (b)(2)를 품는다.
      단, 조 전체(§62.35-50)는 그 조가 문단으로 잘리지 않았을 때만 — 잘렸다면 그 조각은 머리글뿐이다.
    - loc이 gold 안의 일부면 맞다: 정답이 §62.30-1이고 조각이 §62.30-1(a)인 경우.
    """
    if loc == gold:
        return True
    if gold.startswith(loc + "("):
        is_paragraph = "(" in loc
        section_split = any(other.startswith(loc + "(") for other in index_locs)
        return is_paragraph or not section_split
    return loc.startswith(gold + "(")


def _gold(g) -> dict:
    """정답 한 개: "매뉴얼 4.2" 또는 {loc, contains} — contains는 표의 어느 행인지 가린다."""
    return g if isinstance(g, dict) else {"loc": g}


def hit_matches(hit: dict, gold: dict, index_locs: set[str]) -> bool:
    if not covers(hit["locator"], gold["loc"], index_locs):
        return False
    return "contains" not in gold or gold["contains"] in hit["title"] + hit["text"]


def _gold_label(g: dict) -> str:
    return g["loc"] + (f"({g['contains']})" if "contains" in g else "")


def run(k: int) -> tuple[list[dict], dict]:
    questions = yaml.safe_load((HERE / "questions.yaml").read_text(encoding="utf-8"))
    for q in questions:
        q["gold"] = [_gold(g) for g in q["gold"]]
    index_locs = store.all_locators()
    missing = [g["loc"] for q in questions for g in q["gold"] if not any(covers(l, g["loc"], index_locs) for l in index_locs)]
    if missing:
        raise SystemExit(f"색인에 없는 정답 위치: {missing}")

    store.search("준비", k=1)  # 첫 호출의 모델 적재 시간을 측정에서 뺀다
    rows = []
    for q in questions:
        t0 = time.perf_counter()
        hits = store.search(q["q"], k=k)
        ms = (time.perf_counter() - t0) * 1000
        found = {i for i, g in enumerate(q["gold"]) for h in hits if hit_matches(h, g, index_locs)}
        first = next((n for n, h in enumerate(hits, 1) if any(hit_matches(h, g, index_locs) for g in q["gold"])), None)
        rows.append({**q, "recall": len(found) / len(q["gold"]), "first": first, "ms": ms, "top": [h["locator"] for h in hits]})
    summary = {
        "k": k,
        "recall": sum(r["recall"] for r in rows) / len(rows),
        "top1": sum(r["first"] == 1 for r in rows) / len(rows),
        "ms": sum(r["ms"] for r in rows) / len(rows),
    }
    return rows, summary


def to_markdown(rows: list[dict], s: dict, label: str) -> str:
    k = s["k"]
    lines = [
        f"# 검색 평가 — {label}",
        "",
        f"{dt.date.today()} · 질문 {len(rows)}개 · recall@{k} **{s['recall']:.2f}** · 1위 적중 {s['top1']:.2f} · 질문당 {s['ms']:.0f} ms(CPU 임베딩 포함)",
        "",
        f"| # | 언어 | 질문 | 정답 위치 | recall@{k} | 첫 정답 순위 | 상위 {k}개 |",
        "|---|---|---|---|---|---|---|",
    ]
    for i, r in enumerate(rows, 1):
        lines.append(
            f"| {i} | {r['lang']} | {r['q']} | {', '.join(_gold_label(g) for g in r['gold'])} | {r['recall']:.2f} "
            f"| {r['first'] or '-'} | {' · '.join(r['top'])} |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("-k", type=int, default=5)
    ap.add_argument("--label", default="cfr-paragraph", help="결과 파일 이름에 쓰는 설정 이름(기본값 = 지금 기본 설정)")
    args = ap.parse_args()
    rows, s = run(args.k)
    md = to_markdown(rows, s, args.label)
    out = HERE / "results" / f"retrieval-{args.label}.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text(md, encoding="utf-8")
    print(md)
    print(f"→ {out.relative_to(HERE.parent)}")


if __name__ == "__main__":
    main()
