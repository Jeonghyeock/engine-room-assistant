"""LanceDB 색인 — 서버 없이 data/index/ 폴더의 파일로 동작한다."""

from functools import lru_cache
from pathlib import Path

import lancedb

from rag.embed import embed

ROOT = Path(__file__).resolve().parents[2]
INDEX_DIR = ROOT / "data" / "index"
TABLE = "chunks"
SOURCES = {"cfr", "manual"}


def write(rows: list[dict]) -> int:
    """rows: Chunk.to_dict() + "vector". 기존 색인을 통째로 바꾼다."""
    db = lancedb.connect(INDEX_DIR)
    db.create_table(TABLE, data=rows, mode="overwrite")
    _table.cache_clear()
    return len(rows)


@lru_cache(maxsize=1)
def _table():
    return lancedb.connect(INDEX_DIR).open_table(TABLE)


def search(query: str, k: int = 5, source: str | None = None) -> list[dict]:
    """질문과 가까운 조각 k개. 결과마다 score(코사인 유사도, 1이 같음)를 붙인다."""
    if source is not None and source not in SOURCES:
        raise ValueError(f"source는 {sorted(SOURCES)} 중 하나")
    vec = embed([query], cpu=True)[0]
    q = _table().search(vec).distance_type("cosine").limit(k)
    if source:
        q = q.where(f"source = '{source}'", prefilter=True)
    hits = q.to_list()
    for h in hits:
        h.pop("vector", None)
        h["score"] = round(1 - h.pop("_distance"), 4)
    return hits


def all_locators() -> set[str]:
    return set(_table().to_arrow().column("locator").to_pylist())
