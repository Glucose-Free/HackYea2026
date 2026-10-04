# Wall-Aware AI Gateway (HackYea 2026)

A gateway in front of an LLM. Every request passes through a pipeline that checks who is asking, redacts restricted information, calls the model, scans the answer, and logs the decision. If anything fails, it blocks (fails closed).

## Run it

```
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install fastapi uvicorn pydantic httpx
uvicorn main:app --reload
```

Open http://127.0.0.1:8000/docs to try it. Model calls go to a local Ollama server (`llama3.2:1b`), so Ollama must be running. If it isn't, requests are blocked with "model unavailable".

Test users (send as the `X-User-Id` header): `trader_1`, `banker_1`, `compliance_1`.
Outcomes are always one of: `allowed`, `redacted`, `blocked`.

## Files

| File | What it does |

| `main.py` | Web endpoints |  
| `pipeline.py` | Ordered stages: auth, scan, policy, tokenize, model call, response scan, detokenize |  
| `detection.py` | `detect(text)` and `decide(role, entities)`. Keep these two signatures. |  
| `audit.py` | Hash-chained audit log |  
| `policy_middleware.py` | Session memory + tipping-off policy gate for function-based DB access |  
| `mcp.py` | Minimal MCP stdio server exposing named data functions as tools |  
| `selftest.py` | Attack suite with benign controls |  
| `report.py` | Security report and incident reports |  

## Audit, self-testing and reporting

**Audit log.** Every request is written to `audit.jsonl` as an event with a type, severity, and a hash linking it to the previous event. Editing any past entry breaks the chain.
- `GET /audit/verify` says whether the chain is intact, or names the first broken event.
- `GET /audit` returns all events (compliance role only).
- Extra fields added to an event (for example `chain`, `risk_score`, `candidates_remaining`, `policy_version`) are stored and shown in incident reports automatically.

**Self-test.** Run `python selftest.py` before you push. It runs attack scenarios and benign controls straight through the pipeline, using its own temporary log, and also checks that tampering is detected.
- `PASS` / `FAIL`: expected behavior held, or it didn't.
- `GAP`: a known weakness (it flips to `FIXED` when someone fixes it).
- `PEND`: waiting for a feature, such as the inference check.
- Current gaps: spaced-out letters ("F a l c o n"), lookalike characters (Cyrillic "а"), and indirect descriptions with no name.
- Pending: the "dates then phone numbers" scenario and its benign control.
- The same suite is available live at `GET /selftest?user=compliance_1`.

**Reports.** `GET /report?user=compliance_1` shows audit integrity, the latest self-test result, activity counts, gateway overhead, blocks by user (with a flag for repeated blocks), and recent blocked events. Click an event ID for its incident report.

## Notes

- `?user=` on the report, audit and self-test URLs is a demo shortcut so a browser can open them. It is not real authentication.
- `audit.jsonl` and `selftest_last.json` are generated locally. Keep them out of git.
- Not done yet: running the self-test automatically on every push (CI).

## Policy middleware / MCP boilerplate

The new `policy_middleware.py` module stores MCP requests as facts in SQLite, runs a small Datalog engine, and exposes a `decide()` hook for your own policy logic.

Run the MCP server with:

```bash
python mcp.py
```

It exposes a minimal stdio MCP bridge with `evaluate_request` and `snapshot_session`. The server forwards the raw request to `PolicyMiddleware.handle()` and returns whatever the Datalog closure says.

Knowledge is cumulative across sessions for the same `user_id`, so later requests are evaluated against everything the user has already learned.

Policy rules live in [`policy_rules.json`](policy_rules.json). You can add new rules there, toggle them on or off, and reload the live engine without touching the Python code.

The current default policy uses positive Datalog rules only:

- `knows(user, relation, value)` stores accumulated facts for the user across sessions.
- `decision(block, reason)` is derived when AML knowledge is combined with direct contact or workplace facts for the same user.

Available rule-management tools over MCP:

- `list_policy_rules`
- `set_policy_rule_enabled`
- `upsert_policy_rule`
- `reload_policy_rules`

That gives you a clean place to add the next layer: rules for relation composition, mosaic knowledge, and session-wide leakage checks.
