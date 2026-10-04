# Data MCP server interface

What the gateway sends to the data MCP server and what it expects back. Any server that follows this can replace `data-mcp`. In this repo the implementation is `policy_engine/mcp_http_server.py`, serving the tools in `examples/bank_demo.py`.

## Transport

- Streamable HTTP. The gateway reads the URL from `[data_mcp] url` in `config/gateway.toml` (`http://data-mcp:8001/mcp` in Docker Compose).
- The gateway uses the official `mcp` SDK client. It opens one session per chat request, lists the tools, makes the calls and closes the session.
- In Docker, bind to `0.0.0.0`. The SDK's DNS-rebinding protection rejects the `data-mcp` Host header when the server is bound to localhost.

## Tools

The chat model sees every listed tool and chooses which to call. Any listed tool can be called by a prompt-injected model, so:

- List data tools only. Rule management, policy evaluation and session snapshots belong on an admin path. A model that could call `set_policy_rule_enabled` could switch the rules off. In this repo, rules are edited through the gateway's `/admin/policy/rules`.
- Run the policy check inside each tool, before its query.
- Write a one-line description per tool. It is the only thing the model reads when it picks a tool.

## Caller identity

Every `tools/call` carries these keys in `params._meta`:

| Key | Value |
|---|---|
| `ai-control-gateway/user_id` | Stable Open WebUI user id. Key per-user knowledge on it. |
| `ai-control-gateway/session_id` | Open WebUI chat id, or the gateway's request id when there is none |
| `traceparent` | W3C trace context, for correlating server logs with the gateway's trace |

- Never take the user from tool arguments. The model fills those in and could be talked into impersonating someone.
- If `user_id` is missing, deny the call. Do not fall back to a shared bucket, which would pool every user's knowledge.

## Results

**Allowed:** a normal result whose text content is the data, as JSON.

**Denied by policy:** `isError: true`, a short message that is safe to show the user, and the steps that ran in `structuredContent.checkpoint_steps` (or `_meta`), in order:

```python
CallToolResult(
    content=[TextContent(type="text", text="Denied by data policy (aml_contact): ...")],
    is_error=True,
    structured_content={"checkpoint_steps": [
        {"middleware": "tool_permission", "outcome": "passed", "reason": "tool get_customer_contact is permitted for role data_analyst"},
        {"middleware": "datalog_policy", "outcome": "denied", "reason": "aml_contact"},
    ]},
)
```

The gateway sorts each result into one of three outcomes:

| Result | Outcome | Effect |
|---|---|---|
| no `isError` | passed | data goes to the model |
| `isError` with a `denied` step | denied | the model is told not to work around it; the dashboard counts a checkpoint-2 denial and the trace ends at the denying step |
| `isError` without a `denied` step | failed | unknown tool, bad arguments or missing record; the model may retry |

Mark only real policy denials as `denied`. A missing record should fail like any other error, so a reply never reveals whether the record exists.
