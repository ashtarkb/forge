"""
Locust user for MCP 2026-07-28 (stateless) through mcp-gw or direct.

Does not initialize, discover, or tools/list. The first RPC is tools/call.

Reusable across any FORGE project that benchmarks MCP servers or gateways.
Copied into Locust pods as a sibling of locustfile_main.py (/scripts/).

Import (host / tests)::

    from projects.agentic_tools.locust.locust_users.mcp_2026_user import MCP2026User

Environment variables (set by the Locust job template):
    TOOL_PREFIX:        prefix for tool names ("mock_" via gateway, "" direct)
    HOST_HEADER:        Host header for gateway routing (empty = none)
    CALLS_PER_SESSION:  tool calls before rebinding host (0 = never; no handshake)
    NUM_SERVERS:        registered servers for scale-out (0 = single server)
"""

from __future__ import annotations

import json
import os
import random
import time
from dataclasses import dataclass, field

import requests
from locust import User, between, events, task

PROTOCOL_2026 = "2026-07-28"

TOOL_PREFIX = os.environ.get("TOOL_PREFIX", "")
HOST_HEADER = os.environ.get("HOST_HEADER", "")
CALLS_PER_SESSION = int(os.environ.get("CALLS_PER_SESSION", "0"))
NUM_SERVERS = int(os.environ.get("NUM_SERVERS", "0"))

TOOL_SEQUENCE = [
    {"name": "alpha", "args": {"input": "test"}},
    {"name": "bravo", "args": {"input": "test"}},
    {"name": "charlie", "args": {"input": "test"}},
    {"name": "delta", "args": {"input": "test"}},
    {"name": "echo", "args": {"input": "test"}},
    {"name": "foxtrot", "args": {"input": "test"}},
    {"name": "golf", "args": {"input": "test"}},
    {"name": "hotel", "args": {"input": "test"}},
    {"name": "india", "args": {"input": "test"}},
    {"name": "juliet", "args": {"input": "test"}},
]


@dataclass
class MCPResponse:
    success: bool
    response_time_ms: float
    status_code: int
    data: dict | None = None
    error: str | None = None


@dataclass
class MCP2026Client:
    """Stateless MCP client. First RPC may be tools/call."""

    base_url: str
    host_header: str | None = None
    request_id: int = field(default=0, init=False)
    timeout: float = 30.0

    def _next_id(self) -> int:
        self.request_id += 1
        return self.request_id

    def _headers(self, *, method: str, name: str | None = None) -> dict:
        h = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": PROTOCOL_2026,
            "Mcp-Method": method,
        }
        if name:
            h["Mcp-Name"] = name
        if self.host_header:
            h["Host"] = self.host_header
        return h

    def _with_meta(self, params: dict | None) -> dict:
        out = dict(params) if params else {}
        meta = dict(out["_meta"]) if isinstance(out.get("_meta"), dict) else {}
        meta.setdefault("io.modelcontextprotocol/protocolVersion", PROTOCOL_2026)
        meta.setdefault(
            "io.modelcontextprotocol/clientInfo",
            {"name": "forge-mcp-2026", "version": "1.0.0"},
        )
        meta.setdefault("io.modelcontextprotocol/clientCapabilities", {})
        out["_meta"] = meta
        return out

    def _parse_body(self, response, elapsed_ms: float) -> MCPResponse:
        if response.status_code != 200:
            return MCPResponse(
                success=False,
                response_time_ms=elapsed_ms,
                status_code=response.status_code,
                error=f"HTTP {response.status_code}: {response.text[:200]}",
            )

        try:
            ct = response.headers.get("Content-Type", "")
            text = response.text

            if "text/event-stream" in ct or text.lstrip().startswith("event:"):
                data = None
                for line in text.split("\n"):
                    line = line.strip()
                    if line.startswith("data:"):
                        data = json.loads(line[5:].strip())
                        break
                if data is None:
                    return MCPResponse(
                        success=False,
                        response_time_ms=elapsed_ms,
                        status_code=response.status_code,
                        error="Empty SSE response",
                    )
            else:
                data = response.json()

            if not isinstance(data, dict):
                return MCPResponse(
                    success=False,
                    response_time_ms=elapsed_ms,
                    status_code=response.status_code,
                    error=f"Expected JSON object, got {type(data).__name__}",
                )

            if "error" in data:
                msg = data["error"]
                if isinstance(msg, dict):
                    msg = msg.get("message", str(msg))
                return MCPResponse(
                    success=False,
                    response_time_ms=elapsed_ms,
                    status_code=response.status_code,
                    error=str(msg),
                )

            return MCPResponse(
                success=True,
                response_time_ms=elapsed_ms,
                status_code=response.status_code,
                data=data.get("result"),
            )
        except json.JSONDecodeError as e:
            return MCPResponse(
                success=False,
                response_time_ms=elapsed_ms,
                status_code=response.status_code,
                error=f"Invalid JSON: {e}",
            )

    def _send(
        self, method: str, params: dict | None = None, *, name: str | None = None
    ) -> MCPResponse:
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "id": self._next_id(),
            "params": self._with_meta(params),
        }
        start = time.perf_counter()
        try:
            resp = requests.post(
                f"{self.base_url}/mcp",
                json=payload,
                headers=self._headers(method=method, name=name),
                timeout=self.timeout,
            )
            elapsed = (time.perf_counter() - start) * 1000
            return self._parse_body(resp, elapsed)
        except requests.exceptions.RequestException as e:
            elapsed = (time.perf_counter() - start) * 1000
            return MCPResponse(False, elapsed, 0, error=str(e))

    def call_tool(self, name: str, arguments: dict | None = None) -> MCPResponse:
        """POST tools/call. This is a valid first request on 2026-07-28."""
        return self._send(
            "tools/call",
            {"name": name, "arguments": arguments or {}},
            name=name,
        )


def _report(name: str, resp: MCPResponse):
    if resp.success:
        events.request.fire(
            request_type="MCP",
            name=name,
            response_time=resp.response_time_ms,
            response_length=len(str(resp.data)) if resp.data else 0,
            exception=None,
            context={},
        )
    else:
        events.request.fire(
            request_type="MCP",
            name=f"FAIL:{name}",
            response_time=resp.response_time_ms,
            response_length=0,
            exception=Exception(resp.error),
            context={},
        )


class MCP2026User(User):
    """
    Stateless MCP user: round-robin tools/call with no protocol session.

    Time to first tool response is the first tools/call (Locust name ``ttftr``),
    not initialize or server/discover.
    """

    abstract = True
    wait_time = between(0.1, 0.5)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.mcp: MCP2026Client | None = None
        self.calls_done = 0
        self.seq_index = 0
        self._current_server_idx = 0
        self._emitted_ttftr = False

    def _bind_client(self) -> None:
        """Point at a backend. No RPC."""
        host_header = HOST_HEADER or None
        if NUM_SERVERS > 0:
            server_idx = random.randint(1, NUM_SERVERS)
            host_header = f"server{server_idx}.mcp.local"
            self._current_server_idx = server_idx
        else:
            self._current_server_idx = 0

        self.mcp = MCP2026Client(
            base_url=self.host,
            host_header=host_header,
        )
        self.calls_done = 0

    @task
    def do_tool_call(self):
        if self.mcp is None:
            self._bind_client()

        if CALLS_PER_SESSION > 0 and self.calls_done >= CALLS_PER_SESSION:
            self._bind_client()

        entry = TOOL_SEQUENCE[self.seq_index % len(TOOL_SEQUENCE)]
        self.seq_index += 1

        if NUM_SERVERS > 0 and self._current_server_idx > 0:
            prefix = f"server{self._current_server_idx}_"
        else:
            prefix = TOOL_PREFIX
        tool_name = f"{prefix}{entry['name']}"

        r = self.mcp.call_tool(tool_name, dict(entry["args"]))
        if not self._emitted_ttftr:
            _report("ttftr", r)
            self._emitted_ttftr = True
        else:
            _report(f"call:{entry['name']}", r)
        self.calls_done += 1
