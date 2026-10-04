# Architecture

Employees chat with an internal LLM assistant that can read company data. Every request passes two checkpoints, and every decision is written to an audit log the security team can verify.

```
Open WebUI ──OpenAI API, API key, user headers──▶ Gateway
                                                    │
                       IdentityResolver ◀───────────┤
                       PipelineProvider ◀───────────┤  config version pinned per request
                                                    ▼
                             Checkpoint 1: user_input GuardPipeline
                                  │ refuse ──▶ refusal reply
                                  ▼ allow
                             ChatAgent loop
                               ChatModel ⇄ McpToolProvider ──MCP──▶ data-mcp
                                                                    └ checkpoint 2: Datalog policy ─▶ DB
                                  ▼
                             reply (JSON or SSE) + one audit event
```

There are two checkpoints and no output filter:

- **Checkpoint 1** runs in the gateway and judges the user's latest message.
- **Checkpoint 2** runs inside the data MCP server and judges each data read against what that user already knows.

## Gateway (`gateway/`)

| Package | Responsibility |
|---|---|
| `api/` | `/v1/models`, `/v1/chat/completions`; `/admin/*` for the dashboard; `/audit`, `/audit/verify`, `/report` |
| `identity/` | `IdentityResolver`; `OpenWebUiHeaderResolver` reads the `X-OpenWebUI-User-*` headers |
| `core/` | `Gateway` orchestrates one request; `Conversation`, `GatewayReply` |
| `guards/` | `Guard` contract, `GuardPipeline`, entry-point registry, the built-in `jev_semantic` guard |
| `config/` | `GatewayConfig` (pydantic), `FileConfigStore` with history, `PipelineProvider` with hot reload |
| `agent/` | `ChatAgent` tool loop, `ChatModel` adapters, `McpToolProvider` |
| `jev/` | `JevClient` adapters: TypeSafe Jev, Granite Guardian on Ollama, keyword stub |
| `audit/` | Hash-chained JSONL log with an anchor file, request traces, `AuditQuery` read model, HTML report |
| `policy/` | Dashboard editing of checkpoint 2's rules file |
| `dashboard/` | Static security dashboard over `/admin/*` |

### Request flow

1. Check the API key, then resolve the user. Either failing ends the request.
2. Take the guard pipelines from `PipelineProvider` and record the config version.
3. Run the `user_input` pipeline. On refusal, reply with a fixed text.
4. Run `ChatAgent`: call the model, run the tools it asks for, repeat until it answers. The cap is `max_tool_rounds` (8 by default).
5. Return the reply as JSON or SSE, then write the audit event.

A refusal or failure reaches the user as an ordinary assistant message with a fixed text. Details go only to the audit log.

### Checkpoint 1: guard pipeline

Guards return a verdict, and the pipeline decides what happens next. A guard cannot skip the guards after it, as it could in onion-style middleware. The pipeline can therefore run guards in parallel and apply timeouts, failure policy and monitor mode the same way for every guard.

| Field | Meaning |
|---|---|
| `instance_id` | Unique per config entry; recorded in the trace |
| `type` | A registered guard type (`ai_gateway.guards` entry points) |
| `mode` | `enforce` can refuse; `monitor` only records |
| `timeout_ms` | Enforced by the pipeline |
| `on_error` | Decision when the guard raises or times out; `refuse` by default |

All guards run concurrently. The request is refused if any enforce-mode guard refuses. The same type can appear twice, for example one set of checks enforced while another is trialled in monitor mode.

`jev_semantic` asks all of its yes/no checks in one call and refuses when any probability reaches that check's `refuse_threshold`. Each check is worded about the latest user message only. Open WebUI resends refused turns, so judging the whole conversation would refuse every turn after the first refusal. The earlier turns are sent as context.

### Configuration

`config/gateway.toml` holds the guard pipelines, adapters, chat model and MCP URL. Secrets come from environment variables. On every request `PipelineProvider` checks whether the file changed and builds the new pipelines beside the old ones. An invalid file is rejected and the last working pipelines stay active. An invalid file at startup stops the gateway. Adapters are built once at startup.

### Identity

The `X-OpenWebUI-User-*` headers are trusted only because the gateway also requires a shared API key that only Open WebUI holds. The user id and the Open WebUI chat id go to the MCP server in each call's `_meta`, never as a tool argument the model could fill in.

## Checkpoint 2: data MCP server (`policy_engine/`, `examples/`)

`policy_engine/` knows no domain. A domain module (`examples/bank_demo.py`) registers its tools, and each tool declares four things:

- the roles allowed to call it;
- an argument validator;
- `plan_disclosure`: the facts a call could reveal, worked out from its arguments alone;
- a response validator that returns the facts the call actually revealed.

For each call the engine:

1. checks the role and validates the arguments;
2. evaluates the Datalog rules over the user's known facts plus the planned ones, and refuses if a `violation` can be derived;
3. runs the read-only query;
4. checks that the facts in the response are covered by the plan;
5. stores the approved facts for that user and commits, then returns the data.

The decision is made before the data is read. A refusal therefore never depends on a secret value, so refusing cannot leak one. The cost is over-blocking: when a plan cannot name the subject (an AML case before it is read), the engine assumes the call could name any customer.

`policy_engine/mcp_http_server.py` serves the engine over streamable HTTP to the gateway. It takes the user from `_meta`, refuses a call that has no user, and reports denials as `checkpoint_steps`, so the request trace ends at the step that refused. `docs/data-mcp-server.md` specifies that interface. `mcp_stdio_server.py` serves one authenticated principal per process for hosts that want stdio.

Rules live in a JSON file shared by `data-mcp`, which reads it on every call, and the gateway, which edits it from the dashboard. Knowledge lives in SQLite on its own volume.

## Audit

Each request is one event holding a trace: a tree of steps modelled on OpenTelemetry spans.

| Step | Parent | Detail |
|---|---|---|
| `user_prompt` | root | user, latest message, config version |
| `checkpoint` | `user_prompt` | combined decision |
| `guard` | `checkpoint` | instance, mode, findings, model version |
| `agent_turn` | `user_prompt` | model call number |
| `data_fetch` | `agent_turn` | tool, arguments, outcome |
| `fetch_step` | `data_fetch` | checkpoint 2's own steps, from `checkpoint_steps` |
| `reply` | `user_prompt` | final outcome and text |

A data fetch is one tool call. It is *denied* only when checkpoint 2 reports a denied step. Any other error is *failed*, and the model may retry. A prompt refused at checkpoint 1 counts as one denied fetch attempt with `denied_at = checkpoint_1`, so the dashboard can compare the two checkpoints.

Events are appended to a hash-chained JSONL file. After each write, the event count and latest hash go to an anchor file on a separate volume. `/audit/verify` checks the chain against the anchor, so it also detects a log that was truncated or rehashed. `JsonlAuditQuery` reads the file per query. That is enough at demo scale, and the `AuditQuery` protocol lets a database replace it.

## Deployment

`docker compose up` starts everything with stub adapters and no keys. `docker-compose.ollama.yml` adds Ollama with Granite Guardian and qwen2.5:3b.

| Service | Role |
|---|---|
| `gateway` | FastAPI on :8000, healthcheck on `/health` |
| `data-mcp` | `python -m deploy.policy_mcp.server`; only on the internal `backend` network, because it trusts the user id the gateway sends |
| `open-webui` | Chat UI with title, tag, follow-up and autocomplete generation, RAG, web search and tools disabled, so every prompt and every data path goes through the gateway |
| `seed-users` | Creates the demo accounts on first start |

The audit log, anchor, rules, knowledge and bank database live on named volumes.

## Known gaps

- **Indirect prompt injection:** tool results reach the model unguarded. A tool-result guard pipeline would close it.
- **Forged history:** the client sends the whole conversation. Storing conversations on the server would close it.
- **No output check:** what may leave the database is decided only by checkpoint 2.
- **Header identity:** trust rests on the API key and network isolation, not SSO.
- **One admin token:** the token that reads the audit also edits the rules.
