"""가상 매뉴얼 PDF → Docling → 절 단위 조각.

Docling이 PDF에서 제목·문단·목록·표를 구조로 꺼내 준다. 여기서는 그 구조를 따라
- 제목의 번호("4.2 입구 압력 낮음")로 위치를 정하고(`매뉴얼 4.2`),
- 다음 제목이 나오기 전까지의 문단·목록을 한 조각으로 묶고,
- 표는 행마다 "머리글: 값" 문장 한 조각으로 바꾼다(태그 하나를 물으면 그 행이 걸리게).

글자가 들어 있는 PDF라 OCR은 끈다.

    uv run python -m ingest.manual
"""

import re
from pathlib import Path

from ingest.chunks import Chunk, assign_ids

ROOT = Path(__file__).resolve().parents[2]
PDF_PATH = ROOT / "data" / "sources" / "manual.pdf"

HEADING = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+(.+)$")


def clean(text: str) -> str:
    """PDF에서 꺼낸 글의 흔적을 지운다.
    - 줄 끝에서 넘어간 문장의 줄바꿈 → 공백
    - 고정폭 글꼴의 태그 이름에 끼어든 공백("ME _ LO _ PRESS") → "ME_LO_PRESS"(태그 검색이 걸리게)
    """
    text = re.sub(r"\s*\n\s*", " ", text)
    return re.sub(r"\s*_\s*", "_", text).strip()


def heading_locator(text: str) -> tuple[str, str] | None:
    """'4.2 입구 압력 낮음' → ('매뉴얼 4.2', '4.2 입구 압력 낮음'). 번호가 없으면 None."""
    m = HEADING.match(text.strip())
    if not m:
        return None
    return f"매뉴얼 {m.group(1)}", text.strip()


def row_sentence(header: list[str], cells: list[str]) -> str:
    return "; ".join(f"{h}: {c}" for h, c in zip(header, cells) if c and c != "-")


def convert(pdf: Path = PDF_PATH):
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    opts = PdfPipelineOptions(do_ocr=False, do_table_structure=True)
    conv = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})
    return conv.convert(pdf).document


def chunks_from_document(doc) -> list[Chunk]:
    from docling_core.types.doc import ListItem, SectionHeaderItem, TableItem, TextItem, TitleItem

    out: list[Chunk] = []
    loc, title = "매뉴얼 머리말", "머리말"
    buf: list[str] = []
    page = 1

    def flush():
        if buf:
            out.append(Chunk("", "manual", loc, title, "\n".join(buf), page))
            buf.clear()

    for item, _level in doc.iterate_items():
        item_page = item.prov[0].page_no if getattr(item, "prov", None) else page
        if isinstance(item, (SectionHeaderItem, TitleItem)):
            found = heading_locator(clean(item.text))
            if found:
                flush()
                loc, title = found
                page = item_page
            elif isinstance(item, TitleItem):
                title = clean(item.text)  # 문서 제목
            continue
        if isinstance(item, TableItem):
            flush()
            df = item.export_to_dataframe(doc=doc)
            header = [clean(str(c)) for c in df.columns]
            for _, row in df.iterrows():
                cells = [clean(str(v)) for v in row.tolist()]
                if cells == header:  # 쪽이 넘어가며 되풀이된 머리글 행
                    continue
                key = cells[0]
                out.append(Chunk("", "manual", loc, f"{title} — {key}", row_sentence(header, cells), item_page))
            continue
        if isinstance(item, TextItem):
            if not buf:
                page = item_page
            text = clean(item.text)
            if isinstance(item, ListItem) and item.marker:
                text = f"{item.marker} {text}"
            if text:
                buf.append(text)
    flush()
    return assign_ids(out, "manual")


def load(pdf: Path = PDF_PATH) -> list[Chunk]:
    return chunks_from_document(convert(pdf))


if __name__ == "__main__":
    for c in load():
        print(f"{c.locator:14} p{c.page}  {len(c.text):5}  {c.title[:50]}")
