"""가상 알람 감시 시스템(AMS) — 시나리오를 재생하며 현재값·알람·추세를 읽기 전용으로 내준다.

MCP 서버 ① ams가 이 클래스를 그대로 도구로 노출한다. 값을 바꾸는 메서드는 없다.

알람 판정은 tags.yaml의 설정값으로 한다. 낮은 쪽은 설정값 이하, 높은 쪽은 설정값 이상이면 동작한다.
한 태그가 경보와 감속을 함께 넘으면 둘 다 알람으로 낸다(실제 AMS 화면처럼 등급별로 따로 뜬다).
"""

import difflib
from datetime import datetime

from ams.scenario import Scenario, load_tags

# 심각한 순서
LEVELS = [("shutdown", "정지"), ("slowdown", "감속"), ("alarm", "경보")]


class UnknownTagError(ValueError):
    """모르는 태그. 모델이 스스로 고칠 수 있게 비슷한 태그를 함께 알려 준다."""

    def __init__(self, name: str, known: list[str]):
        close = difflib.get_close_matches(name.upper(), known, n=3, cutoff=0.5)
        hint = f" 비슷한 태그: {', '.join(close)}." if close else " list_tags로 태그 목록을 볼 수 있다."
        super().__init__(f"모르는 태그 {name!r}.{hint}")


def _iso(t: datetime) -> str:
    return t.isoformat(timespec="minutes")


class AlarmMonitoringSystem:
    def __init__(self, scenario: Scenario, tags: dict[str, dict] | None = None):
        self.scenario = scenario
        self.tags = tags or load_tags()

    # ---- 조회 ----

    def _tag(self, name: str) -> dict:
        if name not in self.tags:
            raise UnknownTagError(name, list(self.tags))
        return self.tags[name]

    def _violations(self, name: str, value: float) -> list[tuple[str, str, str, float]]:
        """(등급 키, 등급 이름, '≤'|'≥', 설정값) — 심각한 순서."""
        lim = self.tags[name].get("limits", {})
        out = []
        for key, label in LEVELS:
            if f"{key}_low" in lim and value <= lim[f"{key}_low"]:
                out.append((key, label, "≤", lim[f"{key}_low"]))
            if f"{key}_high" in lim and value >= lim[f"{key}_high"]:
                out.append((key, label, "≥", lim[f"{key}_high"]))
        return out

    def _status(self, name: str, value) -> str:
        tag = self.tags[name]
        if "states" in tag:
            return value
        hit = self._violations(name, value)
        if hit:
            return hit[0][1]
        lo, hi = tag["normal"]
        return "정상" if lo <= value <= hi else "정상 범위 밖"

    def list_tags(self, system: str | None = None) -> list[dict]:
        out = []
        for t in self.tags.values():
            if system and t["system"] != system:
                continue
            row = {"tag": t["tag"], "name": t["name"], "system": t["system"]}
            if "states" in t:
                row["states"] = t["states"]
            else:
                row |= {"unit": t["unit"], "normal": t["normal"], "measure": t["measure"]}
            out.append(row)
        return out

    def read_sensors(self, names: list[str]) -> list[dict]:
        out = []
        for name in names:
            tag = self._tag(name)
            value = self.scenario.series[name][-1]
            row = {"tag": name, "name": tag["name"], "value": value, "status": self._status(name, value)}
            if "states" not in tag:
                row |= {"unit": tag["unit"], "normal": tag["normal"], "measure": tag["measure"]}
            out.append(row)
        return out

    def active_alarms(self) -> list[dict]:
        s = self.scenario
        out = []
        for name, row in s.series.items():
            if "states" in self.tags[name]:
                continue
            for key, label, op, limit in self._violations(name, row[-1]):
                # 지금까지 끊기지 않고 이어진 구간의 시작이 발생 시각이다.
                start = s.minutes
                while start > 0 and any(k == key for k, *_ in self._violations(name, row[start - 1])):
                    start -= 1
                out.append(
                    {
                        "tag": name,
                        "name": self.tags[name]["name"],
                        "level": label,
                        "value": row[-1],
                        "unit": self.tags[name]["unit"],
                        "limit": f"{op} {limit:g}",
                        "since": _iso(s.time_at(start)),
                        "minutes_ago": s.minutes - start,
                    }
                )
        rank = {label: i for i, (_, label) in enumerate(LEVELS)}
        return sorted(out, key=lambda a: (rank[a["level"]], -a["minutes_ago"], a["tag"]))

    def trend(self, name: str, minutes: int = 30, points: int = 7) -> dict:
        """지난 minutes분을 points개 점으로 줄인 요약. 원시 값을 다 주면 모델의 문맥만 채운다."""
        tag = self._tag(name)
        s = self.scenario
        minutes = max(1, min(minutes, s.minutes))
        points = max(2, min(points, minutes + 1))
        row = s.series[name][s.minutes - minutes :]
        picks = sorted({round(i * minutes / (points - 1)) for i in range(points)})
        samples = [{"t": _iso(s.time_at(s.minutes - minutes + m)), "minutes_ago": minutes - m, "value": row[m]} for m in picks]
        out = {"tag": name, "name": tag["name"], "window_minutes": minutes, "samples": samples}
        if "states" in tag:
            return out
        steps = [b - a for a, b in zip(row, row[1:])]
        jump = max(steps, key=abs) if steps else 0
        return out | {
            "unit": tag["unit"],
            "first": row[0],
            "last": row[-1],
            "change": round(row[-1] - row[0], 3),
            "min": min(row),
            "max": max(row),
            # 1분 사이 가장 큰 변화(부호 포함). 물리적으로 불가능한 변화면 센서 이상을 의심할 근거가 된다.
            "max_change_per_minute": round(jump, 3),
        }
