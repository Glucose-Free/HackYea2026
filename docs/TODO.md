# TODO

Open work, by priority. Architecture: `docs/architecture.md`.

## Before showing it to anyone

- [ ] Replace the demo secrets in `.env`: `GATEWAY_API_KEY`, `REPORT_ACCESS_TOKEN`, `WEBUI_SECRET_KEY` and the passwords. The gateway logs a warning while the defaults are in use.
- [ ] With API keys, switch to `[jev] adapter = "typesafe"` and `[chat_model] adapter = "openai_compatible"`, then tune the check wording and `refuse_threshold` on real prompts, including benign ones that mention rules or policies.

## Dashboard

- [ ] Guard config editing on top of `ConfigStore` and the guard registry: `GET /admin/guard-types` with JSON schemas for forms, guard CRUD, history and rollback.
- [ ] Admin authentication. Today one shared `REPORT_ACCESS_TOKEN` reads the audit and edits the rules, and it is also accepted as `?token=`, which leaks into access logs and browser history.

## Design gaps

- [ ] **Indirect prompt injection:** tool results reach the model unguarded. Add a tool-result guard pipeline to the agent loop, using the same `Guard` contract.
- [ ] **Forged history:** the client sends the whole conversation. Store conversations on the server, keyed by Open WebUI chat id.
- [ ] **No output check:** what may leave the database is decided only by checkpoint 2.
- [ ] **Identity from headers:** replace `OpenWebUiHeaderResolver` with an OIDC resolver, or verify Open WebUI's signed `X-OpenWebUI-User-Jwt`.
- [ ] **Scale:** the audit log is one JSONL file behind an in-process lock (single worker), and `JsonlAuditQuery` reads the whole file per query. Move both to a database behind the `AuditLog` and `AuditQuery` interfaces.

## Small fixes

- [ ] An admin query with `end` near year 1 returns 500 (`OverflowError` in `build_time_range`) instead of 400.
- [ ] A non-ASCII API key or report token returns 500 (`compare_digest` on `str`). Compare UTF-8 bytes.
- [ ] Audit readers don't take the write lock, so a large event being written can briefly show the chain as broken.
- [ ] An event id can repeat after a corrupt log line (`_count` skips unreadable lines).
- [ ] `tests/` is copied into the runtime image.
- [ ] Naming and constants:
  - `_run_model_turn` returns a tuple instead of a struct;
  - some string literals are not constants (`"system"`, `"assistant"`, `"function"`, OpenAI object names, event keys);
  - `USER_ROLE` is defined twice;
  - `new_trace_id`, `new_span_id`, `ensure_utc` and `count_fetches` don't follow the `get_`/`build_`/`parse_` naming.
