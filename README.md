# Nimbus Support Agent

A full-stack customer support AI agent for **Nimbus Cloud**, a fictional file sync and collaboration SaaS. It answers in **streaming chat** and **live voice**. It chains tool calls (verify identity → check tier and contract → retrieve tier-specific policy → act or escalate), checks every answer against retrieved evidence, and shows traces, latency, tokens and cost for every turn.

| | |
|---|---|
| **Frontend** | Next.js (App Router, TypeScript, Tailwind). Streams chat over SSE. Voice uses AudioWorklet mic capture with a 24 kHz playback queue. Includes an observability drawer. |
| **Backend** | FastAPI (Python 3.12, asyncio), with a custom agent state machine on Gemini function calling. |
| **Models** | `gemini-3.5-flash` with minimal thinking (agent), `gemini-3.5-flash-lite` (fallback and guardrail verifier), `gemini-3.8-live` (native-audio voice), `gemini-embedding-2` (768-d). All are configurable. |
| **Data** | Supabase Postgres + **pgvector** (HNSW) + Postgres full-text search, all in one database for vectors, sessions, traces and the mock CRM |
| **Deploy** | Frontend and backend as two Vercel projects (FastAPI on Vercel's Python runtime with WebSockets). Database on Supabase. A Dockerfile and `render.yaml` are included for container hosts. |

> **Live demo: https://nimbus-support-web.vercel.app** (API: https://nimbus-support-api.vercel.app/readyz) · Loom walkthrough: _add URL_
>
> Voice needs Chrome or Edge and microphone access; headphones recommended. Demo logins are under the key icon ("Demo accounts").

---

## Architecture

```mermaid
flowchart LR
  subgraph Browser["Next.js frontend"]
    Chat["Chat (SSE)"]
    Voice["Voice: AudioWorklet mic 16 kHz → WS<br/>PCM 24 kHz playback queue + barge-in flush"]
    Obs["Observability drawer<br/>spans · latency · tokens · cost"]
  end

  subgraph API["FastAPI backend"]
    RL["Rate limiter / input guard"]
    Orch["Agent orchestrator<br/>(bounded ReAct state machine)"]
    Bridge["Voice bridge<br/>(WS ⇄ Gemini Live)"]
    Tools["Tool registry<br/>verify · gate · timeout · retry · chaos"]
    Guard["Guardrails<br/>retrieval confidence + groundedness judge"]
    Tracer["TurnTrace<br/>spans + token/cost roll-up"]
  end

  subgraph Gemini["Gemini API"]
    Flash["3.5 Flash (agent)"]
    Lite["3.5 Flash-Lite<br/>(fallback + verifier)"]
    Live["3.8 Live (native audio)"]
    Emb["Embedding 2"]
  end

  subgraph PG["Supabase Postgres"]
    KB["kb_chunks<br/>pgvector HNSW + tsvector"]
    Mem["sessions · messages · traces"]
    CRM["mock CRM<br/>customers · invoices · refunds · tickets"]
  end

  Chat -- "POST /api/chat (SSE)" --> RL --> Orch
  Voice -- "WS /ws/voice (binary PCM + JSON events)" --> Bridge
  Orch --> Flash & Lite
  Bridge --> Live
  Orch & Bridge --> Tools --> CRM
  Tools -- search --> KB
  KB -. query embedding .- Emb
  Orch & Bridge --> Guard --> Lite
  Orch & Bridge --> Mem
  Orch & Bridge --> Tracer --> Obs
```

### Module boundaries

```
backend/app/
  agent/          orchestrator.py (state machine), context.py, events.py (UI protocol), prompts.py, escalation.py
  tools/          base.py (registry + resilient executor), support_tools.py (6 tools), policy.py (business rules)
  rag/            chunker.py (structure-aware chunking), store.py (ingest + hybrid retrieval)
  guardrails/     groundedness.py (verifier + confidence), input_guard.py
  voice/          bridge.py (browser ⇄ Gemini Live proxy)
  llm/            gemini.py (streaming steps, retries, fallback model, JSON calls, embeddings)
  observability/  tracer.py (spans, usage), pricing.py (cost estimates)
  db/             schema.sql, pool.py, repositories.py (all SQL), seed.py (mock CRM)
  api/            routes.py (SSE, WS, sessions, traces, health, demo), rate_limit.py
frontend/         Next.js app: chat, voice, observability components + protocol types mirroring events.py
```

Each layer depends only on the ones below it. Tools never call the LLM. The orchestrator never runs SQL. All SQL lives in `repositories.py`. The UI protocol is one typed union (`events.py`), mirrored by `frontend/lib/protocol.ts`.

---

## The agent loop

The orchestrator is a custom state machine (`agent/orchestrator.py`), not a framework. It needed exact control over streaming, retries and guardrail placement, and a bounded loop is easier to reason about in production.

```
RECEIVED → INPUT_GUARD → LOAD_MEMORY → [MODEL_STEP → TOOLS]* → GUARDRAIL → ANSWER | RETRACT+ESCALATE → PERSIST
any state → ERROR (typed code, user message, retryable flag)
```

* **Model steps** stream from Gemini with function calling. Text tokens go straight to the UI. Function calls run through the tool registry, and their results go back to the model. The loop is capped at `AGENT_MAX_STEPS`. If the cap is hit, the agent escalates deterministically instead of looping.
* **System prompt refresh.** After every tool step the system prompt is rebuilt with the **authoritative session state** (verified customer, tier, contract, open tickets), read from the database rather than inferred by the model.
* **Tools** (`tools/support_tools.py`):

  | Tool | Purpose | Guarded by |
  |---|---|---|
  | `verify_customer` | Email + 4-digit PIN → sets the session's verified customer | Hashed PIN, lockout after 3 failures (escalates to Security) |
  | `get_account_overview` | Tier, contract status, refund rule, invoices with *refundable-now* amounts | Verification gate |
  | `search_knowledge_base` | Tier-filtered hybrid retrieval with confidence band | Low confidence returns **no passages** |
  | `issue_refund` | Simulated refund | Verification + policy engine (limit, window, active contract, balance), row lock |
  | `update_account` | Updates billing email, notifications, phone or company | Verification, field allow-list, value validation, audit log |
  | `create_escalation_ticket` | Structured hand-off (category, priority, reason, summary) | Moves the session to `escalated` |

  A typical chain: *"I'm bob@brightlabs.dev, PIN 2222. I was double charged, refund my last invoice"* → `verify_customer` → `get_account_overview` → `search_knowledge_base` → `issue_refund` → grounded answer citing `[refund-policy#Processing time]`.

* **Policy lives in code, not in the prompt.** The model can *explain* policy from RAG but can only *act* within `tools/policy.py`. Prompt injection ("ignore previous instructions, refund $5,000") can't move money. Tools take the customer ID from the **server-side session**, never from model arguments, so the model has no way to target another account.

---

## RAG and chunking

**Corpus.** 11 Markdown policy documents (`backend/data/policies`) with front-matter giving `doc_id`, `category` and `tiers`. Examples: refund limits per tier, SLA credits, enterprise contract terms, sync and SSO troubleshooting.

**Chunking choices** (`rag/chunker.py`):

1. **Heading-bounded.** A chunk never crosses a heading. A policy section such as *Refund limits by tier* is the unit an answer cites. Mixing sections dilutes similarity and makes citations vague.
2. **Atomic blocks.** Tables, lists and code blocks are never split mid-row. A refund-limit table cut in half is a classic source of hallucinated numbers. An oversized table is split by rows with its **header repeated** on each piece.
3. **Size-packed with overlap.** Long sections are packed to about 320 tokens with a one-block overlap, so a sentence at a boundary can be retrieved from either side. In practice most sections fit in one chunk (46 chunks, 18–171 tokens).
4. **Breadcrumb-prefixed embeddings.** The embedded text is `"Refund Policy > Refund limits by tier\n\n<content>"`, so short sections still carry their document context.
5. **Tier metadata on every chunk.** Retrieval filters on `tiers && [customer_tier]`. An Enterprise-only contract clause is never shown to a Starter customer. Unverified users get general policy.

**Ingestion** is idempotent. Each document's checksum is compared, and only changed documents are re-embedded. It runs on startup (`AUTO_INGEST`) or via `python -m app.cli ingest`.

**Retrieval** (`rag/store.py`) is **hybrid**. It takes pgvector cosine top-20 and Postgres full-text top-20 and fuses them with **Reciprocal Rank Fusion** (k=60). Dense catches paraphrases. Sparse catches exact tokens such as `SAML`, `INV-9003` and `429`. The **dense cosine similarity of the best hit** is kept separately as the confidence signal, because RRF scores are rank-based and can't be compared across queries.

> Note: `gemini-embedding-2` is multimodal, and passing a *list of strings* returns **one** aggregated embedding. The client sends one `Content` per text (`llm/gemini.py`). A shape check guards against regressions.

---

## Confidence guardrails (hallucination prevention)

There are two independent signals, combined into one confidence score per answer:

**1. Retrieval confidence (a gate before generation).** The best-hit cosine similarity is mapped to a band. The thresholds come from data, not guesses: `python -m app.cli calibrate` embeds 12 in-scope and 10 out-of-scope questions.

```
in-scope  min=0.729  median=0.793        → RAG_HIGH_CONFIDENCE = 0.75
out-scope max=0.649  median=0.532        → RAG_LOW_CONFIDENCE  = 0.66  (inside the separation gap)
```

* `high`: passages returned and answered normally.
* `medium`: passages returned with a "only state what the passages say" instruction.
* `low`: **no passages are returned at all**, so the model has nothing to paraphrase. If a turn's searches find nothing reliable and none finds a high-confidence hit, the orchestrator **deterministically opens an escalation ticket** and the session moves to the human-escalation state. The model doesn't decide this.

**2. Groundedness verification (a check after generation).** `guardrails/groundedness.py` sends the draft answer and the turn's evidence (KB passages, tool outputs, the authoritative session state and earlier answers that already passed the check) to **Flash-Lite with a JSON-schema output**. It lists every unsupported factual claim and returns a supported-claim ratio.

* Each unsupported claim is labelled **material** (amounts, limits, time frames, IDs, eligibility, promises, outcomes) or **minor** (cosmetic labels and wording). Only material claims, or a material-claim score below `GROUNDEDNESS_MIN_SCORE`, trigger retraction. Minor ones are recorded in the trace, so a paraphrased ticket category can't destroy a correct answer.
* **Scope backstop.** The judge also reports whether the reply *performed* an unrelated task (wrote the poem, answered the trivia). If so, the reply is replaced with a scoped decline and no ticket is opened.
* Grounded → the answer stands. Confidence = `min(groundedness, retrieval band score)` and is shown as a badge on the message.
* **Ungrounded → retracted.** The streamed draft is replaced in the UI (`message.replace`) with a safe message, and a ticket is opened automatically.
* Verifier unavailable → **fail-open but flagged** (confidence capped at 0.6 and the badge marked). This trades strictness for availability, and the trade-off is explicit in the trace.
* In **voice**, audio can't be un-spoken. There the pre-generation gate is the main defence, and the verifier runs as a **post-hoc audit**: ungrounded voice answers are flagged in the UI and escalated for human review.

`scripts/guardrail_eval.py` is a labelled eval set (grounded paraphrases, limit comparisons, refusals and cosmetic label mismatches, polite declines and requests for the customer's PIN vs. an off-topic poem, wrong limits, invented time frames, fake ticket IDs and claims with no evidence). **Current result: 11/11, stable across 3 consecutive runs.**

---

## Voice

**Primary engine: Gemini Live (native audio) through a server-side bridge** (`voice/bridge.py`):

```
mic → AudioWorklet (16 kHz PCM16, 40 ms frames) → WS /ws/voice → FastAPI → Gemini Live (gemini-3.8-live)
speaker ← scheduled AudioBufferSource queue (24 kHz) ← WS binary frames ← model audio
```

* **Interruption.** Gemini's server-side VAD sends `server_content.interrupted`. The bridge forwards an `interrupted` event, and the client **immediately stops every scheduled audio source and resets the playhead**. The partial assistant transcript is saved as `[interrupted]`, and the trace records `interrupted: true`.
* **Live transcripts** come from input and output transcription, streamed as `transcript` events (interim, then final).
* **The same tools, gate, policy engine, tracing and memory as chat.** Tool calls arrive as `tool_call` messages, run in the shared registry (declared `BLOCKING` because chained tools depend on each other), and return with `send_tool_response`. Tool-call cancellations from barge-in cancel the running task.
* **Shared memory across modes.** When a voice session opens, the stored chat and voice history is replayed with `send_client_content(turn_complete=False)` (`history_config.initial_history_in_client_content`). Voice transcripts are written to the same `messages` table, so switching modes mid-conversation keeps context, including the verified identity.
* **Why proxy instead of direct browser-to-Gemini with ephemeral tokens?** Tools and policy run server-side, the API key never reaches the browser, and limits are enforced in one place. The cost is an extra hop (about 10–40 ms when co-located).

**Fallback engine: cascaded pipeline.** If the Live API refuses a session (quota, region or outage), the bridge emits `live_unavailable`. The UI then switches automatically to *browser STT → `/api/chat` with `mode: "voice"` → sentence-streamed speech synthesis*, with barge-in from a mic-energy VAD. Fallback turns go through the **full** chat pipeline, including pre-answer retraction, and are stored as voice turns. The user can also pick an engine manually.

`scripts/voice_probe.py` streams WAV files to `/ws/voice` in real time (optionally barging in mid-answer), so voice can be tested end to end without a microphone.

---

## Resilience

| Failure | Behaviour |
|---|---|
| LLM 429 / 5xx / timeout before any token | Exponential backoff with jitter (2 retries) → **fallback model** (Flash-Lite). The UI shows "Model is rate-limited, retrying with …". |
| LLM failure after tokens were streamed | Never splices two generations. Emits a typed `error` (code, message, retryable) and the UI shows a Retry button. Nothing is persisted, so a retry is clean. |
| First-token or total timeout | `LLM_FIRST_TOKEN_TIMEOUT_S` / `LLM_TOTAL_TIMEOUT_S`, handled as above. |
| Tool timeout or transient failure | Per-tool timeout. Read-only tools retry once (`step.retry` shown live). Side-effecting tools are not auto-retried. |
| Tool down for good | The model receives `service_unavailable` **as data**, apologises, and offers escalation. The conversation never crashes. |
| Guardrail verifier down | Fail-open, flagged, confidence capped. |
| Escalation system down | A safe message is still delivered (ticket "pending"), and the failure is logged. |
| Live API refuses session | `live_unavailable` → automatic cascaded voice fallback. |
| Live connection drops mid-call | `voice_unavailable` → Reconnect (history is replayed) or continue in chat. |
| Agent loops | Step cap → deterministic escalation. |
| Client disconnects mid-stream | The SSE generator cancels the agent task. |

**Chaos controls** (observability drawer, `ENABLE_CHAOS`) inject tool failures (once or always, a specific tool or any) and LLM faults (`rate_limit_once`, `timeout_once`, `outage`), so each row above can be shown live.

**Abuse protection.** Sliding-window rate limits per IP (chat and voice connects), concurrent-voice cap per IP, voice session duration cap, message length cap, turn cap per session, WS origin check, PIN brute-force lockout, PIN redaction in events and traces, and a flag on prompt-injection patterns (logged and traced).

---

## Observability

Every turn (chat or voice) produces a `TurnTrace`. It is streamed to the UI as a `trace` event and saved to the `traces` table (`GET /api/sessions/{id}/traces`):

* **Spans** (waterfall in the drawer): each LLM step (model, attempt, TTFT, prompt preview, output preview, tokens, cost, finish reason, function calls), each tool (redacted arguments, outcome, retries, policy decision), retrieval (query, tier, band, top similarity, every hit with cosine and RRF), and the guardrail verdict.
* **Latency**: time to first token (chat) or end-of-speech → first audio (voice, `voice_response_latency_ms`), plus total.
* **Tokens and cost**: per model and in total, including thinking tokens and audio input/output tokens for Live, priced from `observability/pricing.py`. These are estimates based on public Gemini pricing (Nov 2026) and can be overridden with `PRICING_OVERRIDES`.
* Structured JSON logs (structlog) with a request ID per request, plus `/healthz` (liveness) and `/readyz` (database, KB chunk count, key configured).

---

## Session state and memory

* `sessions` row: verified customer, status (`active` / `escalated`), failed verification count, turn count.
* `messages`: every user and assistant turn from **both** modes (voice transcripts tagged `voice`), with guardrail metadata.
* On each chat turn the last `HISTORY_MAX_MESSAGES` are replayed as Gemini contents (voice lines prefixed `(via voice)`). Voice sessions replay the same history into Live. The frontend keeps the session ID in `localStorage` and restores the full timeline on reload.

---

## Running locally

Prerequisites: Docker, Python 3.12 with [uv](https://docs.astral.sh/uv/), Node 22, and a Gemini API key.

```bash
docker compose up -d db                     # pgvector on localhost:54329
cd backend
cp .env.example .env                        # set GEMINI_API_KEY
uv sync
uv run uvicorn app.main:app --port 8000     # migrates, seeds the mock CRM, embeds the KB
# new terminal
cd frontend && cp .env.example .env.local && npm install && npm run dev   # http://localhost:3000
```

Demo accounts are listed in the UI ("Demo accounts") and come from `backend/app/db/seed.py`:

| Customer | Email / PIN | Scenario |
|---|---|---|
| Alice Chen | alice@acme.io / 1234 | Enterprise, active: refunds up to $1,000 within 60 days |
| Bob Martinez | bob@brightlabs.dev / 2222 | Pro: $200 within 30 days, one invoice outside the window |
| Carol Singh | carol@example.com / 3333 | Starter: $50 within 14 days |
| Dan Okafor | dan@okafor.co / 4444 | Pro, **past due**: refunds blocked → escalation |
| Eve Laurent | eve@example.com / 5555 | Free tier |

### Tests and tooling

```bash
cd backend
uv run pytest -q                      # 20 unit + 9 integration tests (real Postgres, scripted fake LLM)
uv run mypy app                       # strict
uv run ruff check app tests
uv run python -m app.cli calibrate    # retrieval threshold calibration (real API)
uv run python scripts/guardrail_eval.py
uv run python scripts/chat_probe.py "I'm bob@brightlabs.dev pin 2222, refund INV-9003" --chaos '{"llm_fault":"rate_limit_once"}'
uv run python scripts/voice_probe.py q1.wav q2.wav --barge-in
```

The integration tests drive the **real** orchestrator, tools and database with a scripted model. They cover the 4-tool refund chain, the verification gate, policy blocks, memory across turns, retraction with escalation, low-confidence routing, a tool outage, an LLM outage (nothing persisted) and PIN lockout. CI (`.github/workflows/ci.yml`) runs lint, types and tests against a pgvector service container, plus the frontend lint and build.

---

## Deployment

The live demo runs as **two Vercel projects** from this repo plus **Supabase**:

| Project | Root directory | Config |
|---|---|---|
| `nimbus-support-web` | `frontend` | `frontend/vercel.json` (Next.js). Env: `NEXT_PUBLIC_API_URL` |
| `nimbus-support-api` | `backend` | `backend/vercel.json` (FastAPI, `app/main.py`, `maxDuration` 300 s). Env: see below |

1. **Supabase.** Create a project and copy the **transaction pooler** connection string (port 6543). URL-encode special characters in the password.
2. **Initialise the database once, from your machine.** This runs the schema (enables pgvector), seeds the mock CRM and embeds the KB:
   ```bash
   cd backend
   DATABASE_URL=<supabase-url> uv run python -m app.cli migrate
   DATABASE_URL=<supabase-url> uv run python -m app.cli seed
   DATABASE_URL=<supabase-url> uv run python -m app.cli ingest
   ```
3. **Backend project.** Set `GEMINI_API_KEY` (sensitive), `DATABASE_URL`, `CORS_ORIGINS=["https://<frontend>.vercel.app"]`, `ENV=production`, `AUTO_MIGRATE=false`, `AUTO_SEED=false`, `AUTO_INGEST=false`, `DB_POOL_MIN=0`, `DB_POOL_MAX=4` and `VOICE_MAX_SESSION_S=285`, then deploy.
4. **Frontend project.** Set `NEXT_PUBLIC_API_URL=https://<backend>.vercel.app` and deploy.

**Serverless notes:**
* Vercel caps function duration, and with it WebSocket connections, at **300 s on Hobby** (800 s on Pro). Voice sessions are capped at 285 s, and the UI offers Reconnect; conversation history is replayed, so context survives.
* Start-up migration, seeding and ingestion are disabled in production so cold starts stay fast. Schema changes are applied with the CLI.
* Each function instance has its own small asyncpg pool (`statement_cache_size=0` for the Supabase pooler).

**Container alternative.** `backend/Dockerfile` and `render.yaml` run the same API as one always-on instance (Render, Fly, Cloud Run). This is useful when voice sessions must outlast serverless duration limits.

---

## Key trade-offs

* **Agent model chosen by measurement.** The same 5-tool scenario (verify → overview → KB → blocked refund → escalation) was benchmarked twice per model through the real pipeline:

  | Agent model | Turn latency | Chain behaviour |
  |---|---|---|
  | gemini-3.8-flash (thinking `low`; `minimal` unsupported) | 22–28 s | correct |
  | gemini-3.5-flash (`low`) | 17–18 s | correct |
  | **gemini-3.5-flash (`minimal`)**, the default | **10–12 s** (single-tool turns 3–6 s) | correct, consistent |
  | gemini-3.6-flash (`minimal`) | 10–15 s | skipped the refund attempt in 1 of 2 runs |
  | gemini-3.5-flash-lite | 5–6 s | inconsistent (skipped refund or escalation) |

  3.5 Flash has a higher per-token price than 3.8 Flash's introductory rate, but minimal thinking produces far fewer billed thinking tokens, so a multi-tool turn still costs about $0.01–0.02 (shown per turn in the drawer) at half the latency. Less reasoning made the model more likely to drift out of scope, which is why the off-topic backstop exists.
* **Custom state machine instead of LangGraph.** Fewer abstractions, exact control over token streaming, retry and fallback, and guardrail placement, with every transition traced. The cost is re-implementing graph features (checkpointing, branching) that this agent doesn't need.
* **Stream first, verify after (chat).** Tokens stream immediately for latency, and a failed verification visibly retracts the draft. Buffering until verified would add about 1 s to every answer. Because low-confidence retrieval returns no passages, retractions are rare.
* **Voice guardrail is post-hoc.** Speech can't be unsaid, so voice relies on the pre-generation gate and flags and escalates afterwards.
* **Server-side voice proxy.** The key stays private and tools and policy are shared with chat, at the cost of one extra hop and server bandwidth (about 32 KB/s up, 48 KB/s down per call).
* **One database.** pgvector, sessions, traces and the mock CRM all live in Supabase Postgres: one connection pool, transactional refunds, simple operations. A dedicated vector database would only pay off at much larger corpus sizes.
* **In-process rate limiting.** On Vercel each function instance keeps its own counters, so limits are per instance (best-effort). That's fine for a demo. Production would move the limiter and voice concurrency guard to Redis (e.g. Upstash) behind the same interface.
* **Fail-open verifier.** Availability is chosen over strictness when the verifier is down, and the answer is visibly flagged. Set `GROUNDEDNESS_ENABLED` and adjust the code path for fail-closed if needed.
* **Cost estimates** come from a static price table (overridable), not from billing data.
