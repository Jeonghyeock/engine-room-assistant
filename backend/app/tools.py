"""MCP 도구 상자 — ams·docs 서버를 stdio 자식 프로세스로 띄워 한 묶음의 도구로 쓴다.

    async with ToolBox("S2") as box:
        box.ollama_tools()                      # Ollama /api/chat의 tools
        await box.call("read_sensors", {...})   # ToolResult(ok, data, error)

에이전트는 이 상자만 안다. 도구가 어느 서버에 있는지, MCP 결과가 어떤 모양인지는 여기서 감춘다.
"""

import os
import sys
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcp import Client, MCPError, StdioServerParameters

from ams.scenario import list_ids

BACKEND = Path(__file__).resolve().parents[1]

# SDK는 자식 프로세스에 정해진 환경 변수만 넘긴다(그 위에 env를 얹는다). 검색 서버가 쓰는 설정은 따로 넘겨 준다.
PASS_ENV = ("OLLAMA_URL", "EMBED_MODEL")

# 도구 한 번에 기다리는 시간. 검색은 질의 임베딩(CPU)까지 해도 1초 안팎이다 — Ollama가 멈추면 오래 막히지 않게.
CALL_TIMEOUT = 60


@dataclass
class ToolResult:
    ok: bool
    data: dict[str, Any] | None = None  # 서버가 돌려준 객체(structured_content)
    error: str | None = None  # 모델에게 그대로 보여 줄 오류 문장


def _server(module: str, *args: str) -> StdioServerParameters:
    env = {k: os.environ[k] for k in PASS_ENV if k in os.environ}
    return StdioServerParameters(command=sys.executable, args=["-m", module, *args], cwd=str(BACKEND), env=env)


def _clean_schema(schema: Any) -> Any:
    """pydantic이 붙이는 title을 지운다. 도구 정의는 매 호출 문맥에 들어가므로 쓸데없는 글자를 줄인다.
    properties 아래의 키는 인자 이름이라 지우지 않는다(이름이 title인 인자가 사라지지 않게)."""
    if isinstance(schema, dict):
        return {
            k: ({name: _clean_schema(sub) for name, sub in v.items()} if k == "properties" and isinstance(v, dict) else _clean_schema(v))
            for k, v in schema.items()
            if k != "title"
        }
    if isinstance(schema, list):
        return [_clean_schema(v) for v in schema]
    return schema


class ToolBox:
    def __init__(self, scenario: str):
        # 서버를 띄우기 전에 확인한다 — 자식 프로세스가 실패하면 SDK가 이유가 안 보이는 예외 묶음으로 감싼다.
        if scenario not in list_ids():
            raise ValueError(f"모르는 시나리오 {scenario!r} — {', '.join(list_ids())} 중 하나")
        self.servers = {
            "ams": _server("mcp_servers.ams_server", "--scenario", scenario),
            "docs": _server("mcp_servers.docs_server"),
        }
        self._stack = AsyncExitStack()
        self._owner: dict[str, Client] = {}
        self._tools: list[Any] = []

    async def __aenter__(self) -> "ToolBox":
        try:
            for server_name, params in self.servers.items():
                client = await self._stack.enter_async_context(Client(params))
                for tool in (await client.list_tools()).tools:
                    if tool.name in self._owner:
                        raise RuntimeError(f"도구 이름 {tool.name!r}이 두 서버에 있다 — {server_name}")
                    self._owner[tool.name] = client
                    self._tools.append(tool)
        except BaseException:
            await self._stack.aclose()
            raise
        return self

    async def __aexit__(self, *exc) -> None:
        await self._stack.aclose()

    @property
    def names(self) -> list[str]:
        return [t.name for t in self._tools]

    def ollama_tools(self) -> list[dict]:
        return [
            {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": _clean_schema(t.input_schema)}}
            for t in self._tools
        ]

    async def call(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        client = self._owner.get(name)
        if client is None:
            return ToolResult(False, error=f"모르는 도구 {name!r}. 쓸 수 있는 도구: {', '.join(self.names)}.")
        try:
            res = await client.call_tool(name, arguments, read_timeout_seconds=CALL_TIMEOUT)
        except (MCPError, OSError, TimeoutError) as e:  # 서버가 죽거나 멈춤 — 프로그램 오류는 숨기지 않고 그대로 올린다
            return ToolResult(False, error=f"도구 {name} 서버와 통신하지 못했다: {e!r}")
        text = " ".join(getattr(c, "text", "") for c in res.content).strip()
        if res.is_error:
            return ToolResult(False, error=text or f"도구 {name} 오류")
        # 반환 타입을 적지 않은 도구는 structured_content가 비고 글에만 결과가 있다
        return ToolResult(True, data=res.structured_content if res.structured_content is not None else {"text": text})
