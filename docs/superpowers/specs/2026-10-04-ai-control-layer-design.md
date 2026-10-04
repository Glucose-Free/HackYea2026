# AI Control Layer — Design

Date: 2026-10-04
Status: draft, awaiting review

## Purpose

An AI control layer for an organization. Employees chat with an internal
LLM that can retrieve company data. Every message passes through a
gateway that enforces semantic guardrails before the LLM acts, and every
decision is audited.

This is a demo, but the structure is the corporate target: pluggable
guards, a config seam for a future admin dashboard, and swappable
adapters for every external system.

## Scope

**In scope (ours)**

- Gateway skeleton: OpenAI-compatible API, identity, orchestration, audit.
- Checkpoint 1: a configurable pipeline of prompt guards. First guard
  uses TypeSafe's Jev model for generic safety checks.
- Chat agent: hosted chat model with tool calling, connected to the
  data MCP server.
- Config seam: versioned config store and pipeline provider, ready for a
  dashboard to be built later.
- Open WebUI as the chat front end.
- Docker packaging: the whole demo runs with `docker compose up`.
- A stub data MCP server with fake data, used until the real one exists.

**Out of scope (black box, other developer)**

- The data MCP server: predefined SQL queries exposed as MCP tools.
- Checkpoint 2: the middleware stack inside the MCP server that inspects
  SQL before it reaches the DB. Its rules are query-based, not role-based:
  they stop inference attacks built from combinations of queries. Its
  knowledge accumulates per user across sessions, so the gateway sends the
  caller's user id and session id in each tool call's `_meta`. The
  contract is in `docs/contracts/data-mcp-server.md`.

**Deferred**

- Admin dashboard UI and its config-editing API. Built now: the config
  seam and the read-only dashboard API (stats and traces).

## Architecture

```
Open WebUI ──HTTP (OpenAI API, API key, user headers)──▶ Gateway
                                                           │
   IdentityResolver ◀──────────────────────────────────────┤
   PipelineProvider (config version pinned per request) ◀──┤
                                                           ▼
                                  Checkpoint 1: user_input GuardPipeline
                                       │ refuse ──▶ refusal reply
                                       ▼ allow
                                  ChatAgent loop
                                     ChatModel ⇄ ToolProvider ──MCP──▶ data MCP server
                                                                         └ checkpoint 2 ─▶ DB
                                       ▼
                                  reply (JSON or SSE) + audit event
```

There are exactly two checkpoints. Checkpoint 1 is ours and inspects the
conversation. Checkpoint 2 lives inside the MCP server and inspects SQL.
The gateway has no output check and no tool-result guards (see Known gaps).

### Why a verdict pipeline, not `call_next` middleware

Guards return a verdict; the pipeline runner owns control flow. This is
what makes parallel guard execution, per-guard timeouts, per-guard
failure policy, monitor mode and uniform audit possible. With onion-style
middleware each layer controls whether later layers run, so a buggy
plugin can silently skip every check after it. Onion middleware stays
where it fits: FastAPI-level concerns such as request IDs.

## Package layout

```
gateway/
  app.py                    FastAPI app factory, lifespan, wiring
  api/
    openai_compat.py        GET /v1/models, POST /v1/chat/completions
    audit.py                /audit, /audit/verify, /report, /report/incident/{id}
    admin.py                dashboard read API (/admin/...)
  identity/
    resolver.py             IdentityResolver protocol, OpenWebUiHeaderResolver
  core/
    conversation.py         Message, Conversation
    gateway.py              Gateway orchestrator
    reply.py                GatewayReply, ReplyOutcome
  guards/
    contract.py             Guard protocol, GuardVerdict, GuardFinding, GuardDecision
    pipeline.py             GuardPipeline[T], ConfiguredGuard, PipelineVerdict, VerdictCombiner
    registry.py             guard type name → class, via entry points
    builtin/
      jev_semantic.py       JevSemanticGuard
  config/
    model.py                GatewayConfig, CheckpointConfig, GuardInstanceConfig
    store.py                ConfigStore protocol, FileConfigStore
    provider.py             PipelineProvider
  agent/
    chat_agent.py           ChatAgent tool loop
    chat_model.py           ChatModel protocol, OpenAiCompatibleChatModel
    tools.py                ToolProvider protocol, McpToolProvider
  jev/
    client.py               JevClient protocol, TypeSafeJevClient
  audit/
    log.py                  hash-chained audit log (current audit.py)
    trace.py                TraceStep, TraceRecorder (builds a request's steps)
    query.py                AuditQuery protocol, JsonlAuditQuery
    report.py               HTML reports (current report.py)
config/
  gateway.toml              default config
deploy/
  stub_mcp/                 stand-in data MCP server (fake data) until the real one exists
  open_webui/               demo account seeding
tests/
  fakes.py                  FakeJevClient, FakeChatModel, FakeToolProvider
  ...
Dockerfile                  gateway image
docker-compose.yml          gateway + Open WebUI + data MCP server
.env.example                every required variable, no values
pyproject.toml              managed with uv
uv.lock
```

Existing files: `audit.py` and `report.py` move into `gateway/audit/`
with behavior kept. `pipeline.py` and `main.py` are replaced.
`detection.py` (keyword role check) is retired: there are no role-based
rules, and data access rules belong to checkpoint 2. `selftest.py` is replaced by the pytest suite.

## Components

### Guard contract

```python
class GuardDecision(Enum):
    ALLOW = "allow"
    REFUSE = "refuse"


@dataclass(frozen=True)
class GuardFinding:
    label: str        # e.g. "prompt_injection"
    score: float      # 0–1, guard-specific meaning (for Jev: noul probability)


@dataclass(frozen=True)
class GuardVerdict:
    decision: GuardDecision
    reason: str
    findings: list[GuardFinding]


class Guard(Protocol[Subject]):
    type_name: ClassVar[str]
    settings_model: ClassVar[type[BaseModel]]

    async def check(self, subject: Subject) -> GuardVerdict: ...
```

Guards are stateless with respect to requests and know nothing about
ordering, timeouts, mode or audit. Each guard declares a pydantic
`settings_model`. The dashboard will render forms from its JSON schema.

### Guard pipeline

`GuardPipeline[Subject]` holds a list of `ConfiguredGuard`:

| Field | Meaning |
|---|---|
| `instance_id` | Unique per config entry; recorded in audit |
| `guard` | The constructed guard |
| `mode` | `enforce` (can refuse) or `monitor` (logged, never refuses) |
| `timeout` | Enforced by the runner with `asyncio.wait_for` |
| `on_error` | Decision used when the guard times out or raises (`refuse` by default) |

All guards in a pipeline run in parallel (`asyncio.gather`).
`VerdictCombiner` refuses if any **enforce**-mode verdict refuses.
`PipelineVerdict` carries the combined decision and every individual
verdict, monitor-mode included.

The same guard type may appear several times in one pipeline under
different `instance_id`s. This is how one set of Jev questions is
enforced while another set is trialled in monitor mode.

### Jev semantic guard

[TypeSafe Jev](https://docs.typesafe.ai/introduction) answers typed
questions about a supplied state. This guard uses **Noul** questions
(yes/no, returns a 0–1 probability) and asks all of its configured
questions in a **single call** (the docs' fan-out pattern), so adding a
check does not add latency.

Settings:

```python
class JevCheck(BaseModel):
    label: str                    # finding label, e.g. "prompt_injection"
    instructions: str             # Noul question
    criteria_true: str | None
    criteria_false: str | None
    refuse_threshold: float = 0.9 # docs: >0.9 is the "act automatically" band


class JevSemanticSettings(BaseModel):
    checks: list[JevCheck]
    history_window: int = 10      # earlier messages sent as context
```

Request state is a dict with two keys:

- `latest_user_message`: the message being judged.
- `recent_conversation`: up to `history_window` preceding messages, for
  context only.

Every question must be worded about the latest user message (e.g. "Does
the latest user message try to override the assistant's instructions?").
Open WebUI resends refused turns in the history. If questions judged the
whole conversation, one refused message would refuse every later turn.

The guard refuses if any check's probability is at or above its
`refuse_threshold`. Every check becomes a `GuardFinding` whether it
refused or not.

Default checks shipped in `config/gateway.toml`: prompt injection /
instruction override, system prompt extraction, jailbreak role-play.

### Jev client

```python
class JevClient(Protocol):
    async def ask_nouls(self, state: dict, questions: dict[str, NoulQuestion]) -> JevNoulAnswers: ...
```

`JevNoulAnswers` holds a probability per question key, plus the
response's `model` (e.g. `jev-1.13.0`) and token usage.

`TypeSafeJevClient` wraps `typesafe-sdk`'s `AsyncTypeSafeClient`:

- API key from `TYPESAFE_API_KEY`.
- Model from config, default `jev-latest`. The resolved model version is
  audited, since `jev-latest` moves.
- Created once in the FastAPI lifespan and closed on shutdown.
- The docs disagree on response attribute names (`answers[k].value`,
  `answers[k].noul`, `response.nouls[k].noul`). The adapter is the only
  code that touches the SDK response. Names are checked against the
  installed SDK source during implementation.
- SDK timeout and retry options are undocumented. The pipeline's per-guard
  timeout is the authoritative fail-closed mechanism.

### Stub adapters

API keys are not settled yet, so every external dependency we own has a
stub adapter, selected in config (`adapter = "stub"`). The default config
uses stubs, so `docker compose up` works with no keys at all.

- `StubJevClient`: answers Noul questions with a keyword heuristic (e.g.
  "ignore previous instructions" scores high on every check). Enough to
  show a refusal in the demo.
- `StubChatModel`: calls the first available tool when the latest message
  asks for data, otherwise replies with a canned answer. Enough to show the
  tool loop.

Switching to the real adapters means setting `adapter = "typesafe"` /
`adapter = "openai_compatible"` and putting keys in `.env`.

### Config and dashboard seam

```python
class ConfigStore(Protocol):
    def get_active(self) -> GatewayConfigVersion: ...
    def save(self, config: GatewayConfig, author: str) -> GatewayConfigVersion: ...
    def list_history(self) -> list[GatewayConfigVersion]: ...
    def activate(self, version_id: str, author: str) -> GatewayConfigVersion: ...
```

- `GatewayConfig` (pydantic) holds checkpoint pipelines, chat model
  settings, MCP server URL and Jev settings. Secrets come from env vars,
  never config.
- `FileConfigStore` reads `config/gateway.toml`. The version id is a hash
  of the file contents. `save`/`activate` are implemented minimally (write
  file, keep prior versions alongside) so the dashboard can use them
  later.
- Validation happens on load and save: every guard `type_name` must exist
  in the registry, and every settings block must match its
  `settings_model`.
- `PipelineProvider` builds pipelines from the active version. It checks
  the file's modification time on each request, builds new pipelines
  beside the old ones and swaps them in. A failed build keeps the last
  working pipelines and logs an error. An invalid config at startup stops
  the gateway.
- `Gateway` takes the pipelines once per request, so a config change
  never applies mid-request. The audit event records `config_version`.

The future admin API (`GET /admin/guard-types`, checkpoint CRUD,
history, rollback) is a thin layer over `ConfigStore` and the registry.

### Guard registry

Guard types are discovered from the `ai_gateway.guards` entry-point group.
Built-in guards register in this project's `pyproject.toml`. Third-party
guard packages register the same way and need no gateway code changes.

### Identity

`IdentityResolver` turns request headers into `UserIdentity` (id, email,
name). No rule in the gateway depends on who the user is. Identity goes to
the audit log, and the user id (with the Open WebUI chat id, from
`X-OpenWebUI-Chat-Id`, as session id; the request id when absent) goes to
the MCP server in `_meta`, never as a model-filled tool argument.
`OpenWebUiHeaderResolver` reads the
`X-OpenWebUI-User-*` headers that Open WebUI forwards when
`ENABLE_FORWARD_USER_INFO_HEADERS` is on.

These headers are trusted only because the gateway also requires a shared
API key (`Authorization: Bearer`) known only to Open WebUI. Missing or
wrong key returns 401. A missing identity is refused. Real SSO would be a
different `IdentityResolver`.

### Chat agent

- `ChatModel` protocol, with `OpenAiCompatibleChatModel` as the adapter
  (base URL, model and API key env var set in config). This covers most
  hosted providers and Ollama.
- `ToolProvider` protocol, with `McpToolProvider` as the adapter, using the
  official `mcp` Python SDK over streamable HTTP. Tools are listed at
  startup and converted to the model's tool schema.
- `ChatAgent.reply(conversation)` loops: call the model, run any requested
  tools, append the results, repeat until a final answer. The cap is 8 tool
  rounds.
- A refusal from checkpoint 2 is returned to the model as the tool result,
  so it can tell the user the data is unavailable. It is recorded in the
  audit event.

### API

- `GET /v1/models` returns one model, `company-assistant`.
- `POST /v1/chat/completions` takes the OpenAI request shape and supports
  `stream` true and false. The agent loop runs without streaming. With
  `stream=true` the final answer is sent as SSE chunks. This trades time to
  first token for a simpler loop.
- Refusals and failures come back as ordinary assistant messages with a
  fixed, non-revealing text. Details go to the audit log only.
- Existing `/audit`, `/audit/verify`, `/report` and incident endpoints are
  kept, adapted to the new event fields. `/selftest` is removed. With no
  user roles any more, they are protected by an admin token
  (`REPORT_ACCESS_TOKEN`, with a demo default in `.env.example`) accepted
  as a header or as `?token=` so a browser can open them.

### Audit and request traces

Each chat request is recorded as one **trace**: a tree of steps, using the
OpenTelemetry span model (but stored in our own log, not sent to a
collector). The trace is what the dashboard shows as the "stack trace" of a
request.

```python
@dataclass(frozen=True)
class TraceStep:
    step_id: str
    parent_step_id: str | None
    kind: TraceStepKind        # see table
    name: str                  # e.g. guard instance id, tool name
    outcome: StepOutcome       # passed | denied | failed | info
    reason: str
    detail: dict               # kind-specific: findings, tool args, model version...
    started_at: datetime
    duration_ms: float
```

| Kind | Parent | Detail |
|---|---|---|
| `user_prompt` | (root) | user identity, latest message, `config_version` |
| `checkpoint` | `user_prompt` | checkpoint name (`user_input`), combined decision |
| `guard` | `checkpoint` | instance id, type, mode, findings, Jev model version and token usage |
| `agent_turn` | `user_prompt` | model call number |
| `data_fetch` | `agent_turn` | MCP tool name, arguments, passed or denied, refusal text |
| `fetch_step` | `data_fetch` | steps reported by the MCP server, if any (see below) |
| `reply` | `user_prompt` | final outcome, reply text |

A denied fetch therefore reads bottom-up as a stack trace: the step that
denied (`fetch_step` or `data_fetch`) → the agent turn that asked for the
data → the prompt the user typed. A checkpoint-1 refusal reads
`guard` → `checkpoint` → `user_prompt`.

**Data fetch, defined.** A data fetch is one MCP tool call. It is
**passed** if the tool returns data and **denied** if the MCP server
refuses it (checkpoint 2). A prompt refused at checkpoint 1 never reaches
the MCP server, so it counts as **one denied fetch attempt** with
`denied_at = checkpoint_1`. Every denied fetch carries `denied_at`
(`checkpoint_1` or `checkpoint_2`) so the dashboard can split them.

**Steps inside the black box.** The gateway only sees what the MCP server
returns. To show which checkpoint-2 middleware denied a fetch, the gateway:

- sends a W3C `traceparent` in each MCP tool call's `_meta` (MCP has no
  per-call headers), so the MCP server can tie its own logs to our trace
  if it wants to;
- reads optional structured steps from the tool result
  (`structuredContent` or `_meta`, field `checkpoint_steps`: a list of
  `{middleware, outcome, reason}`) and records each as a `fetch_step`.

If the MCP server returns no steps, the trace ends at the `data_fetch`
step with the refusal text. Nothing breaks either way. Whether the black
box fills `checkpoint_steps` is up to its developer; the field name is our
proposal.

**Storage.** The trace is the audit event: one event per request,
appended to the existing hash-chained JSONL log, with `steps` holding the
flat list of `TraceStep`s. Event `type` and `severity` are derived from
the outcome, as today.

### Dashboard read API

The dashboard (built later) reads through `AuditQuery`. It is a separate
read model, so the append-only log never has to answer queries itself.

```python
class AuditQuery(Protocol):
    def get_fetch_totals(self, start: datetime, end: datetime, bucket: TimeBucket) -> list[FetchTotalsBucket]: ...
    def list_user_fetch_stats(self, start: datetime, end: datetime) -> list[UserFetchStats]: ...
    def list_requests(self, filters: RequestFilters, page: PageRequest) -> RequestPage: ...
    def get_request_trace(self, request_id: str) -> RequestTrace | None: ...
```

- `FetchTotalsBucket`: bucket start, passed count, denied count (split by
  `denied_at`).
- `UserFetchStats`: user identity, passed, denied, last fetch time. Only
  users with at least one fetch attempt are listed.
- `RequestTrace`: the request summary plus its steps, in tree order.
- `list_requests` lets the dashboard find a trace: filter by user, outcome,
  `denied_at` and time range.

`JsonlAuditQuery` (demo) reads the log file and computes results in
memory, which is fine at demo scale. Production would project events into
a database and keep the same protocol.

Exposed now as JSON endpoints, protected by the admin token, so the
dashboard has a working backend from day one:

| Endpoint | Returns |
|---|---|
| `GET /admin/fetches/totals?start&end&bucket=hour\|day` | `FetchTotalsBucket[]` |
| `GET /admin/users/fetch-stats?start&end` | `UserFetchStats[]` |
| `GET /admin/requests?user&outcome&denied_at&start&end&cursor` | `RequestPage` |
| `GET /admin/requests/{request_id}/trace` | `RequestTrace` |

The existing HTML `/report` pages are rebuilt on `AuditQuery` too, so there
is one way to read the log.

## Request flow

1. Check the API key, then resolve identity. Either failing ends the
   request.
2. Build `Conversation` from the request messages.
3. Get pipelines from `PipelineProvider`, pinning `config_version`.
4. Evaluate the `user_input` pipeline. On refusal, return the refusal
   reply.
5. Run `ChatAgent.reply`.
6. Return the reply as JSON or SSE.
7. Write the audit event.

## Error handling

Fail closed unless config says otherwise.

| Failure | Behavior |
|---|---|
| Guard times out or raises | Guard's `on_error` applies (default refuse) |
| Jev unreachable | Covered by the row above |
| Chat model or MCP transport error | `failed_closed`, generic message |
| Checkpoint 2 refuses a tool call | Tool result to model; audited |
| Tool-round cap reached | `failed_closed` |
| Invalid config at startup | Gateway does not start |
| Invalid config while running | Keep last working pipelines, log error |
| Audit write fails | Request completes, error logged |

## Deployment (demo)

The whole demo starts with one command and needs nothing on the host but
Docker:

```
docker compose up         # runs on stub adapters, no keys needed
```

Then open Open WebUI in a browser. No Python, venv or Node on the host.
For real Jev and chat model calls, copy `.env.example` to `.env`, add the
keys and switch the adapters in config.

One container per service, run by Docker Compose. Bundling Open WebUI
into the gateway image would mean maintaining a fork of its image for no
gain.

| Service | Image | Notes |
|---|---|---|
| `gateway` | built from `Dockerfile` | `python:3.12-slim` + uv, dependencies locked by `uv.lock`; runs uvicorn; healthcheck on `/health` |
| `open-webui` | official Open WebUI image | starts after `gateway` is healthy; pre-configured, see below |
| `data-mcp` | the other developer's MCP server image (black box) | until that image exists, `deploy/stub_mcp/` stands in: a few tools over fake data, one of which can return a refusal, so the demo runs end to end. Only the MCP tool interface is assumed. |

- `docker-compose.yml` sits at the repo root so `docker compose up` works
  without flags.
- Secrets come from `.env` (git-ignored). `.env.example` is committed and
  lists every variable.
- The audit log and config live on named volumes, so they survive
  restarts. `config/gateway.toml` is copied in as the initial config.
- Tests also run in Docker: `docker compose run --rm gateway pytest`.
- Demo user accounts are created automatically on first start, so the
  jury can log in without setup and see different users in the audit log.
  How to seed them (Open WebUI's admin API or env settings) is decided
  during implementation.

Open WebUI is configured with:

- an OpenAI connection to the gateway with the shared API key
- user-info header forwarding enabled
- title, tag, follow-up and autocomplete generation disabled, since these
  would otherwise send background prompts through checkpoint 1 and into
  the audit log
- its own RAG, web search and tool features disabled, so no data path
  bypasses the gateway

Exact Open WebUI environment variable names are verified against its
current docs during implementation.

## Testing

pytest with pytest-asyncio. External systems are replaced by fakes in
`tests/fakes.py`.

- **Pipeline:** an enforce-mode refusal refuses; a monitor-mode refusal
  does not; timeout and exception follow `on_error`; guards run
  concurrently; all verdicts are kept.
- **Jev guard:** threshold boundaries; one call for all checks; history
  window is applied; findings for every check.
- **Config:** invalid guard type or settings rejected; reload swaps
  pipelines; a failed reload keeps the old ones; the version is pinned per
  request.
- **Gateway scenarios** (carried over from `selftest.py`):
  - direct and disguised injection refused
  - benign questions answered
  - **a refused turn in history followed by a benign turn is answered**
  - Jev down fails closed
  - chat model crash fails closed
  - checkpoint-2 refusal explained to the user
  - tool cap enforced
- **API:** stream and non-stream; `/v1/models`; missing or wrong API key;
  missing identity headers.
- **Audit:** chain integrity and tamper detection (kept from today).
- **Traces:** a checkpoint-1 refusal yields `guard → checkpoint →
  user_prompt`; a checkpoint-2 refusal yields `data_fetch → agent_turn →
  user_prompt`, plus `fetch_step`s when the tool result carries
  `checkpoint_steps`; `traceparent` is sent on MCP calls.
- **Dashboard queries:** totals per bucket with passed/denied split by
  `denied_at`; per-user stats list only users with fetch attempts; trace
  lookup returns steps in tree order; admin endpoints reject a missing
  token.

## Known gaps

Accepted for the demo and listed here so they are not forgotten.

- **Indirect prompt injection:** tool results enter the model's context
  unguarded. A tool-result hook would close this.
- **Forged history:** the client sends the full conversation, so earlier
  turns can be fabricated. Server-side conversation storage would close
  this.
- **No output check:** relies on checkpoint 2 to decide what data may
  leave the DB.
- **Header identity:** trust rests on the shared API key and network
  isolation, not real SSO.
