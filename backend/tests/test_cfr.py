import pytest

from ingest import cfr

SECTION = """<DIV5><DIV8 N="99.1-1" TYPE="SECTION">
<HEAD>&#xA7; 99.1-1 Test section.</HEAD>
<P>Intro text.</P>
<P>(a) First.</P>
<P>(b) <I>Second.</I> (1) Sub one.</P>
<NOTE><HED>Note 1 to paragraph (<E T="01">b</E>)(1):</HED><P>A note.</P></NOTE>
<P>(2) Sub two.</P>
<P>(i) Roman one under (b)(2).</P>
<P>(c) Third.</P>
<CITA>[CGD 81-030, 53 FR 17838]</CITA>
<DIV><DIV class="gpotbl_div"><TABLE><CAPTION>Table 1 to &#xA7; 99.1-1</CAPTION>
<THEAD><TR><TH>System</TH><TH>Alarm</TH></TR></THEAD>
<TBODY><TR><TD>Main engine</TD><TD>Low</TD></TR><TR><TD></TD><TD>High</TD></TR>
<TR><TD>Bilge</TD><TD>High</TD></TR></TBODY>
<TFOOT><TR><TD>1 See part 111.</TD></TR></TFOOT></TABLE></DIV></DIV>
</DIV8></DIV5>"""


@pytest.fixture(scope="module")
def chunks():
    return cfr.parse(SECTION)


def test_splits_by_top_level_paragraph(chunks):
    locs = [c.locator for c in chunks if "표" not in c.locator]
    assert locs == ["§99.1-1", "§99.1-1(a)", "§99.1-1(b)", "§99.1-1(c)"]


def test_roman_i_stays_under_its_paragraph(chunks):
    """(i)는 (h) 다음일 때만 맨 위 문단이다. 여기서는 (b)(2) 아래의 로마 숫자."""
    b = next(c for c in chunks if c.locator == "§99.1-1(b)")
    assert "(1) Sub one." in b.text and "(2) Sub two." in b.text and "(i) Roman one" in b.text
    assert "Note 1 to paragraph (b)(1): A note." in b.text


def test_revision_history_is_dropped(chunks):
    assert not any("CGD" in c.text for c in chunks)


def test_table_rows_grouped_by_first_column(chunks):
    rows = [c for c in chunks if c.locator == "§99.1-1 표 1"]
    assert rows[0].text == "System: Main engine; Alarm: Low\nSystem: Main engine; Alarm: High"
    assert rows[1].text == "System: Bilge; Alarm: High"
    assert rows[2].title.endswith("각주") and "See part 111" in rows[2].text
    assert [c.id for c in rows] == ["cfr:§99.1-1 표 1", "cfr:§99.1-1 표 1#2", "cfr:§99.1-1 표 1#3"]


def test_real_part62_has_the_clauses_scenarios_cite():
    """시나리오가 근거로 쓰는 조항이 실제 원문에서 그대로 잘려 나오는지."""
    by_loc = {c.locator: c for c in cfr.load()}
    assert "Loss of forced lubrication safety trip controls must be provided" in by_loc["§62.35-50(c)"].text
    assert "Override of overspeed and loss of forced lubrication pressure" in by_loc["§62.35-50(d)"].text
    assert "(2) Systems with remote instrumentation must have provisions" in by_loc["§62.25-20(b)"].text
    assert "must be failsafe" in by_loc["§62.30-1(b)"].text
