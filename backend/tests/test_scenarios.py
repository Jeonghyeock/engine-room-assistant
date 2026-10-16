import json

import pytest

from ams import scenario
from ams.scenario import build, list_ids, load, load_tags

TAGS = load_tags()
BASELINE = json.loads((scenario.SCENARIOS / "baseline.json").read_text(encoding="utf-8"))["values"]


def now(s, tag):
    return s.series[tag][-1]


def normal(tag, value):
    lo, hi = TAGS[tag]["normal"]
    return lo <= value <= hi


def test_all_scenarios_load_with_full_series():
    assert list_ids() == ["S1", "S2", "S3"]
    for sid in list_ids():
        s = load(sid)
        assert set(s.series) == set(TAGS)
        assert all(len(v) == s.minutes + 1 for v in s.series.values())
        assert s.time_at(s.minutes) == s.now


def test_series_are_deterministic():
    assert load("S2").series == load("S2").series


def test_baseline_is_normal_everywhere():
    for tag, value in BASELINE.items():
        if "states" not in TAGS[tag]:
            assert normal(tag, value), tag


def test_s1_only_cylinder3_runs_hot():
    s = load("S1")
    assert now(s, "ME_EXH_TEMP_CYL3") >= TAGS["ME_EXH_TEMP_CYL3"]["limits"]["alarm_high"]
    for n in (1, 2, 4, 5, 6):
        assert all(normal(f"ME_EXH_TEMP_CYL{n}", v) for v in s.series[f"ME_EXH_TEMP_CYL{n}"])
    # 갑자기 뛴 것이 아니라 꾸준히 올랐다
    row = s.series["ME_EXH_TEMP_CYL3"]
    assert max(b - a for a, b in zip(row, row[1:])) < 10


def test_s2_filter_clogs_and_pressure_drops_but_not_to_slowdown():
    s = load("S2")
    lim = TAGS["ME_LO_PRESS_IN"]["limits"]
    assert now(s, "LO_FILTER_DP") >= TAGS["LO_FILTER_DP"]["limits"]["alarm_high"]
    assert lim["slowdown_low"] < now(s, "ME_LO_PRESS_IN") <= lim["alarm_low"]
    assert s.series["LO_PUMP1_STATE"][-1] == "운전" and s.series["LO_PUMP2_STATE"][-1] == "대기"
    assert all(normal("LO_SUMP_LEVEL", v) for v in s.series["LO_SUMP_LEVEL"])


def test_s3_outlet_temp_jumps_and_sticks_while_neighbours_stay():
    s = load("S3")
    row = s.series["ME_JCW_TEMP_OUT"]
    assert max(b - a for a, b in zip(row, row[1:])) > 10  # 매뉴얼 5.3: 1분에 10 °C 넘게
    assert row[-3:] == [149.9, 149.9, 149.9]  # 고착 — 흔들리지 않는다
    for tag in ("ME_JCW_TEMP_IN", "ME_JCW_PRESS_IN", "ME_LO_TEMP_IN"):
        assert all(normal(tag, v) for v in s.series[tag]), tag
    assert now(s, "ME_RPM") < s.series["ME_RPM"][27]  # 감속으로 회전수가 내려간다


def _spec(**over):
    return {"id": "T", "title": "t", "summary": "t", "now": "2026-10-16T14:30:00+09:00", "minutes": 10, **over}


def test_unknown_tag_is_rejected():
    with pytest.raises(ValueError, match="모르는 태그"):
        build(_spec(changes=[{"tag": "NOPE", "at": 1, "value": 1}]), BASELINE, TAGS)


def test_value_outside_measure_range_is_rejected():
    with pytest.raises(ValueError, match="측정 범위"):
        build(_spec(changes=[{"tag": "ME_LO_PRESS_IN", "at": 1, "value": 9}]), BASELINE, TAGS)


def test_unknown_state_is_rejected():
    with pytest.raises(ValueError, match="상태는"):
        build(_spec(changes=[{"tag": "LO_PUMP1_STATE", "at": 1, "value": "고장"}]), BASELINE, TAGS)


def test_baseline_must_cover_every_tag():
    partial = {k: v for k, v in BASELINE.items() if k != "GE2_LOAD"}
    with pytest.raises(ValueError, match="GE2_LOAD"):
        build(_spec(), partial, TAGS)
