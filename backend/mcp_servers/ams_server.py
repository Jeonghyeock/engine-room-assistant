"""MCP 서버 ① ams — 가상 알람 감시 시스템을 읽기 전용 도구로 노출한다.

    uv run python -m mcp_servers.ams_server --scenario S2      # stdio로 띄우기(시나리오는 AMS_SCENARIO로도)
    npx @modelcontextprotocol/inspector uv run python -m mcp_servers.ams_server --scenario S2   # MCP Inspector

시나리오는 서버를 띄울 때 정한다. 바꾸려면 서버를 다시 띄운다 — 모델이 부를 수 있는 도구 중에
상태를 바꾸는 것은 하나도 없게 하려는 것이다(설계 문서 2장 "제어 명령 없음").
"""

import argparse
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

    def _known(call):
        try:
            return call()
        except (UnknownTagError, UnknownSystemError) as e:  # 모델이 읽고 고칠 수 있게 메시지를 그대로 돌려준다
            raise ToolError(str(e)) from e

    @server.tool(annotations=READ_ONLY)
    def list_active_alarms() -> list[dict[str, Any]]:
        """지금 울리고 있는 알람 목록. 등급(정지 > 감속 > 경보)이 높은 것부터, 같은 등급은 먼저 뜬 것부터.
        항목마다 태그, 이름, 등급, 현재값, 단위, 설정값(예: "≤ 1.7"), 발생 시각, 몇 분 전에 떴는지.
        기록(30분) 내내 이어진 알람은 발생 시각이 null이고 since_note에 그 사실을 적는다."""
        return ams.active_alarms()

    @server.tool(annotations=READ_ONLY)
    def list_tags(
        system: Annotated[str | None, Field(description="계통으로 거르기: ME 주기관, LO 윤활유, JCW 재킷 냉각수, FO 연료유, GE 발전기. 비우면 전부.")] = None,
    ) -> list[dict[str, Any]]:
        """감시 태그 목록. 태그 이름, 이름, 계통, 단위, 정상 범위, 측정 범위(상태 태그는 가능한 상태)."""
        return _known(lambda: ams.list_tags(system))

    @server.tool(annotations=READ_ONLY)
    def read_sensors(
        tags: Annotated[list[str], Field(description="읽을 태그 이름들. 예: [\"ME_LO_PRESS_IN\", \"LO_FILTER_DP\"]", min_length=1)],
    ) -> list[dict[str, Any]]:
        """태그들의 현재값. 숫자 태그는 상태(정상 / 정상 범위 밖 / 경보 / 감속 / 정지)와 단위·정상 범위·측정 범위도 준다.
        펌프 같은 상태 태그는 값(운전 / 대기 / 정지)만 준다 — 이 "정지"는 펌프가 멈췄다는 뜻이지 알람 등급이 아니다."""
        return _known(lambda: ams.read_sensors(tags))

    @server.tool(annotations=READ_ONLY)
    def get_trend(
        tag: Annotated[str, Field(description="태그 이름. 예: \"ME_JCW_TEMP_OUT\"")],
        minutes: Annotated[int, Field(description="지난 몇 분을 볼지(최대 30)", ge=1, le=30)] = 30,
        points: Annotated[int, Field(description="요약할 점 개수", ge=2, le=31)] = 7,
    ) -> dict[str, Any]:
        """한 태그의 지난 변화 요약. 점 몇 개와 처음·마지막·변화량·최소·최대.
        max_change_per_minute는 1분 사이 가장 큰 변화(크기 기준, +는 오름 −는 내림), max_change_minutes_ago는 그게 몇 분 전이었는지.
        1분 사이 변화가 물리적으로 어려울 만큼 크면 센서 이상을 의심할 근거가 된다."""
        return _known(lambda: ams.trend(tag, minutes, points))

    return server


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scenario", default=os.environ.get("AMS_SCENARIO", "S1"), choices=list_ids())
    create_server(ap.parse_args().scenario).run()  # stdio


if __name__ == "__main__":
    main()
