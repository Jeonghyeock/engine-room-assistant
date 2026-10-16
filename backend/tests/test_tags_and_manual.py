import re

import pytest

from ingest import build_manual
from ingest.manual import clean, heading_locator, row_sentence

TAGS = build_manual.load_tags()
NUMERIC = [t for t in TAGS["tags"] if "states" not in t]


def test_tag_names_are_unique_and_systems_known():
    names = [t["tag"] for t in TAGS["tags"]]
    assert len(names) == len(set(names))
    assert {t["system"] for t in TAGS["tags"]} <= set(TAGS["systems"])


@pytest.mark.parametrize("t", NUMERIC, ids=lambda t: t["tag"])
def test_limits_sit_outside_normal_and_inside_measure(t):
    lo, hi = t["normal"]
    mlo, mhi = t["measure"]
    assert mlo <= lo < hi <= mhi
    lim = t.get("limits", {})
    lows = [lim[k] for k in ("alarm_low", "slowdown_low", "shutdown_low") if k in lim]
    highs = [lim[k] for k in ("alarm_high", "slowdown_high", "shutdown_high") if k in lim]
    # 경보 → 감속 → 정지 순으로 점점 더 벗어난다.
    assert lows == sorted(lows, reverse=True) and all(mlo < v < lo for v in lows)
    assert highs == sorted(highs) and all(hi < v < mhi for v in highs)


def test_setpoint_table_has_one_row_per_tag():
    table = build_manual.setpoint_table(TAGS)
    for t in TAGS["tags"]:
        assert f"`{t['tag']}`" in table
    assert "≤ 1.7" in table and "≤ 1.3" in table  # ME_LO_PRESS_IN 경보·정지


def test_manual_body_does_not_repeat_setpoints():
    """설정값은 tags.yaml 한 곳에만. 본문에 '1.7 bar' 같은 숫자를 직접 쓰면 표와 어긋날 수 있다."""
    body = build_manual.MANUAL_MD.read_text(encoding="utf-8")
    assert build_manual.MARKER in body
    for t in NUMERIC:
        for v in t.get("limits", {}).values():
            assert not re.search(rf"(?<![\d.]){re.escape(f'{v:g}')}\s*{re.escape(t['unit'])}", body), (t["tag"], v)


def test_render_requires_marker():
    with pytest.raises(ValueError):
        build_manual.render_markdown("# 표 자리가 없는 문서", TAGS)


def test_heading_locator():
    assert heading_locator("4.2 입구 압력 낮음") == ("매뉴얼 4.2", "4.2 입구 압력 낮음")
    assert heading_locator("2. 알람·안전 장치 설정값") == ("매뉴얼 2", "2. 알람·안전 장치 설정값")
    assert heading_locator("가상 선박 기관 운전 지침서") is None


def test_clean_joins_wrapped_lines_and_tag_names():
    assert clean("베어링이 손상될\n수 있다") == "베어링이 손상될 수 있다"
    assert clean("예를 들어 ME _ LO _ PRESS _ IN은") == "예를 들어 ME_LO_PRESS_IN은"


def test_row_sentence_skips_empty_cells():
    assert row_sentence(["태그", "경보", "감속"], ["LO_FILTER_DP", "≥ 0.8", "-"]) == "태그: LO_FILTER_DP; 경보: ≥ 0.8"
