"""가상 알람 감시 시스템(AMS) — 시나리오를 재생하며 현재값·알람·추세를 읽기 전용으로 내준다.

MCP 서버 ① ams의 도구들이 이 메서드들을 감싼다. 값을 바꾸는 메서드는 없다.

알람 판정은 tags.yaml의 설정값으로 한다. 낮은 쪽은 설정값 이하, 높은 쪽은 설정값 이상이면 동작한다.
한 태그가 경보와 감속을 함께 넘으면 둘 다 알람으로 낸다(실제 AMS 화면처럼 등급별로 따로 뜬다).

출력은 작은 모델이 잘못 읽지 않게 만든다.
- 상태 태그(펌프)에는 status를 붙이지 않는다. 펌프 "정지"가 알람 등급 "정지"로 읽히지 않게.
- 알람이 기록 창 전체에서 이어졌으면 발생 시각을 지어내지 않고 null과 설명을 준다.
"""

import difflib
from datetime import datetime

import yaml

from ams.scenario import TAGS, Scenario, load_tags

# 심각한 순서
LEVELS = [("shutdown", "정지"), ("slowdown", "감속"), ("alarm", "경보")]


def similar_tags(name: str, known: list[str]) -> list[str]:
    return difflib.get_close_matches(str(name).upper(), known, n=3, cutoff=0.5)


class UnknownTagError(ValueError):
    """모르는 태그. 모델이 스스로 고칠 수 있게 비슷한 태그를 함께 알려 준다."""

    def __init__(self, name: str, known: list[str]):
        close = similar_tags(name, known)
        hint = f" 비슷한 태그: {', '.join(close)}." if close else " list_tags로 태그 목록을 볼 수 있다."
        super().__init__(f"모르는 태그 {name!r}.{hint}")


class UnknownSystemError(ValueError):
    def __init__(self, name: str, systems: dict[str, str]):
        # 코드를 먼저 적는다 — "ME 주기관"처럼 이름까지 붙여 쓰면 작은 모델이 그 문구를 그대로 값으로 넣는다.
        codes = ", ".join(systems)
        meaning = ", ".join(f"{k}={v}" for k, v in systems.items())
        super().__init__(f"모르는 계통 {name!r}. 계통 코드 {codes} 중 하나만 넣거나 비운다({meaning}).")


def _iso(t: datetime) -> str:
    return t.isoformat(timespec="minutes")


def load_systems() -> dict[str, str]:
    return yaml.safe_load(TAGS.read_text(encoding="utf-8"))["systems"]


class AlarmMonitoringSystem:
    def __init__(self, scenario: Scenario, tags: dict[str, dict] | None = None, systems: dict[str, str] | None = None):
        self.scenario = scenario
        self.tags = tags or load_tags()
        self.systems = systems or load_systems()

    # ---- 조회 ----

    def _canonical(self, name: str) -> str:
        """대소문자·앞뒤 공백·공백 대신 쓴 밑줄은 받아 준다(모델이 한 번 덜 되묻게)."""
        key = str(name).strip().upper().replace(" ", "_")
        if key not in self.tags:
            raise UnknownTagError(name, list(self.tags))
        return key

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

    def _status(self, name: str, value: float) -> str:
        hit = self._violations(name, value)
        if hit:
            return hit[0][1]
        lo, hi = self.tags[name]["normal"]
        return "정상" if lo <= value <= hi else "정상 범위 밖"

    def list_tags(self, system: str | None = None) -> list[dict]:
        if system and system.strip():
            # "ME 주기관", "me(주기관)"처럼 이름이 붙어 와도 앞의 코드만 본다.
            code = system.strip().split()[0].split("(")[0].split("=")[0].upper()
            if code not in self.systems:
                raise UnknownSystemError(system, self.systems)
            system = code
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

    def read_known(self, names: list[str] | str) -> tuple[list[dict], list[dict]]:
        """아는 태그는 읽고, 모르는 태그는 비슷한 태그와 함께 따로 돌려준다.

        모르는 태그 하나에 호출 전체가 실패하면, 관련 값을 짐작해 여럿 읽으려던 모델이 틀린 태그를 하나씩 빼며
        실패를 되풀이하다 멈췄다(평가 S2) — 한 번에 무엇이 틀렸는지 다 알려 준다.
        """
        if isinstance(names, str):  # "ME_RPM"을 글자 단위로 돌지 않게
            names = [names]
        known: list[str] = []
        unknown: dict[str, dict] = {}  # 대소문자·공백만 다른 같은 태그는 한 번만
        for raw in names:
            try:
                known.append(self._canonical(raw))
            except UnknownTagError:
                key = str(raw).strip().upper()
                if key not in unknown:
                    unknown[key] = {"tag": raw, "similar": similar_tags(raw, list(self.tags))}
        return [self._row(name) for name in dict.fromkeys(known)], list(unknown.values())  # 같은 태그는 한 번만

    def _row(self, name: str) -> dict:
        tag = self.tags[name]
        value = self.scenario.series[name][-1]
        row = {"tag": name, "name": tag["name"], "value": value}
        if "states" not in tag:
            row |= {"status": self._status(name, value), "unit": tag["unit"], "normal": tag["normal"], "measure": tag["measure"]}
        return row

    def active_alarms(self) -> list[dict]:
        s = self.scenario
        out = []
        for name, row in s.series.items():
            if "states" in self.tags[name]:
                continue
            for key, label, op, limit in self._violations(name, row[-1]):
                # 지금까지 끊기지 않고 이어진 구간의 시작이 발생 시각이다. 같은 등급·같은 방향끼리만 잇는다.
                start = s.minutes
                while start > 0 and (key, op) in {(k, o) for k, _, o, _ in self._violations(name, row[start - 1])}:
                    start -= 1
                alarm = {
                    "tag": name,
                    "name": self.tags[name]["name"],
                    "level": label,
                    "value": row[-1],
                    "unit": self.tags[name]["unit"],
                    "limit": f"{op} {limit:g}",
                }
                if start == 0:
                    alarm |= {"since": None, "minutes_ago": None, "since_note": f"기록된 {s.minutes}분 내내 이어짐 — 발생 시각은 그 전"}
                else:
                    alarm |= {"since": _iso(s.time_at(start)), "minutes_ago": s.minutes - start}
                out.append(alarm)
        rank = {label: i for i, (_, label) in enumerate(LEVELS)}
        oldest_first = lambda a: -(a["minutes_ago"] if a["minutes_ago"] is not None else s.minutes + 1)  # noqa: E731
        return sorted(out, key=lambda a: (rank[a["level"]], oldest_first(a), a["tag"]))

    def trend(self, name: str, minutes: int = 30, points: int = 7) -> dict:
        """지난 minutes분을 points개 점으로 줄인 요약. 원시 값을 다 주면 모델의 문맥만 채운다."""
        name = self._canonical(name)
        tag = self.tags[name]
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
        i = max(range(len(steps)), key=lambda j: abs(steps[j])) if steps else None
        return out | {
            "unit": tag["unit"],
            "first": row[0],
            "last": row[-1],
            "change": round(row[-1] - row[0], 3),
            "min": min(row),
            "max": max(row),
            # 1분 사이 가장 큰 변화(절댓값 기준, 부호 포함)와 그 변화가 끝난 시점.
            # 물리적으로 어려운 변화면 센서 이상을 의심할 근거가 된다.
            "max_change_per_minute": round(steps[i], 3) if i is not None else 0,
            "max_change_minutes_ago": minutes - (i + 1) if i is not None else None,
        }
