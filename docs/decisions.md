# MCSS Implementation Decisions

This document records significant implementation decisions made during development. Each entry includes the context, options considered, decision, and rationale.

---

## 2024-01-15: Repository Structure and Naming

**Context:** Establishing the new MCSS repository as an original implementation.

**Decision:**
- Repository named `mcss` at root level
- All internal identifiers use `MCSS` or `mcss` prefix
- No external project identifiers in code or product text
- Service directories: `api/`, `admin-web/`, `widget/`, `scripts/`, `docs/`

**Rationale:** Clear MCSS ownership and consistent product identity.

---

## 2024-01-15: Technology Stack

**Context:** Choosing the core technology stack for the MVP.

**Decisions:**
- **Backend:** Python 3.11, FastAPI, SQLAlchemy 2.0, Alembic, Pydantic v2, Poetry
- **Database:** PostgreSQL 16 with pgvector extension
- **Cache/jobs:** Redis 7 and Celery worker/Beat
- **Real time:** WuKongIM, with PostgreSQL as the source of truth
- **Admin UI:** React 19, TypeScript 5.9, Vite 7, React Router 7, Zustand, Tailwind CSS 4
- **Widget:** React 18, TypeScript 5.6, Vite 5, Zustand, Emotion, IIFE build, Shadow DOM mounting
- **Auth:** JWT (access + refresh), Argon2id password hashing
- **Real-time:** WuKongIM for delivery; PostgreSQL remains the source of truth
- **LLM:** OpenAI-compatible Chat Completions API behind provider adapter
- **Embeddings:** OpenAI-compatible embeddings API, 1536 dimensions default
- **Deployment:** Docker Compose, Caddy for production reverse proxy

**Rationale:** Modern, well-supported stack with durable job processing and real-time delivery while retaining a simple source of truth.

---

## 2024-01-15: Database Design

**Context:** Designing the PostgreSQL schema for conversations, knowledge base, and audit.

**Decisions:**
- UUID primary keys using `gen_random_uuid()`
- `timestamptz` for all timestamps (UTC)
- `pgcrypto` and `vector` extensions enabled
- Enum types for status fields
- HNSW cosine-distance index on `knowledge_chunks.embedding`
- Single-row `widget_settings` table
- Immutable `audit_events` table (no UPDATE/DELETE)

**Rationale:** UUIDs prevent enumeration, pgvector enables semantic search, enums enforce data integrity.

---

## 2024-01-15: Authentication Design

**Context:** Designing auth for both staff (admin web) and visitors (widget).

**Decisions:**
- Staff: Email/password → 15min access token + 7day refresh token (HttpOnly cookie)
- Visitor: Anonymous session → 24h visitor JWT scoped to conversation
- Refresh token rotation on use
- Single admin bootstrapped via `scripts/seed-admin.py`
- Admin can create/deactivate staff; staff cannot create admins
- Rate limit: 5 login attempts / 15 min / email+IP

**Rationale:** Short-lived access tokens limit exposure; HttpOnly cookies prevent XSS theft; visitor tokens isolate conversations.

---

## 2024-01-15: API Contract

**Context:** Defining the HTTP API surface for both admin and widget consumers.

**Decisions:**
- All responses JSON with consistent error format
- `/api/v1/` prefix for all endpoints
- Cursor-based pagination (not offset)
- UTC ISO-8601 timestamps
- OpenAPI at `/openapi.json` (dev only)
- Error codes: `VALIDATION_ERROR`, `UNAUTHORIZED`, `FORBIDDEN`, `NOT_FOUND`, `RATE_LIMITED`, `INTERNAL_ERROR`

**Rationale:** Predictable, versioned API with standard pagination and error handling.

---

## 2024-01-15: Assistant Workflow

**Context:** Defining how the AI assistant processes visitor messages.

**Decisions:**
- State machine: validate → classify → retrieve → answer/handoff/clarify
- Classifier: JSON output, temperature 0, 120 token limit
- Actions: `answer`, `clarify`, `handoff`
- Retrieval: top 5 chunks from published docs, cosine ≥ 0.72, max 4500 chars context
- Answer: structured JSON with `source_ids`, `confidence`, 1-800 chars
- Verification: citations must match retrieved chunks, confidence 0-1
- Handoff triggers: explicit request, unsafe domain, low retrieval, low confidence, verification failure, provider failure
- Never call LLM if conversation is `staff_active` or `closed`

**Rationale:** Deterministic, auditable pipeline with safe fallbacks. Handoff is the default for uncertainty.

---

## 2024-01-15: Knowledge Base Ingestion

**Context:** How documents are processed for retrieval.

**Decisions:**
- Accept: `.txt`, `.md`, text-based `.pdf`
- Reject: encrypted/scanned PDFs
- Chunking: ~700 tokens, 100 token overlap, heading-aware
- Embedding: after document save, draft until all chunks embedded
- Publish: only when embeddings complete
- Archive: published → archived (read-only), draft can delete

**Rationale:** Synchronous ingestion for ≤5MB documents; publish gate ensures retrieval quality.

---

## 2024-01-15: Widget Architecture

**Context:** Building an embeddable widget that works on any website.

**Decisions:**
- IIFE bundle: `mcss-widget.js` exposing `window.MCSSWidget.init()`
- Shadow DOM mounting to isolate CSS
- Idempotent `init()` call
- Config: `apiBaseUrl` (required), `position`, `accentColor`, `title`
- Visitor ID in localStorage (`mcss_visitor_id`), JWT in memory only
- Polling: 3s when open and visible
- Message rendering: plain text, newlines preserved, HTTPS URLs linkified

**Rationale:** Shadow DOM prevents host CSS conflicts; IIFE is simple to embed; memory-only JWT improves security.

---

## 2024-01-15: Configuration Management

**Context:** Managing environment-specific configuration.

**Decisions:**
- `.env.example` committed, `.env` gitignored
- Pydantic Settings with `MCSS_` prefix
- Validation at startup
- Production rejects weak JWT secret and wildcard CORS
- Secret fields (`SECRET`, `PASSWORD`, `TOKEN`, `API_KEY`) never logged
- Fake provider mode: `MCSS_LLM_BASE_URL=fake` or `MCSS_ENV=test`

**Rationale:** Clear separation of config from code; fail-fast validation; CI-safe fake provider.

---

## 2024-01-15: Security Headers

**Context:** Hardening HTTP responses.

**Decisions:**
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY` (admin routes)
- `Content-Security-Policy`: restrictive
- `Referrer-Policy: no-referrer`
- `X-Request-ID` on every response
- CORS: exact origins only, credentials allowed

**Rationale:** Defense in depth against common web vulnerabilities.

---

## 2024-01-15: Rate Limiting

**Context:** Preventing abuse.

**Decisions:**
- Login: 5 attempts / 15 min / email+IP
- Widget session create: 30 / hour / origin
- Visitor messages: 10 / minute / visitor
- Document upload: 5 MB max
- JSON body: 64 KB max

**Rationale:** Reasonable limits for legitimate use; prevents credential stuffing and spam.

---

## 2024-01-15: Provider Circuit Breaker

**Context:** Handling LLM provider failures gracefully.

**Decisions:**
- Track consecutive failures per provider
- After 3 failures in 60 seconds → open circuit for 60 seconds
- During open circuit: immediate handoff instead of retry
- Timeouts: classification 10s, answer 20s, embeddings 15s
- Retry idempotent calls once with exponential backoff

**Rationale:** Prevents cascade failures; handoff is safe fallback.

---

## 2024-01-15: Testing Strategy

**Context:** Ensuring quality without paid LLM calls in CI.

**Decisions:**
- Fake provider implementation for tests
- `MCSS_ENV=test` or `MCSS_LLM_BASE_URL=fake` enables fake mode
- Unit tests: auth, JWT, RBAC, validation, chunking, retrieval, handoff rules, circuit breaker
- Integration tests: ephemeral PostgreSQL with pgvector
- Frontend: type-check + build + component tests
- E2E: Playwright smoke tests for core flows
- CI runs only fake-provider tests

**Rationale:** Fast, deterministic, free CI; real provider tested manually before release.

---

## 2024-01-15: Development Scripts

**Context:** Safe, reproducible development workflow.

**Decisions:**
- `dev-up.sh`: starts compose, waits for health, never resets volumes
- `dev-down.sh`: stops compose, preserves volumes
- `smoke-test.sh`: verifies health, ready, headers, no secret leakage
- `seed-admin.py`: bootstraps admin, refuses to overwrite
- No `make clean`, `down -v`, or secret regeneration in scripts

**Rationale:** Developer-friendly; preserves data; safe for daily use.

---

## 2024-01-15: Documentation Structure

**Context:** Required documentation files.

**Decisions:**
- `architecture.md`: system diagram, service boundaries, data flow
- `api.md`: complete endpoint reference with examples
- `threat-model.md`: STRIDE analysis, mitigations, residual risks
- `runbook.md`: operations, deployment, incidents, backup
- `decisions.md`: this file, append-only log

**Rationale:** Complete operational documentation from day one.

---

## 2024-01-15: Branding and UI Text

**Context:** Establishing MCSS visual identity.

**Decisions:**
- Product name: "MCSS — Multi-Agent Customer Support System"
- Admin title: "MCSS — Support Console"
- Widget title: "MCSS Support"
- Tagline: "AI-Powered Customer Support"
- CSS text wordmark "MCSS" (no logo assets required)
- English only (`en.json` locale files)
- WCAG AA contrast, keyboard navigation, semantic HTML

**Rationale:** Consistent, accessible, original branding without design dependencies.

---

## 2024-01-15: Conversation Retention

**Context:** Data lifecycle for conversations.

**Decisions:**
- 30-day default retention documented in code
- No automatic deletion in MVP (manual ops task)
- Audit events retained indefinitely
- Knowledge base documents retained until explicitly archived/deleted

**Rationale:** Compliance-friendly default; explicit deletion avoids accidental loss.

---

## 2024-01-15: File Upload Handling

**Context:** Knowledge base document uploads.

**Decisions:**
- Max 5 MB per document
- Synchronous processing (no background worker in MVP)
- Text/Markdown: direct text extraction
- PDF: text extraction, reject encrypted/scanned
- Chunking and embedding happen on publish

**Rationale:** Simplicity for MVP; 5MB covers typical docs; sync processing avoids worker complexity.

---

## 2024-01-15: CORS Policy

**Context:** Cross-origin requests from admin web and widget.

**Decisions:**
- Exact origins in `MCSS_CORS_ORIGINS` (comma-separated)
- `allow_credentials=true`
- Never `*` with credentials
- Widget `Origin` header validated against `widget_settings.allowed_origin`
- Production requires explicit origins (no localhost)

**Rationale:** Prevents CSRF and unauthorized embedding.

---

## 2026-10-30: Phase 0 Target Architecture Reconciliation (v1.1)

**Context:** Reconciling the target Docker Compose stack against the v1.1 master specification. The master specification explicitly excludes Redis, WuKongIM, and Celery worker/Beat from the MVP architecture, using REST polling every 3 seconds and synchronous bounded ingestion.

**Decisions:**
- Reconciled `compose.yaml` to run core MVP services by default: `postgres` (host port 55432), `api`, `admin-web`, `widget`, and `e2e`.
- Deferred non-MVP services (`redis`, `wukongim`, `worker`, `beat`) using Docker Compose profile `expanded` (`profiles: ["expanded"]`).
- `api` service dependency updated to depend strictly on `postgres` (healthy) without blocking on deferred services.
- Persistent volumes (`postgres-data`, `redis-data`, `wukongim-data`) are retained untouched to avoid any data loss.
- Rollback path: run `docker compose --profile expanded up -d` to spin up the expanded stack at any time.

**Rationale:** Aligns development and test environments with the minimal v1.1 contract while safely preserving existing volumes and providing an immediate non-destructive rollback.

---

## 2026-10-30: Inert Scaffolding Retention Approval

**Context:** Phase 0 boundary specifies "no business tables, UI workflows, or widget behavior". The repository contains forward-looking business model classes (`api/app/models/`) and admin/widget route pages (`admin-web/src/pages/`, `widget/src/Widget.tsx`).

**Decisions:**
- Approved retaining inert scaffolding ahead of Phase 1 and Phase 2 implementations.
- Alembic migration `0001_phase_0_extensions.py` remains the sole applied migration in Phase 0; no business tables are migrated to PostgreSQL.
- API routing in `api/app/main.py` is restricted to `/healthz` and `/readyz`.

**Rationale:** Deleting scaffolded models and components only to recreate them in Phase 1 would create unnecessary churn without technical benefit. Keeping them unmigrated and unrouted strictly honors the Phase 0 operational boundary.

---

## 2026-10-30: SQLAlchemy Reserved Name and Pytest Warning Resolution

**Context:** SQLAlchemy emits warnings when declarative model attributes collide with `Base.metadata`. Deprecated custom pytest event loops also emit warnings.

**Decisions:**
- Mapped JSON metadata columns as `metadata_json = mapped_column("metadata", JSONB, default=dict, nullable=False)` in `audit_event.py` and `message.py`.
- Configured `asyncio_default_fixture_loop_scope = "function"` in `api/pyproject.toml` and removed custom event loop fixtures from `api/tests/conftest.py`.

**Rationale:** Eliminates warnings cleanly without obscuring diagnostic logging.

---

## 2026-11-01: In-Memory Sliding Window Rate Limiter for Core Gateway Routes

**Context:** The API contract (`docs/api.md`) defines rate limits on auth login (5/15m), widget session creation (30/h), and visitor messages (10/m). External dependencies like Redis are deferred from the minimal MVP stack.

**Decisions:**
- Implemented a thread-safe sliding window rate limiter (`api/app/common/rate_limit.py`) maintaining in-memory timestamps per key with periodic pruning.
- Applied checks directly in `routes/auth.py` and `routes/widget.py`.
- Made rate limiting configurable via `MCSS_RATE_LIMIT_ENABLED` setting.

**Rationale:** Fulfills security contracts and mitigates brute force/spam without introducing non-MVP distributed cache infrastructure.

---

## 2026-11-01: Vector Retrieval Query Optimization and Two-Phase Text Loading

**Context:** Vector retrieval in `retrieval_service.py` joined `KnowledgeDocument.content` (up to 5 MB per doc) and omitted a SQL `LIMIT`, forcing sequential table scans on PostgreSQL pgvector HNSW index and streaming massive duplicate text into Python memory.

**Decisions:**
- Added `.limit(50)` to the cosine distance query in `retrieval_service.py` to activate HNSW index graph traversal.
- Removed `KnowledgeDocument.content` from candidate chunk scanning; parent document content is retrieved only for the final <= 5 matched chunks.

**Rationale:** Restores sub-millisecond vector retrieval and prevents process memory spikes.

---

## 2026-11-01: Incremental Cursor Polling Contract for Live Surfaces

**Context:** Inbox and Widget polled with `{ limit: 50 }` without cursor, causing chats with >50 messages to stop displaying new messages. Background polling also wiped out paginated conversations in the Admin Inbox.

**Decisions:**
- Both `Inbox.tsx` and `Widget.tsx` track the latest message cursor (`messageCursorRef`) and request only incremental updates.
- Conversation list polling performs state reconciliation by ID, updating existing conversations and prepending new ones without discarding paginated history.

**Rationale:** Ensures chat flows smoothly past 50 messages and preserves operator navigation state during background polling.

---

## 2026-11-01: Constant-Time Argon2id Verification for Unknown Staff Accounts

**Context:** `auth_service.py` attempted dummy password verification using an invalid base64 hash, crashing in ~0.2ms and enabling user enumeration via timing attacks against registered staff emails (~90ms).

**Decisions:**
- Precomputed a valid Argon2id hash `DUMMY_ARGON2_HASH` in `api/app/security/password.py`.
- Non-existent user logins run full Argon2id key derivation identically to existing users.

**Rationale:** Satisfies the security requirement of indistinguishable timing between existing and non-existing accounts.

---

## 2026-11-01: Decoupling Database Row Locks from Assistant Network I/O

**Context:** `handle_assistant_pipeline` held PostgreSQL `SELECT ... FOR UPDATE` row locks across remote LLM and retrieval calls (10–20 seconds), blocking staff take-over (`POST /conversations/{id}/takeover`) and exhausting database connection pools.

**Decisions:**
- Decoupled remote I/O from database transactions: initial status checks read without row locks.
- Serialized assistant pipeline execution per conversation using an in-memory `asyncio.Lock` registry (`_conversation_locks`).
- Performed transactional re-check with `SELECT ... FOR UPDATE` exclusively at Step 6 immediately before saving assistant or handoff messages.

**Rationale:** Allows immediate, non-blocking staff take-over while reliably suppressing in-flight AI answers when staff claims a conversation.

---

## 2026-11-01: Circuit Breaker Error Discrimination and Half-Open Probe Serialization

**Context:** The circuit breaker previously tripped on client 400 Bad Request or validation errors, and permitted concurrent calls during `HALF_OPEN` state.

**Decisions:**
- Restricted failure counter increments to transient/retryable errors (`is_retryable_error`).
- Added `_probing: bool` state enforcing strictly one probe call through during `HALF_OPEN`; concurrent calls raise `ProviderCircuitOpenError` until the probe completes.

**Rationale:** Prevents false-positive breaker trips from client mistakes and protects recovering upstream providers from concurrent request surges.

---

## 2026-11-01: Graceful Handoff Fallback on Knowledge Retrieval Failures

**Context:** Unhandled exceptions during `retrieve_knowledge_chunks` caused the assistant pipeline to crash silently, leaving visitors without an answer or handoff ticket.

**Decisions:**
- Wrapped knowledge retrieval and generation in defensive error-handling blocks.
- On retrieval or provider failure, the pipeline logs the incident and immediately creates a `HandoffReason.unsupported_request` ticket and posts `HANDOFF_MESSAGE_TEXT` (`"I’m connecting you with our support team so they can help further."`).

**Rationale:** Satisfies Master Specification 8.2 ensuring visitors are never abandoned when infrastructure components fail.

---

## 2026-11-01: MCSS Enterprise Multi-Agent Platform Extension (ADR-005)

**Context:** The user expanded the MCSS roadmap to Phase 5 ("MCSS Enterprise — Universal Multi-Agent Customer Support Platform"), transitioning from a single-tenant knowledge FAQ assistant into an extensible multi-agent support platform capable of retrieving live customer-specific business data (orders, refunds, subscriptions, claims) and executing controlled actions.

**Decisions:**
- Sequence implementation into 9 dependency-ordered stages (Stages A through I): Reconcile Foundation $\rightarrow$ Customer Identity $\rightarrow$ Tool Gateway $\rightarrow$ Multi-Agent Live Answers $\rightarrow$ Production Connectors $\rightarrow$ Controlled Actions $\rightarrow$ Enterprise Tenancy $\rightarrow$ Omnichannel $\rightarrow$ Production Readiness.
- Preserve backward-compatibility: Anonymous visitor chat (Level 0) continues to function seamlessly for public FAQ answers.
- Forbid arbitrary LLM-generated SQL: Customer-specific data access is routed exclusively through a server-side Tool Gateway that injects server-verified customer IDs, preventing cross-customer data leakage.
- Generalize evidence citations: Expand the generator citation model from knowledge chunk UUIDs to polymorphic `EvidenceRef` references (knowledge chunks + verified business entity records).
- Keep single-tenant core stable while building customer identity and tool integrations, deferring multi-tenancy database changes to Stage G.

**Rationale:** Allows incremental delivery and rigorous security/isolation testing without breaking the existing verified single-tenant codebase.

---

## 2026-11-03: Risk-Based Resolution Reflection

**Context:** Phase 6F adds multi-agent evidence aggregation and synthesis. Some
financial, policy, multi-intent, and action-proposal resolutions need a second
quality review beyond deterministic fact validation.

**Decision:** Add a server-selected, tool-less `ResolutionCriticAgent` after
initial deterministic validation. Its strict JSON verdict is advisory and
bounded to one revision and one approved supplemental investigation. The final
response always undergoes deterministic policy validation; invalid critic
output, timeout, or provider failure fails closed for mandatory-review cases.

**Rationale:** This catches completeness, uncertainty, policy-interpretation,
and pending-action language issues without granting an LLM data access, action
authority, or a way to bypass existing tenant, identity, policy, and human
takeover controls. The feature is reversible with `MCSS_REFLECTION_ENABLED`.

---

*Add new decisions above this line. Keep chronological order.*
