# MCP Gateway Performance Tests

FORGE project for running performance tests against the MCP Gateway.

## Architecture

```
projects/
├── agentic_tools/                     # Shared toolbox
│   ├── locust/
│   │   ├── locust_users/             # User classes (one per file)
│   │   ├── locust_runtime/           # Pod-mounted scripts (entrypoint, shapes, hooks)
│   │   ├── templates/                # K8s job template (locust_job.yaml)
│   │   ├── helpers/
│   │   │   ├── parse_results.py      # Parse Locust CSV into RunMetrics
│   │   │   └── summary.py           # Save metrics.json + parameters.json
│   │   └── toolbox/
│   │       └── run_distributed/      # Deploy Locust jobs, wait, collect CSV
│   ├── mcp/
│   │   ├── toolbox/
│   │   │   ├── deploy_mock_servers/  # Deploy 1..N mock MCP servers
│   │   │   └── deploy_tokenized_mcp_server/
│   │   └── clients/mcp_client.py     # Generic MCP Streamable HTTP client
│   └── utils/
│       └── token_text.py             # Shared tokenizer utilities
│
├── mcp_gateway/                       # This project
│   ├── orchestration/
│   │   ├── ci.py                     # CLI phases (prepare, test, pre-cleanup, post-cleanup)
│   │   ├── runtime_config.py         # Config accessors + build_locust_config()
│   │   ├── prepare_phase.py          # Platform install + mock server + infra
│   │   ├── test_phase.py             # Test matrix execution
│   │   ├── cleanup_phase.py          # Resource cleanup (pre/post)
│   │   ├── config.d/                 # Layered config
│   │   └── presets.d/                # Named presets
│   └── toolbox/
│       ├── platform_helpers.py       # Shared install/cleanup utilities
│       ├── install_platform/         # Full MCP Gateway platform stack
│       ├── cleanup_platform/         # Reverse of install_platform
│       ├── apply_infrastructure/     # HTTPRoute + MCPServerRegistration
│       └── cleanup_test_resources/   # Test resource cleanup
```

## Dependencies from `agentic_tools`

| Import | Path | Used for |
|--------|------|----------|
| Distributed Locust runner | `agentic_tools/locust/toolbox/run_distributed` | Deploying Locust master+worker K8s Jobs, waiting for completion, collecting CSV results |
| Results parser | `agentic_tools/locust/helpers/parse_results` | Parsing Locust CSV output into structured `RunMetrics` |
| Summary helpers | `agentic_tools/locust/helpers/summary` | Saving metrics.json + parameters.json for caliper multi-run export |
| Locust K8s template | `agentic_tools/locust/templates/locust_job.yaml` | Base Job/Service YAML for distributed Locust deployments |
| Locust runtime | `agentic_tools/locust/locust_runtime/` | Shared entry point, warmup hook, load shapes |
| MCP session user | `agentic_tools/locust/locust_users/mcp_session_user.py` | Locust user class for MCP protocol load (stateful) |
| MCP 2026 user | `agentic_tools/locust/locust_users/mcp_2026_user.py` | Locust user class for MCP 2026-07-28 (stateless) |
| Mock MCP server deployer | `agentic_tools/mcp/toolbox/deploy_mock_servers` | Deploying/restarting/cleaning up 1..N mock MCP server pods |
| MCP HTTP client | `agentic_tools/mcp/clients/mcp_client.py` | Streamable HTTP client implementing MCP protocol |

## Presets

| Preset | Description |
|--------|-------------|
| `smoke` | Quick 30s validation with 4 users against server directly |
| `baseline` | 1 server, sweep concurrency [16..256] × [server, gateway] |
| `scale-out` | Sweep servers [1..100] × concurrency [50, 200, 500] through gateway |
| `demo` | 200 servers, 500 users through gateway |

## Usage

```bash
# Run demo with MCP Gateway version 0.7.0
export MCP_GATEWAY_VERSION=0.7.0 
export FORGE_PRESET=demo 
python -m projects.mcp_gateway.orchestration.ci prepare
python -m projects.mcp_gateway.orchestration.ci test
python -m projects.mcp_gateway.orchestration.ci post-cleanup

# Quick smoke test with an older version
export MCP_GATEWAY_VERSION=0.5.1
export FORGE_PRESET=smoke
python -m projects.mcp_gateway.orchestration.ci prepare
python -m projects.mcp_gateway.orchestration.ci test
python -m projects.mcp_gateway.orchestration.ci pre-cleanup

# Scale-out (latest version, no MCP_GATEWAY_VERSION needed)
export FORGE_PRESET=scale-out
python -m projects.mcp_gateway.orchestration.ci prepare
python -m projects.mcp_gateway.orchestration.ci test
python -m projects.mcp_gateway.orchestration.ci post-cleanup
```
