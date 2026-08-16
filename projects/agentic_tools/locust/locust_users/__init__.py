"""
Shared Locust user classes for load testing.

Reusable across any FORGE project that benchmarks HTTP/MCP APIs.

User classes (one per file):
- ResponsesSimpleUser        (responses_simple_user.py)
- ResponsesMCPUser           (responses_mcp_user.py)
- ResponsesMCPBenchmarkUser  (responses_mcp_benchmark_user.py)
- ChatCompletionsUser        (chat_completions_user.py)
- MCPSessionUser             (mcp_session_user.py)
- MCP2026User                (mcp_2026_user.py)

Shared utilities:
- _common.py                 Prompt loading, round-robin counter

Aggregator (for backward-compat pod mounting):
- responses_users.py         Re-exports all HTTP-based user classes
"""
