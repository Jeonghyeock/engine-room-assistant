import pytest

from ams.scenario import load
from ams.system import AlarmMonitoringSystem, UnknownTagError


@pytest.fixture(scope="module")
def ams():
    return {sid: AlarmMonitoringSystem(load(sid)) for sid in ("S1", "S2", "S3")}


def alarm_keys(system):
    return [(a["tag"], a["level"]) for a in system.active_alarms()]


def test_s1_only_cylinder3_alarm(ams):
    assert alarm_keys(ams["S1"]) == [("ME_EXH_TEMP_CYL3", "경보")]
    a = ams["S1"].active_alarms()[0]
    assert a["limit"] == "≥ 450" and a["value"] >= 450 and 0 < a["minutes_ago"] < 10


def test_s2_filter_and_pressure_alarms_in_order(ams):
    alarms = ams["S2"].active_alarms()
    assert [(a["tag"], a["level"]) for a in alarms] == [("LO_FILTER_DP", "경보"), ("ME_LO_PRESS_IN", "경보")]
    # 필터 차압 경보가 압력 경보보다 먼저 떴다 — 막힘이 원인이라는 단서
    assert alarms[0]["minutes_ago"] > alarms[1]["minutes_ago"]
    assert alarms[1]["limit"] == "≤ 1.7"


def test_s3_slowdown_listed_before_alarm_with_same_onset(ams):
    alarms = ams["S3"].active_alarms()
    assert [(a["tag"], a["level"]) for a in alarms] == [("ME_JCW_TEMP_OUT", "감속"), ("ME_JCW_TEMP_OUT", "경보")]
    assert {a["since"] for a in alarms} == {"2026-10-16T14:28+09:00"}


def test_read_sensors_reports_status(ams):
    cyl3, cyl4 = ams["S1"].read_sensors(["ME_EXH_TEMP_CYL3", "ME_EXH_TEMP_CYL4"])
    assert cyl3["status"] == "경보" and cyl4["status"] == "정상"
    assert cyl3["unit"] == "°C" and cyl3["measure"] == [0, 600]
    pump = ams["S2"].read_sensors(["LO_PUMP2_STATE"])[0]
    assert pump == {"tag": "LO_PUMP2_STATE", "name": "2번 윤활유 펌프 상태", "value": "대기", "status": "대기"}


def test_unknown_tag_suggests_close_names(ams):
    with pytest.raises(UnknownTagError, match="ME_LO_PRESS_IN"):
        ams["S2"].read_sensors(["ME_LO_PRESSURE_IN"])


def test_trend_summarises_window(ams):
    t = ams["S3"].trend("ME_JCW_TEMP_OUT", minutes=30, points=7)
    assert len(t["samples"]) == 7
    assert t["samples"][0]["minutes_ago"] == 30 and t["samples"][-1]["minutes_ago"] == 0
    assert t["last"] == 150.0 and t["max_change_per_minute"] > 10


def test_trend_window_is_clamped_and_steady_tags_change_little(ams):
    t = ams["S1"].trend("ME_EXH_TEMP_CYL4", minutes=999, points=3)
    assert t["window_minutes"] == 30 and len(t["samples"]) == 3
    assert abs(t["max_change_per_minute"]) < 5


def test_trend_of_state_tag_has_samples_only(ams):
    t = ams["S2"].trend("LO_PUMP1_STATE", minutes=10, points=3)
    assert [s["value"] for s in t["samples"]] == ["운전", "운전", "운전"]
    assert "max_change_per_minute" not in t


def test_list_tags_filters_by_system(ams):
    lo = ams["S1"].list_tags("LO")
    assert {t["tag"] for t in lo} == {"ME_LO_PRESS_IN", "ME_LO_TEMP_IN", "LO_FILTER_DP", "LO_SUMP_LEVEL", "LO_PUMP1_STATE", "LO_PUMP2_STATE"}
    assert len(ams["S1"].list_tags()) == 20
