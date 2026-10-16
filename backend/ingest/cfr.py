"""46 CFR Part 62(Vital System Automation) 받기 → 조·문단 단위 조각.

eCFR API에서 XML을 받는다(미국 연방 규정이라 저작권이 없다). 웹 페이지는 자동 접근을
막지만 API는 열려 있고, 압축 응답만 준다.

자르는 규칙
- 조(§)에 문단 (a)(b)…가 있으면 맨 위 문단 단위로 자른다. 문단이 없으면 통째로 한 조각.
- (1)(2), (i)(ii) 같은 하위 문단과 주석(NOTE)은
  자기가 속한 맨 위 문단에 붙인다.
- 표는 행을 "머리글: 값" 문장으로 바꾸고, 첫 열(계통)이 같은 행끼리 한 조각으로 묶는다.

    uv run python -m ingest.cfr fetch
"""

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx

from ingest.chunks import Chunk, assign_ids

ROOT = Path(__file__).resolve().parents[2]
XML_PATH = ROOT / "data" / "sources" / "cfr-46-part62.xml"
API = "https://www.ecfr.gov/api/versioner/v1"

SPLIT_OVER = 0  # 이보다 긴 조만 문단으로 자른다. 0 = 문단이 있으면 늘 자름(평가에서 recall@5 0.87 → 0.93)
TOP_MARKER = re.compile(r"^\(([a-z])\)")


def fetch(path: Path = XML_PATH) -> str:
    """Title 46의 최신 기준일을 묻고 Part 62를 받는다. 기준일을 돌려준다."""
    with httpx.Client(timeout=60) as client:
        titles = client.get(f"{API}/titles.json").raise_for_status().json()["titles"]
        date = next(t for t in titles if t["number"] == 46)["up_to_date_as_of"]
        xml = client.get(f"{API}/full/{date}/title-46.xml", params={"part": "62"}).raise_for_status().text
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(xml, encoding="utf-8")
    return date


def _text(el: ET.Element) -> str:
    return re.sub(r"\s+", " ", "".join(el.itertext())).strip()


def _next_letter(cur: str | None) -> str:
    return "a" if cur is None else chr(ord(cur) + 1)


def _table_chunks(table: ET.Element, sec: str, n: int) -> list[Chunk]:
    caption = table.find("CAPTION")
    cap = _text(caption) if caption is not None else "Table"
    loc = f"§{sec} 표 {n}"
    # 머리글 칸은 줄바꿈 태그로 낱말이 붙어 있어 공백으로 잇는다("See also paragraph").
    header = [re.sub(r"\s+", " ", " ".join(th.itertext())).strip() for th in table.iter("TH")]
    groups: list[tuple[str, list[str]]] = []
    body = table.find("TBODY")
    for tr in (body if body is not None else table).iter("TR"):
        cells = [_text(td) for td in tr.findall("TD")]
        if not cells:
            continue
        if cells[0] or not groups:
            groups.append((cells[0], []))
        name = groups[-1][0]
        pairs = [f"{h}: {c}" for h, c in zip(header, cells) if c]
        if not cells[0] and name:
            pairs.insert(0, f"{header[0]}: {name}")
        groups[-1][1].append("; ".join(pairs))
    out = [Chunk("", "cfr", loc, f"§{sec} {cap} — {name}", "\n".join(rows)) for name, rows in groups]
    foot = table.find("TFOOT")
    if foot is not None and _text(foot):
        out.append(Chunk("", "cfr", loc, f"§{sec} {cap} — 각주", _text(foot)))
    return out


def parse_section(div: ET.Element, split_over: int = SPLIT_OVER) -> list[Chunk]:
    sec = div.get("N", "")
    head = _text(div.find("HEAD"))
    title = "§" + re.sub(r"^§\s*", "", head)

    # (마커, 글) 덩어리로 모은다. 마커가 None이면 첫 문단 앞의 머리글이다.
    paras: list[tuple[str | None, list[str]]] = [(None, [])]
    cur: str | None = None
    for el in div:
        if el.tag == "P":
            t = _text(el)
            m = TOP_MARKER.match(t)
            # (i)는 문단 (i)일 수도, 하위 문단 (i)(로마 숫자)일 수도 있다 — 다음 글자일 때만 맨 위 문단.
            if m and m.group(1) == _next_letter(cur):
                cur = m.group(1)
                paras.append((cur, []))
            paras[-1][1].append(t)
        elif el.tag == "NOTE":
            # 주석 머리(HED)와 본문(P) 사이에 공백이 없을 수 있어 자식끼리 띄워 잇는다.
            paras[-1][1].append(" ".join(_text(child) for child in el))
        # HEAD는 제목, CITA는 개정 이력이라 넣지 않는다. 표는 DIV로 한 번 감싸여 있어 아래에서 따로 찾는다.

    paras = [(k, v) for k, v in paras if v]
    whole = "\n".join(t for _, v in paras for t in v)
    chunks: list[Chunk] = []
    if len(whole) <= split_over or all(k is None for k, _ in paras):
        if whole:
            chunks.append(Chunk("", "cfr", f"§{sec}", title, whole))
    else:
        for k, v in paras:
            loc = f"§{sec}" if k is None else f"§{sec}({k})"
            chunks.append(Chunk("", "cfr", loc, title, "\n".join(v)))
    for n, table in enumerate(div.iter("TABLE"), start=1):
        chunks.extend(_table_chunks(table, sec, n))
    return chunks


def parse(xml: str, split_over: int = SPLIT_OVER) -> list[Chunk]:
    root = ET.fromstring(xml)
    chunks: list[Chunk] = []
    for div in root.iter("DIV8"):
        if div.get("TYPE") == "SECTION":
            chunks.extend(parse_section(div, split_over))
    return assign_ids(chunks, "cfr")


def load(path: Path = XML_PATH, split_over: int = SPLIT_OVER) -> list[Chunk]:
    return parse(path.read_text(encoding="utf-8"), split_over)


if __name__ == "__main__":
    if sys.argv[1:] == ["fetch"]:
        print("eCFR 기준일", fetch(), "→", XML_PATH.relative_to(ROOT))
    for c in load():
        print(f"{c.locator:24} {len(c.text):5}  {c.title[:60]}")
