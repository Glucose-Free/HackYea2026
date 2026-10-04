# TODO

What is left, by priority. Update it as items land. Design: `docs/superpowers/specs/2026-10-04-ai-control-layer-design.md`.

## Now: needed for the demo

- [ ] Merge `origin/main` (the policy engine, `50e32de`) into `feat/ai-control-layer`. Expect a conflict in `README.md`: keep ours and add a short paragraph on the policy engine.
- [ ] Rebuild and smoke-test the stack: `docker compose up --build`. Then log in at http://localhost:3000 and run the README demo script. The image has never been built from the current code.
- [ ] Push the branch and open a PR.
- [ ] Before showing it to anyone, replace the demo secrets in `.env` (the gateway now logs a warning while they are in use) (`GATEWAY_API_KEY`, `REPORT_ACCESS_TOKEN`, `WEBUI_SECRET_KEY`, passwords).
- [x] Bring the data MCP server in line with `docs/contracts/data-mcp-server.md` (`mcp_policy_http_server.py`, `deploy/policy_mcp/`).
- [ ] Untrack `policy_knowledge.sqlite3` and add `*.sqlite3` to `.gitignore`.
- [x] Replace the `data-mcp` stub service in `docker-compose.yml` with the real server, and delete `deploy/stub_mcp/`.
- [ ] Switch to real services once keys exist: `[jev] adapter = "typesafe"` and `[chat_model] adapter = "openai_compatible"` in `config/gateway.toml`, keys in `.env`. Then tune the Jev check wording and `refuse_threshold` on real prompts, including benign ones that mention rules or policies.

## Next: the dashboard

The read API exists (`/admin/fetches/totals`, `/admin/users/fetch-stats`, `/admin/requests`, `/admin/requests/{id}/trace`).

- [x] Dashboard UI: passed/denied fetches over time, per-user stats, and the request trace view (`/dashboard/`).
- [x] Dashboard: a "partially passed" request status (answered with at least one fetch denied at checkpoint 2), shown in the "Data retrievals" donut view.
- [ ] Config-editing admin API on top of `ConfigStore` and `GuardRegistry`:
  - `GET /admin/guard-types` (with JSON schemas for forms)
  - guard config CRUD
  - history and rollback
- [ ] Datalog rule editing for checkpoint 2 through the same admin API (list, enable/disable, upsert), backed by the policy engine's `PolicyConfigStore`. This replaces the rule tools that are coming off the MCP server.
- [ ] Authentication for the admin side: today it is a single shared `REPORT_ACCESS_TOKEN`, also accepted as `?token=`, which leaks into access logs and browser history.

## Later: known design gaps

Accepted for the demo; listed in the README.

- [ ] **Indirect prompt injection:** tool results reach the model unguarded. Add a tool-result hook to the agent loop with its own guard pipeline (same `Guard` contract).
- [ ] **Forged history:** the client sends the whole conversation. Store conversations on the server, keyed by Open WebUI chat id.
- [ ] **No output check:** the gateway relies on checkpoint 2 for what data may leave the DB.
- [ ] **Identity from headers:** trust rests on the shared API key and network isolation. Replace `OpenWebUiHeaderResolver` with an SSO/OIDC resolver, or use Open WebUI's signed `X-OpenWebUI-User-Jwt`.
- [ ] **Scale:** the audit log is one JSONL file with an in-process lock (single uvicorn worker only), and `JsonlAuditQuery` reads the whole file per query. Move both to a database behind the existing `AuditLog` / `AuditQuery` interfaces.

## Small fixes deferred from code review

- [ ] An admin query with `end` near year 1 returns 500 (`OverflowError` in `build_time_range`); it should return 400.
- [ ] A non-ASCII API key or report token returns 500 (`compare_digest` on `str`); compare UTF-8 bytes instead.
- [x] Every MCP `isError` (unknown tool, bad arguments) counts as a checkpoint-2 denial. Treat a result as a denial only when `checkpoint_steps` contains a `denied` step; otherwise let the model retry.
- [ ] Audit readers don't take the write lock, so a large event being written can briefly show the chain as broken.
- [ ] An event id can repeat after a corrupt log line (`_count` skips unreadable lines).
- [ ] Coding-guideline nits:
  - `_run_model_turn` returns a tuple instead of a struct
  - some string literals are not constants (`"system"`, `"assistant"`, `"function"`, OpenAI object names, event keys)
  - `USER_ROLE` is defined twice
  - `new_trace_id`/`new_span_id`/`ensure_utc`/`count_fetches` don't follow the `get_`/`build_`/`parse_` naming
  - `tests/` is copied into the runtime image
