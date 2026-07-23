# MCP test coverage

**Applies to:** local and CI test runs that cover the MCP server

`tests/test_mcp_server.py` uses `pytest.importorskip('mcp')`, so without the
dependency the entire file silently skips and the suite still reports green.
Keeping `mcp` in the dev group is deliberate: it makes default `uv run pytest`
cover the MCP surface. This gap hid a `NameError` in
`test_get_protocol_uses_configured_agent_role` and a stale schema digest across
several commits. `ISSUEKIT_REQUIRE_MCP=1` turns a missing dependency into a
hard failure; CI uses it.
