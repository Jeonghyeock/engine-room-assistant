"""시나리오 — 지난 N분 동안 태그 값이 어떻게 변했는지를 적은 파일을 분 단위 값으로 펼친다.

- 평상시 값은 data/scenarios/baseline.json 한 곳에, 시나리오에는 바뀌는 부분만 적는다.
- 바뀜은 두 가지다.
  - 경사: {"tag", "from", "to", "value"} — from분의 값에서 to분까지 value로 곧게 바뀐다.
  - 계단: {"tag", "at", "value"} — at분부터 value로 바뀌어 그대로 멈춘다(센서 고착, 펌프 정지 등).
- 숫자 값에는 값의 0.3% 안쪽 흔들림을 넣는다. 시나리오·태그마다 씨앗이 정해져 있어 매번 같은 값이 나온다.
  계단으로 고정된 값은 흔들지 않는다 — 고착된 센서는 값이 흔들리지 않는다.
- "현재"는 시나리오의 now(마지막 분)로 고정한다.
"""

import json
import random
import zlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
SCENARIOS = ROOT / "data" / "scenarios"
TAGS = ROOT / "data" / "tags.yaml"

NOISE = 0.003
DECIMALS = {"°C": 1, "bar": 2, "%": 1, "rpm": 1}


@dataclass(frozen=True)
class Scenario:
    id: str
    title: str
    summary: str
    now: datetime
    minutes: int
    series: dict[str, list]  # 태그 → 0분부터 minutes분까지 분마다 값(길이 minutes + 1)

    def time_at(self, minute: int) -> datetime:
        return self.now - timedelta(minutes=self.minutes - minute)


def load_tags(path: Path = TAGS) -> dict[str, dict]:
    return {t["tag"]: t for t in yaml.safe_load(path.read_text(encoding="utf-8"))["tags"]}


def list_ids(folder: Path = SCENARIOS) -> list[str]:
    return sorted(p.stem for p in folder.glob("S*.json"))


def _check_value(tag: dict, value, where: str) -> None:
    if "states" in tag:
        if value not in tag["states"]:
            raise ValueError(f"{where}: {tag['tag']} 상태는 {tag['states']} 중 하나여야 한다 — {value!r}")
        return
    lo, hi = tag["measure"]
    if not isinstance(value, (int, float)) or not lo <= value <= hi:
        raise ValueError(f"{where}: {tag['tag']} 값은 측정 범위 {lo}~{hi} 안의 숫자여야 한다 — {value!r}")


def build(spec: dict, baseline: dict, tags: dict[str, dict]) -> Scenario:
    sid = spec["id"]
    n = spec["minutes"]
    base = {**baseline, **spec.get("baseline", {})}
    if set(base) != set(tags):
        raise ValueError(f"{sid}: 평상시 값의 태그가 tags.yaml과 다르다 — 빠짐 {sorted(set(tags) - set(base))}, 모름 {sorted(set(base) - set(tags))}")
    for name, value in base.items():
        _check_value(tags[name], value, f"{sid} 평상시 값")

    values = {name: [v] * (n + 1) for name, v in base.items()}
    stuck = {name: [False] * (n + 1) for name in base}
    for ch in spec.get("changes", []):
        name = ch["tag"]
        if name not in tags:
            raise ValueError(f"{sid}: 모르는 태그 {name}")
        _check_value(tags[name], ch["value"], f"{sid} 바뀜")
        row = values[name]
        if "at" in ch:
            if not 0 <= ch["at"] <= n:
                raise ValueError(f"{sid}: {name} 계단 시점 {ch['at']}분이 0~{n} 밖이다")
            for m in range(ch["at"], n + 1):
                row[m] = ch["value"]
                stuck[name][m] = True
        else:
            a, b = ch["from"], ch["to"]
            if not 0 <= a < b <= n:
                raise ValueError(f"{sid}: {name} 경사 구간 {a}~{b}분이 잘못됐다")
            start = row[a]
            for m in range(a, n + 1):
                row[m] = ch["value"] if m >= b else start + (ch["value"] - start) * (m - a) / (b - a)

    series: dict[str, list] = {}
    for name, row in values.items():
        tag = tags[name]
        if "states" in tag:
            series[name] = row
            continue
        rng = random.Random(zlib.crc32(f"{sid}:{name}".encode()))
        lo, hi = tag["measure"]
        out = []
        for m, v in enumerate(row):
            if not stuck[name][m]:
                v += v * NOISE * rng.uniform(-1, 1)
            out.append(round(min(max(v, lo), hi), DECIMALS.get(tag["unit"], 2)))
        series[name] = out

    return Scenario(sid, spec["title"], spec["summary"], datetime.fromisoformat(spec["now"]), n, series)


def load(sid: str, folder: Path = SCENARIOS) -> Scenario:
    baseline = json.loads((folder / "baseline.json").read_text(encoding="utf-8"))["values"]
    spec = json.loads((folder / f"{sid}.json").read_text(encoding="utf-8"))
    return build(spec, baseline, load_tags())
