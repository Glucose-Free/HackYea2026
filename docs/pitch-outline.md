# Pitch outline

Structure: SCQA opening (Situation → Complication → Question → Answer), then one slide per evaluation criterion, sized by weight. Slide titles are claims; the body is the evidence. Assumes ~6 minutes plus a live demo.

| # | Title (claim) | Content |
|---|---|---|
| 1 | **"Every answer was allowed. Together they leaked a secret."** | Cold open: Alice's two prompts, the leaked fact in red. Nothing else on the slide. |
| 2 | *Situation:* Employees now query company data through AI agents | Chat → LLM → tools → DB. Productivity is real; the brief's own framing. |
| 3 | *Complication:* Today's guardrails judge one message at a time | Prompt filters see one message. RBAC sees one query. Neither sees what a user has **accumulated** across chats. (OWASP LLM02 Sensitive Information Disclosure fits here.) |
| 4 | *Question → Answer:* A control layer that remembers what each user already knows | One sentence plus a one-line architecture strip. |
| 5 | **Live demo** | The README script: Alice is blocked in a *new* chat, Bob gets the same facts in reverse order and is blocked too. Then the dashboard trace down to the `datalog_policy` step. |
| 6 | Hybrid defense: semantic checkpoint + deterministic checkpoint | The architecture diagram. Checkpoint 1: LLM yes/no guards. Checkpoint 2: Datalog over per-user knowledge. This maps directly onto formal requirement 2 (deterministic + semantic). |
| 7 | We decide before reading the data, so a refusal can't leak it | The over-blocking trade-off, presented as a deliberate choice: deciding on the secret value would make the refusal itself a side channel. |
| 8 | Policy is config: rules and guards change without a redeploy | `gateway.toml` guards with enforce/monitor modes and `on_error`, Datalog rules in JSON, hot reload with last-known-good fallback. Judges will edit the config, so invite them to. |
| 9 | Security teams get a tamper-evident trace of every decision | Dashboard screenshots: per-user denial share, request status donut, trace tree. Hash-chained audit log with a separate anchor, and `/audit/verify`. (Reporting, 20%) |
| 10 | 306 tests prove both allowed and blocked paths | Test counts by area, positive and negative examples, runtime 3.3 s, one command to run. (Tests, 15%) |
| 11 | Pluggable by design | Guards via entry points, `ToolRegistry` per domain (AML and transactions share one engine), adapters for any OpenAI-compatible model or local model. (Implementability, 15%) |
| 12 | Coverage vs. the brief: what's done, what's next | Honest table: requirement → done / partial / roadmap. |
| 13 | Close | Back to slide 1: "Now that leak is blocked, and the trace shows exactly why." |
