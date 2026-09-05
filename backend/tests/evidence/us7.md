# US7 GREEN evidence

Observed locally on 2026-09-05 from checkpoint `a384486`. All providers were
offline fixtures; no live credential or provider endpoint was used.

## Agent authorization, MCP parity, revocation, and workflow

```text
uv run --directory backend pytest -q \
  tests/contract/test_agent_authorization_api.py \
  tests/contract/test_mcp_protocol.py \
  tests/contract/test_http_mcp_parity.py \
  tests/isolation/test_agent_revocation.py \
  tests/integration/test_agent_review_workflow.py
# 23 passed in 28.68s
```

This proves interactive one-time grant/revoke, digest-only bearer storage,
stateless MCP 2026-07-28 transport, exact 12-tool REST/MCP parity, combined
Membership and AgentAuthorization revalidation for REST/MCP/jobs, the scoped
agent review workflow, harmless publication requests, and separate interactive
human publication. No MCP tool performs direct publication.

## Focused implementation regression

```text
uv run --directory backend pytest -q \
  tests/unit/test_mcp_server.py \
  tests/unit/test_mcp_tool_registry.py \
  tests/unit/test_mcp_read_tools.py \
  tests/unit/test_mcp_review_tools.py \
  tests/unit/test_mcp_ai_publication_tools.py \
  tests/unit/test_review_iterations_service.py \
  tests/state/test_submission_review_states.py
# 35 passed in 22.16s

uv run --directory backend ruff check src \
  tests/contract/test_agent_authorization_api.py \
  tests/integration/test_agent_review_workflow.py
# All checks passed.

uv run --directory backend mypy src
# Success: no issues found in 131 source files.
```

The bearer, AI credential binding, signed artifact grant, organization, and
actor context are server-owned. Grant replay does not reveal a token a second
time. Revocation before commit rolls back protected work and prevents queued or
claimed agent jobs from committing afterward.
