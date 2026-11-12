# MCSS — Multi-Agent Customer Support System

<p align="center">
  <strong>Open-source, enterprise-grade AI customer support platform with multi-agent orchestration, pgvector RAG, secure enterprise connectors, controlled action execution, and seamless human agent collaboration.</strong>
</p>

<p align="center">
  <a href="docs/architecture.md">Architecture</a> •
  <a href="docs/api.md">API Reference</a> •
  <a href="docs/threat-model.md">Threat Model</a> •
  <a href="docs/runbook.md">Operations Runbook</a> •
  <a href="docs/decisions.md">Architecture Decisions</a>
</p>

---

## MCSS Introduction

**MCSS (Multi-Agent Customer Support System)** is an open-source, full-stack customer support platform designed to help modern enterprises deploy autonomous AI agent teams safely alongside human support representatives.

MCSS bridges customer-facing touchpoints (embeddable web widgets, email, messaging channels) with internal enterprise systems (orders, inventory, CRM, databases). Built with strict safety guarantees, MCSS provides grounded vector retrieval (RAG) to prevent hallucinations, a two-phase approval protocol for high-impact actions, and guaranteed human agent takeover.

---

## 🚀 Quick Start

### Development Environment

Clone the repository, configure your environment, and start all services using the provided startup script:

```bash
# Clone the repository
git clone https://github.com/your-org/mcss.git
cd mcss

# Copy environment configuration
cp .env.example .env

# Start the full stack (API, Admin Web, Widget, and PostgreSQL)
./scripts/dev-up.sh
```

Alternatively, start services directly with Docker Compose:

```bash
# Start core development services
docker compose up --build -d

# Seed initial admin user
docker compose exec api python scripts/seed-admin.py
```

### Useful Variants

```bash
# Run with expanded background workers (Redis + Celery worker & beat)
docker compose --profile expanded up -d

# Stop services preserving database state
./scripts/dev-down.sh

# Run end-to-end smoke verification
./scripts/smoke-test.sh
```

### Service Endpoints

| Service | Endpoint | Description |
|:---|:---|:---|
| **API** | `http://localhost:8000` | FastAPI backend & OpenAPI documentation (`/docs`) |
| **Headless API** | `http://localhost:8000/api/v2/headless` | Headless conversation API with SSE token streaming |
| **Admin Web** | `http://localhost:5173` | React 19 agent workspace, connectors UI & audit visualizer |
| **Widget Demo** | `http://localhost:5174` | Embeddable widget testbed & multi-industry showcase |
| **PostgreSQL** | `localhost:55432` | PostgreSQL 16 database with `pgvector` extension |

### Health Verification

```bash
curl -fsS http://localhost:8000/healthz   # {"status":"ok"}
curl -fsS http://localhost:8000/readyz    # {"status":"ok"} (verifies database connectivity)
```

---

## ✨ Features

### 🤖 Universal AI Agent Orchestration & Grounded Triage
- **Schema-Driven Dynamic Supervisor** — Plans multi-agent investigation graphs parameterized dynamically by active tenant business entity schemas (e-commerce orders, SaaS workspaces, banking accounts, healthcare patients).
- **Universal Capability Specialists** — `UniversalEntitySpecialistAgent` querying arbitrary tenant entity metamodels with dynamic JSON-Schema generation; preserves legacy domain adapters.
- **Dynamic Reflection Critic** — Decoupled verification loop validating completeness against active tenant schemas, citations, and ground-truth evidence before customer response delivery.
- **Context Auto-Compactor & Rolling Memory** — Automated compaction maintaining full conversational recall across 50+ turn threads while bounding per-turn prompts below 2,000 tokens.
- **Strict Hallucination Fencing** — Answers strictly grounded in retrieved evidence; ungrounded or ambiguous queries trigger safe handoff.
- **Configurable LLM Backends** — Connects to any OpenAI-compatible Chat Completions API with fallback to built-in deterministic test providers.

### 🔌 Declarative Connectors, SSRF Defense & Headless Transport
- **Declarative Enterprise Connectors** — Standardized connectors across REST, GraphQL, and SQL databases configurable directly via the Admin Web UI.
- **Strict SSRF Protection Gate** — Validates all outbound connector destinations at runtime, blocking loopbacks (`127.0.0.0/8`), link-local/cloud metadata (`169.254.169.254`), CG-NAT (`100.64.0.0/10`), and private RFC 1918 subnets.
- **Headless Conversation API & Real-Time SSE Streaming** — Server-Sent Events (`/api/v2/headless/conversations/{id}/messages/stream`) streaming real-time tokens for custom web and mobile integrations.
- **Customer Identity Token Exchange** — Dedicated exchange endpoint (`POST /api/v2/auth/customer-session-exchange`) elevating anonymous IAL 0 sessions to cryptographically verified IAL 2 sessions.
- **Multi-Industry Demonstration Showcase** — Interactive switcher testbed demonstrating live query execution and metamodels across E-Commerce, SaaS (CloudPulse), Banking (Apex Bank), and Healthcare (MediCare).

### ⚡ Distributed Resilience & Enterprise Scalability
- **Redis Distributed Circuit Breakers** — Cluster-wide failure tracking with atomic Lua state transitions (`CLOSED` -> `OPEN` -> `HALF_OPEN`), thundering-herd trial probe leasing, and in-memory fallbacks.
- **Global Sliding-Window Rate Limiting** — Multi-token rate limiter backed by Redis sorted sets protecting upstream enterprise endpoints.
- **Two-Phase Action Execution & Human Confirmation** — High-impact business actions automatically route to the pending actions approval queue (`/actions/pending`).
- **Cryptographic Idempotency** — Action execution enforces unique idempotency keys to eliminate duplicate transactions.

### 🛡️ Enterprise Security, RBAC & Compliance Audit
- **Tenant Scoping & Trusted Authorization** — Organization identity strictly derived from cryptographically verified user JWT claims; unauthenticated header spoofing rejected.
- **Fine-Grained Permission Matrix & Auditor Role** — Enterprise permissions (`conversations:read/write`, `actions:approve`, `knowledge:publish`, `audit:read/export`, `connectors:manage`) with dedicated `auditor` role.
- **Visual Audit Trace Timeline** — Interactive audit modal in Admin Web displaying agent reasoning traces, retrieved citations, and actor attribution.
- **Compliance Audit Exports** — Export tamper-evident audit journals in CSV or JSON format with spreadsheet formula injection neutralization.

### 📚 Knowledge Base & Semantic Search (RAG)
- **Document Ingestion Pipeline** — Ingest `.md`, `.txt`, and documentation files with automated sliding-window chunking (~700 tokens).
- **Native Vector Indexing** — High-performance vector storage powered by PostgreSQL 16 and `pgvector` with HNSW cosine distance indexing.
- **Verifiable Citations** — Generated answers include citation chips linking directly to source passages and confidence scores.
- **Knowledge Admin Console** — Full management interface to upload, review, publish, and test semantic retrieval against chunks.
- **Human-in-the-Loop Approval Gate** — High-risk actions automatically route to the pending actions approval queue (`/actions/pending`).
- **Cryptographic Idempotency** — Action execution enforces unique idempotency keys to eliminate duplicate transactions.

### 🌐 Omnichannel Message Ingestion
- **Normalized Ingestion Gateway** — Uniform message schema across heterogeneous communication channels.
- **Multi-Channel Adapters** — Ready-to-use adapters for Web Widget, Email, SMS, WhatsApp, and Webhooks.
- **Identity Resolution** — Maps customer phone numbers, emails, and external handles to persistent customer records.

### 👥 Human-AI Collaboration & Live Takeover
- **Safe Staff Takeover** — Human agents can take over active conversations with a single click from the Admin Inbox (`/inbox`).
- **AI Silencing Invariant** — AI automation is permanently muted upon staff takeover to prevent conflicting responses.
- **Agent Workspace** — Filter conversations by lifecycle state (`bot_active`, `waiting_for_staff`, `staff_active`, `resolved`).
- **Internal Collaboration** — Private internal notes, ticket reassignment, and QA auditing workflows.

### 🎨 Embeddable Customer Chat Widget
- **Shadow DOM Isolation** — Mounts inside an isolated Shadow DOM root, guaranteeing zero CSS collisions with host store themes.
- **Lightweight & Privacy-Preserving** — Low-overhead bundle built with React 18 and Vite; session tokens held in memory with no tracking cookies.
- **Rich Component Support** — Renders interactive citation chips, status badges, action confirmation cards, and survey dialogs.

### 📊 Support Operations & SLA Engine
- **SLA Tracking & Alerts** — Monitors First Response Time (FRT) and Time to Resolution (TTR) against configured service tier targets.
- **Customer Satisfaction (CSAT)** — Built-in post-resolution rating surveys and sentiment tracking.
- **Operational Analytics** — Metrics on ticket volume, automated resolution rates, and agent response efficiency.

---

## 📦 Repository Structure

| Directory | Description | Tech Stack |
|:---|:---|:---|
| [api](api) | Core gateway and backend handling triage, RAG retrieval, connectors, actions, and omnichannel routing | Python 3.11 / FastAPI / SQLAlchemy 2 / Alembic |
| [admin-web](admin-web) | Unified admin and agent workspace with live inbox, knowledge manager, action approvals, and SLA metrics | TypeScript / React 19 / Vite / Tailwind CSS |
| [widget](widget) | Embeddable, Shadow DOM-isolated customer service chat widget bundle | TypeScript / React 18 / Vite |
| [docs](docs) | Architecture diagrams, API specifications, threat models, and operational runbooks | Markdown |
| [e2e](e2e) | Automated end-to-end integration and browser journey test suite | Playwright / TypeScript |
| [scripts](scripts) | Development lifecycle, database migrations, admin seeding, and smoke testing scripts | Bash / Python |

---

## 🏗️ System Architecture

```mermaid
graph TB
  subgraph CustomerSurfaces["Customer Touchpoints"]
    WIDGET["Web Chat Widget (Shadow DOM)"]
    OMNI["Omnichannel Adapters (Email, SMS, WhatsApp, Webhooks)"]
  end

  subgraph GatewayLayer["API Gateway & Core Services (FastAPI)"]
    ROUTER["API Router (/api/v1)"]
    AUTH["Auth & Multi-Tenancy (JWT / RBAC)"]
    TRIAGE["Triage & Assistant Service"]
    RETRIEVAL["Vector Retrieval Service"]
    CONNECTORS["Enterprise Connectors (REST, GraphQL, SQL)"]
    ACTIONS["Action Engine & Idempotency"]
    SLA["SLA & Operations Engine"]
  end

  subgraph AdminSurface["Agent & Supervisor Workspace"]
    ADMIN["Admin Web Console (React 19)"]
  end

  subgraph DataLayer["Data & Persistence"]
    DB[("PostgreSQL 16 + pgvector")]
    REDIS[("Redis 7 & Celery Workers")]
  end

  WIDGET --> ROUTER
  OMNI --> ROUTER
  ADMIN --> ROUTER
  ROUTER --> AUTH
  ROUTER --> TRIAGE
  TRIAGE --> RETRIEVAL
  TRIAGE --> CONNECTORS
  TRIAGE --> ACTIONS
  ROUTER --> SLA
  RETRIEVAL --> DB
  CONNECTORS --> DB
  ACTIONS --> DB
  SLA --> DB
  ROUTER --> DB
  ROUTER -.-> REDIS
```

---

## 🎨 Storefront Widget Embedding

Embed the lightweight widget in any storefront or web application:

```html
<!-- Load MCSS Widget bundle -->
<script src="https://support.yourdomain.com/mcss-widget.js"></script>
<script>
  window.MCSSWidget.init({
    apiBaseUrl: "https://support.yourdomain.com/api/v1",
    title: "Customer Support",
    accentColor: "#1e40af",
    position: "bottom-right"
  });
</script>
```

---

## ⚙️ Configuration

Copy `.env.example` to `.env` and configure key application settings:

| Variable | Description | Default |
|:---|:---|:---|
| `MCSS_ENV` | Runtime environment (`development`, `test`, `production`) | `development` |
| `MCSS_API_HOST` | API bind address | `0.0.0.0` |
| `MCSS_API_PORT` | API bind port | `8000` |
| `MCSS_DATABASE_URL` | PostgreSQL connection string with `psycopg` driver | `postgresql+psycopg://mcss:change-me@postgres:5432/mcss` |
| `MCSS_JWT_SECRET` | Secret key for JWT token signing (≥ 32 chars in production) | Required |
| `MCSS_CORS_ORIGINS` | Comma-separated list of allowed CORS origins | `http://localhost:5173,http://localhost:5174` |
| `MCSS_WIDGET_ALLOWED_ORIGIN` | Allowed origin header for widget session creation | `http://localhost:5174` |
| `MCSS_LLM_BASE_URL` | OpenAI-compatible endpoint URL or `fake` for test mode | `fake` |
| `MCSS_LLM_API_KEY` | API key for LLM provider | Optional in `fake` mode |
| `MCSS_LLM_CHAT_MODEL` | LLM model identifier (e.g., `gpt-4o`, `claude-3-5-sonnet`) | Provider-specific |
| `MCSS_EMBEDDING_MODEL` | Text embedding model identifier | Provider-specific |
| `MCSS_EMBEDDING_DIMENSIONS` | Vector dimension size for pgvector embeddings | `1536` |
| `MCSS_RETRIEVAL_MIN_SCORE` | Minimum cosine similarity threshold for vector retrieval | `0.72` |
| `MCSS_ADMIN_EMAIL` | Default bootstrap administrator email | `admin@example.com` |
| `MCSS_ADMIN_PASSWORD` | Default bootstrap administrator password | Required |

---

## 🧪 Testing & Verification

```bash
# Run backend unit and integration test suite
docker compose exec api pytest -q

# Run backend tests with code coverage
docker compose exec api pytest --cov=app --cov-report=term-missing

# Run frontend TypeScript type checks
docker compose exec admin-web npm run type-check
docker compose exec widget npm run type-check

# Run production asset builds
docker compose exec admin-web npm run build
docker compose exec widget npm run build

# Run automated end-to-end browser tests
docker compose exec e2e npx playwright test
```

---

## 📋 System Requirements

- **CPU**: ≥ 2 Cores (≥ 4 Cores recommended for high-load production)
- **RAM**: ≥ 4 GiB (≥ 8 GiB recommended)
- **OS**: Linux / macOS / WSL2
- **Runtimes**: Docker 24+ with Docker Compose v2+

---

## 📄 Documentation

- [Architecture Guide](docs/architecture.md) — Service boundaries, data flows, and subsystem interactions.
- [API Reference](docs/api.md) — Comprehensive REST API endpoint definitions and schemas.
- [Security & Threat Model](docs/threat-model.md) — STRIDE threat analysis, asset boundaries, and mitigations.
- [Operations Runbook](docs/runbook.md) — Deployment, health monitoring, backup, and incident response procedures.
- [Architecture Decision Log](docs/decisions.md) — Historical architectural decisions and technical trade-offs.

---

## 📜 License

This project is licensed under the Apache-2.0 License. See the [LICENSE](LICENSE) file for details.
