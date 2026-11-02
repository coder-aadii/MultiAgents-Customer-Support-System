# MCSS — Multi-Agent Customer Support System

A single-tenant customer support application with an AI-powered chat widget, admin console, and knowledge base.

## Quick Start

```bash
# Clone and enter directory
cd mcss

# Copy environment template
cp .env.example .env

# Start development environment
./scripts/dev-up.sh

# Run smoke tests
./scripts/smoke-test.sh
```

### Services

| Service | URL | Description |
|---------|-----|-------------|
| API | http://localhost:8000 | FastAPI backend |
| Admin Web | http://localhost:5173 | React admin console |
| Widget Demo | http://localhost:5174 | Embeddable widget demo |
| PostgreSQL | localhost:55432 | Database (internal port 5432) |

### Health Checks

```bash
curl http://localhost:8000/healthz  # {"status":"ok"}
curl http://localhost:8000/readyz   # {"status":"ok"} (verifies DB)
```

## Architecture

```
Visitor Browser ── Widget (IIFE) ──┐
                                   ├── API (FastAPI) ── PostgreSQL + pgvector
Admin Browser ── React Console ────┘        │
                                            └── LLM + Embedding Providers
```

- **API**: FastAPI, Python 3.11, SQLAlchemy 2, Alembic, Poetry
- **Admin Web**: React 19, TypeScript, Vite, React Router, Zustand, Tailwind CSS 4
- **Widget**: React 18, TypeScript, Vite, Shadow DOM, IIFE bundle
- **Database**: PostgreSQL 16 with pgvector for semantic search

## MCSS Lifecycle Phases & Roadmap Structure

MCSS follows a phased, dependency-ordered architecture:

```mermaid
flowchart TD
    subgraph CoreMVP["Core Foundation & Controlled Assistant (Phases 0–4)"]
        P0["Phase 0: Foundation & Environment"] --> P1["Phase 1: Identity & Conversation Data"]
        P1 --> P2["Phase 2: Conversation Surfaces (Widget + Admin)"]
        P2 --> P3["Phase 3: Knowledge Base & Vector Retrieval"]
        P3 --> P4["Phase 4: Controlled Assistant & Human Takeover"]
    end

    subgraph Enterprise["Enterprise Platform (Phase 5)"]
        CoreMVP --> P5A["Phase 5A: Read-Only Business Data Platform"]
        P5A --> P5B["Phase 5B: Real Enterprise Data Connectors"]
        P5B --> P5C["Phase 5C: Controlled Business Actions"]
        P5C --> P5D["Phase 5D: Enterprise Multi-Tenancy & Identity"]
        P5D --> P5E["Phase 5E: Omnichannel Customer Support"]
        P5E --> P5F["Phase 5F: Advanced Support Operations"]
    end

    subgraph Hardening["Production Release (Phase 6)"]
        Enterprise --> P6["Phase 6: Hardening, Security Verification & Release"]
    end
```

- **Phases 0–4 (Core Foundation & Controlled Assistant):** Complete single-tenant support MVP with knowledge RAG, PostgreSQL 16 + pgvector, embeddable widget, admin inbox, and safe staff takeover. (Migrations `0001`–`0003`, 97 tests).
- **Phase 5 (Enterprise Capabilities & Multi-Agent Roadmap):**
  - **Master Architecture Roadmap:** [`local/MCSS-From-Scratch-docs/phase-5-enterprise.md`](local/MCSS-From-Scratch-docs/phase-5-enterprise.md)
  - **Implementation Gap Analysis:** [`local/MCSS-From-Scratch-docs/phase-5-gap-analysis.md`](local/MCSS-From-Scratch-docs/phase-5-gap-analysis.md)
  - **Sub-phase 5A (Read-Only Business Data Support):** [`local/MCSS-From-Scratch-docs/phase-5a-specification.md`](local/MCSS-From-Scratch-docs/phase-5a-specification.md) *(Completed & Verified)*
    - `5A.1`: Customer Identity Foundation & Organization Context *(Completed — migration `0004_customer_identity`, 19 tests)*
    - `5A.2`: Typed Evidence References & Schema Generalization *(Completed — migration `0005_evidence_records`, 8 tests)*
    - `5A.3`: Universal Tool Gateway & Mock Business Connectors *(Completed — 14 tests)*
    - `5A.4`: Durable Asynchronous Message Processing (`assistant_jobs`) *(Completed — migration `0006_assistant_jobs`, 6 tests)*
    - `5A.5`: Controlled Fact Templates, Triage Routing & Policy Gate *(Completed — 9 tests)*
    - `5A.6`: Security Evaluation, Benchmarking & End-to-End Testing *(Completed — 30 benchmark evals, 100% pass rate)*
  - **Sub-phase 5B (Real Enterprise Data Connectors):** [`local/MCSS-From-Scratch-docs/phase-5b-enterprise-connectors.md`](local/MCSS-From-Scratch-docs/phase-5b-enterprise-connectors.md) *(Completed & Remediated — migration `0007_enterprise_connectors`, 22 connector tests, architectural adapter bridge)*
  - **Sub-phase 5C (Controlled Business Actions):** [`local/MCSS-From-Scratch-docs/phase-5c-controlled-actions.md`](local/MCSS-From-Scratch-docs/phase-5c-controlled-actions.md) *(Next Implementation Target)*
  - **Sub-phase 5D (Enterprise Multi-Tenancy & Identity):** [`local/MCSS-From-Scratch-docs/phase-5d-enterprise-tenancy.md`](local/MCSS-From-Scratch-docs/phase-5d-enterprise-tenancy.md) *(Planned)*
  - **Sub-phase 5E (Omnichannel Customer Support):** [`local/MCSS-From-Scratch-docs/phase-5e-omnichannel.md`](local/MCSS-From-Scratch-docs/phase-5e-omnichannel.md) *(Planned)*
  - **Sub-phase 5F (Advanced Support Operations):** [`local/MCSS-From-Scratch-docs/phase-5f-support-operations.md`](local/MCSS-From-Scratch-docs/phase-5f-support-operations.md) *(Planned)*
  - **Strict Milestone Rule:** Each sub-phase has a dedicated executable specification. Implementation proceeds strictly one approved milestone at a time; no code is written directly from high-level roadmap descriptions.
- **Phase 6 (Hardening, Security Verification & Release):** [`local/MCSS-From-Scratch-docs/phase-6-hardening-release.md`](local/MCSS-From-Scratch-docs/phase-6-hardening-release.md) — Comprehensive security audit, tool authorization gates, prompt injection fencing, and production release rehearsal.

## E-commerce Integration & How It Works

MCSS functions as an embeddable, AI-grounded customer support plugin for any e-commerce platform (Shopify, WooCommerce, Next.js, or custom storefronts):

```mermaid
flowchart TD
    Customer["Store Visitor on Widget"] --> Triage{"MCSS Triage Agent"}

    Triage -->|"FAQ / Policy Question"| RAG["Vector Retrieval (pgvector)"]
    RAG --> AnswerGen["Grounded Answer Generator"]
    AnswerGen --> WidgetAnswer["Visitor receives cited answer + source chips"]

    Triage -->|"Order, Refund, Payment, or Human Request"| Handoff["Safe Handoff Protocol"]
    Handoff --> Queue["Ticket tagged 'Waiting for Staff'"]
    Queue --> AdminInbox["Admin Inbox Dashboard"]
    AdminInbox --> StaffTakeover["Staff clicks 'Take over' & resolves"]
```

### 1. Storefront Embedding (The Customer Surface)

Embed the lightweight widget script in your storefront's layout or theme:

```html
<!-- Load MCSS Widget from your hosted support domain -->
<script src="https://support.yourstore.com/mcss-widget.js"></script>
<script>
  window.MCSSWidget.init({
    apiBaseUrl: "https://support.yourstore.com/api/v1",
    title: "Store Support",
    accentColor: "#1e40af", // Match store brand color
    position: "bottom-right"
  });
</script>
```

- **Shadow DOM Isolation:** The widget mounts inside an open Shadow DOM root to guarantee zero CSS collision between the store theme and the chat UI.
- **REST Polling & Low Overhead:** Uses standard HTTP polling every 3 seconds while active and open; halts completely when closed/hidden. Session credentials are kept in memory only (no tracking cookies).

### 2. Knowledge Base Ingestion (Store Policies)

Support staff upload store documentation via the Admin Web (`/knowledge`):
- **Shipping & Delivery:** Regional transit times, international duties, carrier tiers.
- **Returns & Exchanges:** Return windows (e.g., 30-day policy), condition requirements, store credit options.
- **Sizing & Care Guides:** Product dimensions, warranty terms, washing instructions.

Uploaded `.txt`, `.md`, or text-based `.pdf` files are chunked into ~700-token units, embedded into 1536-dimensional unit vectors, and indexed with PostgreSQL pgvector (HNSW cosine similarity).

### 3. Automated Grounded Answers vs Safe Human Handoff

- **Automated FAQ Answers:** General policy questions (*"What are your international shipping rates?"*) trigger retrieval of top published chunks. The generator returns an answer strictly grounded in the retrieved text with verifiable source citation chips.
- **Zero Hallucination Order/Refund Handoff:** Account queries, order lookups (*"Where is order #12345?"*), payment issues, refund requests, or explicit human requests (*"Talk to an agent"*) bypass the generator and trigger an immediate safe handoff:
  > *"I’m connecting you with our support team so they can help further."*

### 4. Staff Takeover & Resolution

1. Tickets requiring human action appear in the **"Waiting for staff"** queue in the Admin Inbox (`/inbox`).
2. An agent clicks **"Take over"**:
   - Status updates to `staff_active`.
   - **Safety Invariant:** All further AI model calls are permanently silenced for this ticket to prevent conflicting automated responses.
3. The agent looks up the order in your e-commerce platform (Shopify/WooCommerce admin) and replies directly in the chat.
4. The visitor receives real-time staff replies labeled **"Support Agent"**.

## Development

### Prerequisites

- Docker and Docker Compose
- Node.js 22+ (for local frontend development)
- Python 3.11+ (for local backend development)

### Commands

```bash
# Start all services
./scripts/dev-up.sh

# Stop services (preserves data)
./scripts/dev-down.sh

# Run smoke tests
./scripts/smoke-test.sh

# Seed admin user
docker compose exec api python scripts/seed-admin.py

# View logs
docker compose logs -f api

# Run backend tests
docker compose exec api pytest -q

# Run frontend type-check
docker compose exec admin-web npm run type-check
docker compose exec widget npm run type-check

# Build frontend
docker compose exec admin-web npm run build
docker compose exec widget npm run build
```

### Environment Variables

Copy `.env.example` to `.env` and configure:

| Variable | Description | Default |
|----------|-------------|---------|
| `MCSS_ENV` | Environment (development/test/production) | development |
| `MCSS_DATABASE_URL` | PostgreSQL connection string | postgresql+psycopg://mcss:change-me@postgres:5432/mcss |
| `MCSS_JWT_SECRET` | JWT signing key (32+ chars) | **required** |
| `MCSS_CORS_ORIGINS` | Comma-separated allowed origins | http://localhost:5173,http://localhost:5174 |
| `MCSS_WIDGET_ALLOWED_ORIGIN` | Widget embedding origin | http://localhost:5174 |
| `MCSS_LLM_BASE_URL` | LLM provider base URL (or "fake") | fake |
| `MCSS_LLM_API_KEY` | LLM API key | |
| `MCSS_LLM_CHAT_MODEL` | Chat model name | |
| `MCSS_EMBEDDING_MODEL` | Embedding model name | |
| `MCSS_EMBEDDING_DIMENSIONS` | Embedding dimensions | 1536 |
| `MCSS_RETRIEVAL_MIN_SCORE` | Min cosine similarity for retrieval | 0.72 |
| `MCSS_ADMIN_EMAIL` | Bootstrap admin email | admin@example.com |
| `MCSS_ADMIN_PASSWORD` | Bootstrap admin password | **required** |

**Production requirements:**
- `MCSS_JWT_SECRET` ≥ 32 random characters
- `MCSS_CORS_ORIGINS` explicit domains (no wildcards)
- `MCSS_LLM_BASE_URL` real provider (not "fake")
- `MCSS_WIDGET_ALLOWED_ORIGIN` production widget domain

## Testing

### Backend

```bash
# Unit and integration tests (fake provider)
docker compose exec api pytest -q

# With coverage
docker compose exec api pytest --cov=app --cov-report=term-missing
```

### Frontend

```bash
# Type checking
docker compose exec admin-web npm run type-check
docker compose exec widget npm run type-check

# Production build
docker compose exec admin-web npm run build
docker compose exec widget npm run build
```

### E2E (Playwright)

```bash
# Install Playwright
docker compose exec admin-web npx playwright install --with-deps

# Run tests
docker compose exec admin-web npm run test:e2e
```

## Deployment

### Production Compose

```bash
# Create production environment file
cp .env.example .env.production
# Edit with production values

# Deploy with Caddy reverse proxy
docker compose -f compose.yaml -f compose.prod.yaml up -d
```

See `docs/runbook.md` for complete deployment, monitoring, and incident response procedures.

## Documentation

- [Architecture](docs/architecture.md) — System diagram, service boundaries, data flow
- [API Reference](docs/api.md) — Complete endpoint documentation
- [Threat Model](docs/threat-model.md) — STRIDE analysis and mitigations
- [Runbook](docs/runbook.md) — Operations, deployment, incidents
- [Decisions](docs/decisions.md) — Implementation decision log
- [Master Specification](local/MCSS-From-Scratch-docs/00-master-specification.md) — Authoritative baseline specification
- [Phase 5 Enterprise Roadmap](local/MCSS-From-Scratch-docs/phase-5-enterprise.md) — Master enterprise vision & architecture
- [Phase 5A Specification](local/MCSS-From-Scratch-docs/phase-5a-specification.md) — Executable milestone specification (Universal Business Data)
- [Phase 6 Hardening & Release](local/MCSS-From-Scratch-docs/phase-6-hardening-release.md) — Release gate and security verification

## Project Structure

```
mcss/
├── AGENTS.md              # Agent instructions
├── LICENSE                # Apache-2.0
├── README.md              # This file
├── .env.example           # Environment template
├── .gitignore
├── compose.yaml           # Docker Compose (dev)
├── docs/
│   ├── architecture.md
│   ├── api.md
│   ├── threat-model.md
│   ├── runbook.md
│   └── decisions.md
├── api/
│   ├── Dockerfile
│   ├── pyproject.toml
│   ├── alembic.ini
│   ├── alembic/
│   │   ├── env.py
│   │   └── versions/
│   ├── app/
│   │   ├── main.py
│   │   ├── config.py
│   │   ├── database.py
│   │   ├── dependencies.py
│   │   ├── api/routes/
│   │   ├── models/
│   │   ├── schemas/
│   │   ├── services/
│   │   ├── providers/
│   │   ├── prompts/
│   │   └── security/
│   └── tests/
├── admin-web/
│   ├── Dockerfile
│   ├── package.json
│   ├── tsconfig.json
│   ├── vite.config.ts
│   ├── tailwind.config.js
│   ├── postcss.config.js
│   ├── index.html
│   └── src/
├── widget/
│   ├── package.json
│   ├── tsconfig.json
│   ├── vite.config.ts
│   ├── tailwind.config.js
│   ├── postcss.config.js
│   ├── demo/
│   └── src/
└── scripts/
    ├── dev-up.sh
    ├── dev-down.sh
    ├── seed-admin.py
    └── smoke-test.sh
```

## License

Apache-2.0 — see [LICENSE](LICENSE) for details.

## Security

For security issues, see [threat-model.md](docs/threat-model.md) and contact security@example.com.
