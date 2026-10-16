"""가상 매뉴얼 md + tags.yaml → PDF.

설정값 표는 tags.yaml에서 만든다. 매뉴얼 본문에 숫자를 따로 적지 않아서
매뉴얼과 가상 알람 감시 시스템의 설정값이 어긋나지 않는다.

PDF는 설치된 Chromium 계열 브라우저(Edge·Chrome)의 headless 인쇄로 만든다.

    uv run python -m ingest.build_manual
"""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import markdown
import yaml

ROOT = Path(__file__).resolve().parents[2]
TAGS = ROOT / "data" / "tags.yaml"
MANUAL_MD = ROOT / "data" / "manual" / "manual.md"
OUT_PDF = ROOT / "data" / "sources" / "manual.pdf"

MARKER = "{{SETPOINT_TABLE}}"

BROWSERS = [
    os.environ.get("BROWSER_PATH", ""),
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    "chromium",
    "google-chrome",
]

CSS = """
@page { size: A4; margin: 18mm 16mm;
  @bottom-center { content: counter(page) " / " counter(pages); font-size: 9pt; color: #666; } }
body { font-family: 'Malgun Gothic', sans-serif; font-size: 10.5pt; line-height: 1.65; color: #1f2328; word-break: keep-all; }
h1 { font-size: 20pt; margin: 0 0 12px; }
h2 { font-size: 14pt; border-bottom: 2px solid #1f2328; padding-bottom: 4px; margin: 22px 0 8px; break-after: avoid; }
h3 { font-size: 11.5pt; margin: 14px 0 6px; break-after: avoid; }
blockquote { margin: 0 0 14px; padding: 8px 12px; border-left: 4px solid #c0392b; background: #fbeeee; }
table { border-collapse: collapse; width: 100%; font-size: 8.5pt; }
th, td { border: 1px solid #c8ced6; padding: 4px 6px; text-align: left; }
th { background: #f2f4f7; }
tr { break-inside: avoid; }
code { font-family: Consolas, monospace; font-size: 9pt; }
"""


def load_tags(path: Path = TAGS) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _num(v: float) -> str:
    return f"{v:g}"


def _limit(limits: dict, kind: str) -> str:
    """kind = alarm|slowdown|shutdown → '≤ 1.7' / '≥ 450' / '-'."""
    parts = []
    if f"{kind}_low" in limits:
        parts.append(f"≤ {_num(limits[f'{kind}_low'])}")
    if f"{kind}_high" in limits:
        parts.append(f"≥ {_num(limits[f'{kind}_high'])}")
    return ", ".join(parts) or "-"


def setpoint_table(tags: dict) -> str:
    """tags.yaml의 태그마다 한 줄인 마크다운 표."""
    rows = [
        "| 태그 | 이름 | 단위 | 측정 범위 | 정상 범위 | 경보 | 감속 | 정지 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for t in tags["tags"]:
        if "states" in t:
            state = "·".join(t["states"])
            rows.append(f"| `{t['tag']}` | {t['name']} | - | - | 상태 표시({state}) | - | - | - |")
            continue
        lim = t.get("limits", {})
        lo, hi = t["normal"]
        mlo, mhi = t["measure"]
        rows.append(
            f"| `{t['tag']}` | {t['name']} | {t['unit']} | {_num(mlo)} ~ {_num(mhi)} | {_num(lo)} ~ {_num(hi)} "
            f"| {_limit(lim, 'alarm')} | {_limit(lim, 'slowdown')} | {_limit(lim, 'shutdown')} |"
        )
    return "\n".join(rows)


def render_markdown(md_text: str, tags: dict) -> str:
    if MARKER not in md_text:
        raise ValueError(f"매뉴얼에 {MARKER} 자리가 없다")
    return md_text.replace(MARKER, setpoint_table(tags))


def to_html(md_text: str) -> str:
    body = markdown.markdown(md_text, extensions=["tables"])
    return f'<!doctype html><html lang="ko"><head><meta charset="utf-8"><style>{CSS}</style></head><body>{body}</body></html>'


def find_browser() -> str:
    for b in BROWSERS:
        if b and (Path(b).exists() or shutil.which(b)):
            return b
    raise FileNotFoundError("Edge나 Chrome을 찾지 못했다. BROWSER_PATH로 경로를 지정한다.")


def print_pdf(html: str, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        page = Path(tmp) / "manual.html"
        page.write_text(html, encoding="utf-8")
        # 이미 열린 브라우저와 섞이지 않게 별도 프로필로 띄운다.
        subprocess.run(
            [
                find_browser(),
                "--headless",
                "--disable-gpu",
                "--no-pdf-header-footer",
                f"--user-data-dir={Path(tmp) / 'profile'}",
                f"--print-to-pdf={out}",
                page.as_uri(),
            ],
            check=True,
            capture_output=True,
            timeout=120,
        )
    if not out.exists():
        raise RuntimeError("PDF가 만들어지지 않았다")


def main() -> None:
    md = render_markdown(MANUAL_MD.read_text(encoding="utf-8"), load_tags())
    print_pdf(to_html(md), OUT_PDF)
    print(f"wrote {OUT_PDF.relative_to(ROOT)} ({OUT_PDF.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
