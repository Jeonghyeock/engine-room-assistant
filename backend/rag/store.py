"""LanceDB 색인 — 서버 없이 data/index/ 폴더의 파일로 동작한다."""

from pathlib import Path

import lancedb

from rag.embed import embed

ROOT = Path(__file__).resolve().parents[2]
INDEX_DIR = ROOT / "data" / "index"
TABLE = "chunks"
SOURCES = {"cfr", "manual"}


class IndexUnavailableError(RuntimeError):
    """색인이 없거나 손상돼 검색할 수 없다."""


def write(rows: list[dict]) -> int:
    """rows: Chunk.to_dict() + "vector". 기존 색인을 통째로 바꾼다."""
    db = lancedb.connect(INDEX_DIR)
    db.create_table(TABLE, data=rows, mode="overwrite")
    return len(rows)


def _table():
    """검색할 때마다 연다(약 2ms). 열어 둔 것을 들고 있으면, 다른 프로세스가 색인을 다시 만들어도
    오래 도는 MCP 서버는 옛 색인을 계속 읽는다."""
    # 먼저 확인한다 — lancedb.connect는 없는 폴더를 만들어 버려서, 읽기만 해도 빈 색인 폴더가 남는다.
    if not (INDEX_DIR / f"{TABLE}.lance").exists():
        raise IndexUnavailableError(f"{INDEX_DIR}에 색인이 없다 — python -m ingest.build_index로 만든다")
    try:
        return lancedb.connect(INDEX_DIR).open_table(TABLE)
    except ValueError as e:  # 폴더는 있는데 표가 비었거나 깨졌다
        raise IndexUnavailableError(f"{INDEX_DIR}의 색인이 손상됐다 — python -m ingest.build_index로 다시 만든다") from e


def search(query: str, k: int = 5, source: str | None = None) -> list[dict]:
    """질문과 가까운 조각 k개. 결과마다 score(코사인 유사도, 1이 같음)를 붙인다."""
    if source is not None and source not in SOURCES:
        raise ValueError(f"source는 {sorted(SOURCES)} 중 하나")
    table = _table()  # 색인이 없으면 임베딩을 부르기 전에 바로 실패한다
    vec = embed([query], cpu=True)[0]
    q = table.search(vec).distance_type("cosine").limit(k)
    if source:
        q = q.where(f"source = '{source}'", prefilter=True)
    try:
        hits = q.to_list()
    except RuntimeError as e:  # 데이터 파일이 빠진 색인 — lance가 RuntimeError로 알린다
        raise IndexUnavailableError(f"{INDEX_DIR}의 색인이 손상됐다 — python -m ingest.build_index로 다시 만든다") from e
    for h in hits:
        h.pop("vector", None)
        h["score"] = round(1 - h.pop("_distance"), 4)
    return hits


def all_locators() -> set[str]:
    # locator 열만 읽는다(임베딩 벡터까지 통째로 읽지 않게)
    t = _table()
    return set(t.search().select(["locator"]).limit(t.count_rows()).to_arrow().column("locator").to_pylist())


def split_sections(locators: set[str]) -> set[str]:
    """문단으로 잘린 조(예: §62.35-50 — 색인에 §62.35-50(a) 같은 문단 조각이 있다).
    그 조의 위치가 조 전체인 조각은 첫 문단 앞의 머리글뿐이라 하위 문단을 품지 않는다.
    검색 결과 표시(docs_server)와 평가(evals.retrieval.covers)가 같은 규칙을 쓴다."""
    return {loc.split("(", 1)[0] for loc in locators if "(" in loc}
