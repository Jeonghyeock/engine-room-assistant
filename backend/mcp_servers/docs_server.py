"""MCP 서버 ② docs — 매뉴얼·규정 검색을 읽기 전용 도구로 노출한다.

    uv run python -m mcp_servers.docs_server
    npx @modelcontextprotocol/inspector uv run python -m mcp_servers.docs_server   # MCP Inspector

색인(data/index)과 Ollama(bge-m3)가 있어야 검색된다. 색인은 `python -m ingest.build_index`로 만든다.
도구 결과는 ams 서버와 같이 객체 하나({"results": [...]})로 돌려준다.
"""

import inspect
import logging
from collections.abc import Callable
from typing import Annotated, Any, Literal

import httpx
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import Field

from rag import store

log = logging.getLogger(__name__)

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)

INSTRUCTIONS = """선박 기관실 문서 검색이다. 두 가지 문서가 있다.
- manual: 가상 선박의 기관 운전 지침서(한국어). 알람 설정값 표, 이상 시 점검 순서.
- cfr: 미국 연방 규정 46 CFR Part 62 Vital System Automation(영어 원문). 경보·안전 트립·failsafe 원칙.
답에 쓴 내용은 결과의 locator(예: "매뉴얼 4.2", "§62.35-50(d)")로 근거를 밝힌다."""

SearchFn = Callable[..., list[dict[str, Any]]]

# 가져올 조각 수는 서버가 정한다. 인자로 열어 두면 8B 모델이 k=1을 골라 근거를 스스로 좁혔다.
TOP_K = 5

SEARCH_DOC = f"""매뉴얼·규정에서 질문과 가까운 조각(results) {TOP_K}개를 찾는다.
조각마다 id, 출처(source), 위치(locator — 답의 근거로 이 값을 밝힌다), 제목, 본문, 유사도(score, 1에 가까울수록 비슷).
매뉴얼 조각에는 PDF 쪽 번호(page)도 있다.
위치가 조 전체(예: §62.35-50)인 규정 조각에는 header가 붙는다. header=true면 문단으로 나뉜 조의 머리글뿐이라 하위 문단(§62.35-50(d))의 근거가 아니다."""


def create_server(search: SearchFn = store.search, locators: Callable[[], set[str]] = store.all_locators) -> MCPServer:
    """search·locators는 테스트에서 가짜로 바꿔 끼울 수 있다(CI에는 색인·Ollama가 없다)."""
    server = MCPServer("docs", instructions=INSTRUCTIONS)

    def split_sections() -> set[str] | None:
        """문단으로 잘린 조(store.split_sections). 검색할 때마다 읽는다 — 다른 프로세스가 색인을 다시 만들면 다음 검색부터
        새 색인을 본다(store._table과 같다). 읽지 못하면 None — header를 붙이지 않고, 근거 검사는 header가 없는 조 전체
        조각으로 하위 문단을 확인하지 않는다(그쪽이 안전하다). 이 표시 때문에 검색 자체가 실패하지는 않게 한다."""
        try:
            return store.split_sections(locators())
        except Exception as e:
            log.warning("색인 위치 목록을 읽지 못해 머리글 표시를 건너뜀: %r", e)
            return None

    @server.tool(annotations=READ_ONLY, description=inspect.cleandoc(SEARCH_DOC))
    def search_docs(
        query: Annotated[str, Field(description="찾을 내용. 규정(cfr)은 영어 원문이라 영어로 물으면 더 잘 찾는다.", min_length=1)],
        source: Annotated[Literal["all", "manual", "cfr"], Field(description="all=둘 다, manual=매뉴얼만, cfr=규정만")] = "all",
    ) -> dict[str, Any]:
        try:
            hits = search(query, k=TOP_K, source=None if source == "all" else source)
        except store.IndexUnavailableError as e:
            raise ToolError("문서 색인이 없거나 손상됐다. 관리자가 `python -m ingest.build_index`로 색인을 만들어야 한다.") from e
        except httpx.HTTPStatusError as e:  # 서버엔 닿았는데 오류 — 대개 임베딩 모델을 안 받아 둔 경우
            log.warning("임베딩 요청 오류: %r", e)
            raise ToolError(f"임베딩 서버(Ollama)가 오류 {e.response.status_code}로 답했다. 임베딩 모델(bge-m3)이 받아져 있는지 확인해야 한다.") from e
        except httpx.HTTPError as e:
            log.warning("임베딩 요청 실패: %r", e)
            raise ToolError("임베딩 서버(Ollama)에 연결하지 못해 검색할 수 없다.") from e
        results = []
        split: set[str] | None = None
        read_split = False  # 조 전체 위치의 규정 조각이 있을 때만 한 번 읽는다
        for h in hits:
            row = {f: h[f] for f in ("id", "source", "locator", "title", "text", "score")}
            if h.get("page"):  # 규정(CFR)은 쪽이 없어 0 — 내보내지 않는다
                row["page"] = h["page"]
            # 조 전체 위치의 규정 조각에 머리글인지(header) 붙인다. 근거 검사(agent.find_citations)는 header=False 인 조각만
            # 하위 문단 인용(§62.35-50(d))을 품는다고 본다 — evals/retrieval.covers와 같은 규칙.
            # 모델에게 보여 주는 글에는 넣지 않는다(Conversation.number).
            if h["source"] == "cfr" and "(" not in h["locator"]:
                if not read_split:
                    split, read_split = split_sections(), True
                if split is not None:
                    row["header"] = h["locator"] in split
            results.append(row)
        return {"results": results}

    return server


def main() -> None:
    logging.getLogger("httpx").setLevel(logging.WARNING)  # 질문마다 찍히는 임베딩 요청 줄은 숨긴다
    create_server().run()  # stdio


if __name__ == "__main__":
    main()
