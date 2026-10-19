"""MCP 서버 ① ams — 가상 알람 감시 시스템을 읽기 전용 도구로 노출한다.

    uv run python -m mcp_servers.ams_server --scenario S2      # stdio로 띄우기
    npx @modelcontextprotocol/inspector uv run python -m mcp_servers.ams_server --scenario S2   # MCP Inspector

시나리오는 서버를 띄울 때 정한다. 바꾸려면 서버를 다시 띄운다 — 모델이 부를 수 있는 도구 중에
상태를 바꾸는 것은 하나도 없게 하려는 것이다(설계 문서 2장 "제어 명령 없음").
에이전트는 --scenario로 넘긴다. MCP SDK는 stdio 자식 프로세스에 정해진 환경 변수만 넘겨서
AMS_SCENARIO는 직접 띄울 때만 쓰인다.

도구 결과는 모두 객체 하나({"alarms": [...]} 등)로 돌려준다. 목록을 그대로 돌려주면 SDK가 항목마다
글 조각을 따로 만들고 structured_content도 목록일 때만 감싸서, 에이전트가 읽는 방식이 도구마다 달라진다.
"""

import argparse
import inspect
import os
from typing import Annotated, Any

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import Field

from ams.scenario import list_ids, load
from ams.system import AlarmMonitoringSystem, UnknownSystemError, UnknownTagError

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)

INSTRUCTIONS = """선박 기관실의 알람 감시 시스템(AMS)이다. 교육용 가상 선박이다.
모든 도구는 읽기만 한다. 값을 바꾸거나 장비를 조작하는 도구는 없다.
알람을 살필 때는 list_active_alarms로 무엇이 울리는지 보고, read_sensors와 get_trend로 관련 값과 변화를 확인한다.
태그 이름을 모르면 list_tags로 찾는다."""


def create_server(scenario_id: str) -> MCPServer:
    ams = AlarmMonitoringSystem(load(scenario_id))
    server = MCPServer("ams", instructions=INSTRUCTIONS)

    def tool(fn):
        # Python 3.12는 docstring의 들여쓰기를 지우지 않아 그대로 도구 설명에 들어간다.
        return server.tool(annotations=READ_ONLY, description=inspect.cleandoc(fn.__doc__))(fn)

    def _known(call):
        try:
            return call()
        except (UnknownTagError, UnknownSystemError) as e:  # 모델이 읽고 고칠 수 있게 메시지를 그대로 돌려준다
            raise ToolError(str(e)) from e

    @tool
    def list_active_alarms() -> dict[str, Any]:
        """지금 울리고 있는 알람 목록(alarms). 등급(정지 > 감속 > 경보)이 높은 것부터, 같은 등급은 먼저 뜬 것부터.
        항목마다 태그, 이름, 등급, 현재값, 단위, 설정값(예: "≤ 1.7"), 발생 시각, 몇 분 전에 떴는지.
        기록(30분) 내내 이어진 알람은 발생 시각이 null이고 since_note에 그 사실을 적는다."""
        return {"alarms": ams.active_alarms()}

    @tool
    def list_tags(
        system: Annotated[str, Field(description="계통 코드 하나: ME, LO, JCW, FO, GE 중 하나(ME=주기관, LO=윤활유, JCW=재킷 냉각수, FO=연료유, GE=발전기). 빈 문자열이면 전부.")] = "",
    ) -> dict[str, Any]:
        """감시 태그 목록(tags). 태그 이름, 이름, 계통, 단위, 정상 범위, 측정 범위(상태 태그는 가능한 상태)."""
        return {"tags": _known(lambda: ams.list_tags(system or None))}

    @tool
    def read_sensors(
        tags: Annotated[list[str], Field(description="읽을 태그 이름 1개 이상의 배열. 예: [\"ME_LO_PRESS_IN\", \"LO_FILTER_DP\"]", min_length=1)],
    ) -> dict[str, Any]:
        """태그들의 현재값(sensors). 숫자 태그는 상태(정상 / 정상 범위 밖 / 경보 / 감속 / 정지)와 단위·정상 범위·측정 범위도 준다.
        펌프 같은 상태 태그는 값(운전 / 대기 / 정지)만 준다 — 이 "정지"는 펌프가 멈췄다는 뜻이지 알람 등급이 아니다.
        모르는 태그가 섞여 있으면 아는 태그는 읽고, 모르는 태그는 unknown에 비슷한 태그(similar)와 함께 준다."""
        rows, unknown = ams.read_known(tags)
        if not rows:  # 하나도 못 읽었으면 오류로 — 모델이 비슷한 태그나 list_tags로 고치게
            listed = "; ".join(f"{u['tag']}(비슷한 태그: {', '.join(u['similar']) or '없음'})" for u in unknown)
            raise ToolError(f"모르는 태그만 있다: {listed}. list_tags로 태그 목록을 볼 수 있다.")
        return {"sensors": rows} | ({"unknown": unknown} if unknown else {})

    @tool
    def get_trend(
        tag: Annotated[str, Field(description="태그 이름 하나. 예: \"ME_JCW_TEMP_OUT\"")],
        minutes: Annotated[int, Field(description="지난 몇 분을 볼지, 1~30", ge=1, le=30)] = 30,
        points: Annotated[int, Field(description="요약할 점 개수, 2~31", ge=2, le=31)] = 7,
    ) -> dict[str, Any]:
        """한 태그의 지난 변화 요약. 점 몇 개(samples)와 처음·마지막·변화량·최소·최대.
        max_change_per_minute는 1분 사이 가장 큰 변화(크기 기준, +는 오름 −는 내림), max_change_minutes_ago는 그게 몇 분 전이었는지.
        1분 사이 변화가 물리적으로 어려울 만큼 크면 센서 이상을 의심할 근거가 된다."""
        return _known(lambda: ams.trend(tag, minutes, points))

    return server


def main() -> None:
    # 설명은 cp949 콘솔에서도 찍히는 글자만 쓴다(--help 출력을 파이프로 받을 때 깨지지 않게).
    ap = argparse.ArgumentParser(description="MCP 서버 ams: 가상 알람 감시 시스템(읽기 전용)")
    ap.add_argument("--scenario", default=os.environ.get("AMS_SCENARIO", "S1"), help=f"{', '.join(list_ids())} 중 하나")
    args = ap.parse_args()
    scenario = args.scenario.strip().upper()
    if scenario not in list_ids():  # 환경 변수로 들어온 기본값은 argparse가 검사하지 않는다
        ap.error(f"모르는 시나리오 {args.scenario!r} — {', '.join(list_ids())} 중 하나")
    create_server(scenario).run()  # stdio


if __name__ == "__main__":
    main()
