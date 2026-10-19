import json

import pytest

from ams import scenario
from ams.scenario import build, load, load_tags
from ams.system import AlarmMonitoringSystem, UnknownSystemError, UnknownTagError


@pytest.fixture(scope="module")
def ams():
    return {sid: AlarmMonitoringSystem(load(sid)) for sid in ("S1", "S2", "S3")}


def alarm_keys(system):
    return [(a["tag"], a["level"]) for a in system.active_alarms()]


def test_s1_only_cylinder3_alarm(ams):
    assert alarm_keys(ams["S1"]) == [("ME_EXH_TEMP_CYL3", "경보")]
    a = ams["S1"].active_alarms()[0]
    assert a["limit"] == "≥ 450" and a["value"] >= 450
    assert (a["since"], a["minutes_ago"]) == ("2026-10-16T14:27+09:00", 3)


def test_s2_filter_and_pressure_alarms_in_order(ams):
    alarms = ams["S2"].active_alarms()
    assert [(a["tag"], a["level"]) for a in alarms] == [("LO_FILTER_DP", "경보"), ("ME_LO_PRESS_IN", "경보")]
    # 필터 차압 경보가 압력 경보보다 먼저 떴다 — 막힘이 원인이라는 단서
    assert [a["minutes_ago"] for a in alarms] == [6, 3]
    assert alarms[1]["limit"] == "≤ 1.7"


def test_s3_slowdown_listed_before_alarm_with_same_onset(ams):
    alarms = ams["S3"].active_alarms()
    assert [(a["tag"], a["level"]) for a in alarms] == [("ME_JCW_TEMP_OUT", "감속"), ("ME_JCW_TEMP_OUT", "경보")]
    assert {(a["since"], a["minutes_ago"]) for a in alarms} == {("2026-10-16T14:28+09:00", 2)}


def test_read_sensors_reports_status(ams):
    cyl3, cyl4 = ams["S1"].read_sensors(["ME_EXH_TEMP_CYL3", "ME_EXH_TEMP_CYL4"])
    assert cyl3["status"] == "경보" and cyl4["status"] == "정상"
    assert cyl3["unit"] == "°C" and cyl3["measure"] == [0, 600]
    # 상태 태그에는 status가 없다 — 펌프 "정지"가 알람 등급 "정지"로 읽히지 않게
    pump = ams["S2"].read_sensors(["LO_PUMP2_STATE"])[0]
    assert pump == {"tag": "LO_PUMP2_STATE", "name": "2번 윤활유 펌프 상태", "value": "대기"}


def test_read_sensors_forgives_case_strings_and_duplicates(ams):
    rows = ams["S2"].read_sensors([" me_lo_press_in", "ME_LO_PRESS_IN", "lo filter dp"])
    assert [r["tag"] for r in rows] == ["ME_LO_PRESS_IN", "LO_FILTER_DP"]
    assert [r["tag"] for r in ams["S2"].read_sensors("ME_RPM")] == ["ME_RPM"]


def test_unknown_tag_suggests_close_names(ams):
    with pytest.raises(UnknownTagError, match="ME_LO_PRESS_IN"):
        ams["S2"].read_sensors(["ME_LO_PRESSURE_IN"])


def test_trend_summarises_window(ams):
    t = ams["S3"].trend("ME_JCW_TEMP_OUT", minutes=30, points=7)
    assert len(t["samples"]) == 7
    assert t["samples"][0]["minutes_ago"] == 30 and t["samples"][-1]["minutes_ago"] == 0
    assert t["last"] == 150.0 and t["max_change_per_minute"] > 10
    assert t["max_change_minutes_ago"] == 2  # 28분(2분 전)에 뛰었다
    json.dumps(t)  # 그대로 도구 결과로 나간다


def test_trend_window_is_clamped_and_steady_tags_change_little(ams):
    t = ams["S1"].trend("ME_EXH_TEMP_CYL4", minutes=999, points=3)
    assert t["window_minutes"] == 30 and len(t["samples"]) == 3
    assert abs(t["max_change_per_minute"]) < 5


def test_trend_of_state_tag_has_samples_only(ams):
    t = ams["S2"].trend("LO_PUMP1_STATE", minutes=10, points=3)
    assert [s["value"] for s in t["samples"]] == ["운전", "운전", "운전"]
    assert "max_change_per_minute" not in t


def test_list_tags_filters_by_system(ams):
    lo = ams["S1"].list_tags("lo")
    assert {t["tag"] for t in lo} == {"ME_LO_PRESS_IN", "ME_LO_TEMP_IN", "LO_FILTER_DP", "LO_SUMP_LEVEL", "LO_PUMP1_STATE", "LO_PUMP2_STATE"}
    assert len(ams["S1"].list_tags()) == 20


def test_unknown_system_lists_codes_first(ams):
    with pytest.raises(UnknownSystemError, match="계통 코드 ME, LO, JCW, FO, GE 중 하나만"):
        ams["S1"].list_tags("주기관")


def test_system_code_with_name_attached_is_accepted(ams):
    """리뷰에서 qwen3:8b가 설명 문구 그대로 "ME 주기관"을 넣었다 — 앞의 코드만 본다."""
    assert {t["system"] for t in ams["S1"].list_tags("ME 주기관")} == {"ME"}
    assert {t["system"] for t in ams["S1"].list_tags("lo(윤활유)")} == {"LO"}


def test_alarm_through_whole_window_has_no_made_up_onset():
    baseline = json.loads((scenario.SCENARIOS / "baseline.json").read_text(encoding="utf-8"))["values"]
    spec = {"id": "T", "title": "t", "summary": "t", "now": "2026-10-16T14:30:00+09:00", "minutes": 30,
            "changes": [{"tag": "LO_FILTER_DP", "at": 0, "value": 0.95}, {"tag": "ME_LO_PRESS_IN", "at": 25, "value": 1.6}]}
    alarms = AlarmMonitoringSystem(build(spec, baseline, load_tags())).active_alarms()
    dp, press = alarms
    assert dp["tag"] == "LO_FILTER_DP" and dp["since"] is None and dp["minutes_ago"] is None and "30분 내내" in dp["since_note"]
    assert press["minutes_ago"] == 5  # 기록 안에서 뜬 알람은 시각이 있다


def test_read_known_reads_what_it_can_and_lists_the_rest(ams):
    """평가: 짐작한 태그 셋을 섞어 읽다 하나씩 빼며 실패를 되풀이해 멈췄다 — 한 번에 다 알려 준다."""
    rows, unknown = ams["S2"].read_known(["LO_FILTER_DP", "LO_PRESS_IN", "ME_LO_PRESS_IN", "LO_TANK_LEVEL"])
    assert [r["tag"] for r in rows] == ["LO_FILTER_DP", "ME_LO_PRESS_IN"]
    assert [u["tag"] for u in unknown] == ["LO_PRESS_IN", "LO_TANK_LEVEL"]
    assert "ME_LO_PRESS_IN" in unknown[0]["similar"]
