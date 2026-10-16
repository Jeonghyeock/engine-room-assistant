"""CFR XML·매뉴얼 PDF → 조각 → 임베딩 → LanceDB.

    uv run python -m ingest.build_index
"""

import argparse
import time

from ingest import cfr, manual
from rag import store
from rag.embed import embed


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cfr-split", type=int, default=cfr.SPLIT_OVER, help="이보다 긴 CFR 조를 문단으로 자른다(0이면 전부)")
    args = ap.parse_args()
    t0 = time.perf_counter()
    chunks = cfr.load(split_over=args.cfr_split) + manual.load()
    t1 = time.perf_counter()
    vectors = embed([c.embed_text() for c in chunks])  # 수집은 GPU로 한꺼번에
    t2 = time.perf_counter()
    n = store.write([{**c.to_dict(), "vector": v} for c, v in zip(chunks, vectors)])
    by_source = {s: sum(c.source == s for c in chunks) for s in store.SOURCES}
    print(f"조각 {n}개 {by_source} — 파싱 {t1 - t0:.1f}s, 임베딩 {t2 - t1:.1f}s → {store.INDEX_DIR}")


if __name__ == "__main__":
    main()
