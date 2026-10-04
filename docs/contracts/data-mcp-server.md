# Data MCP server contract

What the gateway expects from the data MCP server (checkpoint 2, the black box), and what it sends. Anything not listed here is up to the server.

Status: proposed by the gateway side on 2026-10-04, against the policy middleware in commit `50e32de`.

## How the gateway uses the server

```
Open WebUI ─▶ gateway ─▶ checkpoint 1 (Jev) ─▶ chat model ──tool call──▶ data MCP server
                                                   ▲                      ├─ tool_allowlist, datalog_policy, … (checkpoint 2)
                                                   └──── tool result ─────┴─ SQL ─▶ DB
```

- The chat model sees every tool the server lists and decides which to call. **Every listed tool is reachable by a possibly prompt-injected model.**
- The gateway opens one MCP session per chat request, lists the tools, makes the calls, and closes the session.
- The stand-in server `deploy/stub_mcp/server.py` implements this contract with fake data. The real server replaces it by changing one image in `docker-compose.yml`.

## 1. Transport

- **Streamable HTTP** at `http://data-mcp:8001/mcp` inside Docker Compose. The URL is set in `[data_mcp] url` in `config/gateway.toml`.
- Build the server with the official `mcp` Python SDK (`mcp.server.mcpserver.MCPServer`, version ≥ 2.3), not a hand-written JSON-RPC loop. The SDK handles protocol negotiation, which the gateway's client depends on.
- Bind to `0.0.0.0`. The SDK's DNS-rebinding protection rejects the `data-mcp` Host header when the server is bound to localhost.

## 2. Tools: data only

List **only data-returning tools**. These names are what the Datalog fact inference already keys on:

| Tool | Arguments | Facts it adds |
|---|---|---|
| `get_transaction_anomalies` | none | `transaction_anomaly`, `anonymized_context` |
| `get_blocked_accounts` | none | `blocked_account`, `anonymized_context` |
| `get_aml_case_summary` | none (or `case_id`) | `aml_flag` |
| `get_customer_contact` | `customer_id: str` | `contact_data` |
| `get_customer_workplace` | `customer_id: str` | `workplace_data` |

- Give every tool a clear one-line description. It is the only thing the model reads to choose a tool.
- **Do not list** `evaluate_request`, `snapshot_session`, `list_policy_rules`, `set_policy_rule_enabled`, `upsert_policy_rule` or `reload_policy_rules` on this server.
  - A prompt-injected model could call `set_policy_rule_enabled` and switch the inference rules off.
  - The policy check belongs *inside* each data tool, before it runs its SQL.
  - Rule management belongs on an admin path. The gateway will expose it through its dashboard admin API, the same place guard config lives. Keep `PolicyConfigStore` and `PolicyMiddleware` importable for that.

## 3. Caller identity: read from `_meta`

Every `tools/call` from the gateway carries these keys in `params._meta`:

| Key | Value |
|---|---|
| `ai-control-gateway/user_id` | Stable user id from Open WebUI. Key the knowledge store on this. |
| `ai-control-gateway/session_id` | Open WebUI chat id, or the gateway request id when there is none |
| `traceparent` | W3C trace context, e.g. `00-<32 hex>-<16 hex>-01`. Log it if you want to correlate with the gateway's trace. |

- In an SDK tool, read them with `ctx.request_context.meta` (add a `ctx: Context` parameter; it is not shown to the model).
- **Never take the user from tool arguments.** The model fills arguments in, so it could be talked into impersonating another user.
- If `user_id` is missing, deny rather than falling back to a shared `unknown_user` bucket. A shared bucket pools everyone's knowledge, so one user's AML query would block another user's contact lookup.
- Map onto `MCPRequest` like this:
  - `request_id`: any unique id, e.g. the traceparent's span id.
  - `session_id`, `user_id`: from `_meta`.
  - `tool_name`: the tool being called.
  - `arguments`: the tool arguments.
  - `request_text`: the gateway does not send the user's prompt; use the tool name or an empty string.

## 4. Results

**Allowed:** a normal result whose text content is the data (JSON is fine).

**Denied:** set `isError: true`, put a short message in the text content that is safe to show the user, and add `checkpoint_steps` in `structuredContent` (or `_meta`):

```python
CallToolResult(
    content=[TextContent(type="text", text="Denied by data policy (aml_contact): ...")],
    is_error=True,
    structured_content={"checkpoint_steps": [
        {"middleware": "tool_allowlist", "outcome": "passed", "reason": "tool get_customer_contact is allowed"},
        {"middleware": "datalog_policy", "outcome": "denied", "reason": "aml_contact"},
    ]},
)
```

- `outcome` is `passed` or `denied`. List the middleware in the order it ran.
- A `block` from `PolicyMiddleware.decide()` maps directly: `reason` becomes the denied step's reason, and `tags` can go into it as well.
- **Why it matters:**
  - The gateway counts a fetch as denied only when `isError` is true. A block returned as normal JSON text counts as a **passed** fetch in the dashboard.
  - `checkpoint_steps` is what makes the dashboard's request trace end at *your* middleware, not just at "tool refused".
  - The model is told the request was denied and not to work around it.
- **Known overlap:** the gateway also counts other `isError` results (unknown tool, bad arguments) as denials. If you can, include `checkpoint_steps` only on real policy denials; the gateway may later use their presence to tell the two apart.

## 5. Order inside a data tool

1. Read the caller from `_meta`. Deny if it is missing.
2. Build the `MCPRequest` and call `PolicyMiddleware.handle()`.
   - Note: `handle()` records the tool's facts *before* deciding. If a call is blocked, its facts are still stored, e.g. `contact_data` for a contact request that was denied. Decide whether that is intended. Usually facts should be stored only for data actually returned.
3. On `block`, return the denied result above without running SQL.
4. On `allow`, run the predefined SQL and return the rows.

## 6. Repository hygiene

- Rename `mcp.py`, e.g. to `policy_mcp_server.py`. A top-level `mcp.py` shadows the `mcp` SDK package for anything run from the repo root (`uv run pytest`, `uv run uvicorn`), which breaks the gateway's `from mcp import Client` and the SDK server itself.
- Remove `policy_knowledge.sqlite3` from git and add `*.sqlite3` to `.gitignore`. Set `POLICY_DB_PATH` to a Docker volume path (e.g. `/data/policy/knowledge.sqlite3`).
- Avoid creating the database at import time (`DEFAULT_MIDDLEWARE = PolicyMiddleware()`). Build it in the server's entry point, so importing the module for tests or for the admin API has no side effects.
- Add the server to `docker-compose.yml` as the `data-mcp` service (it can reuse the gateway's Dockerfile). Its tests can run in the same pytest suite under `tests/`.

## Checklist

- [ ] SDK-based server over streamable HTTP on port 8001
- [ ] Only the five data tools listed; rule management and `evaluate_request` removed from the model-facing server
- [ ] User and session read from `_meta`; deny when the user is missing
- [ ] Denials use `isError: true` and `checkpoint_steps`
- [ ] `mcp.py` renamed; SQLite file untracked and kept on a volume
