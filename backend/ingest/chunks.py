"""검색 단위(조각). CFR과 매뉴얼 수집기가 같은 모양으로 낸다."""

from dataclasses import asdict, dataclass


@dataclass
class Chunk:
    id: str  # 출처 안에서 유일. 예: "cfr:§62.35-50(c)", "manual:4.2#2"
    source: str  # "cfr" | "manual"
    locator: str  # 인용 위치. 예: "§62.35-50(c)", "매뉴얼 4.2"
    title: str  # 조·절 제목
    text: str
    page: int = 0  # 매뉴얼 PDF 쪽 번호(CFR은 0)

    def embed_text(self) -> str:
        """임베딩에 넣는 글. 제목을 함께 넣어 짧은 조각도 맥락을 갖게 한다."""
        return f"{self.title}\n{self.text}"

    def to_dict(self) -> dict:
        return asdict(self)


def assign_ids(chunks: list[Chunk], prefix: str) -> list[Chunk]:
    """같은 위치에서 여러 조각이 나오면 #2, #3을 붙인다."""
    seen: dict[str, int] = {}
    for c in chunks:
        n = seen.get(c.locator, 0) + 1
        seen[c.locator] = n
        c.id = f"{prefix}:{c.locator}" + (f"#{n}" if n > 1 else "")
    return chunks
